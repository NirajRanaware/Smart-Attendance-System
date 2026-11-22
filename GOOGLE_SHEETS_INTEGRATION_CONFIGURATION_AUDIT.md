# GOOGLE SHEETS INTEGRATION — CREDENTIAL & ARCHITECTURE CONFIGURATION AUDIT REPORT

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Date:** 2026-11-22  
**Audit Type:** Credential, Architectural, and Read-Only Spreadsheet Access Inspection  
**Status:** COMPLETE (Passed All Read-Only Verification Gates)  

---

## 1. Executive Summary

This audit evaluated the readiness and security of the Google Sheets synchronization architecture within the Smart Attendance Management System.

Using Google Cloud Service Account credentials stored strictly outside the repository, we performed live, read-only authentication and verified access to the designated Google Spreadsheet without appending sample records, creating fake attendance rows, or modifying any production data.

### Key Verification Metrics
- **Credential Storage Location:** `C:\Users\niraj\.config\smart-attendance\google-service-account.json` (**Outside project root**).
- **Service Account Identity:** `smart-attendance-backend@smart-attendance-system-515507.iam.gserviceaccount.com`.
- **Google Cloud Project ID:** `smart-attendance-system-515507` (Google Sheets API Enabled).
- **Spreadsheet Access:** **Verified** (`1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY`, Title: *"Smart Attendance Management System"*).
- **Worksheet Target:** Located worksheet **`Attendance_Log`**.
- **Header Order & Match:** **9/9 columns matched exactly** in expected order.
- **Existing Test Suite:** **67/67 automated tests passed** (`test_google_sheets.py`, `test_sheets_outbox.py`, `test_sheets_worker.py`).
- **Production Source Code & DB Modifications:** **0 lines modified, 0 records written**.

---

## 2. Existing Integration Architecture

The Google Sheets integration operates as a decoupled, asynchronous, secondary reporting sink. It guarantees that physical RFID card scans and edge ESP32 terminals are never blocked by cloud API latency or external network outages.

```
+-----------------------------------------------------------------------------------+
| 1. EDGE INGESTION (POST /api/scans)                                               |
|    - Authenticates hardware device (X-Device-Id, X-API-Key).                      |
|    - Validates student active flag and class session bounds.                      |
|    - Opens atomic SQLite transaction: BEGIN IMMEDIATE;                            |
|      * Inserts into attendance table (Authoritative Single Source of Truth).      |
|      * Inserts into sheets_sync_queue (sync_status='pending', retry_count=0).     |
|      * Commits SQLite transaction.                                                |
|    - Returns HTTP 201 Created to ESP32 immediately (Zero cloud dependency).       |
+-----------------------------------------------------------------------------------+
                                          |
                        Background Polling / Outbox Pattern
                                          v
+-----------------------------------------------------------------------------------+
| 2. LOCAL OUTBOX SERVICE (app/services/sheets_outbox.py)                           |
|    - Table: sheets_sync_queue                                                     |
|    - Lifecycle States: pending -> in_progress -> synced / failed -> dead_letter   |
|    - Concurrency Control: Leased row locks (60s lease) prevent duplicate worker   |
|      processing across concurrent processes.                                      |
|    - Exponential Backoff: Jittered retry delays (15s to 900s cap).                |
|    - Dead-Letter Handling: Transitions after 5 consecutive failed attempts.       |
|    - Orphan Recovery: Automatically resets expired in_progress locks to pending.  |
+-----------------------------------------------------------------------------------+
                                          |
                          Claim batch of up to 20 records
                                          v
+-----------------------------------------------------------------------------------+
| 3. SYNCHRONIZATION WORKER (app/services/sheets_worker.py)                         |
|    - Executable via CLI: flask run-sheets-worker --once (or daemon loop).         |
|    - Builds Google Sheets API v4 Resource using Service Account credentials.      |
|    - Step A: ensure_sheet_headers() verifies Row 1 headers exist.                 |
|    - Step B: get_existing_attendance_ids() reads Column A (idempotency check).    |
|    - Step C: Filters out any Attendance IDs already present in Google Sheets.     |
|    - Step D: format_attendance_batch() applies CWE-1236 Formula Injection         |
|      sanitization (prepends ' to leading =, +, -, @, \t, \r).                     |
|    - Step E: append_attendance_batch() appends new rows via valueInputOption=RAW. |
|    - Step F: Marks records as 'synced' in SQLite with synced_at timestamp.       |
|    - Error Handling: Translates HTTP 429, 5xx, and timeouts to retryable errors;  |
|      maps 401, 403, 404 to fatal client errors.                                   |
+-----------------------------------------------------------------------------------+
                                          |
                              Appends formatted rows
                                          v
+-----------------------------------------------------------------------------------+
| 4. GOOGLE SPREADSHEET (Secondary Reporting Destination)                           |
|    - Spreadsheet ID: 1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY                 |
|    - Worksheet: Attendance_Log                                                    |
|    - Columns: 9 Verified Attendance Columns                                       |
+-----------------------------------------------------------------------------------+
```

---

## 3. Credential Security & Environment Verification

### 3.1 Credential File Verification
- **Target Path:** `C:\Users\niraj\.config\smart-attendance\google-service-account.json`
- **Location Evaluation:** **COMPLIANT**. The file is stored in the operating system user profile directory, completely outside `C:\Projects\Smart-Attendance-System`.
- **Security Check:** Verified file existence via filesystem APIs without printing, echoing, or logging the file's private key or raw JSON contents.
- **Git Protection:**
  - The repository's `.gitignore` explicitly excludes `credentials/`, `credentials/*.json`, `service_account.json`, `service_account*.json`, and `*service_account*.json`.
  - No credential files or service account tokens are tracked in Git.

### 3.2 Environment Variable Naming Discrepancy
An architectural divergence was identified during inspection:

| Environment Variable | Where Referenced | Current Role / Behavior |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `app/config.py` line 106 | The only variable currently parsed by the application for credentials. Defaults to `credentials/service_account.json`. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Standard Google Cloud SDK | The environment variable specified in today's user request. Currently **not parsed** by `app/config.py`. |

> [!IMPORTANT]
> **Proposed Configuration Resolution:**
> To adhere strictly to Google Cloud standards and user requirements without breaking backward compatibility, `app/config.py` should be updated to check `GOOGLE_APPLICATION_CREDENTIALS` as a fallback when `GOOGLE_SERVICE_ACCOUNT_FILE` is not set:
> ```python
> GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
>     "GOOGLE_SERVICE_ACCOUNT_FILE",
>     os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", str(CREDENTIALS_DIR / "service_account.json"))
> )
> ```

---

## 4. Live Spreadsheet Read-Only Access & Header Validation

We executed a dedicated, non-destructive read-only verification script using the project's virtual environment. The script authenticated with the external service account and retrieved metadata and header values directly from the Google Sheets API v4.

### 4.1 Connection & Metadata Results
- **Authentication:** **SUCCESSFUL**
- **Client Email:** `smart-attendance-backend@smart-attendance-system-515507.iam.gserviceaccount.com`
- **Project ID:** `smart-attendance-system-515507`
- **Spreadsheet ID:** `1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY`
- **Spreadsheet Title:** `Smart Attendance Management System`
- **Available Tabs:** `['Attendance_Log']`
- **Target Worksheet:** Located `Attendance_Log` successfully.

### 4.2 Header Row Inspection (Row 1 of `Attendance_Log`)

The live sheet contains **9 columns**. The header strings were retrieved and compared against the specification:

| Column # | Expected Header Name | Actual Header in Google Sheets | Case-Insensitive Match | Status |
|:---:|---|---|:---:|:---:|
| **1** | `Attendance ID` | `Attendance ID` | Exact Match | **PASS** |
| **2** | `Date` | `Date` | Exact Match | **PASS** |
| **3** | `Time` | `Time` | Exact Match | **PASS** |
| **4** | `Roll Number` | `Roll Number` | Exact Match | **PASS** |
| **5** | `Student Name` | `Student Name` | Exact Match | **PASS** |
| **6** | `Course/Session` | `Course/Session` | Exact Match | **PASS** |
| **7** | `RFID UID` | `RFID UID` | Exact Match | **PASS** |
| **8** | `Status` | `Status` | Exact Match | **PASS** |
| **9** | `Source/Device` | `Source/Device` | Exact Match | **PASS** |

**Header Verification Outcome:** **100% MATCH** across all 9 columns in exact order.

---

## 5. Architectural Alignment & Code Observations

While the live Google Cloud environment and spreadsheet are properly provisioned, the existing codebase contains legacy assumptions that must be reconciled before enabling live synchronization:

### Observation A: 10-Column vs. 9-Column Layout
- **Existing Code (`app/services/google_sheets.py` lines 35-46 & 536-601):**
  Defines `DEFAULT_SPREADSHEET_HEADERS` with **10 columns**, including a 10th column `"Sync Timestamp"` and spaces in `"Course / Session"`, `"Attendance Status"`, and `"Source / Device"`.
  `format_attendance_row()` emits a 10-element list.
  `ensure_sheet_headers()` queries `A1:J1`.
  `append_attendance_batch()` appends to range `A:J`.
- **Actual Live Spreadsheet (`Attendance_Log`):**
  Contains **9 columns**, omitting `"Sync Timestamp"`, and using exact column headers `Course/Session`, `Status`, and `Source/Device`.
- **Impact if run without adjustment:**
  `ensure_sheet_headers()` would detect a mismatch between the 9 live headers and the 10 expected headers, report a `CONFLICT`, and safely abort synchronization to prevent data corruption.
- **Recommended Adjustment:**
  Update `DEFAULT_SPREADSHEET_HEADERS` and `format_attendance_row()` to match the verified 9-column format (`A1:I1` and `A:I`).

### Observation B: Default Worksheet Name
- **Existing Code (`app/config.py` line 111):**
  Defaults to `GOOGLE_SHEETS_SHEET_NAME = "Attendance"`.
- **Actual Live Spreadsheet:**
  The worksheet is named `"Attendance_Log"`.
- **Impact:**
  Without `GOOGLE_SHEETS_SHEET_NAME="Attendance_Log"` set in the environment, the worker attempts to access sheet `"Attendance"`, which does not exist in the spreadsheet.
- **Recommended Adjustment:**
  Configure `GOOGLE_SHEETS_SHEET_NAME="Attendance_Log"` in the environment, and adjust the fallback default in `Config` to `"Attendance_Log"`.

### Observation C: Missing `.env` and `.env.example`
- No `.env` or `.env.example` currently exists in the root directory.
- A clean, sanitized `.env.example` should be provided to document all configuration variables without exposing secrets.

---

## 6. Automated Test Suite Results

We executed the complete existing Google Sheets automated test suite:

```bash
pytest tests/test_google_sheets.py tests/test_sheets_outbox.py tests/test_sheets_worker.py -v
```

### Results Summary:
- **`tests/test_google_sheets.py`:** **22 passed | 0 failed**
  - Validated credential parsing, mock authentication, HTTP error translation (401, 403, 404, 429, 503), network timeouts, header provisioning, formula injection protection (CWE-1236), numeric ID preservation, and decoupling from `/api/scans`.
- **`tests/test_sheets_outbox.py`:** **21 passed | 0 failed**
  - Validated queue schema migrations, atomic outbox record creation during scan, rollback integrity, duplicate scan suppression, leased lock concurrency controls, exponential backoff with jitter (capped at 900s), dead-letter transitions after 5 attempts, and cascade deletions.
- **`tests/test_sheets_worker.py`:** **24 passed | 0 failed**
  - Validated worker batch claiming, idempotent duplicate prevention (excluding IDs already present in Google Sheets Column A), partial batch failure recovery, retry scheduling, transaction isolation, secret masking in logs, and CLI execution.

**Total Test Result:** **67 passed, 0 failed in 12.40s**.

---

## 7. Safety, Git Status & Database Integrity Confirmation

- **Production Source Code Safety:** **0 files modified** during this audit. (Stage 6 corrections in `api_client.cpp` and `api_client.h` remain intact).
- **Production Database Safety:** `database/attendance.db` row count remains strictly **2**; `PRAGMA integrity_check;` returned `ok`.
- **Spreadsheet Data Safety:** **0 rows appended**, 0 sample records created.
- **Credential Safety:** Zero keys, passwords, or secrets printed or committed.
- **Git Repository Status:**
  ```text
  git status --short
   M firmware/esp32_attendance_device/api_client.cpp
   M firmware/esp32_attendance_device/api_client.h
  ?? GOOGLE_SHEETS_INTEGRATION_CONFIGURATION_AUDIT.md
  ?? STAGE_6_API_CLIENT_CORRECTION_REPORT.md
  ?? STAGE_7_FIRMWARE_INTEGRATION_REVIEW.md
  ?? STAGE_8_3_FIRMWARE_UNIT_TEST_REPORT.md
  ?? STAGE_8_4_END_TO_END_SIMULATION_REPORT.md
  ?? STAGE_8_5_PRE_HARDWARE_READINESS_REPORT.md
  ?? tests/firmware_sim/
  ```
- **Git Restrictions:** Zero commits, pushes, or branch modifications performed.

---

## 8. Recommended Next Steps (Requires Approval)

Before proceeding to live attendance synchronization, the following targeted adjustments are recommended:

1. **Update `app/config.py`:**
   - Add `GOOGLE_APPLICATION_CREDENTIALS` fallback for `GOOGLE_SERVICE_ACCOUNT_FILE`.
   - Update default `GOOGLE_SHEETS_SHEET_NAME` to `"Attendance_Log"`.
2. **Align Column Mapping in `app/services/google_sheets.py`:**
   - Adjust `DEFAULT_SPREADSHEET_HEADERS` to the verified 9-column layout:
     `["Attendance ID", "Date", "Time", "Roll Number", "Student Name", "Course/Session", "RFID UID", "Status", "Source/Device"]`.
   - Update `format_attendance_row()` to return 9 elements (omitting `Sync Timestamp`).
   - Update range references from `A:J` to `A:I`.
3. **Update Automated Tests:**
   - Update `tests/test_google_sheets.py` and `tests/test_sheets_worker.py` to assert the 9-column layout.
4. **Create `.env.example`:**
   - Document `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_SHEETS_SPREADSHEET_ID`, and `GOOGLE_SHEETS_SHEET_NAME` with placeholder values.
5. **Single-Record Verification:**
   - Perform an authorized, single-batch test sync using `flask run-sheets-worker --once` to confirm live append functionality.

*In strict accordance with audit instructions, zero production code changes have been applied. We await your review and explicit approval.*
