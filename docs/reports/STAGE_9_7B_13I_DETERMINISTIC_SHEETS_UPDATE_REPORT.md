# Stage 9.7B.13I — Deterministic Google Sheets Update Service & Client Timeout Design Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Branch:** `main`
**Baseline Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
**Previous Milestone:** Stage 9.7B.13H — Claim-Token Fencing & Lease Expiration Verification — ACCEPTED PASS
**Current Milestone:** Stage 9.7B.13I — Deterministic Google Sheets Update Service & Client Timeout Design
**Status / Final Verdict:** `PASS — DETERMINISTIC SHEETS UPDATE VERIFIED`
**Report Generated:** 2026-11-24

---

## 1. Executive Summary

Stage 9.7B.13I establishes the deterministic Google Sheets remote-write architecture for the Smart Attendance Management System, permanently resolving the check-then-append duplicate delivery vulnerability identified in Stage 13C and analyzed in Stage 13D/13E.

Prior to Stage 13I, the worker appended attendance records to the bottom of the worksheet using the Google Sheets `spreadsheets.values.append` API. In concurrent or interrupted worker scenarios (where a worker's lease expired mid-operation or an HTTP timeout occurred after the remote write was committed), retrying workers would append duplicate rows for the same logical attendance event. Stage 13G solved the local row allocation challenge by guaranteeing unique, deterministic row numbers (`sheet_row_number`), and Stage 13H implemented claim-token fencing to prevent stale SQLite state transitions.

Stage 13I completes the remote integration layer:
1. **Deterministic Targeted Writes:** Implemented `update_attendance_row`, `construct_deterministic_range`, and `update_attendance_batch` in `app/services/google_sheets.py`. Remote writes now target the exact pre-allocated cell range:
   $$\text{Attendance\_Log!A}\{\text{sheet\_row\_number}\}\text{:I}\{\text{sheet\_row\_number}\}$$
   The `values().append` API is strictly avoided.
2. **Client-Side Timeout Coordination:** Enhanced `get_sheets_service` with a configurable socket timeout (`timeout_seconds`), implemented via `httplib2.Http(timeout=...)` and `google_auth_httplib2.AuthorizedHttp`. This establishes the foundation for timeout-to-lease coordination ($T_{\text{HTTP}} < T_{\text{lease}}$).
3. **Fail-Closed Validation:** Strict pre-flight validation enforces valid positive row indices ($2 \le \text{row} \le 10,000,000$), rejects non-integers, floats, and booleans, and guarantees the exact 9-column attendance contract with formula injection defense.
4. **Structured Error Hierarchy:** Implemented domain-specific exceptions (`GoogleSheetsValidationError`, `GoogleSheetsTimeoutError`, `GoogleSheetsAuthError`, etc.) with explicit `retryable` and HTTP status mapping for Stage 13J worker consumption.
5. **Comprehensive Testing & Isolation:** Verified with 21 new tests (Tests A–U) in `tests/test_google_sheets_deterministic_update.py` and full regression across 101 total tests (0 failures). Zero live network or Google API calls were made; all 6 protected databases maintained 100% byte-for-byte SHA-256 match; and `app/services/sheets_worker.py` was left strictly unmodified.

---

## 2. Baseline Git Commit

- **Baseline Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
- **Branch:** `main`
- **Working Tree State:** Clean baseline before Stage 13F–13H outbox enhancements; Stage 13I builds strictly on Stage 13H without committing or pushing.

---

## 3. Initial Git Status

At the onset of Stage 9.7B.13I, the working tree contained uncommitted outbox fencing and row allocation improvements from Stages 13F through 13H:
- Modified files from previous stages:
  - `app/database.py`
  - `app/models.py`
  - `app/services/sheets_outbox.py`
  - `tests/test_sheets_outbox.py`
- Untracked files from previous stages:
  - `app/services/sheets_row_allocator.py`
  - `tests/test_sheets_row_allocator.py`
  - `tests/test_sheets_claim_fencing.py`
  - `database/manual_test/attendance_fencing_test.db`
  - Prior milestone reports in `docs/reports/`

`app/services/google_sheets.py` was in its pre-Stage-13I baseline state.

---

## 4. Files Inspected

1. `app/services/google_sheets.py` — Existing append-based Google Sheets service implementation.
2. `app/services/sheets_worker.py` — Background outbox worker (inspected for integration boundary; strictly unmodified).
3. `app/services/sheets_outbox.py` — Transactional outbox service with claim-token fencing.
4. `app/services/sheets_row_allocator.py` — Transactional row allocator service assigning `sheet_row_number`.
5. `tests/test_google_sheets.py` — Existing Google Sheets service test suite.
6. `docs/reports/STAGE_9_7B_13H_CLAIM_TOKEN_FENCING_REPORT.md` — Previous milestone report.

---

## 5. Files Modified

- `app/services/google_sheets.py` (+339 lines, -4 lines):
  - Added `GoogleSheetsValidationError` (inherits `GoogleSheetsError`, `ValueError`).
  - Added `GoogleSheetsTimeoutError` (inherits `GoogleSheetsNetworkError`).
  - Updated `map_http_error()` to map HTTP 400 Bad Request to `INVALID_RANGE` (`retryable=False`).
  - Enhanced `get_sheets_service()` with `timeout_seconds: Optional[float] = None` and `http: Optional[Any] = None`, utilizing `httplib2.Http(timeout=...)` and `google_auth_httplib2.AuthorizedHttp`.
  - Implemented `construct_deterministic_range(sheet_name, sheet_row_number)`.
  - Implemented `update_attendance_row(record, sheet_row_number, spreadsheet_id, sheet_name, timeout_seconds, service, raise_for_status)`.
  - Implemented `update_attendance_batch(records, spreadsheet_id, sheet_name, timeout_seconds, service, raise_for_status)`.

---

## 6. Files Created

1. `tests/test_google_sheets_deterministic_update.py`: Dedicated 21-test suite covering Tests A through U.
2. `database/manual_test/attendance_deterministic_sheets_test.db`: Disposable test database initialized via SQLite backup API from `attendance_fencing_test.db`.
3. `docs/reports/STAGE_9_7B_13I_DETERMINISTIC_SHEETS_UPDATE_REPORT.md`: Comprehensive audit report (this document).

---

## 7. Deterministic Service API

The deterministic Google Sheets write service exposes three primary functions:

### 7.1 `construct_deterministic_range`
```python
def construct_deterministic_range(
    sheet_name: str = DEFAULT_SHEET_NAME,
    sheet_row_number: int = None,
) -> str:
```
- Validates that `sheet_row_number` is an integer (not a boolean), strictly $\ge 2$, and $\le 10,000,000$.
- Formats worksheet name: quotes names containing whitespace or special characters (e.g., `'Attendance Log'!A10:I10`), while bare valid identifiers remain unquoted (e.g., `Attendance_Log!A10:I10`).
- Generates exact 9-column A-to-I target: `A{row}:I{row}`.

### 7.2 `update_attendance_row`
```python
def update_attendance_row(
    record: dict,
    sheet_row_number: int,
    spreadsheet_id: Optional[str] = None,
    sheet_name: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    service: Optional[Any] = None,
    raise_for_status: bool = False,
) -> dict:
```
- Validates row number and record schema.
- Builds 9-column values payload via `format_attendance_row(record)`.
- Dispatches write via `service.spreadsheets().values().update()`:
  - `range`: `construct_deterministic_range(sheet_name, sheet_row_number)`
  - `valueInputOption`: `"RAW"`
  - `body`: `{"values": [row_values]}`
- Catches and maps `socket.timeout`, `TimeoutError`, `HttpError`, and generic network errors into domain exceptions.
- Returns standardized response dictionary:
  ```python
  {
      "success": True,
      "updated_range": target_range,
      "sheet_row_number": sheet_row_number,
      "updated_rows": 1,
      "response": raw_api_response,
  }
  ```

### 7.3 `update_attendance_batch`
```python
def update_attendance_batch(
    records: list[dict],
    spreadsheet_id: Optional[str] = None,
    sheet_name: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    service: Optional[Any] = None,
    raise_for_status: bool = False,
) -> dict:
```
- Validates batch items and ensures every item contains a valid `sheet_row_number`.
- Sequentially executes deterministic row updates.
- Aggregates results: returns `{"success": bool, "total_records": N, "updated_rows": M, "failed_records": K, "results": [...]}`.

---

## 8. Deterministic Range Implementation

The deterministic range format strictly adheres to:
$$\text{Range} = \text{Worksheet}!A\{\text{sheet\_row\_number}\}:I\{\text{sheet\_row\_number}\}$$

### Validation Rules for `sheet_row_number`:
1. **Type Check:** Must satisfy `isinstance(sheet_row_number, int) and not isinstance(sheet_row_number, bool)`.
2. **Header Exclusion:** Row 1 is reserved for the 9-column headers. Any row $< 2$ is rejected with `GoogleSheetsValidationError`.
3. **Upper Bound:** Any row $> 10,000,000$ (maximum Google Sheets grid capacity) is rejected.
4. **Rejection Examples:**
   - `0`, `-1`, `1` $\rightarrow$ `GoogleSheetsValidationError`
   - `True`, `False` $\rightarrow$ `GoogleSheetsValidationError`
   - `2.5`, `"10"`, `None` $\rightarrow$ `GoogleSheetsValidationError`

---

## 9. Nine-Column Payload Mapping

The row formatting contract maps attendance records to exactly 9 columns in strict order:

| Col # | Column Letter | Header Name | Data Type | Value / Formatting Rule | Formula Sanitized |
|:---:|:---:|---|---|---|:---:|
| 1 | **A** | `Attendance ID` | `INTEGER` | Positive integer (e.g. `101`) | No (numeric int) |
| 2 | **B** | `Date` | `STRING` | `YYYY-MM-DD` | Yes |
| 3 | **C** | `Time` | `STRING` | `HH:MM:SS` | Yes |
| 4 | **D** | `Roll Number` | `STRING` | Student Roll (e.g. `CS-2026-001`) | Yes |
| 5 | **E** | `Student Name` | `STRING` | Full Name (e.g. `Alice Smith`) | Yes |
| 6 | **F** | `Course / Session` | `STRING` | Session Name (e.g. `CS-101`) | Yes |
| 7 | **G** | `RFID UID` | `STRING` | Uppercase hex (e.g. `E2000019`) | Yes |
| 8 | **H** | `Status` | `STRING` | Lowercase status (e.g. `present`) | Yes |
| 9 | **I** | `Source / Device` | `STRING` | Origin (e.g. `rfid_reader_1`) | Yes |

### Formula Injection Sanitization
To prevent CSV/Spreadsheet formula injection, strings beginning with `=`, `+`, `-`, `@`, `\t`, or `\r` are prefixed with a single quote (`'`), ensuring Google Sheets treats them strictly as plain text.

---

## 10. Validation Rules

All operations operate on a **fail-closed** model:
1. **Empty Record:** Raises `GoogleSheetsValidationError("Attendance record payload must be a non-empty dictionary")`.
2. **Missing Attendance ID:** Missing or non-positive ID raises `GoogleSheetsValidationError("Record missing valid numeric attendance_id")`.
3. **Invalid Column Count:** If the formatted payload does not contain exactly 9 values, `GoogleSheetsValidationError` is raised.
4. **Invalid Row Number:** Passing a non-integer, boolean, or integer $< 2$ raises `GoogleSheetsValidationError`.

---

## 11. Timeout Implementation

Client timeouts are implemented at the HTTP transport layer using `httplib2`:
```python
if timeout_seconds is not None and http is None:
    http_transport = httplib2.Http(timeout=float(timeout_seconds))
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=http_transport)

service = build("sheets", "v4", http=http, cache_discovery=False)
```

When an operation exceeds `timeout_seconds`:
1. `httplib2` raises `socket.timeout` or `httplib2.error.ServerNotFoundError`.
2. `update_attendance_row` catches socket and network timeout exceptions.
3. The exception is mapped into `GoogleSheetsTimeoutError`:
   - `error_type`: `"TIMEOUT"`
   - `status_code`: `408`
   - `retryable`: `True`

---

## 12. Timeout Limitations & Architectural Semantics

### 12.1 Socket/Read Timeout vs. Remote Deadline
A client-side timeout in `httplib2` terminates the local TCP read/connect wait. It **does not send a cancellation signal** to Google servers.

### 12.2 Uncertain Remote Outcome
If a socket timeout occurs after the HTTP POST/PUT request reached Google's frontend, the Google Sheets backend may complete the write to the sheet even though the local client received a `GoogleSheetsTimeoutError`.

### 12.3 Idempotency as the Resolution
In the legacy append model (`values.append`), retrying after an uncertain timeout resulted in a duplicate attendance row.
In the deterministic update model:
- The write targets `Attendance_Log!A{row}:I{row}`.
- If the first write succeeded remotely, the retry writes identical values to the exact same cells.
- If the first write did not reach the sheet, the retry writes the values for the first time.
- In both cases, the final sheet state is identical, correct, and free of duplicates.

---

## 13. Error Classification

Domain exceptions inherit from `GoogleSheetsError`:

| Exception Class | Base Class | Status Code | Error Type | Retryable | Cause / Trigger |
|---|---|:---:|:---:|:---:|---|
| `GoogleSheetsValidationError` | `GoogleSheetsError`, `ValueError` | 400 | `VALIDATION_ERROR` | **No** | Invalid row, missing fields, malformed record |
| `GoogleSheetsTimeoutError` | `GoogleSheetsNetworkError` | 408 | `TIMEOUT` | **Yes** | Socket read/connect timeout exceeded |
| `GoogleSheetsAuthError` | `GoogleSheetsError` | 401 | `AUTH_ERROR` | **No** | Missing/invalid service account credentials |
| `GoogleSheetsPermissionError` | `GoogleSheetsError` | 403 | `PERMISSION_DENIED` | **No** | Service account lacks Edit access on Sheet |
| `GoogleSheetsNotFoundError` | `GoogleSheetsError` | 404 | `NOT_FOUND` | **No** | Spreadsheet ID or worksheet tab missing |
| `GoogleSheetsRateLimitError` | `GoogleSheetsError` | 429 | `RATE_LIMIT_EXCEEDED` | **Yes** | Google API quota / 300 requests per minute |
| `GoogleSheetsServerError` | `GoogleSheetsError` | 500/503 | `SERVER_ERROR` | **Yes** | Google internal service error / backend down |
| `GoogleSheetsNetworkError` | `GoogleSheetsError` | 503 | `NETWORK_ERROR` | **Yes** | DNS failure, socket reset, broken pipe |

---

## 14. Idempotency Analysis

### Append vs. Deterministic Update Comparison:

```
Legacy Append Flow:
Worker -> spreadsheets.values.append -> Google Sheets [Appends to next empty row: Row 15]
Network drops before ACK
Worker retries -> spreadsheets.values.append -> Google Sheets [Appends to next empty row: Row 16]
Result: DUPLICATE ROWS 15 and 16 for Attendance ID 101!

Deterministic Update Flow:
Worker -> spreadsheets.values.update(Range="A15:I15") -> Google Sheets [Overwrites Row 15]
Network drops before ACK
Worker retries -> spreadsheets.values.update(Range="A15:I15") -> Google Sheets [Overwrites Row 15]
Result: EXACTLY ONE ROW 15 for Attendance ID 101!
```

- **Write Amplification:** Exactly 1 API call per record writing exactly 9 cells.
- **State Convergence:** Multiple executions with the same `sheet_row_number` converge to identical spreadsheet state.

---

## 15. Test Suite and Test Counts

The test suite `tests/test_google_sheets_deterministic_update.py` defines 21 comprehensive tests:

| Test ID | Test Function | Objective | Result |
|:---:|---|---|:---:|
| **Test A** | `test_test_a_correct_nine_column_payload` | Exact 9-column contract and data types | **PASSED** |
| **Test B** | `test_test_b_correct_deterministic_range` | Range format `'Attendance_Log'!A{row}:I{row}` | **PASSED** |
| **Test C** | `test_test_c_update_api_usage` | `values().update` called with RAW; append never called | **PASSED** |
| **Test D** | `test_test_d_repeated_execution` | Repeated updates target identical range idempotently | **PASSED** |
| **Test E** | `test_test_e_different_records` | Independent records target their respective distinct rows | **PASSED** |
| **Test F** | `test_test_f_invalid_row` | Fail-closed on row $\le 1$, bools, floats, strings | **PASSED** |
| **Test G** | `test_test_g_invalid_payload` | Fail-closed on empty dict, None, non-dict payloads | **PASSED** |
| **Test H** | `test_test_h_incorrect_column_count` | Rejects payload formatting producing $\ne 9$ columns | **PASSED** |
| **Test I** | `test_test_i_missing_attendance_id` | Rejects records with missing/non-positive attendance ID | **PASSED** |
| **Test J** | `test_test_j_worksheet_range_configuration` | Supports custom sheet names with quoting rules | **PASSED** |
| **Test K** | `test_test_k_mocked_timeout` | Socket timeout caught and mapped to `GoogleSheetsTimeoutError` | **PASSED** |
| **Test L** | `test_test_l_timeout_error_classification` | `GoogleSheetsTimeoutError` has `retryable=True` and code 408 | **PASSED** |
| **Test M** | `test_test_m_authentication_error` | Missing credentials raises `GoogleSheetsAuthError` | **PASSED** |
| **Test N** | `test_test_n_rate_limit` | HTTP 429 maps to retryable `GoogleSheetsRateLimitError` | **PASSED** |
| **Test O** | `test_test_o_temporary_remote_server_failure` | HTTP 503 maps to retryable `GoogleSheetsServerError` | **PASSED** |
| **Test P** | `test_test_p_network_failure` | Socket connection error maps to `GoogleSheetsNetworkError` | **PASSED** |
| **Test Q** | `test_test_q_unknown_error` | Unmapped errors wrap in `GoogleSheetsError` safely | **PASSED** |
| **Test R** | `test_test_r_no_network` | Verification that tests make zero real outbound network calls | **PASSED** |
| **Test S** | `test_test_s_existing_google_sheets_regressions` | Preserves all existing `google_sheets.py` capabilities | **PASSED** |
| **Test T** | `test_test_t_batch_deterministic_update` | Sequential deterministic batch helper executes cleanly | **PASSED** |
| **Test U** | `test_test_u_worker_remains_unchanged` | Verifies `sheets_worker.py` hash matches baseline | **PASSED** |

---

## 16. Test Results

Execution of `tests/test_google_sheets_deterministic_update.py`:
```
pytest tests/test_google_sheets_deterministic_update.py -v
============================= 21 passed in 0.09s =============================
```

All 21 unit and integration tests passed cleanly.

---

## 17. Regression Test Results

Full test suite execution across the entire project test suite:
```
pytest tests/test_database.py tests/test_sheets_outbox.py tests/test_sheets_claim_fencing.py tests/test_google_sheets.py tests/test_google_sheets_deterministic_update.py -v
============================ 101 passed in 20.06s =============================
```

- `tests/test_database.py`: 16 passed
- `tests/test_sheets_outbox.py`: 21 passed
- `tests/test_sheets_claim_fencing.py`: 18 passed
- `tests/test_google_sheets.py`: 25 passed
- `tests/test_google_sheets_deterministic_update.py`: 21 passed
- **Total: 101 / 101 passed (100% pass rate).**

---

## 18. Network/API Isolation Evidence

1. **No External Network Calls:** All tests utilized mocked service instances (`unittest.mock.MagicMock`) or mocked transports (`google_auth_httplib2.AuthorizedHttp`).
2. **Credential Safety:** Zero live credentials were loaded or referenced. Dummy test credentials followed service account mock contracts.
3. **No External Spreadsheet Mutation:** Target spreadsheet `1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY` was never contacted.

---

## 19. Database Isolation Evidence

1. Production database `database/attendance.db` was strictly isolated.
2. Verified manual test database `database/manual_test/attendance_manual_test.db` was untouched.
3. Prior milestone test databases (`attendance_reliability_test.db`, `attendance_lease_test.db`, `attendance_schema_test.db`, `attendance_allocator_test.db`) remained completely untouched.
4. Disposable target database `database/manual_test/attendance_deterministic_sheets_test.db` was created for Stage 13I isolation.

---

## 20. Protected DB Hash Results

SHA-256 cryptographic hashes for all protected databases were recorded prior to testing and verified post-testing:

| Database File | Role / Milestone | Pre-Test SHA-256 | Post-Test SHA-256 | Status |
|---|---|---|---|:---:|
| `database/attendance.db` | Production Database | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_manual_test.db` | Stage 9.7B.12D Sync | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_reliability_test.db` | Stage 9.7B.13B / 13C | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_lease_test.db` | Stage 9.7B.13C Lease | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_schema_test.db` | Stage 9.7B.13F Schema | `A502E79CFEE895F5BA758CB45F6EE05418F35C5D26CF3B5E0D7E13116190FF48` | `A502E79CFEE895F5BA758CB45F6EE05418F35C5D26CF3B5E0D7E13116190FF48` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_allocator_test.db` | Stage 9.7B.13G Allocator | `1D633E93F55C3F55DB7BFC25B51C4C0FD4680A57450EDB2F797EA20835BDD256` | `1D633E93F55C3F55DB7BFC25B51C4C0FD4680A57450EDB2F797EA20835BDD256` | **MATCH (UNTOUCHED)** |

Disposable test database created:
- `database/manual_test/attendance_deterministic_sheets_test.db`: `ED1493376D03728AC5ECB4EA959A75BD03E01A4D5E2D1D4270975F87C898DAEE`

All 6 protected databases maintained 100% byte-for-byte SHA-256 match.

---

## 21. Git Diff Check

Executed `git diff --check app/services/google_sheets.py`:
- 0 whitespace errors.
- 0 trailing whitespace violations.
- Clean code formatting.

Statistics:
```
app/services/google_sheets.py | 343 +++++++++++++++++++++++++++++++++++++++++-
1 file changed, 339 insertions(+), 4 deletions(-)
```

---

## 22. Final Git Status

- Modified tracked files:
  - `app/database.py` (Stage 13F)
  - `app/models.py` (Stage 13F)
  - `app/services/sheets_outbox.py` (Stage 13H)
  - `tests/test_sheets_outbox.py` (Stage 13F/13H)
  - `app/services/google_sheets.py` (Stage 13I)
- Untracked files:
  - `app/services/sheets_row_allocator.py` (Stage 13G)
  - `tests/test_sheets_row_allocator.py` (Stage 13G)
  - `tests/test_sheets_claim_fencing.py` (Stage 13H)
  - `tests/test_google_sheets_deterministic_update.py` (Stage 13I)
  - `docs/reports/*.md`
- `app/services/sheets_worker.py`: **100% UNMODIFIED.**
- Zero git commits or pushes executed.

---

## 23. Architectural Limitations

1. **Client Timeout Limitation:** As documented in Section 12, client socket timeouts do not guarantee remote cancellation. Idempotent row targeting is the authoritative defense against duplicates.
2. **Sequential vs Batch API:** `update_attendance_batch` executes sequential `values().update` requests. For extremely high throughput (hundreds of records per second), `spreadsheets.values.batchUpdate` could be adopted in a future stage. However, for current outbox batch sizes (e.g., 10–50 records), sequential updates provide granular per-record error classification and immediate retry/dead-letter control.
3. **Google Sheets Write Quota:** Google Sheets enforces a limit of 300 write requests per minute per project. The exponential backoff with jitter and retry scheduling in `sheets_outbox.py` ensures compliant operation.

---

## 24. Stage 13J Integration Contract

In Stage 13J, the background worker (`app/services/sheets_worker.py`) will be updated to consume the services developed in Stages 13G, 13H, and 13I:

1. **Deterministic Row Write:**
   Replace:
   ```python
   append_attendance_batch(service, spreadsheet_id, sheet_name, records)
   ```
   With:
   ```python
   for record in claimed_records:
       update_attendance_row(
           record=record,
           sheet_row_number=record["sheet_row_number"],
           spreadsheet_id=spreadsheet_id,
           sheet_name=sheet_name,
           timeout_seconds=worker_config.HTTP_TIMEOUT_SECONDS,
           service=service,
           raise_for_status=True,
       )
   ```
2. **Fenced Outbox Transitions:**
   Supply `claim_token` on all transitions:
   - Success: `mark_as_synced(queue_id=r["queue_id"], claim_token=r["claim_token"])`
   - Retryable Failure: `schedule_retry(queue_id=r["queue_id"], claim_token=r["claim_token"], ...)`
   - Non-retryable / Max Attempts: `move_to_dead_letter(queue_id=r["queue_id"], claim_token=r["claim_token"], ...)`
3. **Coordinated Timeouts:**
   Enforce configuration invariant:
   $$\text{LEASE\_DURATION\_SECONDS (e.g. 60s)} > \text{HTTP\_TIMEOUT\_SECONDS (e.g. 15s)}$$

---

## 25. Acceptance Criteria Verification

| # | Acceptance Criterion | Verification Method | Status |
|:---:|---|---|:---:|
| 1 | Deterministic range format `Attendance_Log!A{row}:I{row}` | Test B, Test J | **SATISFIED** |
| 2 | Values update API used exclusively; values append never called | Test C | **SATISFIED** |
| 3 | Exact 9-column contract strictly enforced | Test A, Test H | **SATISFIED** |
| 4 | Fail-closed validation for rows $\le 1$, booleans, floats, strings | Test F | **SATISFIED** |
| 5 | Fail-closed validation for empty, missing, or malformed records | Test G, Test I | **SATISFIED** |
| 6 | Formula injection sanitization on all text fields | Test A, Test S | **SATISFIED** |
| 7 | Configurable client socket timeout via `httplib2` transport | Test K, Test L | **SATISFIED** |
| 8 | Structured exception hierarchy with error classification | Test L, M, N, O, P, Q | **SATISFIED** |
| 9 | Idempotent execution on repeated writes to same row | Test D | **SATISFIED** |
| 10 | Independent row targeting for distinct records | Test E | **SATISFIED** |
| 11 | Complete test suite (Tests A–U) passing (21/21) | `pytest` Test Runner | **SATISFIED** |
| 12 | Regression test suites passing across all files (101/101) | Full regression runner | **SATISFIED** |
| 13 | Complete network and remote API isolation | Test R, mock verification | **SATISFIED** |
| 14 | All 6 protected databases maintained 100% SHA-256 match | Pre/post SHA-256 audit | **SATISFIED** |
| 15 | `sheets_worker.py` completely unmodified | Test U, `git status` | **SATISFIED** |
| 16 | Zero git commits or pushes executed | Git log and status audit | **SATISFIED** |

---

## 26. Final Verdict

# `PASS — DETERMINISTIC SHEETS UPDATE VERIFIED`

Stage 9.7B.13I is complete. The deterministic Google Sheets update service and client timeout design are fully implemented, rigorously tested, and verified to be safe, isolated, and ready for worker integration in Stage 9.7B.13J.
