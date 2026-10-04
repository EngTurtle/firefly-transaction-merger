"""Test that prepare_merge_update keeps the deleted transaction's metadata."""
from datetime import datetime

from matcher import prepare_merge_update


def test_prepare_merge_update_carries_later_metadata():
    earlier = {
        "date": datetime(2026, 1, 5),
        "transaction_journal_id": "10",
        "destination_id": "2",
        "description": "Payment received",
        "notes": "kept note",
        "tags": ["a", "b"],
        "external_id": "EXT-1",
        "category_name": None,
    }
    later = {
        "date": datetime(2026, 1, 6),
        "source_id": "1",
        "description": "Card payment",
        "notes": "deleted note",
        "tags": ["b", "c"],
        "external_id": "EXT-2",
        "category_name": "Bills",
        "internal_reference": "",
    }

    update = prepare_merge_update(earlier, later, is_deposit_earlier=True, later_id="99")

    assert update["type"] == "transfer"
    assert update["source_id"] == "1"
    assert update["destination_id"] == "2"
    assert update["transaction_journal_id"] == "10"
    assert update["process_date"] == datetime(2026, 1, 6)
    assert update["tags"] == ["a", "b", "c"]
    assert update["category_name"] == "Bills"  # filled because earlier was empty
    assert "external_id" not in update  # earlier value kept, later one goes to notes
    assert "internal_reference" not in update
    assert update["notes"] == (
        "kept note\n\n"
        "Merged with deleted withdrawal #99:\n"
        "- Description: Card payment\n"
        "- Date: 2026-01-06\n"
        "- external_id: EXT-2\n"
        "- Notes:\ndeleted note"
    )


def test_prepare_merge_update_withdrawal_earlier_without_notes():
    earlier = {"date": datetime(2026, 1, 5), "source_id": "1", "transaction_journal_id": "7"}
    later = {"date": datetime(2026, 1, 6), "destination_id": "2", "description": "Transfer in"}

    update = prepare_merge_update(earlier, later, is_deposit_earlier=False, later_id="8")

    assert update["source_id"] == "1"
    assert update["destination_id"] == "2"
    assert "tags" not in update
    assert update["notes"] == (
        "Merged with deleted deposit #8:\n- Description: Transfer in\n- Date: 2026-01-06"
    )
