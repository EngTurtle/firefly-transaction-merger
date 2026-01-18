"""FastAPI application for Firefly III Transaction Merger."""

import logging
import os
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import firefly_client
from matcher import find_matching_pairs, parse_date, prepare_merge_update
from utils import DEBUG, handle_errors, log_exception

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


@app.post("/merge/{deposit_id}/{withdrawal_id}", response_class=HTMLResponse)
@handle_errors(templates, "merge_result.html")
async def merge(request: Request, deposit_id: str, withdrawal_id: str):
    """Merge a deposit/withdrawal pair into a transfer."""
    client = get_client_from_session(request)
    if not client:
        return templates.TemplateResponse(
            "merge_result.html", {"request": request, "error": "Session expired"}
        )

    result = merge_pair(client, deposit_id, withdrawal_id)

    return templates.TemplateResponse(
        "merge_result.html",
        {
            "request": request,
            "source_name": result["source_name"],
            "destination_name": result["destination_name"],
        },
    )


@app.post("/merge-bulk", response_class=HTMLResponse)
@handle_errors(templates, "bulk_merge_result.html")
async def merge_bulk(request: Request, pairs: str = Form(...)):
    """Merge multiple deposit/withdrawal pairs."""
    client = get_client_from_session(request)
    if not client:
        return templates.TemplateResponse(
            "bulk_merge_result.html", {"request": request, "error": "Session expired"}
        )

    pair_list = [p.strip() for p in pairs.split(",") if p.strip()]
    results = []

    for pair in pair_list:
        deposit_id, withdrawal_id = pair.split(":")
        try:
            merge_pair(client, deposit_id, withdrawal_id)
            results.append({"pair": pair, "success": True})
        except Exception as e:
            results.append({"pair": pair, "success": False, "error": str(e)})

    return templates.TemplateResponse(
        "bulk_merge_result.html", {"request": request, "results": results}
    )


if __name__ == "__main__":
    import uvicorn

    log_level = "debug" if DEBUG else "info"
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=log_level)
