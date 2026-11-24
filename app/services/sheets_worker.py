"""
Google Sheets Background Synchronization Worker Service.

Stage 9.7B.13J Architecture & Design:
1. SQLite Authoritative Persistence:
   SQLite remains the single source of truth. Attendance scans are never blocked
   by external cloud API availability or network latency.
2. Decoupled Outbox Processing with Claim-Token Fencing:
   Polls `sheets_sync_queue` for eligible pending records, claiming them via leased
   locks that increment monotonic `claim_token` integers to fence against stale workers.
3. Deterministic Permanent Row Allocation:
   Each claimed record is guaranteed a permanent, monotonically increasing row
   assignment (`sheet_row_number`) via `sheets_row_allocator`. Rows are never derived
   from Attendance ID, never appended, and never reassigned.
4. Deterministic In-Place Updates (values.update):
   Updates Google Sheets targeting exact cells Attendance_Log!A{row}:I{row}.
   Eliminates check-then-append race conditions and ensures safe convergence
   even after client timeouts or worker interruptions.
5. Coordinated Timeouts & Safe Lease Expiration:
   Enforces HTTP client timeout (e.g. 15s) strictly lower than lease duration (e.g. 60s).
   Stale workers whose lease expired or whose claim was reclaimed fail closed
   because SQLite updates require matching `claim_token`.
6. Robust Retry & Dead-Letter Handling:
   Transient network errors, rate limits, and timeouts schedule retries with
   exponential backoff and jitter. Non-retryable permanent errors (403, 404, invalid payload)
   transition immediately to 'dead_letter'.
7. Process Isolation:
   Designed to run as a dedicated CLI process (`flask run-sheets-worker`) or
   standalone worker loop, avoiding thread proliferation in multi-worker Flask web servers.
"""

import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union, Any, List, Dict, Set

from flask import Flask, current_app, has_app_context
from googleapiclient.errors import HttpError

from app.config import get_config
from app.services.sheets_outbox import (
    claim_records,
    recover_expired_leases,
    mark_as_synced,
    schedule_retry,
    move_to_dead_letter,
    mark_as_failed,
    release_claim,
    get_queue_stats,
)
from app.services.sheets_row_allocator import (
    allocate_sheet_rows,
    get_allocated_sheet_row,
    get_next_sheet_row,
)
from app.services.google_sheets import (
    get_google_config,
    load_credentials,
    get_sheets_service,
    ensure_sheet_headers,
    construct_deterministic_range,
    format_attendance_row,
    update_attendance_row,
    update_attendance_batch,
    map_http_error,
    GoogleSheetsError,
    GoogleSheetsConfigError,
    GoogleSheetsAuthError,
    GoogleSheetsPermissionError,
    GoogleSheetsNotFoundError,
    GoogleSheetsRateLimitError,
    GoogleSheetsServerError,
    GoogleSheetsNetworkError,
    GoogleSheetsTimeoutError,
    GoogleSheetsValidationError,
)

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT_SECONDS: float = 15.0


# ==============================================================================
# 1. Single Batch Synchronization Logic
# ==============================================================================

def sync_pending_records_batch(
    batch_size: int = 20,
    lease_duration_seconds: int = 60,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = "Attendance_Log",
    db_path: Optional[Union[str, Path]] = None,
    service: Optional[Any] = None,
    http_timeout_seconds: Optional[float] = None
) -> dict:
    """
    Executes a single deterministic synchronization cycle for pending attendance records.

    Workflow:
    1. Pre-condition checks (feature enabled, configuration valid).
    2. Recover any expired worker leases back to 'pending'.
    3. Atomically claim up to batch_size eligible records with claim-token fencing.
    4. Ensure row 1 headers exist in the target worksheet.
    5. Ensure permanent row allocation (`sheet_row_number`) for all claimed records.
    6. For each claimed record:
       a. Validate attendance payload (fail-closed to dead-letter if malformed).
       b. Execute deterministic update to Attendance_Log!A{row}:I{row}.
       c. If successful: mark_as_synced with claim_token.
       d. If retryable (timeout, rate limit, server/network error): schedule_retry with claim_token.
       e. If non-retryable (validation, auth, permission, 404): move_to_dead_letter with claim_token.
       f. If claim was lost (stale worker): cease mutations and log safely.

    :param batch_size: Maximum records to claim in this batch.
    :param lease_duration_seconds: Leased lock duration in seconds.
    :param spreadsheet_id: Optional spreadsheet ID override.
    :param sheet_name: Target sheet/tab name.
    :param db_path: Optional database path override.
    :param service: Optional mock/pre-built Google Sheets API Resource.
    :param http_timeout_seconds: Optional HTTP client socket timeout in seconds.
    :return: Structured batch execution telemetry dictionary.
    """
    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]
    target_sheet = sheet_name or cfg["sheet_name"] or "Attendance_Log"

    # Step 1: Pre-condition checks
    if has_app_context():
        sync_enabled = current_app.config.get("GOOGLE_SHEETS_SYNC_ENABLED", True)
        configured_timeout = current_app.config.get("GOOGLE_SHEETS_HTTP_TIMEOUT_SECONDS")
    else:
        app_cfg = get_config()
        sync_enabled = getattr(app_cfg, "GOOGLE_SHEETS_SYNC_ENABLED", True)
        configured_timeout = getattr(app_cfg, "GOOGLE_SHEETS_HTTP_TIMEOUT_SECONDS", None)

    timeout_sec = (
        http_timeout_seconds
        if http_timeout_seconds is not None
        else (float(configured_timeout) if configured_timeout is not None else DEFAULT_HTTP_TIMEOUT_SECONDS)
    )

    if not sync_enabled:
        logger.info("Google Sheets synchronization is currently disabled by configuration.")
        return {
            "success": True,
            "status": "DISABLED",
            "claimed_count": 0,
            "synced_count": 0,
            "appended_count": 0,
            "failed_count": 0,
            "message": "Google Sheets synchronization is disabled."
        }

    # Step 2: Recover expired leases (orphaned records from crashed or timed-out workers)
    recovered = recover_expired_leases(db_path=db_path)
    if recovered > 0:
        logger.info(f"Recovered {recovered} expired in_progress queue record(s) back to pending.")

    # Step 3: Check spreadsheet configuration & initialize service
    if not target_id or not target_id.strip():
        logger.warning("Google Sheets sync worker: GOOGLE_SHEETS_SPREADSHEET_ID is not configured.")
        return {
            "success": False,
            "status": "MISSING_SPREADSHEET_ID",
            "claimed_count": 0,
            "synced_count": 0,
            "failed_count": 0,
            "message": "Google Spreadsheet ID is not configured."
        }

    creds_path = cfg["credentials_path"]
    if service is None:
        try:
            service = get_sheets_service(credentials_path=creds_path, timeout_seconds=timeout_sec)
        except (GoogleSheetsConfigError, FileNotFoundError) as e:
            logger.warning(
                "Google Sheets sync worker: Service Account credentials not found at "
                f"'{creds_path}'. Sync will remain idle until credentials are provided."
            )
            return {
                "success": False,
                "status": "MISSING_CREDENTIALS",
                "claimed_count": 0,
                "synced_count": 0,
                "failed_count": 0,
                "message": f"Service account credentials not found at '{creds_path}'."
            }
        except GoogleSheetsAuthError as e:
            logger.error(f"Google Sheets sync worker: Authentication failed: {e}")
            return {
                "success": False,
                "status": "AUTH_ERROR",
                "claimed_count": 0,
                "synced_count": 0,
                "failed_count": 0,
                "message": str(e)
            }
        except Exception as e:
            logger.error(f"Google Sheets sync worker: Service initialization error: {e}")
            return {
                "success": False,
                "status": "CLIENT_ERROR",
                "claimed_count": 0,
                "synced_count": 0,
                "failed_count": 0,
                "message": str(e)
            }

    # Step 4: Atomically claim a batch of eligible records with claim-token fencing
    claimed = claim_records(
        limit=batch_size,
        lease_duration_seconds=lease_duration_seconds,
        db_path=db_path
    )

    if not claimed:
        return {
            "success": True,
            "status": "IDLE",
            "claimed_count": 0,
            "synced_count": 0,
            "appended_count": 0,
            "failed_count": 0,
            "message": "No pending records to synchronize."
        }

    claimed_count = len(claimed)
    logger.info(f"Worker claimed {claimed_count} record(s) for synchronization.")

    # Step 5: Verify or provision headers on target worksheet
    try:
        header_result = ensure_sheet_headers(
            spreadsheet_id=target_id,
            sheet_name=target_sheet,
            service=service
        )
        if not header_result.get("success", False):
            err_msg = header_result.get("message", "Failed to verify or provision headers.")
            logger.error(f"Header check failed: {err_msg}")
            # Fenced retry scheduling for all claimed records
            for r in claimed:
                schedule_retry(
                    queue_id=r["queue_id"],
                    claim_token=r["claim_token"],
                    error_message=f"Header provisioning failed: {err_msg}",
                    db_path=db_path
                )
            error_type = header_result.get("error_type", "")
            status_code = header_result.get("status_code")
            if (status_code and 400 <= status_code < 500 and status_code != 429) or error_type in ("PERMISSION_DENIED", "AUTH_ERROR", "NOT_FOUND", "CONFIG_ERROR"):
                err_status = "CLIENT_ERROR"
            elif status_code == 429 or error_type == "RATE_LIMITED":
                err_status = "RATE_LIMITED"
            elif (status_code and status_code >= 500) or error_type == "SERVER_ERROR":
                err_status = "SERVER_ERROR"
            else:
                err_status = error_type or "HEADER_ERROR"

            return {
                "success": False,
                "status": err_status,
                "claimed_count": claimed_count,
                "synced_count": 0,
                "failed_count": claimed_count,
                "message": err_msg
            }
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Header provisioning exception: {err_msg}")
        for r in claimed:
            schedule_retry(
                queue_id=r["queue_id"],
                claim_token=r["claim_token"],
                error_message=f"Service initialization error: {err_msg}",
                db_path=db_path
            )
        return {
            "success": False,
            "status": "CLIENT_ERROR",
            "claimed_count": claimed_count,
            "synced_count": 0,
            "failed_count": claimed_count,
            "message": err_msg
        }

    # Step 6: Ensure permanent row allocation for all claimed records
    unallocated_ids = [r["queue_id"] for r in claimed if r.get("sheet_row_number") is None]
    if unallocated_ids:
        try:
            allocated_map = allocate_sheet_rows(unallocated_ids, db_path=db_path)
            for r in claimed:
                if r["queue_id"] in allocated_map:
                    r["sheet_row_number"] = allocated_map[r["queue_id"]]
        except Exception as e:
            alloc_err = f"Failed to allocate permanent row numbers: {str(e)}"
            logger.error(alloc_err)
            for r in claimed:
                schedule_retry(
                    queue_id=r["queue_id"],
                    claim_token=r["claim_token"],
                    error_message=alloc_err,
                    db_path=db_path
                )
            return {
                "success": False,
                "status": "ALLOCATION_FAILED",
                "claimed_count": claimed_count,
                "synced_count": 0,
                "failed_count": claimed_count,
                "message": alloc_err
            }

    # Step 7: Deterministic in-place update for each record independently
    synced_count = 0
    failed_count = 0
    dead_letter_count = 0
    claim_lost_count = 0
    now_sync_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for r in claimed:
        q_id = r["queue_id"]
        token = r["claim_token"]
        row_num = r.get("sheet_row_number")

        # Validate allocated row bounds
        if row_num is None or not isinstance(row_num, int) or isinstance(row_num, bool) or row_num < 2:
            invalid_row_msg = f"Invalid permanent sheet_row_number: {row_num}"
            logger.error(f"Record queue_id={q_id}: {invalid_row_msg}")
            res = move_to_dead_letter(
                queue_id=q_id,
                claim_token=token,
                error_message=invalid_row_msg,
                db_path=db_path
            )
            failed_count += 1
            dead_letter_count += 1
            if res.get("claim_lost"):
                claim_lost_count += 1
            continue

        # Validate attendance ID
        raw_att_id = r.get("attendance_id")
        if raw_att_id is None:
            att_err_msg = f"Queue record {q_id} missing attendance_id"
            logger.error(att_err_msg)
            res = move_to_dead_letter(
                queue_id=q_id,
                claim_token=token,
                error_message=att_err_msg,
                db_path=db_path
            )
            failed_count += 1
            dead_letter_count += 1
            if res.get("claim_lost"):
                claim_lost_count += 1
            continue

        try:
            int_att_id = int(raw_att_id)
            if int_att_id <= 0:
                raise ValueError("Attendance ID must be a positive integer.")
        except (ValueError, TypeError) as e:
            att_err_msg = f"Queue record {q_id} invalid attendance_id '{raw_att_id}': {e}"
            logger.error(att_err_msg)
            res = move_to_dead_letter(
                queue_id=q_id,
                claim_token=token,
                error_message=att_err_msg,
                db_path=db_path
            )
            failed_count += 1
            dead_letter_count += 1
            if res.get("claim_lost"):
                claim_lost_count += 1
            continue

        # Execute deterministic update via Stage 13I service
        try:
            update_res = update_attendance_row(
                attendance_record=r,
                sheet_row_number=row_num,
                spreadsheet_id=target_id,
                sheet_name=target_sheet,
                timeout_seconds=timeout_sec,
                service=service,
                raise_for_status=False
            )
        except Exception as e:
            # Catch any unexpected unhandled exception safely
            logger.error(f"Unexpected exception during update for queue_id={q_id}: {e}", exc_info=True)
            res = schedule_retry(
                queue_id=q_id,
                claim_token=token,
                error_message=f"Unexpected update exception: {str(e)}",
                db_path=db_path
            )
            failed_count += 1
            if res.get("claim_lost"):
                claim_lost_count += 1
            continue

        if update_res.get("success", False):
            # Remote update succeeded: perform fenced state transition to 'synced'
            transition_ok = mark_as_synced(
                queue_id=q_id,
                claim_token=token,
                synced_at=now_sync_utc,
                db_path=db_path
            )
            if transition_ok:
                synced_count += 1
                logger.info(f"Record queue_id={q_id} (row={row_num}) synchronized successfully.")
            else:
                claim_lost_count += 1
                logger.warning(
                    f"Record queue_id={q_id} remote update succeeded at row={row_num}, "
                    f"but mark_as_synced was rejected (claim token={token} stale or lease expired). "
                    "Halting further local state changes."
                )
        else:
            # Remote update failed
            failed_count += 1
            err_msg = update_res.get("message", "Google Sheets update failed.")
            err_type = update_res.get("error_type", "UPDATE_ERROR")
            retryable = update_res.get("retryable", True)

            logger.warning(
                f"Record queue_id={q_id} (row={row_num}) update failed: [{err_type}] {err_msg} "
                f"(retryable={retryable})"
            )

            if retryable:
                # Transient error: timeout, rate limit, temporary server error, network error
                retry_res = schedule_retry(
                    queue_id=q_id,
                    claim_token=token,
                    error_message=f"[{err_type}] {err_msg}",
                    db_path=db_path
                )
                if retry_res.get("claim_lost"):
                    claim_lost_count += 1
                    logger.warning(f"Record queue_id={q_id} claim lost during schedule_retry.")
            else:
                # Permanent error: validation, permission, not found, non-retryable 4xx
                dead_res = move_to_dead_letter(
                    queue_id=q_id,
                    claim_token=token,
                    error_message=f"[{err_type}] {err_msg}",
                    db_path=db_path
                )
                dead_letter_count += 1
                if dead_res.get("claim_lost"):
                    claim_lost_count += 1
                    logger.warning(f"Record queue_id={q_id} claim lost during move_to_dead_letter.")

    # Step 8: Build summary telemetry
    is_overall_success = (failed_count == 0 and synced_count > 0) or (claimed_count == 0)
    if failed_count == 0 and synced_count > 0:
        batch_status = "SUCCESS"
    elif synced_count > 0 and failed_count > 0:
        batch_status = "PARTIAL_SUCCESS"
    elif dead_letter_count > 0:
        batch_status = "DEAD_LETTER"
    else:
        batch_status = "RETRY_SCHEDULED" if failed_count > 0 else "IDLE"

    summary_msg = (
        f"Successfully synchronized {synced_count} record(s). "
        f"Batch completed: {synced_count} synced, {failed_count} failed "
        f"({dead_letter_count} dead-letter), {claim_lost_count} claim-lost of {claimed_count} claimed."
    )
    logger.info(summary_msg)

    return {
        "success": is_overall_success,
        "status": batch_status,
        "claimed_count": claimed_count,
        "synced_count": synced_count,
        "appended_count": 0,
        "failed_count": failed_count,
        "dead_letter_count": dead_letter_count,
        "claim_lost_count": claim_lost_count,
        "message": summary_msg
    }


# ==============================================================================
# 2. Background Worker Class & Lifecycle Loop
# ==============================================================================

class SheetsSyncWorker:
    """
    Dedicated worker service managing the background polling loop for Google Sheets sync.
    Designed for clean lifecycle management with SIGINT/SIGTERM graceful termination.
    """

    def __init__(
        self,
        app: Optional[Flask] = None,
        poll_interval: Optional[int] = None,
        batch_size: Optional[int] = None,
        lease_duration: Optional[int] = None,
        db_path: Optional[Union[str, Path]] = None,
        spreadsheet_id: Optional[str] = None,
        sheet_name: str = "Attendance_Log",
        http_timeout: Optional[float] = None
    ):
        self.app = app
        self._stop_event = threading.Event()
        self._is_running = False

        # Resolve configuration
        if app is not None:
            cfg = app.config
        elif has_app_context():
            cfg = current_app.config
        else:
            cfg = get_config()

        def _get_val(k: str, default: Any = None) -> Any:
            if hasattr(cfg, "get"):
                val = cfg.get(k)
                return val if val is not None else default
            return getattr(cfg, k, default)

        self.poll_interval = poll_interval or _get_val("GOOGLE_SHEETS_SYNC_POLL_INTERVAL", 10)
        self.batch_size = batch_size or _get_val("GOOGLE_SHEETS_SYNC_BATCH_SIZE", 20)
        self.lease_duration = lease_duration or _get_val("GOOGLE_SHEETS_SYNC_LEASE_SECONDS", 60)
        self.db_path = db_path or _get_val("DATABASE_PATH", None)
        self.spreadsheet_id = spreadsheet_id or _get_val("GOOGLE_SHEETS_SPREADSHEET_ID", "")
        self.sheet_name = sheet_name or _get_val("GOOGLE_SHEETS_SHEET_NAME", "Attendance_Log")
        self.http_timeout = http_timeout or _get_val("GOOGLE_SHEETS_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)

    def process_batch(self, service: Optional[Any] = None) -> dict:
        """
        Executes a single batch, establishing Flask application context if available.

        :param service: Optional mock/pre-built Google Sheets API client.
        :return: Execution summary dictionary.
        """
        if self.app is not None:
            with self.app.app_context():
                return sync_pending_records_batch(
                    batch_size=self.batch_size,
                    lease_duration_seconds=self.lease_duration,
                    spreadsheet_id=self.spreadsheet_id,
                    sheet_name=self.sheet_name,
                    db_path=self.db_path,
                    service=service,
                    http_timeout_seconds=self.http_timeout
                )
        return sync_pending_records_batch(
            batch_size=self.batch_size,
            lease_duration_seconds=self.lease_duration,
            spreadsheet_id=self.spreadsheet_id,
            sheet_name=self.sheet_name,
            db_path=self.db_path,
            service=service,
            http_timeout_seconds=self.http_timeout
        )

    def run(self, once: bool = False, service: Optional[Any] = None) -> Optional[dict]:
        """
        Runs the worker execution loop.

        :param once: If True, executes one batch and exits immediately.
        :param service: Optional service instance.
        :return: Result dict if once=True, else None when stopped.
        """
        if once:
            logger.info("Executing single-shot Google Sheets sync batch...")
            return self.process_batch(service=service)

        if self._stop_event.is_set():
            logger.info("Worker stop event is set before run; exiting immediately.")
            return None

        self._is_running = True
        self._stop_event.clear()

        # Register termination signal handlers if running in main thread
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            # Not in main thread or signal unavailable
            pass

        logger.info(
            f"Google Sheets Sync Worker started. "
            f"Poll interval: {self.poll_interval}s, Batch size: {self.batch_size}, "
            f"Lease duration: {self.lease_duration}s, Timeout: {self.http_timeout}s. Press Ctrl+C to stop."
        )

        try:
            while not self._stop_event.is_set():
                try:
                    result = self.process_batch(service=service)
                    if result.get("claimed_count", 0) > 0:
                        logger.info(f"Sync result: {result.get('message')}")
                except Exception as e:
                    logger.error(f"Unexpected error in sync worker iteration: {e}", exc_info=True)

                # Wait for next polling interval or early termination signal
                self._stop_event.wait(timeout=self.poll_interval)

        finally:
            self._is_running = False
            logger.info("Google Sheets Sync Worker stopped cleanly.")
        return None

    def stop(self) -> None:
        """Signals the worker loop to stop gracefully."""
        logger.info("Stopping Google Sheets Sync Worker...")
        self._stop_event.set()

    def _handle_signal(self, signum, frame):
        """Signal handler for graceful shutdown on Ctrl+C or SIGTERM."""
        logger.info(f"Received termination signal ({signum}). Initiating shutdown...")
        self.stop()
