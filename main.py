"""FastAPI application for Firefly III Transaction Merger."""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import firefly_client
from firefly_iii_client.rest import ApiException
from matcher import find_matching_pairs
from merge_service import (
    MergeJob,
    cleanup_old_jobs,
    job_store,
    process_merge_job,
)
from utils import DEBUG, handle_errors, json_serial, log_exception

# Configure logging for app modules
_log_level = logging.DEBUG if DEBUG else logging.INFO
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))

for _logger_name in ("firefly_client", "matcher", "utils", "urllib3.connectionpool"):
    _logger = logging.getLogger(_logger_name)
    _logger.setLevel(_log_level)
    _logger.addHandler(_handler)

app = FastAPI(title="Firefly Transaction Merger")
secret_key = os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=secret_key)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.on_event("startup")
async def startup_event():
    """Start background cleanup task."""
    asyncio.create_task(cleanup_old_jobs())
    logging.getLogger(__name__).info("Job cleanup task started")


def get_client_from_session(request: Request):
    """Create API client from session credentials."""
    url = request.session.get("firefly_url")
    token = request.session.get("firefly_token")
    if not url or not token:
        return None
    return firefly_client.create_client(url, token)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Show login page or redirect to search if already logged in."""
    if get_client_from_session(request):
        return RedirectResponse(url="/search", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "logged_in": False}
    )


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, url: str = Form(...), token: str = Form(...)):
    """Validate credentials and create session."""
    try:
        client = firefly_client.create_client(url, token)
        firefly_client.validate_connection(client)

        request.session["firefly_url"] = url
        request.session["firefly_token"] = token

        return RedirectResponse(url="/search", status_code=302)
    except ApiException as e:
        # Firefly API error (invalid credentials, connection error, etc.)
        log_exception(e, "login")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": str(e), "url": url, "logged_in": False},
        )
    except (ValueError, KeyError, TypeError) as e:
        # Invalid URL format or configuration issue
        log_exception(e, "login")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Invalid configuration: {e}", "url": url, "logged_in": False},
        )


@app.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    """Show search form."""
    client = get_client_from_session(request)
    if not client:
        return RedirectResponse(url="/", status_code=302)

    try:
        accounts = firefly_client.get_asset_accounts(client)
    except ApiException as e:
        # Firefly API error - log but continue with empty account list
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to fetch accounts: {e}")
        accounts = []

    today = date.today()
    start_date = (today - timedelta(days=30)).isoformat()
    end_date = today.isoformat()

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "accounts": accounts,
            "start_date": start_date,
            "end_date": end_date,
            "logged_in": True,
        },
    )


@app.post("/search", response_class=HTMLResponse)
@handle_errors(templates, "results.html")
async def search(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    account_id: list[str] = Form(default=[]),
    business_days: int = Form(5),
    limit: int = Form(50),
    order: str = Form("desc"),
):
    """Execute search and return results partial."""
    client = get_client_from_session(request)
    if not client:
        return templates.TemplateResponse(
            "results.html", {"request": request, "error": "Session expired"}
        )

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    # Fetch deposits
    deposits = firefly_client.get_transactions(client, "deposit", start, end, limit)

    # Sort by date
    deposits.sort(
        key=lambda x: x.get("attributes", {}).get("transactions", [{}])[0].get(
            "date", ""
        ),
        reverse=(order == "desc"),
    )

    if limit:
        deposits = deposits[:limit]

    # Fetch withdrawals (need more to find matches)
    withdrawals = firefly_client.get_transactions(
        client, "withdrawal", start, end, None
    )

    # Filter by accounts if specified
    if account_id:
        account_ids = set(account_id)
        deposits = [
            d
            for d in deposits
            if d.get("attributes", {})
            .get("transactions", [{}])[0]
            .get("destination_id")
            in account_ids
        ]

    # Find matches
    matches = find_matching_pairs(deposits, withdrawals, business_days)

    # Pre-serialize alternatives to JSON strings for template
    for match in matches:
        # Convert WithdrawalMatch objects to dicts and serialize with custom handler
        alternatives_dicts = [
            {
                "withdrawal": alt.withdrawal,
                "withdrawal_split": alt.withdrawal_split,
                "days_apart": alt.days_apart,
            }
            for alt in match.alternatives
        ]
        match.alternatives_json = json.dumps(alternatives_dicts, default=json_serial)

    return templates.TemplateResponse(
        "results.html", {"request": request, "matches": matches}
    )


@app.post("/merge/{deposit_id}/{withdrawal_id}")
async def submit_merge(
    request: Request,
    deposit_id: str,
    withdrawal_id: str,
    background_tasks: BackgroundTasks,
):
    """Submit a merge job to background tasks and return job ID."""
    # Get credentials from session
    firefly_url = request.session.get("firefly_url")
    firefly_token = request.session.get("firefly_token")

    if not firefly_url or not firefly_token:
        return {"error": "Session expired", "status": "error"}

    # Create job
    job_id = str(uuid.uuid4())
    job = MergeJob(
        job_id=job_id,
        deposit_id=deposit_id,
        withdrawal_id=withdrawal_id,
        firefly_url=firefly_url,
        firefly_token=firefly_token,
        created_at=time.time(),
    )

    # Store job
    job_store[job_id] = job

    # Add background task
    background_tasks.add_task(process_merge_job, job_id)

    return {"job_id": job_id, "status": "queued"}


@app.get("/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get current status of a merge job."""
    job = job_store.get(job_id)

    if not job:
        return {"status": "not_found"}

    return {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.error,
        "error_type": job.error_type,
        "api_error_message": job.api_error_message,
        "result": job.result,
    }


if __name__ == "__main__":
    import uvicorn

    log_level = "debug" if DEBUG else "info"
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=log_level)
