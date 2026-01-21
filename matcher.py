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


@dataclass
class WithdrawalMatch:
    """A single withdrawal that matches a deposit."""

    withdrawal: dict[str, Any]
    withdrawal_split: dict[str, Any]
    days_apart: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        # Create a copy of the splits to avoid modifying the original
        withdrawal_split_copy = self.withdrawal_split.copy()

        # Convert datetime to string
        if "date" in withdrawal_split_copy:
            date_obj = withdrawal_split_copy["date"]
            withdrawal_split_copy["date"] = date_obj.strftime("%Y-%m-%d") if hasattr(date_obj, "strftime") else str(date_obj)

        return {
            "withdrawal": self.withdrawal,
            "withdrawal_split": withdrawal_split_copy,
            "days_apart": self.days_apart,
        }


@dataclass
class MatchedPairWithAlternatives:
    """A deposit with one or more matching withdrawals."""

    deposit: dict[str, Any]
    deposit_split: dict[str, Any]
    primary_match: WithdrawalMatch
    alternatives: list[WithdrawalMatch]
    amount: Decimal


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
) -> list[MatchedPairWithAlternatives]:
    """Find matching deposit/withdrawal pairs with alternatives.

    A match is defined as:
    - Same currency
    - Same amount (exact match)
    - Different asset accounts (deposit destination != withdrawal source)
    - Within the specified number of business days

    Returns deposits matched with their closest withdrawal (by date),
    plus any alternative matches sorted by date proximity.

    Algorithm:
    1. For each deposit, find ALL matching withdrawals (don't exclude any)
    2. Sort matches by date proximity
    3. Select primary match preferring withdrawals not yet assigned
    4. All other matches become alternatives
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

        # Collect ALL matching withdrawals for this deposit
        # Don't exclude already-used withdrawals - user should see all options
        withdrawal_matches = []

        for withdrawal in withdrawals:
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

            # Found a match - add to list with usage flag
            withdrawal_matches.append(
                WithdrawalMatch(
                    withdrawal=withdrawal,
                    withdrawal_split=withdrawal_split,
                    days_apart=days_apart,
                )
            )

        # If we found any matches, select primary and alternatives
        if withdrawal_matches:
            # Sort by days_apart (ascending - closest first)
            withdrawal_matches.sort(key=lambda m: m.days_apart)

            # Select primary match: prefer unused withdrawals
            # Find first unused withdrawal, or fall back to closest if all are used
            primary_match = None
            primary_index = -1

            for i, match in enumerate(withdrawal_matches):
                if match.withdrawal["id"] not in used_withdrawal_ids:
                    primary_match = match
                    primary_index = i
                    break

            # If all withdrawals are already used, take the closest one anyway
            if primary_match is None:
                primary_match = withdrawal_matches[0]
                primary_index = 0

            # All other matches become alternatives (including the primary if we want)
            # Remove primary from the list to create alternatives
            alternatives = withdrawal_matches[:primary_index] + withdrawal_matches[primary_index + 1:]

            # Mark primary match as used for subsequent deposits
            used_withdrawal_ids.add(primary_match.withdrawal["id"])

            # Create matched pair with alternatives
            matches.append(
                MatchedPairWithAlternatives(
                    deposit=deposit,
                    deposit_split=deposit_split,
                    primary_match=primary_match,
                    alternatives=alternatives,
                    amount=deposit_amount,
                )
            )

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
