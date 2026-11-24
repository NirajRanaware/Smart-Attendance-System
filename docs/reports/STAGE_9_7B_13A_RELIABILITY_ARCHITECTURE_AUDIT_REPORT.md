# Stage 9.7B.13A — Google Sheets Reliability Architecture & Test Readiness Audit Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Execution Date:** 2026-11-24
**Audit Mode:** STRICT READ-ONLY CODE AND DATABASE AUDIT
**Lead Auditor:** Antigravity

---

## 1. Executive Summary

Following the successful execution of Stage 9.7B.12D, where two synthetic attendance records were synchronized to Google Sheets, this audit performed a comprehensive architectural and reliability review of the Google Sheets synchronization subsystem.

The goal of this audit is to inspect and document how the system handles transient network errors, API rate limits, worker process interruptions, duplicate executions, lease recovery, and dead-letter movements. Furthermore, it establishes a formal test coverage matrix of existing automated tests and specifies an isolated, disposable testing methodology for upcoming failure-injection tests (Stage 9.7B.13B).

### Key Audit Findings:
1. **Architectural Robustness Verified:** The system employs a decoupled, transactional outbox pattern in SQLite (`sheets_sync_queue`). Concurrency control uses exclusive SQLite write transactions (`BEGIN IMMEDIATE;`) and leased locks (`in_progress` with `lease_expires_at`).
2. **True Idempotent Deduplication:** Before appending rows to Google Sheets, the worker performs a live query of Column A (`'Attendance_Log'!A2:A`). Records already present in the spreadsheet are excluded from the append batch and transitioned directly to `synced` in SQLite, preventing duplicate rows even under worker crashes or network dropouts mid-flight.
3. **Resilient Failure Lifecycle:** Transient failures (timeouts, HTTP 429, HTTP 5xx) trigger exponential backoff with randomized jitter ($\pm 10\%$, capped at 900s). Records failing 5 consecutive times transition safely to `dead_letter`, preventing infinite retry loops.
4. **Rich Existing Automated Test Suite:** Over 80 specialized unit and integration tests exist in `tests/test_sheets_worker.py`, `tests/test_milestone5_reliability.py`, `tests/test_sheets_outbox.py`, and `tests/test_google_sheets.py`. All automated tests use mocked Google API responses and temporary SQLite databases, guaranteeing zero live network calls or production database pollution during test runs.
5. **Disposable Test Environment Required for Stage 13B:** To safely simulate edge-case failure conditions (e.g. lease expiry, dead-letter recovery, repeated worker invocation) without corrupting the verified manual-test baseline (`attendance_manual_test.db`), a separate, disposable test database (`attendance_reliability_test.db`) must be used for Stage 9.7B.13B.

**Final Verdict:** `ARCHITECTURE VERIFIED — READY FOR ISOLATED RELIABILITY TEST DESIGN`

---

## 2. Current Verified Baseline

Prior to beginning this audit, the authoritative project state from Stage 9.7B.12D was confirmed:

- **Manual-Test Database:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
  - Total Attendance Records: **2** (Attendance ID 1 for `MANUAL-001`, Attendance ID 2 for `MANUAL-002`)
  - Total Queue Records: **2**
  - Queue Status: Both records are in status **`synced`** with `synced_at = 2026-11-24T04:18:32Z`.
  - Integrity Check: `ok`
- **Google Sheets Target:** `Smart Attendance Management System` (`1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY`)
  - Worksheet: `'Attendance_Log'`
  - Total Rows: Exactly **4** rows (Row 1 headers, Row 2 synthetic baseline `-92022`, Row 3 Attendance 1, Row 4 Attendance 2).
  - Existing Synthetic Record `-92022`: Completely intact and untouched.
- **Production Database:** `database/attendance.db`
  - Total Attendance Records: **3**
  - Total Queue Records: **3** (all `pending`)
  - Status: Completely untouched throughout Stages 9.7B.11 through 9.7B.12D.

---

## 3. Synchronization Architecture

The synchronization subsystem is implemented across four primary modules:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Local Flask Application                         │
│                                                                        │
│   POST /api/scans  ────────►  record_attendance()                      │
│                                      │                                 │
│                                      ▼ (Atomic Transaction)            │
│                         ┌───────────────────────────┐                  │
│                         │   attendance table        │                  │
│                         │   sheets_sync_queue table │                  │
│                         └───────────────────────────┘                  │
└──────────────────────────────────────┬─────────────────────────────────┘
                                       │
                      Decoupled Outbox Polling
                                       │
┌──────────────────────────────────────▼─────────────────────────────────┐
│              Google Sheets Sync Worker (sheets_worker.py)               │
│                                                                        │
│  1. recover_expired_leases()  ──► Reclaim orphaned 'in_progress' rows  │
│  2. claim_records()           ──► Atomic BEGIN IMMEDIATE lock & lease  │
│  3. get_existing_attendance_ids() ──► Query Google Sheets Column A     │
│  4. Partition Records:                                                 │
│       ├─ New Records          ──► format_attendance_batch()            │
│       │                                  │                             │
│       │                                  ▼ RAW Append                  │
│       │                           Google Sheets API                    │
│       │                                  │                             │
│       └─ Already Present      ───────────┼─────────────────────────┐   │
│                                          ▼                         ▼   │
│  5. mark_as_synced()          ──► Update SQLite status to 'synced' │   │
│  6. On Failure: schedule_retry() ──► Backoff (or 'dead_letter' >= 5)   │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Modules and Roles:
1. **`app/services/sheets_outbox.py`:**
   Manages the authoritative SQLite outbox queue (`sheets_sync_queue`). Implements `create_queue_record()`, `claim_records()`, `recover_expired_leases()`, `mark_as_synced()`, `calculate_retry_delay()`, `schedule_retry()`, `move_to_dead_letter()`, `get_dead_letter_records()`, and `retry_dead_letter_records()`.
2. **`app/services/sheets_worker.py`:**
   Coordinates the worker lifecycle and batch synchronization. Implements `sync_pending_records_batch()` and the `SheetsSyncWorker` background loop with graceful `SIGINT`/`SIGTERM` handling.
3. **`app/services/google_sheets.py`:**
   Handles Google Sheets API client authentication, spreadsheet metadata retrieval, header provisioning, formula injection sanitization (CWE-1236), batch appending (`valueInputOption='RAW'`), and live Column A existing ID queries.
4. **`app/cli.py`:**
   Provides the administrative CLI interface via `@app.cli.command("run-sheets-worker")` supporting `--once`, `--interval`, and `--batch-size`.

---

## 4. Complete Queue Lifecycle

The state machine for `sheets_sync_queue.sync_status` enforces strict, unambiguous transitions:

```
                  ┌──────────────┐
                  │   pending    │ ◄────────────────────────┐
                  └──────┬───────┘                          │
                         │                                  │
                   claim_records()                 recover_expired_leases()
                   (Lease: 60s)                    (lease_expires_at <= now)
                         │                                  │
                         ▼                                  │
                  ┌──────────────┐                          │
           ┌───── │ in_progress  │ ─────────────────────────┘
           │      └──────┬───────┘
           │             │
           │       API Append / Remote Verification Success
           │       mark_as_synced()
           │             │
           │             ▼
           │      ┌──────────────┐
           │      │    synced    │ (Terminal Success)
           │      └──────────────┘
           │
     API Error / Timeout / HTTP 429 / HTTP 5xx
     schedule_retry()
           │
           ├──────────────────────────────────────┐
           │ (retry_count < 5)                    │ (retry_count >= 5)
           ▼                                      ▼
    ┌──────────────┐                       ┌──────────────┐
    │    failed    │                       │ dead_letter  │ (Quarantined)
    └──────┬───────┘                       └──────┬───────┘
           │                                      │
     next_retry_at <= now                         │ admin retry
     (Eligible for claim_records)                 │ retry_dead_letter_records()
           │                                      │
           └──────────────────────────────────────┘
```

### Lifecycle Transition Specifications:
- **`pending` → `in_progress`:** Atomically claimed by `claim_records()` inside `BEGIN IMMEDIATE;`. Sets `lease_expires_at = now + 60s`.
- **`in_progress` → `synced`:** Set by `mark_as_synced()` once row is appended or verified present in Column A. Clears `lease_expires_at` and sets `synced_at`.
- **`in_progress` → `failed`:** Scheduled by `schedule_retry()` upon transient failure. Increments `retry_count` ($1 \dots 4$), sets `next_retry_at = now + backoff + jitter`, clears `lease_expires_at`, and stores `last_error`.
- **`in_progress` / `failed` → `dead_letter`:** Triggered when `retry_count >= 5` or when explicitly designated permanent failure (`move_to_dead_letter()`). Clears `next_retry_at` and `lease_expires_at`.
- **`in_progress` → `pending`:** Triggered by `recover_expired_leases()` when a worker crashes or exceeds the 60s lease duration without updating status.
- **`dead_letter` → `pending`:** Triggered via administrative action (`retry_dead_letter_records()`), optionally resetting `retry_count = 0`.

---

## 5. Duplicate Execution and Idempotency Analysis

### A. How Existing Attendance IDs Are Detected:
`get_existing_attendance_ids()` in `app/services/google_sheets.py` reads `'Attendance_Log'!A2:A` via `service.spreadsheets().values().get`.
- Cell values are stripped of whitespace and leading single quotes (`'`).
- Numeric strings are parsed to native Python `int` values and added to a `set`.
- If a row contains invalid or empty values, it is safely ignored without throwing exceptions.

### B. ID Comparison Reliability:
In `sync_pending_records_batch()`:
```python
records_to_append = [r for r in claimed if r["attendance_id"] not in existing_ids]
already_synced_records = [r for r in claimed if r["attendance_id"] in existing_ids]
```
- Because SQLite `attendance.id` is an `INTEGER PRIMARY KEY` and `get_existing_attendance_ids()` parses Column A integers into a `set[int]`, comparison is exact ($O(1)$ set lookup).
- If an Attendance ID is already present, it is **never added** to `records_to_append`.

### C. Live vs. Cached Detection:
Detection is **100% LIVE**. Every batch execution dispatches a `values().get` API call to `'Attendance_Log'!A2:A`. No local in-memory caching of spreadsheet contents is used, ensuring that modifications made by external processes or earlier batches are immediately reflected.

### D. Concurrent Worker Execution Risk:
- If two workers run against SQLite, `claim_records()` uses `BEGIN IMMEDIATE;` to partition claimed records into disjoint sets.
- **Distributed Concurrency Window:** If two separate workers simultaneously claim different batches and both query Column A at the exact same moment before either appends, neither will see the other's pending append. However, because both workers have disjoint `attendance_id` sets, they append different records. Therefore, **no duplicate rows are created**.
- If Worker 1 claims Record 1, appends to Google Sheets, but network drops before SQLite is updated, Worker 1's lease will expire in 60s. When Worker 2 picks up Record 1, Worker 2 reads Column A, finds Record 1 already in Google Sheets, skips appending, and marks it `synced` in SQLite.

---

## 6. Retry and Backoff Analysis

### A. Delay Calculation Formula:
Implemented in `calculate_retry_delay()` ([app/services/sheets_outbox.py#L363-L400](file:///C:/Projects/Smart-Attendance-System/app/services/sheets_outbox.py#L363-L400)):
$$\text{exponential\_delay} = \text{base\_delay} \times 2^{\text{retry\_count}}$$
$$\text{capped\_delay} = \min(900.0, \text{exponential\_delay})$$
$$\text{jitter} \sim \mathcal{U}(-0.10 \times \text{capped\_delay}, +0.10 \times \text{capped\_delay})$$
$$\text{final\_delay} = \max(1.0, \min(900.0, \text{capped\_delay} + \text{jitter}))$$

- **Base delay:** 15 seconds.
- **Multiplication factor:** $2.0$.
- **Jitter:** $\pm 10\%$ uniform distribution.
- **Hard Ceiling:** 900 seconds (15 minutes).

### B. Retry Schedule Progression Table:
| Attempt # (`retry_count`) | Nominal Delay | Delay Range with $\pm 10\%$ Jitter | Status |
| :---: | :---: | :---: | :--- |
| **0 → 1** | 15s | 13.5s – 16.5s | `failed` |
| **1 → 2** | 30s | 27.0s – 33.0s | `failed` |
| **2 → 3** | 60s | 54.0s – 66.0s | `failed` |
| **3 → 4** | 120s | 108.0s – 132.0s | `failed` |
| **4 → 5** | 240s | 216.0s – 264.0s | `failed` |
| **5th Failure** | N/A | Immediate Transition | **`dead_letter`** |

### C. Error Classification and Handling:
`map_http_error()` maps exceptions into retryable vs non-retryable categories:
- **Retryable Errors:** HTTP 429 (Rate Limit), HTTP 500, 502, 503, 504 (Server Errors), `socket.timeout`, `TimeoutError`, and `ConnectionError`. These invoke `schedule_retry()`.
- **Non-Retryable Errors:** HTTP 401 (Auth Error), HTTP 403 (Permission Denied), HTTP 404 (Spreadsheet Not Found), and missing credential files. These abort batch claiming or invoke `move_to_dead_letter()`.

---

## 7. Lease and Recovery Analysis

### A. Transactional Record Claiming:
`claim_records()` ensures that record claiming is atomic:
1. Executes `BEGIN IMMEDIATE;` on the SQLite connection.
2. Selects candidate IDs: `SELECT id FROM sheets_sync_queue WHERE sync_status IN ('pending', 'failed') AND (next_retry_at IS NULL OR next_retry_at <= now) ORDER BY id ASC LIMIT ?;`.
3. Immediately updates status to `in_progress` and sets `lease_expires_at = now + 60s`.
4. Closes the SQLite transaction.
5. External Google Sheets network calls are performed with the SQLite connection closed, preventing database lock contention during network latency.

### B. Expired Lease Recovery:
At the start of every sync batch:
```python
UPDATE sheets_sync_queue
SET sync_status = 'pending',
    lease_expires_at = NULL,
    updated_at = ?
WHERE sync_status = 'in_progress'
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at <= ?;
```
- Active leases (`lease_expires_at > now`) are strictly protected and never touched.
- Crashed or killed worker leases (`lease_expires_at <= now`) are reset to `pending` without data loss.

---

## 8. Dead-Letter Analysis

1. **Dead-Letter State Guarantees:**
   - When `retry_count >= 5`, `sheets_sync_queue.sync_status` is updated to `'dead_letter'`.
   - `next_retry_at` is set to `NULL` so automatic polling loops never query or retry the record.
   - `last_error` stores the truncated (500 chars) diagnostic error message.
2. **Administrative Visibility & APIs:**
   - REST API `GET /api/admin/sheets-sync/dead-letter` provides paginated administrative access to dead-letter records, joining student and session data and categorizing errors into `exhausted_retries`, `permission_or_auth`, `not_found`, or `permanent_failure`.
   - Endpoint is protected by `@login_required` (admin authentication required).
3. **Administrative Requeueing:**
   - REST API `POST /api/admin/sheets-sync/dead-letter/retry` accepts optional `queue_ids` (or retries all if empty/omitted).
   - Resets status to `pending`, clears `last_error` and `lease_expires_at`, and optionally resets `retry_count = 0`.

---

## 9. Existing Test Coverage Matrix

Audit of `tests/test_sheets_worker.py`, `tests/test_milestone5_reliability.py`, `tests/test_sheets_outbox.py`, and `tests/test_google_sheets.py`:

| Scenario | Existing Automated Test | Test Type | Coverage Evaluation |
| :--- | :--- | :---: | :--- |
| **Duplicate Execution** | `test_sheets_worker.py::test_6_idempotent_duplicate_prevention_does_not_append_existing_ids`<br>`test_milestone5_reliability.py::test_m5_08_duplicate_prevention_across_repeated_syncs` | Mocked API | **PASS (Comprehensive Unit/Mock Coverage)**. Verified live in Stage 12D preflight. |
| **Existing Attendance ID** | `test_google_sheets.py::test_14_existing_header_detection_avoids_duplicate_write`<br>`test_sheets_worker.py::test_6_idempotent_duplicate_prevention_does_not_append_existing_ids` | Mocked API | **PASS (Comprehensive)**. Tests string, int, and empty cell parsing in Column A. |
| **API Timeout** | `test_google_sheets.py::test_12_network_timeout_handling`<br>`test_sheets_worker.py::test_9_temporary_network_error_schedules_retry`<br>`test_milestone5_reliability.py::test_m5_02_transient_network_error_timeout` | Mocked API | **PASS (Comprehensive)**. Tests `socket.timeout` and `TimeoutError` backoff scheduling. |
| **API Authentication Failure** | `test_google_sheets.py::test_8_permission_denied_http_403_handling`<br>`test_sheets_worker.py::test_2_worker_handles_missing_credentials_safely`<br>`test_sheets_worker.py::test_12_permission_failure_http_403_handled_safely`<br>`test_milestone5_reliability.py::test_m5_06_client_error_http_403_and_404` | Mocked API | **PASS (Comprehensive)**. Confirms HTTP 401/403 classified without credential leakage. |
| **Retry Scheduling** | `test_sheets_outbox.py::test_14_retry_scheduling_works_correctly`<br>`test_milestone5_reliability.py::test_m5_02_transient_network_error_timeout` | Mocked DB | **PASS (Comprehensive)**. Tests state update to `failed`, count increment, and timestamp generation. |
| **Exponential Backoff** | `test_sheets_outbox.py::test_15_exponential_backoff_respects_maximum_delay`<br>`test_sheets_outbox.py::test_21_retry_delay_absolute_cap_never_exceeds_900_seconds`<br>`test_milestone5_reliability.py::test_m5_01_retry_policy_parameters` | Unit Logic | **PASS (Comprehensive)**. Tests mathematical progression, jitter range, and 900s cap. |
| **Worker Interruption** | `test_sheets_worker.py::test_17_worker_loop_terminates_cleanly_on_stop`<br>`test_sheets_worker.py::test_18_database_transactions_closed_during_network_calls` | Unit / Mock | **PASS (Unit/Mock)**. Missing OS-level abrupt process kill test. |
| **Lease Expiration** | `test_sheets_outbox.py::test_10_records_can_be_claimed_safely`<br>`test_milestone5_reliability.py::test_m5_11_active_lease_not_reclaimed_prematurely` | Mocked DB | **PASS (Comprehensive)**. Tests that active unexpired leases are preserved. |
| **Lease Recovery** | `test_sheets_outbox.py::test_12_expired_leases_can_be_recovered`<br>`test_sheets_worker.py::test_14_expired_leases_recovered_automatically`<br>`test_milestone5_reliability.py::test_m5_12_expired_lease_recovered_and_resumed` | Mocked API | **PASS (Comprehensive)**. Tests automatic reset of expired leases back to `pending`. |
| **Maximum Retries** | `test_sheets_outbox.py::test_16_maximum_attempts_lead_to_dead_letter`<br>`test_sheets_worker.py::test_21_exhausted_retries_transition_to_dead_letter`<br>`test_milestone5_reliability.py::test_m5_07_dead_letter_transition_after_five_attempts` | Mocked API | **PASS (Comprehensive)**. Verifies exact transition after attempt 5. |
| **Dead-Letter Transition & API** | `test_milestone5_reliability.py::test_m5_13_dead_letter_retrieval_and_error_categorization`<br>`test_milestone5_reliability.py::test_m5_14_retry_dead_letter_records_all_and_selective`<br>`test_milestone5_reliability.py::test_m5_15_admin_dead_letter_endpoints_auth_protection`<br>`test_milestone5_reliability.py::test_m5_16_admin_dead_letter_endpoints_authenticated_usage` | Integration | **PASS (Comprehensive)**. Tests categorization, admin REST listing, and selective retry. |
| **Database Isolation** | `test_database_override.py`<br>`test_sheets_worker.py::test_20_worker_never_modifies_authoritative_attendance_records` | Integration | **PASS (Unit/Mock)**. Missing CLI-level validation enforcing non-production database. |
| **Concurrent Workers** | `test_sheets_outbox.py::test_11_concurrent_workers_cannot_claim_same_record`<br>`test_sheets_worker.py::test_15_concurrent_workers_cannot_claim_same_records`<br>`test_milestone5_reliability.py::test_m5_10_concurrent_workers_claim_disjoint_batches` | Mocked DB | **PASS (Comprehensive)**. Tests multi-threaded / concurrent claim atomicity and disjoint sets. |

---

## 10. Database Isolation Analysis

1. **Configuration Precedence:**
   In `app/config.py`:
   ```python
   DATABASE_PATH = os.environ.get(
       "DATABASE_PATH",
       os.path.join(basedir, "../database/attendance.db")
   )
   ```
2. **Root Vulnerability Identified:**
   If `DATABASE_PATH` is **omitted** in the executing shell, the application and CLI commands automatically fall back to `database/attendance.db` (the **PRODUCTION DATABASE**).
3. **Safe Disposable Database Strategy:**
   To guarantee production isolation, all failure simulations in Stage 9.7B.13B must be pointed to a dedicated disposable database path:
   `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_reliability_test.db`.
   The shell executing tests must verify that `Config.DATABASE_PATH` matches this disposable path before any command runs.

---

## 11. Recommended Disposable Testing Architecture (Stage 13B Design)

To protect:
1. The **Production Database** (`database/attendance.db`),
2. The **Verified Manual-Test Database** (`database/manual_test/attendance_manual_test.db`), and
3. The **Live Google Sheet** (`Attendance_Log`),

The following three-tier testing architecture is recommended for Stage 9.7B.13B:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        STAGE 9.7B.13B TEST RUNNER                      │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  [ Tier 1: Disposable SQLite Database ]                                │
│  Path: database/manual_test/attendance_reliability_test.db             │
│  - Fresh copy initialized with standard schema and synthetic fixtures. │
│  - Zero modifications to attendance_manual_test.db or attendance.db.   │
│                                                                        │
│  [ Tier 2: Controlled Fault-Injection Worker Tests ]                   │
│  - Script-driven CLI invocations using mock/fault-injected services.   │
│  - Simulates socket.timeout, HTTP 429, HTTP 503, lease expiration.     │
│  - Verifies retry count increments, next_retry_at, and dead-letter.    │
│                                                                        │
│  [ Tier 3: Live Idempotency & Repeated Worker Test ]                   │
│  - Runs 'flask run-sheets-worker --once' against real Google Sheet     │
│    using already-synced attendance_manual_test.db state.               │
│  - Verifies zero rows appended, already_present_count = 2,             │
│    and live Column A preservation.                                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 12. Detailed Test Scenarios for Stage 13B

### Scenario 1: Idempotency Across Repeated Syncs (Live Google Sheets)
- **Fixture:** `database/manual_test/attendance_manual_test.db` (current state: Queue IDs 1 & 2 are `synced`).
- **Pre-State:** SQLite: 2 synced records. Google Sheets: Rows 3 and 4 contain Attendance IDs 1 and 2.
- **Simulated Event:** Manually reset Queue IDs 1 and 2 to `pending` in a **disposable copy** (`attendance_reliability_test.db`) and run `flask run-sheets-worker --once`.
- **Expected Post-State:** Worker claims both records, reads Column A from Google Sheets, detects Attendance IDs 1 and 2 already present, appends **0 rows**, transitions both records to `synced`, and reports `already_present_count = 2`.
- **Google Sheets Impact:** Zero new rows added. Total rows remains exactly 4.

### Scenario 2: Transient Network Failure & Exponential Backoff (Fault Injection)
- **Fixture:** Disposable DB with 1 pending record.
- **Simulated Event:** Worker attempts sync with mock service raising `socket.timeout`.
- **Expected Post-State:** Status transitions to `failed`, `retry_count = 1`, `next_retry_at` is scheduled $\approx 15\text{s} \pm 10\%$, and `last_error` contains timeout description.
- **Google Sheets Impact:** Zero API calls dispatched.

### Scenario 3: Lease Expiration & Worker Crash Recovery
- **Fixture:** Disposable DB with 1 record artificially set to `in_progress` with `lease_expires_at = now - 10s` (simulating worker killed 70 seconds ago).
- **Simulated Event:** Invoke `recover_expired_leases()`.
- **Expected Post-State:** Status resets to `pending`, `lease_expires_at` is cleared to `NULL`, and recovered count = 1.

### Scenario 4: Maximum Retries & Dead-Letter Quarantine
- **Fixture:** Disposable DB with 1 record at `retry_count = 4` in `failed` status.
- **Simulated Event:** Worker attempts sync with mock service raising `HttpError(503)`.
- **Expected Post-State:** `retry_count` reaches 5, status transitions directly to **`dead_letter`**, `next_retry_at` is set to `NULL`, and the record is excluded from subsequent sync polling.

### Scenario 5: Dead-Letter Administrative Recovery
- **Fixture:** Disposable DB with 1 record in `dead_letter` status.
- **Simulated Event:** Admin invokes `retry_dead_letter_records(queue_ids=[...], reset_retries=True)`.
- **Expected Post-State:** Status resets to `pending`, `retry_count` resets to 0, and record becomes immediately eligible for synchronization.

---

## 13. Identified Bugs, Risks, and Observations

1. **Unset `DATABASE_PATH` Production Fallback Risk:**
   *Observation:* If `DATABASE_PATH` is unset in the execution environment, the worker defaults to `database/attendance.db`.
   *Risk:* A test run without proper environment initialization could accidentally query production data.
   *Mitigation:* Stage 13B must require an automated pre-flight assertion script that checks `$env:DATABASE_PATH` and immediately halts if it points to `attendance.db` or is unset.
2. **Delivery Semantics (At-Least-Once with Remote Deduplication):**
   *Observation:* Because Google Sheets API does not participate in distributed two-phase commit (2PC) transactions with SQLite, there exists a brief window where Google Sheets appends the row, but network drops before SQLite records `synced`.
   *Assessment:* The architecture correctly mitigates this via `get_existing_attendance_ids()`. Upon the subsequent retry, the worker detects the row in Column A, skips the append, and marks SQLite as `synced`. Delivery semantics are strictly **at-least-once with idempotent remote deduplication**, which guarantees zero duplicate rows.
3. **Lease Duration vs Network Timeout:**
   *Observation:* Lease duration is 60 seconds (`DEFAULT_LEASE_DURATION_SECONDS`).
   *Assessment:* Default Google API socket timeout is 30 seconds. Because lease duration (60s) is comfortably greater than the API timeout (30s), active worker leases will not expire prematurely during normal transient retries.

---

## 14. Recommended Implementation Enhancements (For Future Phases)

*(No code modifications are authorized or made in Stage 13A; these are noted for post-testing consideration)*
1. **CLI Target Database Safety Guard:** Add an explicit confirmation prompt or `--test-db` flag to `flask run-sheets-worker` to prevent accidental execution against `attendance.db` when running outside production.
2. **Admin UI Manual Lease Reclaim Button:** Surface an administrative control on the Settings page allowing manual invocation of `recover_expired_leases()` without waiting for the next worker cycle.

---

## 15. Recommended Execution Order for Stage 9.7B.13B

1. **Step 1:** Prepare disposable test database `database/manual_test/attendance_reliability_test.db` with identical schema and isolated test records.
2. **Step 2:** Execute automated reliability tests using `pytest` against mocked Google APIs to verify failure backoff, lease recovery, and dead-letter transitions without external network dependency.
3. **Step 3:** Execute controlled fault-injection script to verify the dead-letter administrative recovery API (`retry_dead_letter_records`).
4. **Step 4:** Execute the live idempotency test using `flask run-sheets-worker --once` against the real Google Sheet to prove zero duplicate rows are appended.
5. **Step 5:** Clean up disposable database and verify pristine retention of `attendance_manual_test.db` and `attendance.db`.

---

## 16. Final Verdict

# `ARCHITECTURE VERIFIED — READY FOR ISOLATED RELIABILITY TEST DESIGN`

The synchronization subsystem's failure handling, retry logic, concurrency locking, lease recovery, and deduplication mechanisms are architecturally sound, thoroughly tested via unit fixtures, and verified ready for controlled reliability testing in Stage 9.7B.13B.
