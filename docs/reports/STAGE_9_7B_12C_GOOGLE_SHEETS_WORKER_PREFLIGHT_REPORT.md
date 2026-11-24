# Stage 9.7B.12C — Google Sheets Isolated Sync Worker Preflight Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Execution Date:** 2026-11-24
**Audit Mode:** STRICT READ-ONLY PREFLIGHT
**Worker Execution:** PROHIBITED (Zero Worker Runs)
**External API:** READ-ONLY / NO WRITES
**Lead Auditor:** Antigravity

---

## 1. Executive Summary

This preflight evaluation audited all prerequisites, architectural mechanics, and safety boundaries for the upcoming Google Sheets synchronization test. In earlier phases (Stage 9.7B.11F through 9.7B.12B), two synthetic attendance scans were recorded via the web scanner simulator against active Session 3 (`CS-101: Test Lecture One`), creating 2 pending queue records in the isolated manual-test database (`attendance_manual_test.db`).

The objective of Stage 9.7B.12C is to determine exactly how the synchronization worker operates, verify all preconditions, and evaluate what would occur upon execution, **without performing any synchronization, worker runs, or database modifications.**

### Key Audit Findings:
1. **Isolated Manual-Test Database Verified:** `attendance_manual_test.db` contains exactly 2 pending queue records (Queue IDs 1 and 2), referencing Attendance IDs 1 and 2 for synthetic test students (`MANUAL-001` and `MANUAL-002`). Zero records are in other queue states.
2. **Worker CLI & Entry Point Audited:** The worker is registered as `flask run-sheets-worker` in `app/cli.py`, backed by `SheetsSyncWorker` in `app/services/sheets_worker.py`.
3. **Database Path Resolution & Default Risk Identified:** The worker resolves its database path from `app.config.get("DATABASE_PATH")`, which falls back to `os.path.join(basedir, "../database/attendance.db")` (**the production database**) if `DATABASE_PATH` is unset. Because environment variables do not persist across separate shells, running the worker in a fresh shell without explicit environment variable overrides would default to production.
4. **Execution Scope & Absence of Record Filtering:** The worker has **no CLI parameter to limit processing to specific queue IDs** and **no dry-run mode**. It claims all eligible pending records up to `batch_size` (default 20). Because `attendance_manual_test.db` contains only the 2 test records, running the worker with `--once` will process exactly these two records—provided the manual database path is strictly enforced.
5. **Google Sheets Destination Ready:** Service account credentials exist securely outside the project repository at `C:\Users\niraj\.config\smart-attendance\google-service-account.json`. Target spreadsheet `1G2k...07PY` and worksheet `Attendance_Log` are verified.
6. **Zero Write Operations:** No worker processes were started, no queue statuses were changed, no database modifications were made, and no Google Sheets API write calls were issued.

**Final Preflight Verdict:** `PREFLIGHT PASSED — READY FOR USER AUTHORIZATION`

---

## 2. Previous Stage 12B Verification Summary

Stage 9.7B.12B established the authoritative database isolation baseline:
- The running Flask application and the settings dashboard (`/settings`) query `database/manual_test/attendance_manual_test.db`.
- The telemetry endpoint `GET /api/admin/sheets-sync/status` queries the same database via `get_queue_stats()`.
- The production database (`database/attendance.db`) holds 3 pending queue records from earlier system operations (Nov 8, 20, and 22). Its SHA-256 hash was verified as `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`, matching its pre-audit baseline with 100% fidelity.
- During Stage 9.7B.12C, the production database was **not opened, inspected, or modified**, preserving strict isolation.

---

## 3. Manual-Test Database Verification

The isolated manual-test database was inspected using a read-only SQLite connection (`mode=ro`):

- **Resolved Absolute Path:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
- **File Existence:** Confirmed (`True`)
- **File Size:** 94,208 bytes
- **SQLite Integrity Check:** `ok`
- **Total Attendance Records:** 2
- **Total Queue Records:** 2

### Outbox Queue Status Breakdown:
| Queue Status | Count | Notes |
| :--- | :---: | :--- |
| **`pending`** | **2** | Eligible for synchronization |
| **`in_progress`** | 0 | No active or orphaned leases |
| **`synced`** | 0 | None yet processed |
| **`failed`** | 0 | Zero transient failures |
| **`dead_letter`** | 0 | Zero permanent failures |
| **Total** | **2** | Matches exactly 2 simulated attendance events |

---

## 4. Pending Queue Record Summary

The two pending queue records were audited row-by-row. Both records correspond exclusively to the synthetic manual-test attendance events generated on 2026-11-23:

| Field | Queue Record 1 | Queue Record 2 |
| :--- | :--- | :--- |
| **Queue ID** | `1` | `2` |
| **Attendance ID** | `1` | `2` |
| **Session ID** | `3` (`CS-101: Test Lecture One`, 2026-11-24) | `3` (`CS-101: Test Lecture One`, 2026-11-24) |
| **Sync Status** | `pending` | `pending` |
| **Retry Count** | `0` | `0` |
| **Created Timestamp** | `2026-11-23T17:11:28Z` | `2026-11-23T17:11:32Z` |
| **Next Retry Timestamp** | `NULL` | `NULL` |
| **Last Error** | `NULL` | `NULL` |
| **Student Roll Number** | `MANUAL-001` (Synthetic) | `MANUAL-002` (Synthetic) |
| **Student Full Name** | `Test Student One` (Synthetic) | `Test Student Two` (Synthetic) |
| **Card UID** | `E004...01` (Synthetic) | `E004...02` (Synthetic) |
| **Scan Source** | `SIMULATOR` | `SIMULATOR` |
| **Attendance Status** | `Present` | `Present` |

> [!NOTE]
> No real student personal information exists in these records. All data originates from synthetic manual-test fixtures.

---

## 5. Worker Static Implementation Review

A static source-code audit was conducted across `app/cli.py`, `app/services/sheets_worker.py`, `app/services/sheets_outbox.py`, and `app/services/google_sheets.py`:

1. **Worker CLI Command:**
   `flask run-sheets-worker` (or `python -m flask run-sheets-worker`).
2. **CLI Entry Point:**
   Registered via `@app.cli.command("run-sheets-worker")` in [app/cli.py#L83-L120](file:///C:/Projects/Smart-Attendance-System/app/cli.py#L83-L120).
   CLI options:
   - `--once`: Flag. Executes a single synchronization batch and terminates immediately.
   - `--interval`: Integer. Polling interval in seconds (default: 10s).
   - `--batch-size`: Integer. Maximum records to process per batch (default: 20).
3. **Worker Module & Lifecycle:**
   `SheetsSyncWorker` class in [app/services/sheets_worker.py#L360-L485](file:///C:/Projects/Smart-Attendance-System/app/services/sheets_worker.py#L360-L485).
   - `run(once=True)` calls `process_batch()`, which invokes `sync_pending_records_batch()`.
   - `run(once=False)` enters a while-loop polling every `poll_interval` seconds, gracefully terminating on `SIGINT` (Ctrl+C) or `SIGTERM`.
4. **Queue Selection & Concurrency Locking:**
   Implemented in `claim_records()` ([app/services/sheets_outbox.py#L182-L276](file:///C:/Projects/Smart-Attendance-System/app/services/sheets_outbox.py#L182-L276)).
   - Executes `BEGIN IMMEDIATE;` to acquire an exclusive SQLite write lock.
   - Queries `SELECT id FROM sheets_sync_queue WHERE sync_status IN ('pending', 'failed') AND (next_retry_at IS NULL OR next_retry_at <= ?) ORDER BY id ASC LIMIT ?;`.
   - Atomically transitions claimed IDs to `sync_status = 'in_progress'` and sets `lease_expires_at = now + lease_duration` (default: 60s).
   - Prevents concurrent worker instances from double-claiming records.
5. **Queue Ordering:** Strict FIFO by primary key (`ORDER BY id ASC`).
6. **Batch Size:** Configurable via `--batch-size` CLI flag or `GOOGLE_SHEETS_SYNC_BATCH_SIZE`, defaulting to 20 records.
7. **Maximum Records Processed Per Execution:**
   - With `--once`: Up to 1 batch (maximum 20 records).
   - Without `--once`: Continuous loop processing batches indefinitely until interrupted.
8. **Retry & Dead-Letter Handling:**
   Managed by `schedule_retry()` in [app/services/sheets_outbox.py#L320-L420](file:///C:/Projects/Smart-Attendance-System/app/services/sheets_outbox.py#L320-L420):
   - Transitions record from `in_progress` to `failed`.
   - Increments `retry_count`.
   - Schedules exponential backoff with jitter: `delay = min(max_delay, base_delay * (2 ** retry_count)) + jitter`.
   - If `retry_count >= 5`, transitions record to `dead_letter`.
9. **Lease Duration & Recovery:**
   Lease duration defaults to 60 seconds. On worker startup and at the beginning of every batch, `recover_expired_leases()` resets any orphaned `in_progress` records whose `lease_expires_at <= now` back to `pending`.
10. **Duplicate & Idempotency Protection:**
    Before appending records, `get_existing_attendance_ids()` queries Column A of the target sheet (`'Attendance_Log'!A2:A`). Records whose `attendance_id` already exists in Google Sheets are excluded from the append payload and directly transitioned to `synced` in SQLite.
11. **Formula-Injection Protection (CWE-1236):**
    `sanitize_sheet_cell()` in [app/services/google_sheets.py#L278-L300](file:///C:/Projects/Smart-Attendance-System/app/services/google_sheets.py#L278-L300) inspects all string fields. If a value begins with `=`, `+`, `-`, `@`, `\t`, or `\r`, it prepends a single quote (`'`), ensuring Google Sheets treats the cell as literal text rather than an executable formula. Integers (such as `attendance_id`) remain raw numeric values.
12. **Error Handling & Classification:**
    `map_http_error()` maps Google API HTTP errors:
    - HTTP 401 (Auth Error), 403 (Permission Denied), 404 (Not Found) are classified non-retryable client errors.
    - HTTP 429 (Rate Limit), 5xx (Server Error), and socket/connection timeouts are classified retryable.
13. **Worker Exit Codes:** Clean exit code `0` on successful completion of `--once` or clean SIGINT shutdown; non-zero if unhandled fatal exceptions occur.

---

## 6. Worker Database Path Resolution

The database path resolution in `SheetsSyncWorker` was closely examined:

```python
# app/services/sheets_worker.py (Lines 380-397)
if app is not None:
    cfg = app.config
elif has_app_context():
    cfg = current_app.config
else:
    cfg = get_config()

self.db_path = db_path or _get_val("DATABASE_PATH", None)
```

And in `app/config.py`:
```python
DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(basedir, "../database/attendance.db")
)
```

### Critical Architectural Finding:
1. When `flask run-sheets-worker` is invoked, Flask calls `create_app()`, which loads `Config.DATABASE_PATH`.
2. **If `DATABASE_PATH` is NOT defined in the shell environment, it falls back to `database/attendance.db` (the PRODUCTION database).**
3. Because Windows PowerShell process environments are isolated, running `flask run-sheets-worker` in a fresh terminal **without** first exporting `$env:DATABASE_PATH = "database/manual_test/attendance_manual_test.db"` will cause the worker to process the **production database**.
4. The worker does not read a persistent `.env` file automatically at the CLI level unless explicitly loaded or exported into the shell.

---

## 7. Worker Execution Scope and Limitations

| Capability / Feature | Implementation Status | Technical Details |
| :--- | :---: | :--- |
| **Queue ID Level Filtering** | **NOT SUPPORTED** | The CLI command does not accept `--queue-id`, `--id`, or `--attendance-id`. |
| **Dry-Run Mode** | **NOT SUPPORTED** | The worker has no `--dry-run` or `--simulate` flag. |
| **Single-Batch Execution** | **SUPPORTED** | `--once` processes up to 1 batch and exits. |
| **Batch Size Limiting** | **SUPPORTED** | `--batch-size N` can restrict the maximum records claimed. |

### Critical Preflight Conclusion:
Because the worker has no record-filtering flags and processes all eligible pending records in the target database, **database isolation is the sole barrier protecting production data.**
Because `attendance_manual_test.db` contains exactly 2 pending records (and zero other records), running `flask run-sheets-worker --once` against `attendance_manual_test.db` will claim and process **strictly those two synthetic records**.

---

## 8. Google Sheets Destination Configuration

Configuration parameters and credential security were audited:

- **Target Spreadsheet Title:** `Smart Attendance Management System`
- **Spreadsheet ID:** `1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY` (Masked: `1G2k...07PY`)
- **Target Worksheet (Tab):** `Attendance_Log`
- **Synchronization State:** Enabled (`GOOGLE_SHEETS_SYNC_ENABLED=True`)
- **Service Account Credential Path:**
  `C:\Users\niraj\.config\smart-attendance\google-service-account.json`
- **Credential Storage Security:**
  - File exists on disk (verified size: 2,432 bytes).
  - Located completely **outside** the project workspace.
  - Zero private keys, passwords, or tokens were printed or logged.
- **Worksheet Headers Compatibility:**
  Verified in Stage 9.7B.12A to match the 9 standard columns:
  `["Attendance ID", "Date", "Time", "Roll Number", "Student Name", "Course/Session", "RFID UID", "Status", "Source/Device"]`.
- **Existing Synthetic Row:**
  Attendance ID `-92022` exists in row 2 from earlier milestone verification.

---

## 9. Same-Shell Worker Safety Analysis

Because environment variables do not persist across separate Windows PowerShell sessions, the future synchronization test must be executed strictly within a configured shell session.

### Required Environment Variable Configuration for Future Authorized Test:
```powershell
$env:DATABASE_PATH = "database/manual_test/attendance_manual_test.db"
$env:GOOGLE_SERVICE_ACCOUNT_FILE = "C:\Users\niraj\.config\smart-attendance\google-service-account.json"
$env:GOOGLE_SHEETS_SPREADSHEET_ID = "1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY"
$env:GOOGLE_SHEETS_SHEET_NAME = "Attendance_Log"
$env:GOOGLE_SHEETS_SYNC_ENABLED = "True"
```

### Pre-Execution Database Verification Command:
Before executing the worker, the operator must verify that Flask resolves the intended database path:
```powershell
python -c "from app import create_app; app = create_app(); print('RESOLVED_DATABASE_PATH:', app.config['DATABASE_PATH'])"
```
**Mandatory Safety Rule:** If the command does not output `database/manual_test/attendance_manual_test.db`, execution must be aborted immediately.

---

## 10. Expected Impact of Future Sync Execution

When the worker is executed with `--once` against `attendance_manual_test.db`:

1. **Eligible Records:** Queue ID 1 and Queue ID 2.
2. **Records Claimed:** Exactly 2 records.
3. **Google Sheets Operations:**
   - 1 read operation (`values().get`) to `'Attendance_Log'!A1:I1` to verify headers.
   - 1 read operation (`values().get`) to `'Attendance_Log'!A2:A` to check existing IDs.
   - 1 write operation (`values().append`) with `valueInputOption="RAW"` appending exactly 2 rows.
4. **Appended Rows Schema:**
   - **Row 1:** `[1, "2026-11-23", "22:41:28", "MANUAL-001", "Test Student One", "CS-101: Test Lecture One", "04A1B2C3D4", "Present", "SIMULATOR"]`
   - **Row 2:** `[2, "2026-11-23", "22:41:32", "MANUAL-002", "Test Student Two", "CS-101: Test Lecture One", "04E5F6A7B8", "Present", "SIMULATOR"]`
5. **Untouched Rows:** Row 2 (`Attendance ID: -92022`) remains completely intact; `append` writes to the next available rows (rows 3 and 4).
6. **SQLite Outbox State Transition:**
   - Queue ID 1: `sync_status` transitions from `pending` -> `in_progress` -> `synced`, `synced_at` set to current UTC timestamp.
   - Queue ID 2: `sync_status` transitions from `pending` -> `in_progress` -> `synced`, `synced_at` set to current UTC timestamp.

---

## 11. Duplicate-Prevention Analysis

The system enforces multi-layered duplicate prevention:

1. **Local Concurrency Lock:** `claim_records()` atomically transitions records to `in_progress` within a `BEGIN IMMEDIATE;` transaction. No second worker can claim the same records.
2. **Remote Idempotency Check:** `get_existing_attendance_ids()` reads `'Attendance_Log'!A2:A`. If the worker is executed a second time, Attendance IDs 1 and 2 will be detected in Google Sheets. The worker will automatically classify them as `already_synced_records`, skip the append call, and mark them `synced` in SQLite without generating duplicate rows.

---

## 12. Failure and Retry Analysis

1. **Network Drop or API Outage During Append:**
   If Google Sheets returns HTTP 429, 500, 503, or a socket timeout occurs, `append_attendance_batch()` returns `success: False`.
   - The worker catches this and invokes `schedule_retry()`.
   - Records transition from `in_progress` to `failed`.
   - `retry_count` increments to 1.
   - `next_retry_at` is scheduled with exponential backoff (~15 seconds + jitter).
   - `last_error` stores the sanitized error message.
2. **Network Interruption After Append Succeeds but Before Client Response:**
   If the rows were added to Google Sheets but the HTTP 200 response was dropped, the subsequent retry will query Column A, find Attendance IDs 1 and 2 already present, and safely mark them `synced` without re-appending.
3. **Repeated Failures:** If 5 consecutive retries fail, records transition to `dead_letter`, where they remain safely quarantined for administrator review.

---

## 13. Safety Verification

- **Zero Write Requests Dispatched:** No Google Sheets API write calls were dispatched during this audit.
- **Zero Database Writes:** No `INSERT`, `UPDATE`, `DELETE`, or schema modifications were executed.
- **Worker Execution Prohibited:** `flask run-sheets-worker` was not run.
- **Production Database Immutability:** `database/attendance.db` was not opened or accessed.
- **Credential Protection:** Secrets and service account keys were kept secure and never exposed.

---

## 14. Remaining Risks and Mitigations

| Identified Risk | Severity | Required Mitigation / Operational Control |
| :--- | :---: | :--- |
| **Worker Launched in Fresh Shell Without Env Vars** | **HIGH** | Operator must run the mandatory PowerShell environment configuration commands before launching the worker. |
| **Worker Executed Without `--once` Flag** | **MEDIUM** | Operator must explicitly include `--once` to ensure single-batch termination rather than an infinite loop. |
| **Spreadsheet ID Mismatch** | **LOW** | Pre-execution verification command validates both database path and spreadsheet ID before execution. |

---

## 15. Recommended Next Stage

### Stage 9.7B.12D — Controlled Isolated Google Sheets Synchronization Execution
Authorize and execute the single-batch synchronization test:
1. Set the validated environment variables in the test shell.
2. Run the pre-execution database path verification command.
3. Execute `flask run-sheets-worker --once`.
4. Perform post-sync verification of `attendance_manual_test.db` (verify Queue IDs 1 and 2 are `synced`).
5. Perform read-only verification of Google Sheets `Attendance_Log` (verify rows 3 and 4 contain the 2 attendance events).
6. Verify production database remains untouched.

---

## 16. Final Preflight Verdict

# `PREFLIGHT PASSED — READY FOR USER AUTHORIZATION`
