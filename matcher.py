"""Transaction matching logic for finding withdrawal/deposit pairs."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any


@dataclass
class WithdrawalMatch:
    """A single withdrawal that matches a deposit."""

    withdrawal: dict[str, Any]
    withdrawal_split: dict[str, Any]
    days_apart: int


@dataclass
class MatchedPairWithAlternatives:
    """A deposit with one or more matching withdrawals."""

    deposit: dict[str, Any]
    deposit_split: dict[str, Any]
    primary_match: WithdrawalMatch
    alternatives: list[WithdrawalMatch]
    amount: Decimal


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
        deposit_date = deposit_split["date"].date()
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
            withdrawal_date = withdrawal_split["date"].date()
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


# Copied from the deleted transaction when the kept one leaves them empty.
# Budget and bill are left out: Firefly III only allows them on withdrawals.
MERGE_FILL_FIELDS = (
    "category_name",
    "external_id",
    "internal_reference",
    "external_url",
    "book_date",
    "interest_date",
    "due_date",
    "payment_date",
    "invoice_date",
    "sepa_cc",
    "sepa_ct_op",
    "sepa_ct_id",
    "sepa_db",
    "sepa_country",
    "sepa_ep",
    "sepa_ci",
    "sepa_batch_id",
)


def prepare_merge_update(
    earlier_split: dict[str, Any],
    later_split: dict[str, Any],
    is_deposit_earlier: bool,
    later_id: str,
) -> dict[str, Any]:
    """Prepare the update payload for merging transactions.

    Converts the earlier transaction to a transfer, sets process_date to the
    later transaction's date, and carries over the later transaction's
    metadata, since the later transaction is deleted after the update:
    - tags are combined
    - fields in MERGE_FILL_FIELDS are copied where the earlier one is empty
    - the later one's description, date, notes and any conflicting field
      values are appended to the notes
    """
    update = {
        "type": "transfer",
        "source_id": (later_split if is_deposit_earlier else earlier_split).get("source_id"),
        "destination_id": (earlier_split if is_deposit_earlier else later_split).get("destination_id"),
        "process_date": later_split.get("date", ""),
        "transaction_journal_id": earlier_split.get("transaction_journal_id"),
    }

    later_type = "withdrawal" if is_deposit_earlier else "deposit"
    record = [
        f"Merged with deleted {later_type} #{later_id}:",
        f"- Description: {later_split.get('description')}",
        f"- Date: {later_split['date'].date().isoformat()}",
    ]

    for field in MERGE_FILL_FIELDS:
        later_value = later_split.get(field)
        if later_value in (None, ""):
            continue
        earlier_value = earlier_split.get(field)
        if earlier_value in (None, ""):
            update[field] = later_value
        elif earlier_value != later_value:
            record.append(f"- {field}: {later_value}")

    tags = list(dict.fromkeys((earlier_split.get("tags") or []) + (later_split.get("tags") or [])))
    if tags:
        update["tags"] = tags

    if later_split.get("notes"):
        record.append(f"- Notes:\n{later_split['notes']}")
    update["notes"] = "\n\n".join(n for n in (earlier_split.get("notes"), "\n".join(record)) if n)

    return update
