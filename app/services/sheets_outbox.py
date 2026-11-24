"""
Google Sheets Local Outbox Queue Service with Claim-Token Fencing.

Architecture & Timestamp Conventions:
- INTERNAL DATABASE TIMESTAMPS:
  All timestamps stored in `sheets_sync_queue` (created_at, updated_at, next_retry_at,
  lease_expires_at, synced_at) use ISO 8601 UTC formatted strings ending in 'Z'
  (e.g., '2026-11-08T11:45:00Z'). This matches established database conventions
  in `attendance.scanned_at` and `admin_users.created_at`.
- APPLICATION & INSTITUTIONAL TIMESTAMPS:
  When displaying dates and times to users or writing report headers, timestamps are
  converted to Asia/Kolkata (UTC+05:30) using `app.time_utils`.
- CLAIM-TOKEN FENCING:
  Guarantees mutual exclusion and prevents stale-worker race conditions.
  Every successful claim increments `claim_token` atomically.
  Every worker state transition requires the matching `claim_token`, `sync_status = 'in_progress'`,
  and an active unexpired lease.
"""

from contextlib import contextmanager
import logging
import random
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Union, Sequence, Dict, Any, List

from flask import current_app, has_app_context
from app.config import get_config
from app.database import get_db_connection, close_db_connection

logger = logging.getLogger(__name__)

# Supported synchronization states
ALLOWED_STATUSES = ("pending", "in_progress", "synced", "failed", "dead_letter")

# Retry policy defaults
DEFAULT_BASE_DELAY_SECONDS = 15
DEFAULT_MAX_DELAY_SECONDS = 900   # 15 minutes
DEFAULT_MAX_RETRIES = 5
DEFAULT_LEASE_DURATION_SECONDS = 60


class OutboxError(Exception):
    """Base exception for outbox service errors."""
    pass


class ClaimFencingError(OutboxError):
    """Raised when a claim token or lease fencing invariant is violated."""
    pass


def _get_active_db_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Helper to resolve database connection."""
    if db_path is None:
        if has_app_context() and current_app.config.get("DATABASE_PATH"):
            db_path = current_app.config.get("DATABASE_PATH")
        else:
            db_path = get_config().DATABASE_PATH
    return get_db_connection(db_path)


def _get_utc_now(now_dt: Optional[datetime] = None) -> tuple[datetime, str]:
    """Helper returning (datetime_obj, iso_utc_string)."""
    if now_dt is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = now_dt if now_dt.tzinfo else now_dt.replace(tzinfo=timezone.utc)
    return dt, dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _managed_transaction(
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None,
    timeout_seconds: float = 30.0,
    savepoint_prefix: str = "outbox_sp"
):
    """
    Context manager providing safe transaction ownership.
    - If conn is None: creates connection, runs BEGIN IMMEDIATE;, commits on success,
      rolls back on exception, and closes connection in finally.
    - If conn is provided and already in transaction: creates a unique SAVEPOINT.
      Releases savepoint on success, rolls back to savepoint on exception.
      Does NOT commit, rollback, or close the caller's connection.
    - If conn is provided and NOT in transaction: runs BEGIN IMMEDIATE;, commits on success,
      rolls back on exception. Does NOT close the caller's connection.
    """
    own_conn = (conn is None)
    if own_conn:
        active_conn = _get_active_db_connection(db_path)
        active_conn.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)};")
        active_conn.execute("BEGIN IMMEDIATE;")
        try:
            yield active_conn
            active_conn.commit()
        except Exception:
            try:
                active_conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            close_db_connection(active_conn)
    else:
        active_conn = conn
        if active_conn.in_transaction:
            sp_name = f"{savepoint_prefix}_{random.randint(100000, 999999)}"
            active_conn.execute(f"SAVEPOINT {sp_name};")
            try:
                yield active_conn
                active_conn.execute(f"RELEASE SAVEPOINT {sp_name};")
            except Exception:
                try:
                    active_conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                    active_conn.execute(f"RELEASE SAVEPOINT {sp_name};")
                except sqlite3.Error:
                    pass
                raise
        else:
            active_conn.execute(f"PRAGMA busy_timeout = {int(timeout_seconds * 1000)};")
            active_conn.execute("BEGIN IMMEDIATE;")
            try:
                yield active_conn
                active_conn.commit()
            except Exception:
                try:
                    active_conn.rollback()
                except sqlite3.Error:
                    pass
                raise


def _validate_queue_and_token(queue_id: Any, claim_token: Any) -> None:
    """Validates that queue_id and claim_token are valid positive integers."""
    if isinstance(queue_id, bool) or not isinstance(queue_id, int) or queue_id <= 0:
        raise ValueError(f"Invalid queue_id '{queue_id}': must be a positive integer (> 0).")
    if isinstance(claim_token, bool) or not isinstance(claim_token, int) or claim_token <= 0:
        raise ValueError(f"Invalid claim_token '{claim_token}': must be a positive integer (> 0).")


def create_queue_record(
    conn: sqlite3.Connection,
    attendance_id: int,
    created_at: Optional[str] = None
) -> int:
    """
    Inserts a pending outbox record into sheets_sync_queue for a given attendance record.
    Executed inside the caller's active transaction (does NOT commit).
    Sets initial claim_token = 0 and sheet_row_number = NULL.

    :param conn: Active sqlite3 connection with transaction in progress.
    :param attendance_id: Foreign key ID referencing attendance.id.
    :param created_at: Optional ISO 8601 UTC timestamp.
    :return: Generated queue record ID (integer).
    """
    if created_at is None:
        _, created_at = _get_utc_now()

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (
            attendance_id, sync_status, retry_count, claim_token, sheet_row_number, created_at, updated_at
        ) VALUES (?, 'pending', 0, 0, NULL, ?, ?);
        """,
        (attendance_id, created_at, created_at)
    )
    return cursor.lastrowid


def get_queue_stats(db_path: Optional[Union[str, Path]] = None) -> dict:
    """
    Retrieves real-time telemetry and state counts from sheets_sync_queue.

    :param db_path: Optional SQLite database path.
    :return: Dictionary containing counts for each status and total records.
    """
    conn = _get_active_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT sync_status, COUNT(*) AS count FROM sheets_sync_queue GROUP BY sync_status;")
        counts = {row["sync_status"]: row["count"] for row in cursor.fetchall()}

        cursor.execute("SELECT COUNT(*) FROM sheets_sync_queue;")
        total = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT MIN(created_at) FROM sheets_sync_queue
            WHERE sync_status IN ('pending', 'failed');
            """
        )
        oldest_pending = cursor.fetchone()[0]

        return {
            "pending": counts.get("pending", 0),
            "in_progress": counts.get("in_progress", 0),
            "synced": counts.get("synced", 0),
            "failed": counts.get("failed", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "total": total,
            "oldest_pending_at": oldest_pending,
        }
    finally:
        close_db_connection(conn)


def get_pending_records(
    limit: int = 50,
    now_utc: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> list[sqlite3.Row]:
    """
    Retrieves records eligible for synchronization (pending or retry-ready failed items)
    with full attendance, student, and class session data joined.

    :param limit: Maximum number of records to return.
    :param now_utc: Optional current UTC ISO string for retry-time comparison.
    :param db_path: Optional SQLite database path.
    :return: List of sqlite3.Row records.
    """
    if now_utc is None:
        _, now_utc = _get_utc_now()

    conn = _get_active_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                q.id AS queue_id,
                q.attendance_id,
                q.sync_status,
                q.claim_token,
                q.sheet_row_number,
                q.retry_count,
                q.next_retry_at,
                q.lease_expires_at,
                q.last_error,
                q.created_at AS queue_created_at,
                a.id AS attendance_id,
                a.scanned_at,
                a.status AS attendance_status,
                a.source,
                s.roll_number,
                s.full_name AS student_name,
                s.card_uid,
                c.course_name,
                c.session_date,
                c.start_time,
                c.end_time
            FROM sheets_sync_queue q
            INNER JOIN attendance a ON q.attendance_id = a.id
            INNER JOIN students s ON a.student_id = s.id
            INNER JOIN class_sessions c ON a.session_id = c.id
            WHERE q.sync_status IN ('pending', 'failed')
              AND (q.next_retry_at IS NULL OR q.next_retry_at <= ?)
            ORDER BY q.id ASC
            LIMIT ?;
            """,
            (now_utc, limit)
        )
        return cursor.fetchall()
    finally:
        close_db_connection(conn)


def claim_records(
    worker_id: str = "worker-1",
    limit: int = 50,
    lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> list[dict]:
    """
    Atomically claims a batch of pending/failed synchronization records with claim-token fencing.

    Guarantees:
    - Atomically increments claim_token for each successfully claimed record.
    - Sets sync_status = 'in_progress' and lease_expires_at.
    - Excludes records with active unexpired leases.
    - Returns records with their newly assigned claim_token and sheet_row_number.

    :param worker_id: Identifier of claiming worker.
    :param limit: Batch size limit.
    :param lease_duration_seconds: Duration before lease expires and becomes recoverable.
    :param now_dt: Optional datetime for deterministic testing.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: List of claimed record dictionaries.
    """
    dt_now, now_utc = _get_utc_now(now_dt)
    lease_expires_dt = dt_now + timedelta(seconds=lease_duration_seconds)
    lease_expires_utc = lease_expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()

        # 1. Select candidate IDs currently eligible
        cursor.execute(
            """
            SELECT id FROM sheets_sync_queue
            WHERE sync_status IN ('pending', 'failed')
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY id ASC
            LIMIT ?;
            """,
            (now_utc, limit)
        )
        candidate_ids = [row["id"] for row in cursor.fetchall()]

        if not candidate_ids:
            return []

        # 2. Atomically transition candidate IDs to 'in_progress' and increment claim_token
        placeholders = ",".join("?" for _ in candidate_ids)
        cursor.execute(
            f"""
            UPDATE sheets_sync_queue
            SET sync_status = 'in_progress',
                claim_token = claim_token + 1,
                lease_expires_at = ?,
                updated_at = ?
            WHERE id IN ({placeholders})
              AND sync_status IN ('pending', 'failed');
            """,
            [lease_expires_utc, now_utc] + candidate_ids
        )

        # 3. Fetch detailed data including claim_token and sheet_row_number
        cursor.execute(
            f"""
            SELECT
                q.id AS queue_id,
                q.attendance_id,
                q.sync_status,
                q.claim_token,
                q.sheet_row_number,
                q.retry_count,
                q.lease_expires_at,
                q.last_error,
                a.scanned_at,
                a.status AS attendance_status,
                a.source,
                s.roll_number,
                s.full_name AS student_name,
                s.card_uid,
                c.course_name,
                c.session_date,
                c.start_time,
                c.end_time
            FROM sheets_sync_queue q
            INNER JOIN attendance a ON q.attendance_id = a.id
            INNER JOIN students s ON a.student_id = s.id
            INNER JOIN class_sessions c ON a.session_id = c.id
            WHERE q.id IN ({placeholders})
              AND q.sync_status = 'in_progress'
              AND q.lease_expires_at = ?
            ORDER BY q.id ASC;
            """,
            candidate_ids + [lease_expires_utc]
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def recover_expired_leases(
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> int:
    """
    Recovers orphaned records whose workers crashed or timed out while 'in_progress'.
    Resets sync_status back to 'pending' and clears lease_expires_at.
    Preserves claim_token, sheet_row_number, and retry_count.

    :param now_dt: Optional current datetime for lease comparison.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: Number of recovered records.
    """
    _, now_utc = _get_utc_now(now_dt)

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()
        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sync_status = 'pending',
                lease_expires_at = NULL,
                updated_at = ?
            WHERE sync_status = 'in_progress'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?;
            """,
            (now_utc, now_utc)
        )
        return cursor.rowcount


def mark_as_synced(
    queue_id: int,
    claim_token: int,
    synced_at: Optional[str] = None,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """
    Fenced transition: marks a queue record as successfully synchronized to Google Sheets.
    Requires matching queue_id, active claim_token, sync_status = 'in_progress', and valid lease.
    Rejects stale workers whose lease expired or whose claim was reclaimed.

    :param queue_id: Primary key ID of the sheets_sync_queue row.
    :param claim_token: Current active ownership token held by worker.
    :param synced_at: Optional timestamp of the Google Sheets write.
    :param now_dt: Optional current datetime for lease validity check.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: True if record was updated; False if ownership was lost or lease expired.
    :raises ValueError: If queue_id or claim_token format is invalid.
    """
    _validate_queue_and_token(queue_id, claim_token)
    _, now_utc = _get_utc_now(now_dt)
    if synced_at is None:
        synced_at = now_utc

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()
        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sync_status = 'synced',
                synced_at = ?,
                lease_expires_at = NULL,
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
              AND claim_token = ?
              AND sync_status = 'in_progress'
              AND lease_expires_at >= ?;
            """,
            (synced_at, now_utc, queue_id, claim_token, now_utc)
        )
        updated = (cursor.rowcount == 1)
        if not updated:
            logger.warning(
                "mark_as_synced rejected for queue_id=%d with token=%d: "
                "stale claim, invalid status, or lease expired.",
                queue_id, claim_token
            )
        return updated


def calculate_retry_delay(
    retry_count: int,
    base_delay: int = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: int = DEFAULT_MAX_DELAY_SECONDS,
    jitter: bool = True,
    jitter_seconds: Optional[float] = None
) -> float:
    """
    Calculates exponential backoff delay with ±10% jitter and absolute maximum cap.
    Formula: min(max_delay, capped_delay + jitter)
    - Base delay: 15 seconds
    - Exponential multiplier: 2
    - Jitter: ±10% of capped delay (or explicit deterministic jitter_seconds)
    - Absolute final delay maximum: 900 seconds (15 minutes)

    :param retry_count: Current number of previous failed attempts.
    :param base_delay: Base delay in seconds (default: 15s).
    :param max_delay: Absolute maximum delay ceiling in seconds (default: 900s / 15m).
    :param jitter: Whether to add random jitter (±10%).
    :param jitter_seconds: Explicit jitter value (used for deterministic tests).
    :return: Calculated delay in seconds (never exceeds max_delay, minimum 1.0s).
    """
    exponential_delay = base_delay * (2 ** max(0, retry_count))
    capped_delay = min(float(max_delay), float(exponential_delay))

    if jitter:
        if jitter_seconds is not None:
            added_jitter = float(jitter_seconds)
        else:
            added_jitter = random.uniform(-0.10 * capped_delay, 0.10 * capped_delay)
        final_delay = capped_delay + added_jitter
        return max(1.0, min(float(max_delay), final_delay))

    return capped_delay


def schedule_retry(
    queue_id: int,
    claim_token: int,
    error_message: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: int = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: int = DEFAULT_MAX_DELAY_SECONDS,
    jitter: bool = True,
    jitter_seconds: Optional[float] = None,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> dict:
    """
    Fenced transition: schedules a retry for a failed queue record.
    Requires matching queue_id, active claim_token, sync_status = 'in_progress', and valid lease.
    Increments retry_count, computes exponential backoff, and sets next_retry_at.
    If retry_count reaches max_retries, transitions the record to 'dead_letter'.

    :param queue_id: Primary key of sheets_sync_queue row.
    :param claim_token: Current active ownership token held by worker.
    :param error_message: Descriptive error message (truncated to 500 chars).
    :param max_retries: Maximum attempts before moving to dead_letter.
    :param base_delay: Base delay in seconds.
    :param max_delay: Maximum delay in seconds.
    :param jitter: Whether to inject jitter.
    :param jitter_seconds: Deterministic jitter injection for testing.
    :param now_dt: Optional current datetime.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: Result dict with updated status and retry details, or claim_lost=True if stale.
    :raises ValueError: If queue_id or claim_token format is invalid, or if queue_id not in DB.
    """
    _validate_queue_and_token(queue_id, claim_token)
    dt_now, now_utc = _get_utc_now(now_dt)
    sanitized_error = str(error_message)[:500] if error_message else "Unknown error"

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()

        # Fetch current record state
        cursor.execute(
            """
            SELECT id, sync_status, claim_token, retry_count, lease_expires_at
            FROM sheets_sync_queue
            WHERE id = ?;
            """,
            (queue_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Queue record ID {queue_id} does not exist.")

        # Fencing pre-check
        db_token = row["claim_token"]
        db_status = row["sync_status"]
        db_lease = row["lease_expires_at"]

        if db_token != claim_token or db_status != "in_progress" or (db_lease is not None and db_lease < now_utc):
            logger.warning(
                "schedule_retry rejected for queue_id=%d: stale worker (token %d vs db %d, status '%s', lease '%s').",
                queue_id, claim_token, db_token, db_status, db_lease
            )
            return {
                "success": False,
                "queue_id": queue_id,
                "claim_lost": True,
                "sync_status": db_status,
                "retry_count": row["retry_count"],
                "message": "Ownership lost or lease expired: stale worker update rejected."
            }

        current_retries = row["retry_count"]
        new_retries = current_retries + 1

        if new_retries >= max_retries:
            # Exhausted retries -> Dead Letter
            cursor.execute(
                """
                UPDATE sheets_sync_queue
                SET sync_status = 'dead_letter',
                    retry_count = ?,
                    next_retry_at = NULL,
                    lease_expires_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                  AND claim_token = ?
                  AND sync_status = 'in_progress'
                  AND lease_expires_at >= ?;
                """,
                (new_retries, sanitized_error, now_utc, queue_id, claim_token, now_utc)
            )
            if cursor.rowcount != 1:
                return {
                    "success": False,
                    "queue_id": queue_id,
                    "claim_lost": True,
                    "sync_status": db_status,
                    "message": "Conditional update failed: ownership lost during retry transition."
                }
            return {
                "success": True,
                "queue_id": queue_id,
                "sync_status": "dead_letter",
                "retry_count": new_retries,
                "next_retry_at": None,
                "max_retries_exceeded": True
            }

        # Schedule next retry with exponential backoff
        delay_seconds = calculate_retry_delay(
            retry_count=current_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter=jitter,
            jitter_seconds=jitter_seconds
        )
        next_retry_dt = dt_now + timedelta(seconds=delay_seconds)
        next_retry_utc = next_retry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sync_status = 'failed',
                retry_count = ?,
                next_retry_at = ?,
                lease_expires_at = NULL,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
              AND claim_token = ?
              AND sync_status = 'in_progress'
              AND lease_expires_at >= ?;
            """,
            (new_retries, next_retry_utc, sanitized_error, now_utc, queue_id, claim_token, now_utc)
        )
        if cursor.rowcount != 1:
            return {
                "success": False,
                "queue_id": queue_id,
                "claim_lost": True,
                "sync_status": db_status,
                "message": "Conditional update failed: ownership lost during retry transition."
            }

        return {
            "success": True,
            "queue_id": queue_id,
            "sync_status": "failed",
            "retry_count": new_retries,
            "next_retry_at": next_retry_utc,
            "delay_seconds": delay_seconds,
            "max_retries_exceeded": False
        }


def mark_as_failed(
    queue_id: int,
    claim_token: int,
    error_message: str,
    permanent: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: int = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: int = DEFAULT_MAX_DELAY_SECONDS,
    jitter: bool = True,
    jitter_seconds: Optional[float] = None,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> dict:
    """
    Fenced transition: marks a record as failed.
    If permanent is True, moves record to 'dead_letter'; otherwise schedules retry.

    :param queue_id: Primary key of sheets_sync_queue row.
    :param claim_token: Current active ownership token held by worker.
    :param error_message: Error description.
    :param permanent: True for non-retryable errors.
    :return: Status dictionary.
    """
    if permanent:
        return move_to_dead_letter(
            queue_id=queue_id,
            claim_token=claim_token,
            error_message=error_message,
            now_dt=now_dt,
            db_path=db_path,
            conn=conn
        )
    return schedule_retry(
        queue_id=queue_id,
        claim_token=claim_token,
        error_message=error_message,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter=jitter,
        jitter_seconds=jitter_seconds,
        now_dt=now_dt,
        db_path=db_path,
        conn=conn
    )


def move_to_dead_letter(
    queue_id: int,
    claim_token: int,
    error_message: str,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> dict:
    """
    Fenced transition: directly moves a record to the 'dead_letter' state (permanent failure).
    Requires matching queue_id, active claim_token, sync_status = 'in_progress', and valid lease.

    :param queue_id: Primary key of sheets_sync_queue row.
    :param claim_token: Current active ownership token held by worker.
    :param error_message: Descriptive reason for permanent failure.
    :param now_dt: Optional datetime.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: Status dictionary with success=True or claim_lost=True.
    """
    _validate_queue_and_token(queue_id, claim_token)
    _, now_utc = _get_utc_now(now_dt)
    sanitized_error = str(error_message)[:500] if error_message else "Permanent synchronization failure"

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()

        cursor.execute("SELECT claim_token, sync_status, lease_expires_at FROM sheets_sync_queue WHERE id = ?;", (queue_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Queue record ID {queue_id} does not exist.")

        if row["claim_token"] != claim_token or row["sync_status"] != "in_progress" or (row["lease_expires_at"] and row["lease_expires_at"] < now_utc):
            logger.warning("move_to_dead_letter rejected for queue_id=%d: stale worker claim.", queue_id)
            return {
                "success": False,
                "queue_id": queue_id,
                "claim_lost": True,
                "sync_status": row["sync_status"],
                "message": "Ownership lost or lease expired: stale worker dead-letter transition rejected."
            }

        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sync_status = 'dead_letter',
                next_retry_at = NULL,
                lease_expires_at = NULL,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
              AND claim_token = ?
              AND sync_status = 'in_progress'
              AND lease_expires_at >= ?;
            """,
            (sanitized_error, now_utc, queue_id, claim_token, now_utc)
        )
        if cursor.rowcount != 1:
            return {
                "success": False,
                "queue_id": queue_id,
                "claim_lost": True,
                "message": "Ownership lost during dead-letter transition."
            }

        return {
            "success": True,
            "queue_id": queue_id,
            "sync_status": "dead_letter",
            "permanent_failure": True,
            "last_error": sanitized_error
        }


def release_claim(
    queue_id: int,
    claim_token: int,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> bool:
    """
    Fenced transition: safely releases an active worker claim back to 'pending' state.
    Requires matching queue_id, active claim_token, sync_status = 'in_progress', and valid lease.
    Rejects stale workers attempting to release a claim they no longer own.

    :param queue_id: Primary key of sheets_sync_queue row.
    :param claim_token: Current active ownership token held by worker.
    :param now_dt: Optional datetime.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: True if claim was released; False if ownership was lost or lease expired.
    """
    _validate_queue_and_token(queue_id, claim_token)
    _, now_utc = _get_utc_now(now_dt)

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()
        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sync_status = 'pending',
                lease_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND claim_token = ?
              AND sync_status = 'in_progress'
              AND lease_expires_at >= ?;
            """,
            (now_utc, queue_id, claim_token, now_utc)
        )
        return cursor.rowcount == 1


def get_dead_letter_records(
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[Union[str, Path]] = None
) -> list[dict]:
    """
    Retrieves dead-letter queue records with joined attendance, student,
    and class session information for administrative review and recovery.
    """
    conn = _get_active_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                q.id AS queue_id,
                q.attendance_id,
                q.sync_status,
                q.claim_token,
                q.sheet_row_number,
                q.retry_count,
                q.last_error,
                q.created_at AS queue_created_at,
                q.updated_at AS queue_updated_at,
                a.scanned_at,
                a.status AS attendance_status,
                a.source,
                s.roll_number,
                s.full_name AS student_name,
                s.card_uid,
                c.course_name,
                c.session_date
            FROM sheets_sync_queue q
            INNER JOIN attendance a ON q.attendance_id = a.id
            INNER JOIN students s ON a.student_id = s.id
            INNER JOIN class_sessions c ON a.session_id = c.id
            WHERE q.sync_status = 'dead_letter'
            ORDER BY q.updated_at DESC, q.id DESC
            LIMIT ? OFFSET ?;
            """,
            (limit, offset)
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            rec = dict(r)
            err = (rec.get("last_error") or "").lower()
            retries = rec.get("retry_count", 0)
            if retries >= DEFAULT_MAX_RETRIES:
                category = "exhausted_retries"
            elif any(w in err for w in ("permission", "403", "auth", "401", "unauthorized", "forbidden")):
                category = "permission_or_auth"
            elif any(w in err for w in ("not found", "404", "invalid spreadsheet")):
                category = "not_found"
            else:
                category = "permanent_failure"
            rec["error_category"] = category
            results.append(rec)
        return results
    finally:
        close_db_connection(conn)


def retry_dead_letter_records(
    queue_ids: Optional[list[int]] = None,
    reset_retries: bool = True,
    now_dt: Optional[datetime] = None,
    db_path: Optional[Union[str, Path]] = None,
    conn: Optional[sqlite3.Connection] = None
) -> dict:
    """
    Administrative requeue: safely requeues dead-letter records back to 'pending' state.
    Only operates on records currently in 'dead_letter' status.
    Atomically increments claim_token to invalidate any prior worker claim generation.
    Preserves sheet_row_number and authorative attendance records permanently.

    :param queue_ids: Optional list of specific queue IDs to retry. If None or empty, retries all dead-letter items.
    :param reset_retries: If True, resets retry_count to 0.
    :param now_dt: Optional datetime for deterministic testing.
    :param db_path: Optional SQLite database path.
    :param conn: Optional existing SQLite connection.
    :return: Dictionary with requeued_count and list of requeued_ids.
    """
    _, now_utc = _get_utc_now(now_dt)

    with _managed_transaction(db_path=db_path, conn=conn) as active_conn:
        cursor = active_conn.cursor()

        if queue_ids is not None:
            if not queue_ids:
                return {
                    "success": True,
                    "requeued_count": 0,
                    "requeued_ids": []
                }
            placeholders = ",".join("?" for _ in queue_ids)
            cursor.execute(
                f"""
                SELECT id FROM sheets_sync_queue
                WHERE id IN ({placeholders}) AND sync_status = 'dead_letter';
                """,
                queue_ids
            )
            target_ids = [row["id"] for row in cursor.fetchall()]
        else:
            cursor.execute(
                """
                SELECT id FROM sheets_sync_queue
                WHERE sync_status = 'dead_letter';
                """
            )
            target_ids = [row["id"] for row in cursor.fetchall()]

        if not target_ids:
            return {
                "success": True,
                "requeued_count": 0,
                "requeued_ids": []
            }

        target_placeholders = ",".join("?" for _ in target_ids)
        if reset_retries:
            cursor.execute(
                f"""
                UPDATE sheets_sync_queue
                SET sync_status = 'pending',
                    claim_token = claim_token + 1,
                    retry_count = 0,
                    next_retry_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id IN ({target_placeholders})
                  AND sync_status = 'dead_letter';
                """,
                [now_utc] + target_ids
            )
        else:
            cursor.execute(
                f"""
                UPDATE sheets_sync_queue
                SET sync_status = 'pending',
                    claim_token = claim_token + 1,
                    next_retry_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id IN ({target_placeholders})
                  AND sync_status = 'dead_letter';
                """,
                [now_utc] + target_ids
            )

        return {
            "success": True,
            "requeued_count": len(target_ids),
            "requeued_ids": target_ids
        }
