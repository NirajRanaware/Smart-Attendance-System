"""
Google Sheets Background Synchronization Worker Service.

Phase 3 Milestone 4 Architecture & Design:
1. SQLite Authoritative Persistence:
   SQLite remains the single source of truth. Attendance scans are never blocked
   by external cloud API availability or network latency.
2. Decoupled Outbox Processing:
   Polls `sheets_sync_queue` for eligible pending records, claiming them via leased
   locks to prevent duplicate worker pickup.
3. Idempotent Duplicate Prevention:
   Before appending records to Google Sheets, queries existing Attendance IDs from
   Column A of the worksheet. Genuinely new records are appended; records already
   present in Google Sheets are excluded from append and marked 'synced' in SQLite.
4. Robust Retry & Lease Recovery:
   Expired leases from crashed workers are recovered back to 'pending'.
   Transient errors (HTTP 429, 5xx, timeouts) are scheduled with exponential backoff;
   permanently failing records transition to 'dead_letter' after 5 attempts.
5. Process Isolation:
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
    get_queue_stats
)
from app.services.google_sheets import (
    get_google_config,
    load_credentials,
    get_sheets_service,
    ensure_sheet_headers,
    get_existing_attendance_ids,
    format_attendance_batch,
    append_attendance_batch,
    map_http_error,
    GoogleSheetsError,
    GoogleSheetsConfigError,
    GoogleSheetsAuthError,
    GoogleSheetsPermissionError,
    GoogleSheetsNotFoundError,
    GoogleSheetsRateLimitError,
    GoogleSheetsServerError,
    GoogleSheetsNetworkError
)

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. Single Batch Synchronization Logic
# ==============================================================================

def sync_pending_records_batch(
    batch_size: int = 20,
    lease_duration_seconds: int = 60,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = "Attendance",
    db_path: Optional[Union[str, Path]] = None,
    service: Optional[Any] = None
) -> dict:
    """
    Executes a single synchronization cycle for pending attendance records.
    
    Workflow:
    1. Check configuration and credentials.
    2. Recover any expired worker leases.
    3. Claim up to batch_size eligible records from SQLite outbox.
    4. Ensure row 1 headers exist in the target worksheet.
    5. Query existing Attendance IDs from the worksheet (idempotency check).
    6. Filter out records already present in Google Sheets.
    7. Append genuinely new records with valueInputOption='RAW'.
    8. Mark newly appended and already-present records as 'synced'.
    9. On failure, schedule retry or dead-letter per policy.
    
    :param batch_size: Maximum records to claim in this batch.
    :param lease_duration_seconds: Leased lock duration in seconds.
    :param spreadsheet_id: Optional spreadsheet ID override.
    :param sheet_name: Target sheet/tab name.
    :param db_path: Optional database path override.
    :param service: Optional mock/pre-built Google Sheets API Resource.
    :return: Structured batch execution telemetry dictionary.
    """
    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]
    target_sheet = sheet_name or cfg["sheet_name"] or "Attendance"

    # Step 1: Pre-condition checks
    if has_app_context():
        sync_enabled = current_app.config.get("GOOGLE_SHEETS_SYNC_ENABLED", True)
    else:
        sync_enabled = getattr(get_config(), "GOOGLE_SHEETS_SYNC_ENABLED", True)

    if not sync_enabled:
        logger.info("Google Sheets synchronization is currently disabled by configuration.")
        return {
            "success": True,
            "status": "DISABLED",
            "claimed_count": 0,
            "synced_count": 0,
            "appended_count": 0,
            "message": "Google Sheets synchronization is disabled."
        }

    # Step 2: Recover expired leases (orphaned records from previous worker interruptions)
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
            "appended_count": 0,
            "message": "Google Spreadsheet ID is not configured."
        }

    creds_path = cfg["credentials_path"]
    if service is None:
        try:
            service = get_sheets_service(credentials_path=creds_path)
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
                "appended_count": 0,
                "message": f"Service account credentials not found at '{creds_path}'."
            }
        except GoogleSheetsAuthError as e:
            logger.error(f"Google Sheets sync worker: Authentication failed: {e}")
            return {
                "success": False,
                "status": "AUTH_ERROR",
                "claimed_count": 0,
                "synced_count": 0,
                "appended_count": 0,
                "message": str(e)
            }
        except Exception as e:
            logger.error(f"Google Sheets sync worker: Service initialization error: {e}")
            return {
                "success": False,
                "status": "CLIENT_ERROR",
                "claimed_count": 0,
                "synced_count": 0,
                "appended_count": 0,
                "message": str(e)
            }

    # Step 4: Atomically claim a batch of eligible records
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
            "message": "No pending records to synchronize."
        }

    claimed_count = len(claimed)
    logger.info(f"Worker claimed {claimed_count} record(s) for synchronization.")

    # Step 5: Build Google client and verify/provision headers
    try:
        if service is None:
            service = get_sheets_service(credentials_path=creds_path)

        # Ensure headers exist in row 1
        header_result = ensure_sheet_headers(
            spreadsheet_id=target_id,
            sheet_name=target_sheet,
            service=service
        )
        if not header_result.get("success", False):
            err_msg = header_result.get("message", "Failed to verify or provision headers.")
            logger.error(f"Header check failed: {err_msg}")
            # Schedule retry for all claimed records so they are not stuck in_progress
            for r in claimed:
                schedule_retry(
                    queue_id=r["queue_id"],
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
                "appended_count": 0,
                "message": err_msg
            }

    except Exception as e:
        err_msg = str(e)
        logger.error(f"Failed to initialize Google Sheets service or verify headers: {err_msg}")
        for r in claimed:
            schedule_retry(
                queue_id=r["queue_id"],
                error_message=f"Service initialization error: {err_msg}",
                db_path=db_path
            )
        return {
            "success": False,
            "status": "CLIENT_ERROR",
            "claimed_count": claimed_count,
            "synced_count": 0,
            "appended_count": 0,
            "message": err_msg
        }

    # Step 6: Duplicate Prevention (Idempotent Pre-Check)
    # Query all Attendance IDs currently stored in Google Sheets Column A
    try:
        existing_ids: Set[int] = get_existing_attendance_ids(
            spreadsheet_id=target_id,
            sheet_name=target_sheet,
            service=service
        )
    except Exception as e:
        err_msg = f"Failed to query existing spreadsheet IDs: {str(e)}"
        logger.error(err_msg)
        for r in claimed:
            schedule_retry(
                queue_id=r["queue_id"],
                error_message=err_msg,
                db_path=db_path
            )
        return {
            "success": False,
            "status": "READ_ERROR",
            "claimed_count": claimed_count,
            "synced_count": 0,
            "appended_count": 0,
            "message": err_msg
        }

    # Partition claimed records into genuinely missing vs already present
    records_to_append = [r for r in claimed if r["attendance_id"] not in existing_ids]
    already_synced_records = [r for r in claimed if r["attendance_id"] in existing_ids]

    appended_count = 0
    now_sync_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Step 7: Append missing records to Google Sheets
    if records_to_append:
        formatted_rows = format_attendance_batch(records_to_append, sync_timestamp=now_sync_utc)
        append_res = append_attendance_batch(
            spreadsheet_id=target_id,
            sheet_name=target_sheet,
            rows=formatted_rows,
            service=service,
            value_input_option="RAW"
        )

        if not append_res.get("success", False):
            append_err = append_res.get("message", "Append operation failed.")
            logger.error(f"Google Sheets append failed: {append_err}")

            # Schedule retries for the records that could not be appended
            for r in records_to_append:
                schedule_retry(
                    queue_id=r["queue_id"],
                    error_message=append_err,
                    db_path=db_path
                )

            # Even if append failed, mark already_synced_records as 'synced' since they were verified
            for r in already_synced_records:
                mark_as_synced(queue_id=r["queue_id"], synced_at=now_sync_utc, db_path=db_path)

            return {
                "success": False,
                "status": "APPEND_FAILED",
                "claimed_count": claimed_count,
                "synced_count": len(already_synced_records),
                "appended_count": 0,
                "already_present_count": len(already_synced_records),
                "message": append_err
            }

        appended_count = len(records_to_append)

    # Step 8: Mark newly appended records as 'synced' in SQLite
    for r in records_to_append:
        mark_as_synced(queue_id=r["queue_id"], synced_at=now_sync_utc, db_path=db_path)

    # Step 9: Mark already-present records as 'synced' in SQLite
    for r in already_synced_records:
        mark_as_synced(queue_id=r["queue_id"], synced_at=now_sync_utc, db_path=db_path)

    total_synced = appended_count + len(already_synced_records)
    logger.info(
        f"Batch completed: {appended_count} newly appended, "
        f"{len(already_synced_records)} already present. Total synced={total_synced}."
    )

    return {
        "success": True,
        "status": "SUCCESS",
        "claimed_count": claimed_count,
        "appended_count": appended_count,
        "already_present_count": len(already_synced_records),
        "synced_count": total_synced,
        "message": (
            f"Successfully synchronized {total_synced} record(s) "
            f"({appended_count} appended, {len(already_synced_records)} duplicate-prevented)."
        )
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
        sheet_name: str = "Attendance"
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
        self.sheet_name = sheet_name or _get_val("GOOGLE_SHEETS_SHEET_NAME", "Attendance")

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
                    service=service
                )
        return sync_pending_records_batch(
            batch_size=self.batch_size,
            lease_duration_seconds=self.lease_duration,
            spreadsheet_id=self.spreadsheet_id,
            sheet_name=self.sheet_name,
            db_path=self.db_path,
            service=service
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
            f"Lease duration: {self.lease_duration}s. Press Ctrl+C to stop."
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
