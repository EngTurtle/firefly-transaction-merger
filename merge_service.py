"""Background merge service for Firefly III transactions."""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import firefly_client
from matcher import parse_date, prepare_merge_update
from utils import DEBUG, log_exception


class MergeUpdateError(Exception):
    """Exception raised when transaction update fails during merge."""

    pass


class MergeDeleteError(Exception):
    """Exception raised when transaction delete fails after successful update."""

    pass


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
    error_type: Optional[str] = None  # "update_failed", "delete_failed_after_update", "other"
    api_error_message: Optional[str] = None  # Raw API error message for display
    result: Optional[dict] = None
    created_at: float = 0.0
    completed_at: Optional[float] = None


# In-memory job store
job_store: dict[str, MergeJob] = {}


def merge_pair(client, deposit_id: str, withdrawal_id: str) -> dict:
    """Merge a deposit/withdrawal pair into a transfer (synchronous).

    Args:
        client: Firefly API client
        deposit_id: ID of deposit transaction
        withdrawal_id: ID of withdrawal transaction

    Returns:
        dict with source_name and destination_name on success

    Raises:
        Exception on failure
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

    # CRITICAL: Delete MUST only happen if update succeeds
    # If update fails, wrap in custom exception and propagate
    try:
        firefly_client.update_transaction(client, earlier_id, update_data)
    except Exception as e:
        raise MergeUpdateError(
            f"Failed to update transaction {earlier_id} to transfer. "
            f"Original error: {type(e).__name__}: {str(e)}"
        ) from e

    # Update succeeded - now safe to delete the later transaction
    try:
        firefly_client.delete_transaction(client, later_id)
    except Exception as e:
        # Delete failed but update succeeded - this is a critical problem
        # The earlier transaction is now a transfer, but later one still exists
        logger = logging.getLogger(__name__)
        logger.error(
            f"CRITICAL: Updated transaction {earlier_id} to transfer, "
            f"but failed to delete {later_id}. Manual cleanup required."
        )
        raise MergeDeleteError(
            f"CRITICAL: Successfully updated transaction {earlier_id} to transfer, "
            f"but failed to delete transaction {later_id}. Manual cleanup required. "
            f"Original error: {type(e).__name__}: {str(e)}"
        ) from e

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
    """Merge a deposit/withdrawal pair into a transfer (async).

    Runs blocking I/O operations in thread pool to avoid blocking event loop.

    Args:
        firefly_url: Firefly III instance URL
        firefly_token: API token
        deposit_id: ID of deposit transaction
        withdrawal_id: ID of withdrawal transaction

    Returns:
        dict with source_name and destination_name on success

    Raises:
        Exception on failure
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

    # CRITICAL: Delete MUST only happen if update succeeds
    # If update fails, wrap in custom exception and propagate
    try:
        await asyncio.to_thread(
            firefly_client.update_transaction, client, earlier_id, update_data
        )
    except Exception as e:
        raise MergeUpdateError(
            f"Failed to update transaction {earlier_id} to transfer. "
            f"Original error: {type(e).__name__}: {str(e)}"
        ) from e

    # Update succeeded - now safe to delete the later transaction
    try:
        await asyncio.to_thread(firefly_client.delete_transaction, client, later_id)
    except Exception as e:
        # Delete failed but update succeeded - this is a critical problem
        # The earlier transaction is now a transfer, but later one still exists
        logger = logging.getLogger(__name__)
        logger.error(
            f"CRITICAL: Updated transaction {earlier_id} to transfer, "
            f"but failed to delete {later_id}. Manual cleanup required."
        )
        raise MergeDeleteError(
            f"CRITICAL: Successfully updated transaction {earlier_id} to transfer, "
            f"but failed to delete transaction {later_id}. Manual cleanup required. "
            f"Original error: {type(e).__name__}: {str(e)}"
        ) from e

    return {
        "source_name": withdrawal_split.get("source_name", "Unknown"),
        "destination_name": deposit_split.get("destination_name", "Unknown"),
    }


async def process_merge_job(job_id: str) -> None:
    """Background task that processes a merge job and updates job store.

    Args:
        job_id: UUID of the job to process
    """
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

    except MergeUpdateError as e:
        # Update operation failed - no data corruption
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.error_type = "update_failed"
        # Extract original API error message from the wrapped exception
        job.api_error_message = str(e.__cause__) if e.__cause__ else str(e)
        job.completed_at = time.time()
        logger.error(f"Job {job_id} failed during update: {e}")
        if DEBUG:
            log_exception(e, f"process_merge_job {job_id}")

    except MergeDeleteError as e:
        # Delete failed after successful update - CRITICAL
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.error_type = "delete_failed_after_update"
        # Extract original API error message from the wrapped exception
        job.api_error_message = str(e.__cause__) if e.__cause__ else str(e)
        job.completed_at = time.time()
        logger.error(f"Job {job_id} CRITICAL FAILURE - partial merge: {e}")
        if DEBUG:
            log_exception(e, f"process_merge_job {job_id}")

    except Exception as e:
        # Other unexpected error
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.error_type = "other"
        # For unexpected errors, use the error directly
        job.api_error_message = str(e)
        job.completed_at = time.time()
        logger.error(f"Job {job_id} failed with unexpected error: {e}")
        if DEBUG:
            log_exception(e, f"process_merge_job {job_id}")


async def cleanup_old_jobs() -> None:
    """Periodically remove completed/failed jobs older than 1 hour.

    Runs every 5 minutes and removes jobs that completed more than 1 hour ago.
    """
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
