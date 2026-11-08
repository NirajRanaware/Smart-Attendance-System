# Phase 3 — Milestone 4 Final Completion Report
## Background Sync Worker & End-to-End Google Sheets Synchronization

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Users\niraj\Downloads\Smart-Attendance-System`  
**Phase:** Phase 3 — Cloud Synchronization & External Integration  
**Milestone:** Milestone 4 — Background Sync Worker & End-to-End Google Sheets Synchronization  
**Execution Date:** November 8, 2026  
**Status:** **IMPLEMENTATION COMPLETE — AUTOMATED TESTS 100% PASSING (397/397)**

---

## 1. Executive Summary

Phase 3 Milestone 4 establishes reliable, asynchronous, and idempotent synchronization of attendance records from the local SQLite database to Google Sheets. 

Prior to Milestone 4:
- Milestone 1 established the synchronization architecture, data contract, and safety guidelines.
- Milestone 2 implemented the atomic local SQLite outbox queue (`sheets_sync_queue`) and exponential backoff retry calculations.
- Milestone 3 established secure Google Cloud Service Account authentication and the Google Sheets API client with formula injection protection (CWE-1236).

Milestone 4 connects these components into an autonomous background synchronization pipeline:
1. **Decoupled Outbox Processing:** An autonomous worker polls `sheets_sync_queue`, claims eligible pending records using leased locks, batches them, queries Google Sheets Column A for existing Attendance IDs, and appends only genuinely new rows.
2. **Authoritative Database Invariance:** SQLite remains the single source of truth. Attendance scans (`POST /api/scans`) record attendance and queue entries in an immediate local SQLite transaction (< 5 ms) and return to the hardware/user without waiting on cloud network latency or Google API availability.
3. **Idempotent Duplicate Prevention:** Pre-checking Column A in Google Sheets prevents duplicate rows even if network failures occur mid-transfer or worker retries execute after uncertain Google responses.
4. **Isolated Process CLI:** A dedicated CLI command (`flask --app app run-sheets-worker`) allows the worker to run in an independent process, eliminating worker thread duplication across multi-worker WSGI servers.
5. **Administrative Monitoring:** A telemetry endpoint (`GET /api/admin/sheets-sync/status`) provides real-time outbox counts, sync state, and configuration health to administrators without exposing credentials or filesystem paths.

All 24 dedicated worker test scenarios and all 397 full-system regression tests pass with 0 failures and 0 regressions.

---

## 2. Technical Architecture & Lifecycle

### 2.1 Authoritative Persistence vs. Synchronized Replica
```
+-----------------------------------------------------------------------------+
|                          LOCAL SYSTEM (AUTHORITATIVE)                       |
|                                                                             |
|  [RFID Scanner / UI] ---> POST /api/scans                                  |
|                                |                                            |
|                                v                                            |
|                   [BEGIN IMMEDIATE TRANSACTION]                             |
|                   1. INSERT INTO attendance                                 |
|                   2. INSERT INTO sheets_sync_queue (status='pending')       |
|                   [COMMIT TRANSACTION (< 5ms)]                              |
|                                |                                            |
|                                v                                            |
|                   HTTP 201 Response (Immediate)                             |
+-----------------------------------------------------------------------------+
                                 | (Asynchronous / Out-of-band)
                                 v
+-----------------------------------------------------------------------------+
|                     BACKGROUND WORKER (ISOLATED PROCESS)                    |
|                                                                             |
|  1. Recover expired leases (orphaned records -> 'pending')                  |
|  2. Atomic claim batch (transition to 'in_progress' + lease_expires_at)     |
|  3. Commit & release SQLite lock immediately                                |
|  4. Pre-check: Read Google Sheets Column A for existing Attendance IDs      |
|  5. Filter batch: Identify genuinely missing rows vs already synchronized   |
|  6. Call Google Sheets API values.append (valueInputOption='RAW')           |
|  7. Update SQLite outbox in short transaction:                              |
|     - Successfully appended rows -> sync_status = 'synced'                 |
|     - Already present rows -> sync_status = 'synced'                        |
|     - Failed rows -> sync_status = 'failed', schedule exponential retry     |
+-----------------------------------------------------------------------------+
                                 |
                                 v
+-----------------------------------------------------------------------------+
|                         GOOGLE SHEETS (REPORTING REPLICA)                   |
|                                                                             |
|  Sheet: 'Attendance'                                                        |
|  Columns: [ID, Scanned At, Full Name, Roll No, Course, Date, ...]          |
+-----------------------------------------------------------------------------+
```

### 2.2 Worker Lifecycle & Execution Model
The worker service is encapsulated in `SheetsSyncWorker` within `app/services/sheets_worker.py`:
- **State Management:** Uses thread-safe primitives (`threading.Event`, `_stop_event`) and state flag `_is_running`.
- **Signal Handling:** Binds `SIGINT` (Ctrl+C) and `SIGTERM` when run in the main thread to initiate graceful shutdown without abandoning active in-progress batches.
- **Graceful Termination:** When a stop signal is received, `_stop_event.set()` breaks the wait cycle; any currently executing batch finishes its network call and updates the SQLite queue before exiting.
- **Zero Orphaned Thread Risk:** Designed to execute as an independent process via Flask CLI rather than an in-process thread spawned inside WSGI workers.

---

## 3. SQLite Outbox & Queue Concurrency

### 3.1 Dual-Write Transaction Safety
In `app/routes/attendance.py`, RFID scans are processed within a strict `BEGIN IMMEDIATE` transaction:
```python
conn.execute("BEGIN IMMEDIATE;")
# 1. Insert attendance record
cursor.execute("INSERT INTO attendance (...) VALUES (...);")
att_id = cursor.lastrowid
# 2. Insert outbox queue record within the exact same transaction
cursor.execute("INSERT INTO sheets_sync_queue (attendance_id, sync_status) VALUES (?, 'pending');", (att_id,))
conn.commit()
```
If anything fails, both records roll back together. A student scan is never saved without an outbox entry, and an outbox entry is never created for a non-existent attendance record.

### 3.2 Leased Lock Claiming (`claim_records`)
Multiple workers or worker restarts cannot process the same queue items simultaneously:
1. `claim_records(limit=20, lease_duration_seconds=60)` executes `BEGIN IMMEDIATE`.
2. Selects candidate records with `sync_status IN ('pending', 'failed') AND (next_retry_at IS NULL OR next_retry_at <= now_utc)`.
3. Immediately updates selected records to `sync_status = 'in_progress'` and sets `lease_expires_at = now_utc + 60s`.
4. Commits and closes the SQLite database connection before any network calls begin.

### 3.3 Automatic Expired Lease Recovery
If a worker process is terminated (e.g., power loss, container kill) while holding claimed records:
- The items remain in `sync_status = 'in_progress'` with their `lease_expires_at` timestamp.
- On the next cycle of any worker, `recover_expired_leases()` executes:
  ```sql
  UPDATE sheets_sync_queue
  SET sync_status = 'pending', lease_expires_at = NULL, updated_at = ?
  WHERE sync_status = 'in_progress'
    AND lease_expires_at IS NOT NULL
    AND lease_expires_at <= ?;
  ```
- Orphaned records return to `pending` and are reclaimed cleanly. Active, unexpired leases held by running workers are not disturbed.

---

## 4. Google Sheets Batch Synchronization & Duplicate Prevention

### 4.1 Ten-Column Schema Mapping
Attendance rows are formatted according to the standard 10-column contract defined in Milestone 1:
1. **Attendance ID** (Integer from `attendance.id`)
2. **Timestamp** (ISO 8601 formatted string `YYYY-MM-DD HH:MM:SS`)
3. **Student Name** (String from `students.full_name`)
4. **Roll Number** (String from `students.roll_number`)
5. **Course Name** (String from `class_sessions.course_name`)
6. **Session Date** (String `YYYY-MM-DD`)
7. **Session Start** (String `HH:MM`)
8. **Session End** (String `HH:MM` or `"Not Set"`)
9. **Status** (String, default `"Present"`)
10. **Device Source** (String, e.g., `"rfid"`, `"manual"`, `"simulator"`)

### 4.2 Formula Injection Protection (CWE-1236)
Every text cell is processed via `sanitize_sheet_cell()`. If a string begins with `=`, `+`, `-`, `@`, `\t`, or `\r`, a leading single quote (`'`) is prepended. Numeric integers (such as Attendance ID) are preserved as raw numbers.

### 4.3 Idempotent Duplicate Prevention via Column A Pre-Check
Network requests can fail ambiguously: an HTTP timeout can occur after Google accepted the data, or a network drop can happen before receiving the acknowledgement.

The worker achieves complete idempotency through a two-step append workflow:
1. **Pre-Check:** Before appending, the worker calls `get_existing_attendance_ids()`, reading range `'Attendance'!A2:A` to fetch all Attendance IDs currently stored in Google Sheets.
2. **Partitioning:** The claimed batch is split:
   - `records_to_append`: Records whose `attendance_id` is NOT in the existing Google Sheet IDs.
   - `already_present_records`: Records whose `attendance_id` is already in Google Sheets.
3. **Conditional Append:** Only `records_to_append` are sent via `values.append()`.
4. **Resolution:** Both newly appended records and already-present records are marked as `sync_status = 'synced'` in SQLite.
5. **Uncertain Response Recovery:** If a previous attempt succeeded on Google's servers but dropped before SQLite could be updated, the next retry automatically detects the ID in Column A, skips appending a duplicate row, and transitions the SQLite queue record to `synced`.

### 4.4 Append Mode (`valueInputOption="RAW"`)
The worker passes `valueInputOption="RAW"` to Google Sheets API:
- Values are written literally as submitted.
- Eliminates unwanted date/number parsing ambiguities.
- Preserves Attendance ID integers as raw numeric values for indexing and formulas.

---

## 5. Retry Policy & Error Classification

### 5.1 Retry Policy Specification
Failed synchronization records follow the established exponential backoff schedule implemented in `app/services/sheets_outbox.py`:

$$\text{exponential\_delay} = \text{base\_delay} \times 2^{\text{retry\_count}}$$

$$\text{final\_delay} = \min(900.0, \, \max(1.0, \, \text{exponential\_delay} + \text{jitter}))$$

- **Base delay:** 15 seconds.
- **Multiplier:** 2.
- **Jitter:** $\pm 10\%$ uniform random jitter.
- **Absolute maximum cap:** 900.0 seconds (15 minutes).
- **Maximum retry attempts:** 5.

| Attempt | Base Exponential | Delay Range ($\pm 10\%$ Jitter) | Max Bound Enforced |
|---|---|---|---|
| 0 (Initial Failure) | 15.0 s | 13.5 s – 16.5 s | 16.5 s |
| 1 | 30.0 s | 27.0 s – 33.0 s | 33.0 s |
| 2 | 60.0 s | 54.0 s – 66.0 s | 66.0 s |
| 3 | 120.0 s | 108.0 s – 132.0 s | 132.0 s |
| 4 | 240.0 s | 216.0 s – 264.0 s | 264.0 s |
| 5 (Exhausted) | — | Transitions to `dead_letter` | No further automatic retry |

### 5.2 Error Classification & Recovery Strategy
- **Transient Errors (Retried):**
  - HTTP 429 Quota / Rate Limit Exceeded (`GoogleSheetsRateLimitError`).
  - HTTP 500, 502, 503, 504 Google Backend Errors (`GoogleSheetsServerError`).
  - Socket timeouts, connection resets, DNS resolution drops (`GoogleSheetsNetworkError`).
  - Claimed records transition to `sync_status = 'failed'` with `next_retry_at` computed via backoff.
- **Permanent Client & Config Errors:**
  - HTTP 401 Authentication Failure (`GoogleSheetsAuthError`).
  - HTTP 403 Permission Denied (`GoogleSheetsPermissionError` — service account lacks Editor access to sheet).
  - HTTP 404 Spreadsheet Not Found (`GoogleSheetsNotFoundError`).
  - Missing credentials file or empty spreadsheet ID (`GoogleSheetsConfigError`).
  - Logged with clear diagnostic error messages. In offline mode without credentials, worker remains idle without endlessly claiming or churning queue items into dead-letter.
- **Partial Batch Failures:**
  - If a batch has 5 records, 2 of which are already present in the sheet, and the append call fails for the remaining 3:
  - The 2 verified records are safely committed as `synced`.
  - The remaining 3 records are scheduled for exponential retry.

---

## 6. Flask CLI Command & Admin Telemetry

### 6.1 Flask CLI Command (`run-sheets-worker`)
Registered in `app/cli.py` to allow dedicated, production-safe worker execution:
```powershell
# Run a single batch cycle and exit immediately (ideal for cron or test verification)
.venv\Scripts\python.exe -m flask --app app run-sheets-worker --once

# Run continuous background worker with custom interval and batch size
.venv\Scripts\python.exe -m flask --app app run-sheets-worker --interval 15 --batch-size 50
```

CLI options:
- `--once`: Processes available records in a single batch and terminates cleanly with exit code 0.
- `--interval <int>`: Polling sleep duration between iterations in seconds (default: 10s or from `GOOGLE_SHEETS_SYNC_POLL_INTERVAL`).
- `--batch-size <int>`: Maximum records claimed per batch (default: 20 or from `GOOGLE_SHEETS_SYNC_BATCH_SIZE`).

### 6.2 Admin Telemetry API (`GET /api/admin/sheets-sync/status`)
Located in `app/routes/admin.py`:
- **Authentication:** Protected by `@login_required` (requires admin session).
- **Zero Information Leakage:** Never exposes private keys, secrets, or absolute server directory paths.
- **Payload Structure:**
  ```json
  {
    "success": true,
    "sync_enabled": true,
    "credentials_configured": false,
    "spreadsheet_id_configured": true,
    "queue_stats": {
      "pending": 0,
      "in_progress": 0,
      "synced": 42,
      "failed": 0,
      "dead_letter": 0
    },
    "last_sync_timestamp": "2026-11-08T09:15:22Z"
  }
  ```

---

## 7. Security & Credential Protection

1. **Logging Hygiene (CWE-532 Prevention):**
   - Verified by automated scenario `test_22_worker_logs_do_not_expose_secrets`.
   - Loggers verify that `BEGIN PRIVATE KEY`, `client_secret`, and token blocks are never output to stdout, stderr, or logfiles.
2. **Git Repository Hygiene:**
   - `.gitignore` specifically excludes:
     - `credentials/`
     - `credentials/*.json`
     - `service_account.json`
     - `service_account*.json`
     - `*service_account*.json`
     - `.env`
   - Verified that no JSON credential keys are tracked.
3. **Principle of Least Privilege:**
   - Scopes restricted to `https://www.googleapis.com/auth/spreadsheets`. Full Google Drive scopes (`drive`) are prohibited.

---

## 8. Verification Evidence

### 8.1 Summary of Automated Test Verification
All tests run against deterministic mocked Google API Discovery resources with zero outbound network calls.

| Test Suite | Tests | Result | Execution Time | Coverage Scope |
|---|---|---|---|---|
| **Four Targeted Corrections** | 4 | **4 Passed, 0 Failed** | 1.85s | HTTP 403, Empty ID, Past Leases, CLI `--once` |
| **Dedicated Worker Suite (`test_sheets_worker.py`)** | 24 | **24 Passed, 0 Failed** | 10.51s | Full worker lifecycle, idempotency, retries, concurrency |
| **Full Project Regression Suite** | 397 | **397 Passed, 0 Failed** | 156.21s | Entire application (Phase 1, 2, and 3) |

### 8.2 Dedicated Worker Scenarios Verified (24/24)
1. `test_1_worker_initialization`: Worker initializes with default and custom configurations.
2. `test_2_worker_handles_missing_credentials_safely`: Missing credentials keep worker idle without queue churn.
3. `test_3_empty_queue_produces_no_api_append`: Empty queue issues zero append calls.
4. `test_4_single_pending_record_synchronized_successfully`: Appends single row with `RAW` input and marks synced.
5. `test_5_multiple_records_synchronized_in_batch`: Formats and appends multi-row batch in single API call.
6. `test_6_idempotent_duplicate_prevention_does_not_append_existing_ids`: Existing Column A IDs excluded from append.
7. `test_7_uncertain_previous_response_retry_never_duplicates`: Recovers from dropped HTTP responses without duplication.
8. `test_8_successful_records_store_synced_at_timestamp`: Updates `synced_at` ISO timestamp in SQLite.
9. `test_9_temporary_network_error_schedules_retry`: Network timeout schedules exponential backoff.
10. `test_10_rate_limit_http_429_schedules_retry`: HTTP 429 classified as retryable.
11. `test_11_server_error_http_503_schedules_retry`: HTTP 503 classified as retryable.
12. `test_12_permission_failure_http_403_handled_safely`: HTTP 403 classified as `CLIENT_ERROR` without crashing.
13. `test_13_invalid_spreadsheet_id_handled_safely`: Empty spreadsheet ID returns `MISSING_SPREADSHEET_ID`.
14. `test_14_expired_leases_recovered_automatically`: Orphaned `in_progress` leases reset to `pending` and reclaimed.
15. `test_15_concurrent_workers_cannot_claim_same_records`: Leased records isolated between concurrent workers.
16. `test_16_partial_batch_failure_preserves_already_synced`: Appended records marked synced even if batch partner fails.
17. `test_17_worker_loop_terminates_cleanly_on_stop`: `stop()` event terminates polling loop cleanly.
18. `test_18_database_transactions_closed_during_network_calls`: SQLite lock closed before Google API calls.
19. `test_19_attendance_scan_does_not_block_on_worker`: `POST /api/scans` records in local SQLite in < 5ms.
20. `test_20_worker_never_modifies_authoritative_attendance_records`: Attendance table records strictly read-only.
21. `test_21_exhausted_retries_transition_to_dead_letter`: Records failing 5 times move to `dead_letter`.
22. `test_22_worker_logs_do_not_expose_secrets`: Private keys and secrets never printed to logs.
23. `test_23_admin_sync_status_endpoint`: Telemetry endpoint returns queue statistics safely.
24. `test_24_flask_cli_run_sheets_worker_once_option`: Flask CLI `run-sheets-worker --once` executes cleanly.

### 8.3 Physical Database Integrity Verification
Verification executed on `database/attendance.db`:
- `PRAGMA integrity_check`: `ok`
- `PRAGMA foreign_key_check`: `0 violations`
- Administrator account `Niraj` (role: `admin`, active: `1`) preserved intact.
- Existing student, session, attendance, and outbox tables preserved intact.

---

## 9. Beginner-Friendly Manual Testing Guide

Follow these steps to manually test the synchronization worker.

> [!NOTE]
> Automated tests use mocked Google Sheets services. To perform live cloud synchronization, you must have a valid Google Cloud Service Account JSON key and a shared Google Spreadsheet.

### Step 1: Google Service Account Setup
1. In Google Cloud Console, enable the **Google Sheets API**.
2. Create a **Service Account** (e.g. `smart-attendance-sync@<project-id>.iam.gserviceaccount.com`).
3. Under the Service Account **Keys** tab, click **Add Key -> Create new key -> JSON**.
4. Save the downloaded JSON file to:
   ```
   C:\Users\niraj\Downloads\Smart-Attendance-System\credentials\service_account.json
   ```
   *(Ensure the filename matches `service_account.json`)*.

### Step 2: Google Spreadsheet Configuration
1. Create a new Google Spreadsheet in your Google Drive (or open an existing one).
2. Click **Share** (top-right).
3. Paste the Service Account email address and grant **Editor** permissions.
4. Copy the Spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`<SPREADSHEET_ID>`**`/edit`
5. Create or edit `.env` in the project root:
   ```env
   GOOGLE_SERVICE_ACCOUNT_FILE=credentials/service_account.json
   GOOGLE_SHEETS_SPREADSHEET_ID=your_actual_spreadsheet_id_here
   GOOGLE_SHEETS_SHEET_NAME=Attendance
   GOOGLE_SHEETS_SYNC_ENABLED=true
   GOOGLE_SHEETS_SYNC_POLL_INTERVAL=10
   GOOGLE_SHEETS_SYNC_BATCH_SIZE=20
   ```

### Step 3: Start the Flask Application
Open PowerShell Terminal 1:
```powershell
cd C:\Users\niraj\Downloads\Smart-Attendance-System
.\.venv\Scripts\Activate.ps1
$env:FLASK_APP = "app"
flask run --port 5000
```
Open `http://127.0.0.1:5000` in your web browser.

### Step 4: Run the Sync Worker

#### Option A: Single Batch Test (`--once`)
Open PowerShell Terminal 2:
```powershell
cd C:\Users\niraj\Downloads\Smart-Attendance-System
.\.venv\Scripts\Activate.ps1
flask --app app run-sheets-worker --once
```
Expected output:
```
Initializing Google Sheets Synchronization Worker...
Running single batch synchronization...
Result: Successfully synchronized X attendance record(s) to Google Sheets.
```

#### Option B: Continuous Background Polling
In PowerShell Terminal 2:
```powershell
flask --app app run-sheets-worker
```
Expected output:
```
Initializing Google Sheets Synchronization Worker...
Starting continuous sync worker loop. Press Ctrl+C to terminate.
Google Sheets Sync Worker started. Poll interval: 10s, Batch size: 20...
```

### Step 5: Simulate Attendance and Verify End-to-End Sync
1. Open the Attendance Simulator page in your browser (`http://127.0.0.1:5000/simulator`) or trigger an RFID scan.
2. Scan a registered student card for an active class session.
3. Observe:
   - **Web UI:** Attendance is marked immediately (< 10 ms).
   - **Terminal 2 (Worker):** Within 10 seconds, the worker logs:
     `Worker claimed 1 record(s) for synchronization.`
     `Successfully synchronized 1 attendance record(s) to Google Sheets.`
   - **Google Sheets:** Row 1 shows the 10 headers (`Attendance ID`, `Timestamp`, `Student Name`, etc.). Row 2 shows the attendance record.
   - **SQLite Outbox:** In the SQLite database, `SELECT sync_status FROM sheets_sync_queue;` now reports `synced`.

### Step 6: Verify Admin Telemetry Endpoint
Log in as administrator and visit:
`http://127.0.0.1:5000/api/admin/sheets-sync/status`
Verify that `credentials_configured: true`, `spreadsheet_id_configured: true`, and `queue_stats.synced` reflects the synchronized record count.

---

## 10. Files Created and Modified

| File Path | Action | Description |
|---|---|---|
| `app/config.py` | Modified | Added worker configuration defaults (`GOOGLE_SHEETS_SYNC_ENABLED`, poll interval, batch size, lease duration). |
| `app/services/google_sheets.py` | Modified | Added `get_existing_attendance_ids()` for duplicate detection; set default `valueInputOption="RAW"` on append. |
| `app/services/sheets_worker.py` | Created | Complete background sync worker implementation (`sync_pending_records_batch` and `SheetsSyncWorker` class). |
| `app/services/__init__.py` | Modified | Exported `sheets_worker` module alongside `google_sheets` and `sheets_outbox`. |
| `app/cli.py` | Modified | Registered `flask run-sheets-worker` command with `--once`, `--interval`, and `--batch-size` options. |
| `app/routes/admin.py` | Modified | Added protected telemetry route `GET /api/admin/sheets-sync/status`. |
| `tests/test_sheets_worker.py` | Created | Comprehensive automated test suite with 24 dedicated scenarios. |
| `PHASE_3_MILESTONE_4_COMPLETION_REPORT.md` | Created | Comprehensive final milestone completion report. |
| `PHASE_3_MILESTONE_4_TARGETED_CORRECTION_REPORT.md` | Created | Targeted diagnostic report resolving the 4 initial test failures. |

---

## 11. Known Limitations & Cloud Verification Status

- **Automated Testing:** 100% verified using isolated mocked Google APIs.
- **Live Cloud Connectivity:** Remains safely unverified until you supply real Google Cloud Service Account credentials (`credentials/service_account.json`) and share a designated Google Spreadsheet with that service account.
- **Safe Offline Operation:** If credentials or spreadsheet ID are not configured, the worker logs a clean warning and remains safely idle without crashing, corrupting SQLite data, or entering infinite retry loops.

---

## 12. Milestone Approval Readiness

Phase 3 — Milestone 4 is **complete and ready for final review and approval**:
- All architectural contracts are preserved.
- SQLite remains the authoritative database.
- Attendance scanning performance is completely protected.
- Duplicate prevention and recovery mechanisms are fully implemented.
- 397/397 tests pass with 0 regressions.
- SQLite database integrity is clean.
