"""Transaction matching logic for finding withdrawal/deposit pairs."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any


@dataclass
class MatchedPair:
    """A matched deposit/withdrawal pair."""

    deposit: dict[str, Any]
    withdrawal: dict[str, Any]
    deposit_split: dict[str, Any]
    withdrawal_split: dict[str, Any]
    amount: Decimal
    days_apart: int


def parse_date(date_value: datetime) -> date:
    """Extract date from datetime returned by Firefly III API client."""
    return date_value.date()


def count_business_days(start: date, end: date) -> int:
    """Count business days between two dates (excluding weekends)."""
    if start > end:
        start, end = end, start

    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            days += 1
        current += timedelta(days=1)
    return days - 1  # Don't count the start day


def get_transaction_split(tx: dict[str, Any]) -> dict[str, Any] | None:
    """Get the first split from a transaction."""
    splits = tx.get("attributes", {}).get("transactions", [])
    return splits[0] if splits else None


def find_matching_pairs(
    deposits: list[dict[str, Any]],
    withdrawals: list[dict[str, Any]],
    max_business_days: int,
) -> list[MatchedPair]:
    """Find matching deposit/withdrawal pairs.

    A match is defined as:
    - Same currency
    - Same amount (exact match)
    - Different asset accounts (deposit destination != withdrawal source)
    - Within the specified number of business days
    """
    matches = []
    used_withdrawal_ids = set()

    for deposit in deposits:
        deposit_split = get_transaction_split(deposit)
        if not deposit_split:
            continue

        deposit_amount = Decimal(deposit_split.get("amount", "0"))
        deposit_date = parse_date(deposit_split.get("date", ""))
        deposit_dest_id = deposit_split.get("destination_id")
        deposit_currency = deposit_split.get("currency_id")

        for withdrawal in withdrawals:
            if withdrawal["id"] in used_withdrawal_ids:
                continue

            withdrawal_split = get_transaction_split(withdrawal)
            if not withdrawal_split:
                continue

            withdrawal_amount = Decimal(withdrawal_split.get("amount", "0"))
            withdrawal_date = parse_date(withdrawal_split.get("date", ""))
            withdrawal_source_id = withdrawal_split.get("source_id")
            withdrawal_currency = withdrawal_split.get("currency_id")

            # Check if currencies match
            # TODO: Support cross-currency transfers with exchange rate handling
            if deposit_currency != withdrawal_currency:
                continue

            # Check if amounts match exactly
            if deposit_amount != withdrawal_amount:
                continue

            # Check if accounts are different (not the same account)
            if deposit_dest_id == withdrawal_source_id:
                continue

            # Check if within business day window
            days_apart = count_business_days(deposit_date, withdrawal_date)
            if days_apart > max_business_days:
                continue

            # Found a match
            matches.append(
                MatchedPair(
                    deposit=deposit,
                    withdrawal=withdrawal,
                    deposit_split=deposit_split,
                    withdrawal_split=withdrawal_split,
                    amount=deposit_amount,
                    days_apart=days_apart,
                )
            )
            used_withdrawal_ids.add(withdrawal["id"])
            break  # Move to next deposit

    return matches


def prepare_merge_update(
    earlier_split: dict[str, Any],
    later_split: dict[str, Any],
    is_deposit_earlier: bool,
) -> dict[str, Any]:
    """Prepare the update payload for merging transactions.

    Converts the earlier transaction to a transfer and sets process_date
    to the later transaction's date.
    """
    later_date = later_split.get("date", "")

    if is_deposit_earlier:
        # Deposit is earlier: keep it, set source to withdrawal's source
        return {
            "type": "transfer",
            "source_id": later_split.get("source_id"),
            "destination_id": earlier_split.get("destination_id"),
            "process_date": later_date,
            "transaction_journal_id": earlier_split.get("transaction_journal_id"),
        }
    else:
        # Withdrawal is earlier: keep it, set destination to deposit's destination
        return {
            "type": "transfer",
            "source_id": earlier_split.get("source_id"),
            "destination_id": later_split.get("destination_id"),
            "process_date": later_date,
            "transaction_journal_id": earlier_split.get("transaction_journal_id"),
        }
