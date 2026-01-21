"""FastAPI application for Firefly III Transaction Merger."""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import firefly_client
from matcher import find_matching_pairs, parse_date, prepare_merge_update
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


# Cleanup task for old jobs
async def cleanup_old_jobs():
    """Periodically remove completed/failed jobs older than 1 hour."""
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes
        now = time.time()
        to_remove = [
            jid
            for jid, job in job_store.items()
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
            and job.completed_at
            and (now - job.completed_at) > 3600
        ]
        for jid in to_remove:
            del job_store[jid]
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old jobs")


@app.on_event("startup")
async def startup_event():
    """Start background cleanup task."""
    asyncio.create_task(cleanup_old_jobs())
    logging.getLogger(__name__).info("Job cleanup task started")


# Job tracking infrastructure
class JobStatus(str, Enum):
    """Status of a background merge job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MergeJob:
    """Represents a background merge job."""

    job_id: str
    deposit_id: str
    withdrawal_id: str
    firefly_url: str
    firefly_token: str
    status: JobStatus = JobStatus.PENDING
    error: Optional[str] = None
    result: Optional[dict] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None


# In-memory job store
job_store: dict[str, MergeJob] = {}


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
    except Exception as e:
        log_exception(e, "login")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": str(e), "url": url, "logged_in": False},
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
    except Exception:
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


def merge_pair(client, deposit_id: str, withdrawal_id: str) -> dict:
    """Merge a deposit/withdrawal pair into a transfer.

    Returns dict with source_name and destination_name on success.
    Raises exception on failure.
    """
    # Fetch both transactions
    deposit = firefly_client.get_transaction(client, deposit_id)
    withdrawal = firefly_client.get_transaction(client, withdrawal_id)

    deposit_split = deposit.get("attributes", {}).get("transactions", [{}])[0]
    withdrawal_split = withdrawal.get("attributes", {}).get("transactions", [{}])[0]

    deposit_date = parse_date(deposit_split.get("date", ""))
    withdrawal_date = parse_date(withdrawal_split.get("date", ""))

    # Determine which is earlier
    is_deposit_earlier = deposit_date <= withdrawal_date

    if is_deposit_earlier:
        earlier_id = deposit_id
        earlier_split = deposit_split
        later_id = withdrawal_id
        later_split = withdrawal_split
    else:
        earlier_id = withdrawal_id
        earlier_split = withdrawal_split
        later_id = deposit_id
        later_split = deposit_split

    # Prepare and apply update
    update_data = prepare_merge_update(earlier_split, later_split, is_deposit_earlier)
    firefly_client.update_transaction(client, earlier_id, update_data)

    # Delete the later transaction
    firefly_client.delete_transaction(client, later_id)

    return {
        "source_name": withdrawal_split.get("source_name", "Unknown"),
        "destination_name": deposit_split.get("destination_name", "Unknown"),
    }


async def merge_pair_async(
    firefly_url: str,
    firefly_token: str,
    deposit_id: str,
    withdrawal_id: str,
) -> dict:
    """Async version of merge_pair that runs blocking I/O in thread pool.

    Returns dict with source_name and destination_name on success.
    Raises exception on failure.
    """
    # Create client
    client = firefly_client.create_client(firefly_url, firefly_token)

    # Fetch both transactions (run in thread pool to avoid blocking)
    deposit = await asyncio.to_thread(
        firefly_client.get_transaction, client, deposit_id
    )
    withdrawal = await asyncio.to_thread(
        firefly_client.get_transaction, client, withdrawal_id
    )

    deposit_split = deposit.get("attributes", {}).get("transactions", [{}])[0]
    withdrawal_split = withdrawal.get("attributes", {}).get("transactions", [{}])[0]

    deposit_date = parse_date(deposit_split.get("date", ""))
    withdrawal_date = parse_date(withdrawal_split.get("date", ""))

    # Determine which is earlier
    is_deposit_earlier = deposit_date <= withdrawal_date

    if is_deposit_earlier:
        earlier_id = deposit_id
        earlier_split = deposit_split
        later_id = withdrawal_id
        later_split = withdrawal_split
    else:
        earlier_id = withdrawal_id
        earlier_split = withdrawal_split
        later_id = deposit_id
        later_split = deposit_split

    # Prepare and apply update (run in thread pool)
    update_data = prepare_merge_update(earlier_split, later_split, is_deposit_earlier)
    await asyncio.to_thread(
        firefly_client.update_transaction, client, earlier_id, update_data
    )

    # Delete the later transaction (run in thread pool)
    await asyncio.to_thread(firefly_client.delete_transaction, client, later_id)

    return {
        "source_name": withdrawal_split.get("source_name", "Unknown"),
        "destination_name": deposit_split.get("destination_name", "Unknown"),
    }


async def process_merge_job(job_id: str):
    """Background task that processes a merge job and updates job store."""
    logger = logging.getLogger(__name__)
    job = job_store.get(job_id)

    if not job:
        logger.error(f"Job {job_id} not found in store")
        return

    try:
        # Update status to processing
        job.status = JobStatus.PROCESSING
        logger.info(
            f"Processing merge job {job_id}: {job.deposit_id}/{job.withdrawal_id}"
        )

        # Perform the merge
        result = await merge_pair_async(
            job.firefly_url, job.firefly_token, job.deposit_id, job.withdrawal_id
        )

        # Update job with success
        job.status = JobStatus.COMPLETED
        job.result = result
        job.completed_at = time.time()
        logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        # Update job with failure
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.completed_at = time.time()
        logger.error(f"Job {job_id} failed: {e}")
        if DEBUG:
            log_exception(e, f"process_merge_job {job_id}")


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
        "result": job.result,
    }


if __name__ == "__main__":
    import uvicorn

    log_level = "debug" if DEBUG else "info"
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=log_level)
