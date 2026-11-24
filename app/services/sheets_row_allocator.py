"""
Google Sheets Deterministic Row Allocator Service.

Architecture & Responsibilities:
- Assigns permanent, unique, monotonically increasing Google Sheets row numbers
  to attendance records in `sheets_sync_queue`.
- Guarantees write serialization using SQLite's `BEGIN IMMEDIATE;` transaction.
- Strictly adheres to the historical spreadsheet row schema:
  Row 1: Header
  Row 2: Existing synthetic baseline record (-92022)
  Row 3: Attendance ID 1 (MANUAL-001)
  Row 4: Attendance ID 2 (MANUAL-002)
  Row 5+: Future attendance records (initial next_sheet_row = 5)
- Guarantees idempotency: repeatedly requesting an allocated record returns the
  existing row number without consuming counter numbers.
- Guarantees no row reuse: once allocated, row numbers remain permanently bound
  even across retries, failures, or dead-letter transitions.
- Atomic commit: metadata counter advancement and queue updates occur in the
  same atomic transaction.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union, Sequence, Dict

from flask import current_app, has_app_context
from app.config import get_config
from app.database import get_db_connection, close_db_connection

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = ("pending", "in_progress", "synced", "failed", "dead_letter")
MINIMUM_VALID_SHEET_ROW = 5


class AllocatorError(Exception):
    """Base exception for all sheets row allocator errors."""
    pass


class AllocatorInputError(AllocatorError):
    """Raised when input queue IDs are invalid (non-integer, <= 0, wrong type)."""
    pass


class AllocatorRecordNotFoundError(AllocatorError):
    """Raised when a requested queue record does not exist in sheets_sync_queue."""
    pass


class AllocatorMetadataError(AllocatorError):
    """Raised when sheets_sync_meta table is missing row id=1 or next_sheet_row < 5."""
    pass


class AllocatorConsistencyError(AllocatorError):
    """Raised when metadata counter is inconsistent with assigned row numbers."""
    pass


class AllocatorInvariantError(AllocatorError):
    """Raised when an illegal record invariant is detected (e.g. synced record without row)."""
    pass


class AllocatorConcurrencyError(AllocatorError):
    """Raised when concurrency conflicts or lock timeouts occur during row allocation."""
    pass


class AllocatorLockTimeoutError(AllocatorConcurrencyError):
    """Raised when SQLite database lock timeout occurs."""
    pass


def _get_utc_now_iso() -> str:
    """Return current UTC timestamp formatted as ISO 8601 string ending in 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_connection(
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None,
    timeout_seconds: float = 30.0,
) -> tuple[sqlite3.Connection, bool]:
    """
    Resolve or establish database connection.
    Returns (conn, own_connection).
    """
    if conn is not None:
        return conn, False

    if db_path is None:
        if has_app_context() and current_app.config.get("DATABASE_PATH"):
            db_path = current_app.config.get("DATABASE_PATH")
        else:
            db_path = get_config().DATABASE_PATH

    active_conn = get_db_connection(db_path)
    active_conn.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)};")
    return active_conn, True


def get_next_sheet_row(
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """
    Read the current `next_sheet_row` counter from `sheets_sync_meta`.

    :param db_path: Optional SQLite database path.
    :param conn: Optional active SQLite connection.
    :return: Current integer counter value.
    """
    active_conn, own_conn = _resolve_connection(db_path, conn)
    try:
        cursor = active_conn.cursor()
        cursor.execute("SELECT id, next_sheet_row FROM sheets_sync_meta WHERE id = 1;")
        row = cursor.fetchone()
        if not row:
            raise AllocatorMetadataError("sheets_sync_meta row with id=1 does not exist.")
        return row["next_sheet_row"]
    finally:
        if own_conn:
            close_db_connection(active_conn)


def get_allocated_sheet_row(
    queue_id: int,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """
    Read the assigned `sheet_row_number` for a queue record, or None if unassigned.

    :param queue_id: Integer primary key in sheets_sync_queue.
    :param db_path: Optional SQLite database path.
    :param conn: Optional active SQLite connection.
    :return: Assigned sheet row number (integer) or None.
    """
    if isinstance(queue_id, bool) or not isinstance(queue_id, int) or queue_id <= 0:
        raise AllocatorInputError(f"Invalid queue_id '{queue_id}': must be a positive integer.")

    active_conn, own_conn = _resolve_connection(db_path, conn)
    try:
        cursor = active_conn.cursor()
        cursor.execute("SELECT sheet_row_number FROM sheets_sync_queue WHERE id = ?;", (queue_id,))
        row = cursor.fetchone()
        if not row:
            raise AllocatorRecordNotFoundError(f"Queue record id={queue_id} does not exist.")
        return row["sheet_row_number"]
    finally:
        if own_conn:
            close_db_connection(active_conn)


def allocate_sheet_rows(
    queue_ids: Union[int, Sequence[int]],
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None,
    timeout_seconds: float = 30.0,
) -> Dict[int, int]:
    """
    Transactionally allocates permanent, unique Google Sheets row numbers to queue records.

    Guarantees:
    - Atomicity: rows and metadata counter are updated in a single BEGIN IMMEDIATE transaction.
    - Idempotency: repeated requests for already allocated records return existing row numbers.
    - Determinism: unallocated records are processed in ascending queue ID order.
    - Monotonicity: counter advances by the exact number of new assignments and never decrements.
    - Integrity: verified against sheets_sync_meta constraints and partial unique index.

    :param queue_ids: Single queue ID or sequence of queue IDs.
    :param db_path: Optional SQLite database path (used when conn is None).
    :param conn: Optional existing SQLite connection.
    :param timeout_seconds: SQLite busy timeout in seconds.
    :return: Dictionary mapping {queue_id: sheet_row_number} for all requested records.
    :raises AllocatorInputError: If input format or any ID is invalid.
    :raises AllocatorRecordNotFoundError: If any requested queue ID is missing.
    :raises AllocatorMetadataError: If sheets_sync_meta is missing, corrupt, or invalid.
    :raises AllocatorConsistencyError: If metadata counter is <= highest allocated row.
    :raises AllocatorInvariantError: If record is in an illegal state (e.g. synced with NULL row).
    :raises AllocatorConcurrencyError: If transaction or constraint conflict occurs.
    :raises AllocatorLockTimeoutError: If SQLite lock acquisition times out.
    """
    # 1. Input normalization and validation
    if isinstance(queue_ids, bool):
        raise AllocatorInputError("queue_ids cannot be a boolean.")
    elif isinstance(queue_ids, int):
        id_list = [queue_ids]
    elif hasattr(queue_ids, "__iter__") and not isinstance(queue_ids, (str, bytes, dict)):
        id_list = list(queue_ids)
    else:
        raise AllocatorInputError(
            f"queue_ids must be an int or sequence of ints, got {type(queue_ids).__name__}."
        )

    if not id_list:
        return {}

    for qid in id_list:
        if isinstance(qid, bool) or not isinstance(qid, int):
            raise AllocatorInputError(
                f"Invalid queue ID '{qid}': must be an integer, got {type(qid).__name__}."
            )
        if qid <= 0:
            raise AllocatorInputError(
                f"Invalid queue ID '{qid}': must be a positive integer (> 0)."
            )

    sorted_unique_ids = sorted(set(id_list))

    # 2. Connection resolution
    active_conn, own_conn = _resolve_connection(db_path, conn, timeout_seconds=timeout_seconds)

    # 3. Transaction management setup
    manage_tx = False
    try:
        if own_conn or not active_conn.in_transaction:
            try:
                active_conn.execute("BEGIN IMMEDIATE;")
                manage_tx = True
            except sqlite3.OperationalError as e:
                err_msg = str(e).lower()
                if "locked" in err_msg or "busy" in err_msg:
                    raise AllocatorLockTimeoutError(
                        f"SQLite lock timeout acquiring write lock: {e}"
                    ) from e
                raise AllocatorConcurrencyError(
                    f"Operational error starting immediate transaction: {e}"
                ) from e

        cursor = active_conn.cursor()

        # 4. Metadata verification (singleton row id=1)
        cursor.execute("SELECT COUNT(*) AS meta_count FROM sheets_sync_meta;")
        meta_count = cursor.fetchone()["meta_count"]
        if meta_count != 1:
            raise AllocatorMetadataError(
                f"sheets_sync_meta has unexpected row count: {meta_count} (expected exactly 1)."
            )

        cursor.execute("SELECT id, next_sheet_row FROM sheets_sync_meta WHERE id = 1;")
        meta_row = cursor.fetchone()
        if not meta_row:
            raise AllocatorMetadataError("sheets_sync_meta row with id=1 does not exist.")

        next_sheet_row = meta_row["next_sheet_row"]
        if (
            isinstance(next_sheet_row, bool)
            or not isinstance(next_sheet_row, int)
            or next_sheet_row < MINIMUM_VALID_SHEET_ROW
        ):
            raise AllocatorMetadataError(
                f"Invalid next_sheet_row in metadata: {next_sheet_row}. "
                f"Must be integer >= {MINIMUM_VALID_SHEET_ROW}."
            )

        # 5. Counter consistency check
        cursor.execute("SELECT MAX(sheet_row_number) AS max_assigned FROM sheets_sync_queue;")
        max_assigned_row = cursor.fetchone()["max_assigned"]
        if max_assigned_row is not None and next_sheet_row <= max_assigned_row:
            raise AllocatorConsistencyError(
                f"Metadata next_sheet_row ({next_sheet_row}) is inconsistent with highest "
                f"allocated row ({max_assigned_row}). Counter must be strictly greater than max row."
            )

        # 6. Fetch requested queue records
        placeholders = ",".join("?" for _ in sorted_unique_ids)
        cursor.execute(
            f"""
            SELECT id, attendance_id, sync_status, sheet_row_number, retry_count
            FROM sheets_sync_queue
            WHERE id IN ({placeholders})
            ORDER BY id ASC;
            """,
            sorted_unique_ids,
        )
        fetched_rows = cursor.fetchall()
        found_ids = {row["id"] for row in fetched_rows}
        missing_ids = set(sorted_unique_ids) - found_ids
        if missing_ids:
            raise AllocatorRecordNotFoundError(
                f"Queue records not found for IDs: {sorted(missing_ids)}"
            )

        # 7. Record validation and partition (assigned vs unassigned)
        results: Dict[int, int] = {}
        unassigned_ids = []

        for row in fetched_rows:
            qid = row["id"]
            status = row["sync_status"]
            current_row = row["sheet_row_number"]

            if status not in ALLOWED_STATUSES:
                raise AllocatorInvariantError(
                    f"Queue record id={qid} has unsupported sync_status: '{status}'."
                )

            # Invariant check: synced records MUST have an assigned row
            if status == "synced" and current_row is None:
                raise AllocatorInvariantError(
                    f"Queue record id={qid} has sync_status='synced' but sheet_row_number is NULL. "
                    "Invariant violation: synced records must have a permanently assigned row."
                )

            if current_row is not None:
                results[qid] = current_row
            else:
                unassigned_ids.append(qid)

        # 8. Deterministic assignment for unassigned records
        if unassigned_ids:
            current_counter = next_sheet_row
            now_utc = _get_utc_now_iso()

            for qid in unassigned_ids:
                row_to_assign = current_counter
                try:
                    cursor.execute(
                        """
                        UPDATE sheets_sync_queue
                        SET sheet_row_number = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND sheet_row_number IS NULL;
                        """,
                        (row_to_assign, now_utc, qid),
                    )
                except sqlite3.IntegrityError as e:
                    raise AllocatorConcurrencyError(
                        f"Unique constraint violation during row allocation: {e}"
                    ) from e

                if cursor.rowcount != 1:
                    raise AllocatorConcurrencyError(
                        f"Failed to allocate row for queue id={qid}: row count was "
                        f"{cursor.rowcount} (expected 1). Record may have been modified concurrently."
                    )

                results[qid] = row_to_assign
                current_counter += 1

            # 9. Advance metadata counter atomically
            cursor.execute(
                """
                UPDATE sheets_sync_meta
                SET next_sheet_row = ?,
                    updated_at = ?
                WHERE id = 1;
                """,
                (current_counter, now_utc),
            )
            if cursor.rowcount != 1:
                raise AllocatorMetadataError(
                    f"Failed to update sheets_sync_meta: row count was {cursor.rowcount} (expected 1)."
                )

            logger.info(
                "Allocated %d sheet row(s). next_sheet_row advanced from %d to %d.",
                len(unassigned_ids),
                next_sheet_row,
                current_counter,
            )

        # 10. Commit transaction if managed
        if manage_tx:
            active_conn.commit()

        return results

    except Exception:
        if manage_tx:
            try:
                active_conn.rollback()
            except sqlite3.Error:
                pass
        raise
    finally:
        if own_conn:
            close_db_connection(active_conn)
