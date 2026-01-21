"""Test find_matching_pairs function against CSV test data."""
import csv
from datetime import datetime
from pathlib import Path

from matcher import find_matching_pairs


def test_find_matching_pairs_with_csv_data():
    """Test find_matching_pairs against CSV test data with max_business_days=1."""
    # Load CSV test data
    csv_path = Path(__file__).parent / "match_test_1_bus_day.csv"
    transactions = []
    expected_matches = {}

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Create transaction in Firefly III format
            tx = {
                "id": str(row["id"]),
                "attributes": {
                    "transactions": [{
                        "id": str(row["id"]),
                        "date": datetime.strptime(row["date"], "%Y-%m-%d %H:%M"),
                        "amount": row["amount"],
                        "currency_id": row["currency_id"],
                        "source_id": row["source_id"] if row["source_id"] else None,
                        "destination_id": row["destination_id"] if row["destination_id"] else None,
                        "transaction_journal_id": f"journal_{row['id']}",
                    }]
                }
            }
            transactions.append(tx)

            # Parse expected matches from correct_match_ids column
            # Store as list (empty if no matches expected)
            if row["correct_match_ids"]:
                match_ids = [m.strip() for m in row["correct_match_ids"].split(",")]
                expected_matches[row["id"]] = match_ids
            else:
                expected_matches[row["id"]] = []

    # Separate deposits and withdrawals
    deposits = []
    withdrawals = []

    for tx in transactions:
        split = tx["attributes"]["transactions"][0]
        if split["destination_id"] is not None:
            deposits.append(tx)
        elif split["source_id"] is not None:
            withdrawals.append(tx)

    # Run find_matching_pairs with max_business_days=1
    results = find_matching_pairs(deposits, withdrawals, max_business_days=1)

    # Build set of deposit IDs that appear in results
    result_deposit_ids = {result.deposit["id"] for result in results}

    # Validate results against expected matches
    for deposit in deposits:
        deposit_id = deposit["id"]

        if deposit_id not in expected_matches:
            # Skip deposits that aren't in the CSV (shouldn't happen)
            continue

        expected_ids = expected_matches[deposit_id]

        if expected_ids:
            # Deposit should have matches - verify it's in results with correct matches
            assert deposit_id in result_deposit_ids, (
                f"Deposit {deposit_id} expected to have matches {expected_ids}, "
                f"but was not in results"
            )

            # Find the result for this deposit
            result = next(r for r in results if r.deposit["id"] == deposit_id)

            # Build actual match IDs (primary + alternatives)
            actual_ids = [result.primary_match.withdrawal["id"]]
            actual_ids.extend([alt.withdrawal["id"] for alt in result.alternatives])

            # Assert they match
            assert actual_ids == expected_ids, (
                f"Deposit {deposit_id}: expected matches {expected_ids}, "
                f"but got {actual_ids}"
            )
        else:
            # Deposit should have NO matches - verify it's NOT in results
            assert deposit_id not in result_deposit_ids, (
                f"Deposit {deposit_id} expected to have no matches, "
                f"but was found in results"
            )
