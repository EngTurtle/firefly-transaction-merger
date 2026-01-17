"""Wrapper around firefly_iii_client for simplified access."""

from datetime import date
from typing import Any

import firefly_iii_client
from firefly_iii_client import (
    AboutApi,
    AccountsApi,
    TransactionsApi,
    TransactionSplitUpdate,
    TransactionUpdate,
)
from firefly_iii_client.configuration import Configuration
from firefly_iii_client.rest import ApiException


def create_client(url: str, token: str) -> firefly_iii_client.ApiClient:
    """Create a configured Firefly III API client."""
    config = Configuration(host=url.rstrip("/") + "/api")
    config.access_token = token
    return firefly_iii_client.ApiClient(config)


def validate_connection(client: firefly_iii_client.ApiClient) -> dict[str, Any]:
    """Test connection by fetching system info. Returns system info or raises."""
    api = AboutApi(client)
    response = api.get_about()
    return response.data.to_dict()


def get_asset_accounts(client: firefly_iii_client.ApiClient) -> list[dict[str, Any]]:
    """Fetch all asset accounts."""
    api = AccountsApi(client)
    accounts = []
    page = 1
    while True:
        response = api.list_account(type="asset", page=page)
        accounts.extend(acc.to_dict() for acc in response.data)
        if response.meta.pagination.current_page >= response.meta.pagination.total_pages:
            break
        page += 1
    return accounts


def get_transactions(
    client: firefly_iii_client.ApiClient,
    tx_type: str,
    start_date: date,
    end_date: date,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch transactions of a given type within date range."""
    api = TransactionsApi(client)
    transactions = []
    page = 1
    while True:
        response = api.list_transaction(
            type=tx_type,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            page=page,
        )
        for tx in response.data:
            transactions.append(tx.to_dict())
            if limit and len(transactions) >= limit:
                return transactions
        if response.meta.pagination.current_page >= response.meta.pagination.total_pages:
            break
        page += 1
    return transactions


def get_transaction(client: firefly_iii_client.ApiClient, transaction_id: str) -> dict[str, Any]:
    """Fetch a single transaction by ID."""
    api = TransactionsApi(client)
    response = api.get_transaction(transaction_id)
    return response.data.to_dict()


def update_transaction(
    client: firefly_iii_client.ApiClient,
    transaction_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update a transaction."""
    api = TransactionsApi(client)

    split_update = TransactionSplitUpdate(**updates)
    update_data = TransactionUpdate(transactions=[split_update])

    response = api.update_transaction(transaction_id, update_data)
    return response.data.to_dict()


def delete_transaction(client: firefly_iii_client.ApiClient, transaction_id: str) -> None:
    """Delete a transaction."""
    api = TransactionsApi(client)
    api.delete_transaction(transaction_id)
