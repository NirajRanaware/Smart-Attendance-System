# STAGE 8.4 — END-TO-END ESP32 FIRMWARE & LIVE FLASK BACKEND SIMULATION REPORT

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Branch:** `main`  
**Milestone:** 2 — Software Verification and Simulation  
**Stage:** 8.4 — End-to-End Firmware & Live Backend Simulation (Final Audit & Report Corrections)  
**Status:** COMPLETE (Passed All Verification Gates)  

---

## 1. Executive Summary

In **Stage 8.4**, we designed, implemented, audited, and verified a complete software-in-the-loop End-to-End (E2E) simulation environment connecting the **actual production C++ ESP32 firmware logic** with the **actual live Flask attendance backend**.

Unlike traditional mock-only unit tests, this integration testing architecture executes:
1. The **actual compiled C++ production firmware modules** (`AttendanceApiClient::sendScan()`, `DisplayManager`, `BuzzerController`) running as a native Windows executable (`firmware_e2e_runner.exe`).
2. A **live Flask backend instance** running in a background thread on `127.0.0.1` on dynamically allocated ephemeral ports, strictly enforcing device authentication, rate-limiting, and CSRF protection.
3. A **real TCP socket transport layer** built directly into the host-side mock `HTTPClient`, transmitting raw HTTP/1.1 POST requests with production headers and JSON payloads over localhost loopback sockets to the Flask server.
4. An **isolated temporary SQLite database** per test session, verifying atomic attendance persistence, active session boundaries, and Google Sheets outbox queue (`sheets_sync_queue`) record creation.

### Key Verification Metrics
- **Stage 8.4 E2E Integration Test Suite:** **18 executed | 18 passed | 0 failed** (`pytest tests/firmware_sim/e2e -v`, 20.26s).
- **Stage 8.3 Native C++ Unit Test Suite:** **79 executed | 79 passed | 0 failed** (`tests/firmware_sim/native/firmware_test_runner.exe`, 2 ms).
- **Firmware Unit Pytest Wrapper:** **2 passed | 0 failed** (`test_firmware_unit.py`, 22.44s).
- **Backend Security & Integration Suite:** **42 passed | 0 failed** (`test_device_auth.py` and `test_attendance.py`, 14.53s).
- **Total Automated Test Count:** **141 tests executed across all test suites, 0 failures**.
- **Real ESP32 Hardware Compilation:** **PASSED** via `arduino-cli` with ESP32-DA core (Flash: 60% used [788,777 B / 1,310,720 B], Dynamic RAM: 13% used [45,208 B / 327,680 B]).
- **Production Database Safety:** **100% untouched** (`database/attendance.db` row count remained 2, `PRAGMA integrity_check;` returned `ok`).
- **Production Source Code Safety:** **0 lines modified** in `firmware/esp32_attendance_device/*` or `app/*` during Stage 8.4 (Stage 6 corrections preserved intact).

---

## 2. Architecture Diagram & Workflow

```
+-------------------------------------------------------------------------------+
|                             TEST RUNNER (pytest)                             |
|                                                                               |
|  1. Allocates ephemeral port on 127.0.0.1 (find_free_port)                    |
|  2. Creates temporary SQLite database in tmp_path (applies production schema) |
|  3. Starts live Flask app in background thread (enforcing auth & CSRF)        |
|  4. Polls /api/health until server is verified ready                          |
+-------------------------------------------------------------------------------+
                                      |
                     Spawns process with CLI arguments
                                      v
+-------------------------------------------------------------------------------+
|                       firmware_e2e_runner.exe (C++17)                         |
|                                                                               |
|  1. Configures test-controllable pointers (DEVICE_ID, DEVICE_API_KEY,         |
|     SERVER_HOST=127.0.0.1, SERVER_PORT=<port>)                                |
|  2. Enables live socket transport in mock HTTPClient                          |
|  3. Invokes production AttendanceApiClient::sendScan(cardUid)                 |
|     -> Constructs JSON payload {"card_uid": "...", "source": "..."}           |
|     -> Connects TCP socket to 127.0.0.1:<port>                                |
|     -> Sends POST /api/scans with X-Device-Id, X-API-Key, Accept, Content-Type|
|     -> Reads raw HTTP/1.1 response bytes                                      |
|     -> Deserializes JSON via ArduinoJson v7                                   |
|     -> Returns production ScanResponse enum                                   |
|  4. Dispatches outcome to production DisplayManager & BuzzerController        |
|  5. Inspects simulated OLED frame buffer & GPIO buzzer transitions            |
|  6. Emits structured JSON summary: E2E_JSON_OUTPUT:{...}                      |
+-------------------------------------------------------------------------------+
                                      |
                           Real TCP Socket Connection
                                      v
+-------------------------------------------------------------------------------+
|                           LIVE FLASK BACKEND SERVER                           |
|                                                                               |
|  1. @app.before_request validates X-Device-Id & X-API-Key (constant-time)     |
|  2. POST /api/scans route handler executes                                    |
|  3. Verifies student registration & active flag in SQLite                     |
|  4. Opens BEGIN IMMEDIATE SQLite transaction                                  |
|  5. Checks active class_sessions & auto-expires if past end_time              |
|  6. Prevents duplicate attendance for student in session                      |
|  7. Inserts record into attendance table                                      |
|  8. Atomically inserts outbox event into sheets_sync_queue                    |
|  9. Commits transaction and returns HTTP 201 JSON response                    |
+-------------------------------------------------------------------------------+
                                      |
                                Reads changes
                                      v
+-------------------------------------------------------------------------------+
|                            TEMPORARY SQLITE DB                                |
|  - Table: students                                                            |
|  - Table: class_sessions                                                      |
|  - Table: attendance                                                          |
|  - Table: sheets_sync_queue                                                   |
+-------------------------------------------------------------------------------+
```

---

## 3. Explicit Simulation Boundaries & Scope

To ensure complete engineering transparency, the simulation boundaries are strictly defined:

### 3.1 Executed Production Components
1. **`firmware/esp32_attendance_device/api_client.cpp`:**
   - Compiled directly into the native runner.
   - Executes real endpoint construction, header injection, JSON payload serialization via `ArduinoJson v7`, local credential validation guards, HTTP response status classification, and JSON deserialization logic.
2. **`firmware/esp32_attendance_device/display_manager.cpp`:**
   - Compiled directly into the native runner.
   - Executes actual screen formatting, string truncation boundaries, text coordinate calculation, and display routing.
3. **`firmware/esp32_attendance_device/buzzer.cpp`:**
   - Compiled directly into the native runner.
   - Executes real non-blocking tone pattern sequencing, millisecond timing checks, and GPIO pin level driving.
4. **Flask Application (`app/`):**
   - The real Flask application runs live in a background thread.
   - Executes real `app.before_request` device authentication (constant-time token comparison), CSRF exemptions, database transactions (`BEGIN IMMEDIATE`), constraint enforcement, and error payload generation.

### 3.2 Test-Only & Shim Components
1. **Loopback Socket Transport (`tests/firmware_sim/native/mocks/HTTPClient.h`):**
   - Replaces the ESP32 lwIP/WiFiClient network stack with native Windows Winsock2 (`ws2_32.lib`) TCP stream sockets.
   - Implements real HTTP/1.1 serialization and socket transmission over `127.0.0.1`.
2. **Simulated Peripheral Hardware:**
   - **OLED Display:** Rendered to an in-memory frame buffer (`Adafruit_SSD1306` mock) that captures text lines for exact substring verification.
   - **Buzzer:** Output is routed to mock GPIO state trackers recording digital level transitions and microsecond/millisecond timestamps.

### 3.3 Explicit Exclusions (Not Executed in Stage 8.4)
1. **`esp32_attendance_device.ino` Orchestration:**
   - The Arduino FreeRTOS main loop (`setup()` and `loop()`), multi-component state machine loop, and non-blocking scheduling are not executed inside this runner.
2. **Physical Hardware Transceivers:**
   - Physical MFRC522 SPI radio-frequency registers and physical 13.56 MHz RFID card induction are not tested.
   - Physical ESP32 Wi-Fi 802.11 b/g/n radio association and antenna performance are not tested.
   - Physical I2C pull-ups, bus capacitance, and power rails (3.3V / 5V) are not tested.

---

## 4. Files Created and Modified

All additions for Stage 8.4 are strictly isolated within `tests/firmware_sim/`:

| File Path | Action | Description |
|---|---|---|
| `tests/firmware_sim/native/mocks/config_override.h` | Modified | Added test-controllable `SERVER_HOST` (`g_mock_server_host`) and `SERVER_PORT` (`g_mock_server_port`) macro overrides. |
| `tests/firmware_sim/native/mocks/HTTPClient.h` | Modified | Implemented native Windows Winsock2 TCP transport (`performLiveHttpPost`) gated by `useLiveTransport`. Uses `#pragma push_macro("INPUT")` to isolate Arduino macro namespace from Windows `winuser.h`. |
| `tests/firmware_sim/native/test_main.cpp` | Modified | Defined default storage for `g_mock_server_host = "192.168.1.100"` and `g_mock_server_port = 5000`. |
| `tests/firmware_sim/native/build_tests.bat` | Modified | Added `-lws2_32` link flag to resolve Winsock symbols. |
| `tests/firmware_sim/native/e2e_main.cpp` | Created / Audited | Standalone C++17 E2E simulation harness exercising production `api_client.cpp`, `display_manager.cpp`, and `buzzer.cpp` with CLI arguments, socket transmission counters (`postCallCount`, `httpTransmitted`), and structured JSON output. |
| `tests/firmware_sim/native/build_e2e.bat` | Created | Compilation script building `firmware_e2e_runner.exe`. |
| `tests/firmware_sim/e2e/__init__.py` | Created | Python package marker for E2E tests. |
| `tests/firmware_sim/e2e/conftest.py` | Created | Pytest fixtures managing background Flask server instances, ephemeral ports, temporary databases, process execution, and database seeding helpers. |
| `tests/firmware_sim/e2e/test_e2e_attendance.py` | Created / Audited | E2E integration tests for Scenarios A (Success), B (Duplicate), C (Unknown Card), and D (Inactive Student). Verified complete 6-step duplicate attendance sequence. |
| `tests/firmware_sim/e2e/test_e2e_authentication.py` | Created / Audited | E2E integration tests for Scenario E: wrong key, empty key, empty device ID, short key (<32 chars), unknown device ID, and CSRF bypass. Verified local abort vs live HTTP transmission. |
| `tests/firmware_sim/e2e/test_e2e_sessions.py` | Created | E2E integration tests for Scenarios F (No Session), G (Expired Session), H (Server Refused), and Wi-Fi Offline Guard. |
| `tests/firmware_sim/e2e/test_e2e_sync_queue.py` | Created | E2E integration tests for Google Sheets synchronization queue persistence and deduplication. |
| `tests/firmware_sim/e2e/README.md` | Created | Complete documentation of E2E architecture, test runner usage, and scenario matrix. |

**Production Files Modified:** **0 files.**

---

## 5. Temporary Database Isolation Details

To ensure zero risk to the production database:
1. Every test scenario executes against a newly provisioned, isolated SQLite database file created inside pytest's isolated directory (`tmp_path / "e2e_temp_attendance.db"`).
2. The schema is initialized using `app.database.init_db()`, ensuring identical DDL, indexes, and constraints as production.
3. The background Flask server's `app.config["DATABASE_PATH"]` is pointed exclusively to this temporary file.
4. In `conftest.py`, the modification timestamp (`os.path.getmtime`) of `database/attendance.db` is recorded before server launch and asserted unchanged during test teardown.
5. In teardown, `PRAGMA integrity_check;` is executed on the temporary database to verify zero database corruption.

---

## 6. Local Server Startup and Cleanup Design

- **Address Binding:** Bound strictly to loopback interface `127.0.0.1`. Never exposed to external network interfaces.
- **Port Allocation:** Dynamic ephemeral port allocation via `find_free_port()`, binding a temporary socket to port `0` and retrieving `s.getsockname()[1]`. This eliminates port conflict issues and permits parallel execution.
- **Server Lifecycle:** Uses `werkzeug.serving.make_server` running in a daemon thread.
- **Readiness Verification:** Before invoking the native C++ client, the test runner polls `http://127.0.0.1:<port>/api/health` with a 5-second timeout, ensuring the Flask application context and routing table are ready.
- **Graceful Teardown:** Upon test completion, `server.shutdown()` is called and the background thread joined with a 3-second timeout, releasing the socket immediately.

---

## 7. HTTP Transport Implementation

In `tests/firmware_sim/native/mocks/HTTPClient.h`:
- Enabled via `g_mockHttp.useLiveTransport = true`.
- Parses the URL generated by `apiClient.getScanEndpointUrl()` (e.g. `http://127.0.0.1:<port>/api/scans`).
- Opens a Windows TCP stream socket (`AF_INET`, `SOCK_STREAM`, `IPPROTO_TCP`) using Winsock2 (`ws2_32.dll`).
- Applies `SO_RCVTIMEO` and `SO_SNDTIMEO` socket timeouts based on `HTTP_REQUEST_TIMEOUT` (default 5000ms).
- Formats and transmits a compliant HTTP/1.1 POST request containing:
  - `POST /api/scans HTTP/1.1`
  - `Host: 127.0.0.1:<port>`
  - `Connection: close`
  - `Content-Length: <len>`
  - All headers added by production firmware (`Content-Type`, `Accept`, `X-Device-Id`, `X-API-Key`).
  - Serialized JSON request body `{"card_uid": "...", "source": "..."}`.
- Reads raw HTTP/1.1 response stream, parses status code from status line (`HTTP/1.1 <code> <phrase>`), separates headers from payload at `\r\n\r\n`, and stores the body in `g_mockHttp.responseBody`.
- In `AttendanceApiClient::sendScan()`, the production logic consumes `http.getString()` and deserializes JSON using `ArduinoJson v7`.
- If the server is offline or connection refused, Winsock `connect()` returns `SOCKET_ERROR`, mapping to `HTTPC_ERROR_CONNECTION_REFUSED (-1)`. Production code cleanly maps this to `SCAN_OUTCOME_NETWORK_ERROR`.

---

## 8. Scenario A — Successful Attendance

- **Action:** Valid card UID `A1B2C3D4` scanned for registered student Rahul Sharma (Roll: 101) in active session "Embedded Systems".
- **Backend Execution:** Flask authenticates `X-Device-Id` and `X-API-Key`, enters `BEGIN IMMEDIATE;`, finds active session, checks duplicates, inserts attendance record, inserts outbox record, commits, and returns HTTP 201 Created.
- **Production C++ Parsing:** `apiClient.sendScan()` parses student and attendance objects.
- **Outcome:** `SCAN_OUTCOME_SUCCESS` (HTTP 201).
- **Extracted Fields:** Roll: `101`, Student: `Rahul Sharma`, Status: `Present`.
- **Database Verification:** Exactly 1 attendance row created in temporary SQLite (`status = 'Present'`, `source = 'ESP32_ATTENDANCE_01'`, UTC ISO 8601 timestamp).
- **Hardware Feedback:** Display rendered `"STUDENT IDENTIFIED"`, `"Roll: 101"`, `"Name: Rahul Sharma"`, `"Status: Present"`. Buzzer played `BUZZER_BEEP_SUCCESS` (240ms duration, 6 transitions).
- **Status:** **PASSED**.

---

## 9. Scenario B — Duplicate Attendance Verification

During the Stage 8.4 audit, `test_e2e_scenario_b_duplicate_attendance` was expanded to rigorously execute and verify the exact sequential transaction lifecycle:

1. **First Scan Execution:**
   - Card `B2C3D4E5` (Sneha Patil, Roll: 102) scanned in active session "Digital Signal Processing".
   - Client returns: `SCAN_OUTCOME_SUCCESS`, `httpCode = 201`, `studentName = "Sneha Patil"`, `attendanceStatus = "Present"`.
   - SQLite Verification:
     - `attendance` table row count = **1**.
     - `sheets_sync_queue` table row count = **1** (`sync_status = 'pending'`).
2. **Second Immediate Scan Execution (Duplicate Attempt):**
   - Identical card `B2C3D4E5` scanned again in the same active session.
   - Flask backend checks existing attendance in `attendance` table within `BEGIN IMMEDIATE;`, detects duplicate, rolls back transaction, and returns HTTP 409 Conflict with `{"success": false, "scan_result": "DUPLICATE", "attendance": {"roll_number": "102", "student_name": "Sneha Patil", "status": "ALREADY PRESENT"}}`.
   - Production C++ Client parses `scan_result == "DUPLICATE"` (Stage 6 correction) and extracts details.
   - Client returns: `SCAN_OUTCOME_DUPLICATE`, `httpCode = 409`, `rollNumber = "102"`, `studentName = "Sneha Patil"`, `attendanceStatus = "ALREADY PRESENT"`.
   - SQLite Verification:
     - `attendance` table row count remains strictly **1** (no duplicate record inserted).
     - `sheets_sync_queue` table row count remains strictly **1** (no duplicate outbox event staged).
3. **Hardware Feedback:**
   - Display rendered: `"DUPLICATE SCAN"`, `"Roll: 102"`, `"Name: Sneha Patil"`, `"ALREADY PRESENT"`.
   - Buzzer played: `BUZZER_BEEP_DUPLICATE` (380ms duration, 6 transitions).
- **Status:** **PASSED**.

---

## 10. Scenario C — Unknown RFID Card

- **Action:** Scan unregistered card UID `DEADBEEF`.
- **Backend Execution:** Query on `students` table returns `None`. Backend returns HTTP 404 Not Found with `scan_result: "UNKNOWN_CARD"`.
- **Production C++ Parsing:** `apiClient.sendScan()` maps HTTP 404 to `SCAN_OUTCOME_UNKNOWN_CARD`.
- **Outcome:** `SCAN_OUTCOME_UNKNOWN_CARD` (HTTP 404).
- **Database Verification:** 0 rows created in `attendance` table.
- **Hardware Feedback:** Display rendered `"UNKNOWN CARD"`, `"Card not registered!"`. Buzzer played `BUZZER_BEEP_ERROR` (280ms duration, 6 transitions).
- **Status:** **PASSED**.

---

## 11. Scenario D — Inactive Student

- **Action:** Scan registered card UID `C3D4E5F6` belonging to student Aarav Mehta (`active = 0`).
- **Backend Execution:** Backend identifies student but checks `student["active"] != 1`. Returns HTTP 403 Forbidden with `scan_result: "INACTIVE_STUDENT"`.
- **Production C++ Parsing:** Stage 6 correction identifies `scan_result == "INACTIVE_STUDENT"`.
- **Outcome:** `SCAN_OUTCOME_INACTIVE_STUDENT` (HTTP 403).
- **Database Verification:** 0 rows created in `attendance` table.
- **Hardware Feedback:** Display rendered `"ACCOUNT INACTIVE"`, `"Student account"`, `"is deactivated"`. Buzzer played `BUZZER_BEEP_ERROR` (280ms duration, 6 transitions).
- **Status:** **PASSED**.

---

## 12. Scenario E — Authentication Failure Audit & Distinction

The audit verified the exact distinction between **local client-side aborts** and **live backend HTTP 401 rejections**:

| Test ID | Scenario | Value Provided | Live HTTP Socket Transmitted? | `postCallCount` | Actual C++ Outcome | HTTP Code | Error Message Reported |
|---|---|---|---|---|---|---|---|
| **E1** | Invalid API Key | 32-char dummy key | **YES** (`httpTransmitted = True`) | 1 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | 401 | `"Unauthorized"` (Flask backend) |
| **E2** | Missing API Key | `""` (Empty string) | **NO** (`httpTransmitted = False`) | 0 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | 401 | `"Missing DEVICE_ID or DEVICE_API_KEY in firmware config"` (Local firmware abort) |
| **E3** | Missing Device ID | `""` (Empty string) | **NO** (`httpTransmitted = False`) | 0 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | 401 | `"Missing DEVICE_ID or DEVICE_API_KEY in firmware config"` (Local firmware abort) |
| **E4** | Short API Key | 8-char short key | **YES** (`httpTransmitted = True`) | 1 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | 401 | `"Unauthorized"` (Flask backend rejects <32 chars) |
| **E5** | Unknown Device ID | `"ESP32_UNKNOWN_99"` | **YES** (`httpTransmitted = True`) | 1 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | 401 | `"Unauthorized"` (Flask backend) |
| **E6** | CSRF Bypass | Valid credentials | **YES** (`httpTransmitted = True`) | 1 | `SCAN_OUTCOME_SUCCESS` | 201 | Succeeded without CSRF token (`POST /api/scans` exempted) |

### Engineering Findings:
1. **Local Firmware Guards:** In `api_client.cpp` lines 38-46:
   ```cpp
   if (strlen(DEVICE_ID) == 0 || strlen(DEVICE_API_KEY) == 0) {
       resp.outcome = SCAN_OUTCOME_DEVICE_AUTH_ERROR;
       resp.httpCode = 401;
       resp.scanResult = "DEVICE_AUTH_ERROR";
       resp.message = "Missing DEVICE_ID or DEVICE_API_KEY in firmware config";
       return resp;
   }
   ```
   When `DEVICE_ID` or `DEVICE_API_KEY` are empty strings, the production C++ client safely synthesizes an error response and immediately returns without attempting a TCP socket connection (`httpTransmitted == False`, `postCallCount == 0`).
2. **Backend Remote Enforcement:** If credentials are non-empty but invalid or shorter than 32 characters, the C++ client correctly transmits the HTTP request (`postCallCount == 1`). The Flask backend's `app/auth.py` validation rejects the key via `validate_device_credentials()` and returns a real HTTP 401 response (`{"success": false, "error": "Unauthorized"}`).

- **Status:** **PASSED**.

---

## 13. Scenarios F & G — Session Constraints

- **Scenario F (No Active Session):** When no class session has `active = 1`, backend returns HTTP 409 Conflict with `scan_result: "NO_ACTIVE_SESSION"`. Stage 6 C++ correction maps to `SCAN_OUTCOME_NO_SESSION`. Display rendered `"NO ACTIVE SESSION"`, buzzer played `BUZZER_BEEP_ERROR`. (PASSED)
- **Scenario G (Expired Session):** Session with `end_time` in the past triggers automatic session deactivation in backend (`UPDATE class_sessions SET active = 0 WHERE id = ?`). Backend returns HTTP 409 Conflict with `scan_result: "SESSION_EXPIRED"`. Stage 6 C++ correction maps to `SCAN_OUTCOME_SESSION_EXPIRED`. Display rendered `"SESSION ENDED"`, `"The active session"`, `"has expired"`, buzzer played `BUZZER_BEEP_ERROR`. (PASSED)

---

## 14. Scenario H — Server Unavailable & Offline Guard

- **Scenario H (Server Down):** Directing client to inactive port `59999` causes Winsock TCP connect failure (`WSAECONNREFUSED`). Mock transport returns `HTTPC_ERROR_CONNECTION_REFUSED (-1)`. Production C++ `sendScan()` maps to `SCAN_OUTCOME_NETWORK_ERROR`. Display rendered `"Server Offline"`, buzzer played `BUZZER_BEEP_NETWORK_ERROR` (400ms duration). (PASSED)
- **Offline Wi-Fi Guard:** Simulating disconnected Wi-Fi (`--offline`) causes C++ `sendScan()` line 49 (`WiFi.status() != WL_CONNECTED`) to abort locally before HTTP transport. Returns `SCAN_OUTCOME_NETWORK_ERROR` with `"Wi-Fi disconnected"`. (PASSED)

---

## 15. SQLite Persistence Verification

- **Atomic Commits:** Attendance records are inserted and committed inside a `BEGIN IMMEDIATE;` transaction.
- **Field Integrity:** Verified `student_id`, `session_id`, `scanned_at` (ISO 8601 UTC format ending in 'Z'), `status` ('Present'), and `source` ('ESP32_ATTENDANCE_01').
- **Unique Constraint:** Verified that SQLite table constraint `UNIQUE(student_id, session_id)` is strictly enforced.
- **Teardown Integrity Check:** After test execution, `PRAGMA integrity_check;` returned `ok`.

---

## 16. Google Sheets Synchronization Queue Verification

- **Atomic Outbox Insertion:** Every successful attendance scan atomically creates exactly one record in `sheets_sync_queue` in the same SQLite transaction.
- **Initial State:** Verified initial `sync_status = 'pending'`, `retry_count = 0`, `created_at == updated_at`.
- **Deduplication:** Duplicate attendance scans roll back and do NOT create a second queue record.
- **Failure Immunity:** Rejected scans (unknown card, inactive student, expired session, auth errors) never insert records into `sheets_sync_queue`.
- **Offline Resilience:** In the absence of external Google Sheets credentials, outbox records remain safely staged in SQLite awaiting future worker synchronization.

---

## 17. OLED & Buzzer Simulation Results

All simulated output states were validated by inspecting the mock frame buffer (`Adafruit_SSD1306::mockGetCurrentFrameText()`) and GPIO transitions (`mockGetPinWriteCount(BUZZER_PIN)`):

| Scan Result | Display Screen Title | Text Lines Rendered | Buzzer Pattern | Duration (ms) |
|---|---|---|---|---|
| **SUCCESS** | `STUDENT IDENTIFIED` | `Roll: <roll>`, `Name: <name>`, `Status: Present` | `BUZZER_BEEP_SUCCESS` | 240 ms |
| **DUPLICATE** | `DUPLICATE SCAN` | `Roll: <roll>`, `Name: <name>`, `ALREADY PRESENT` | `BUZZER_BEEP_DUPLICATE` | 380 ms |
| **UNKNOWN CARD** | `UNKNOWN CARD` | `UID: <uid>`, `Card not registered!` | `BUZZER_BEEP_ERROR` | 280 ms |
| **INACTIVE** | `ACCOUNT INACTIVE` | `Student account`, `is deactivated`, `Scan rejected` | `BUZZER_BEEP_ERROR` | 280 ms |
| **NO SESSION** | `NO ACTIVE SESSION`| `No lecture session`, `is currently active` | `BUZZER_BEEP_ERROR` | 280 ms |
| **EXPIRED** | `SESSION ENDED` | `The active session`, `has expired`, `Scan rejected` | `BUZZER_BEEP_ERROR` | 280 ms |
| **AUTH ERROR** | `SERVER ERROR` | `Device Auth Error` | `BUZZER_BEEP_ERROR` | 280 ms |
| **OFFLINE** | `SERVER ERROR` | `Server Offline`, `Cannot reach backend` | `BUZZER_BEEP_NETWORK_ERROR` | 400 ms |

---

## 18. Native Test Results

Executed `tests/firmware_sim/native/firmware_test_runner.exe`:
- **Modules Tested:** `rfid_scanner.cpp`, `api_client.cpp`, `wifi_manager.cpp`, `display_manager.cpp`, `buzzer.cpp`.
- **Total Tests:** 79.
- **Passed:** 79.
- **Failed:** 0.
- **Execution Time:** 2 ms.

---

## 19. Pytest Results

### E2E Integration Suite (`pytest tests/firmware_sim/e2e -v`):
```
collected 18 items

tests/firmware_sim/e2e/test_e2e_attendance.py::test_e2e_scenario_a_successful_attendance PASSED [  5%]
tests/firmware_sim/e2e/test_e2e_attendance.py::test_e2e_scenario_b_duplicate_attendance PASSED [ 11%]
tests/firmware_sim/e2e/test_e2e_attendance.py::test_e2e_scenario_c_unknown_rfid_card PASSED [ 16%]
tests/firmware_sim/e2e/test_e2e_attendance.py::test_e2e_scenario_d_inactive_student PASSED [ 22%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e1_invalid_device_api_key PASSED [ 27%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e2_missing_device_api_key PASSED [ 33%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e3_missing_device_id PASSED [ 38%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e4_short_device_api_key PASSED [ 44%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e5_unknown_device_id PASSED [ 50%]
tests/firmware_sim/e2e/test_e2e_authentication.py::test_e2e_scenario_e6_device_auth_bypasses_csrf_for_scans PASSED [ 55%]
tests/firmware_sim/e2e/test_e2e_sessions.py::test_e2e_scenario_f_no_active_session PASSED [ 61%]
tests/firmware_sim/e2e/test_e2e_sessions.py::test_e2e_scenario_g_expired_session PASSED [ 66%]
tests/firmware_sim/e2e/test_e2e_sessions.py::test_e2e_scenario_h_server_unavailable PASSED [ 72%]
tests/firmware_sim/e2e/test_e2e_sessions.py::test_e2e_scenario_offline_wifi PASSED [ 77%]
tests/firmware_sim/e2e/test_e2e_sync_queue.py::test_e2e_sync_queue_entry_created_on_success PASSED [ 83%]
tests/firmware_sim/e2e/test_e2e_sync_queue.py::test_e2e_sync_queue_no_duplicate_sync_entry PASSED [ 88%]
tests/firmware_sim/e2e/test_e2e_sync_queue.py::test_e2e_sync_queue_failed_scans_never_enqueued PASSED [ 94%]
tests/firmware_sim/e2e/test_e2e_sync_queue.py::test_e2e_sync_queue_persistence_without_google_creds PASSED [100%]

======================= 18 passed, 1 warning in 20.26s ========================
```

### Firmware Unit Wrapper Suite (`pytest tests/firmware_sim/test_firmware_unit.py -v`):
```
tests/firmware_sim/test_firmware_unit.py::test_native_firmware_build_and_run PASSED [ 50%]
tests/firmware_sim/test_firmware_unit.py::test_native_firmware_runner_executable_direct PASSED [100%]

======================== 2 passed, 1 warning in 22.44s ========================
```

---

## 20. Backend Regression Results

Executed `pytest tests/test_device_auth.py tests/test_attendance.py -v`:
- `tests/test_device_auth.py`: 14 passed.
- `tests/test_attendance.py`: 28 passed.
- **Result:** **42 passed, 0 failed in 14.53s**.

---

## 21. Arduino CLI Compilation Results

```
Command:
tools\arduino-cli\arduino-cli.exe --config-file tools\arduino-cli\arduino-cli.yaml compile --fqbn esp32:esp32:esp32da firmware\esp32_attendance_device

Output:
Sketch uses 788777 bytes (60%) of program storage space. Maximum is 1310720 bytes.
Global variables use 45208 bytes (13%) of dynamic memory, leaving 282472 bytes for local variables. Maximum is 327680 bytes.
Exit code: 0
```

---

## 22. Database Integrity Results

- Temporary SQLite databases: `PRAGMA integrity_check;` returned `ok`.
- Production SQLite database (`database/attendance.db`):
  - `PRAGMA integrity_check;` returned `ok`.
  - Attendance row count: exactly 2 rows (unchanged from initial state).
  - Modification timestamp: unchanged.

---

## 23. Git Status and Production-File Safety

```
Command: git status --short
 M firmware/esp32_attendance_device/api_client.cpp
 M firmware/esp32_attendance_device/api_client.h
?? STAGE_6_API_CLIENT_CORRECTION_REPORT.md
?? STAGE_7_FIRMWARE_INTEGRATION_REVIEW.md
?? STAGE_8_3_FIRMWARE_UNIT_TEST_REPORT.md
?? STAGE_8_4_END_TO_END_SIMULATION_REPORT.md
?? tests/firmware_sim/
```

`git diff -- firmware/esp32_attendance_device app` verifies that:
1. `app/` has **zero modifications**.
2. `firmware/esp32_attendance_device/` only contains the approved Stage 6 corrections in `api_client.cpp` and `api_client.h`. Zero new modifications were introduced in Stage 8.4.
3. No Git commits, pushes, or branch modifications were performed.

---

## 24. Known Limitations and Remaining Physical Tests

While the software end-to-end simulation validates complete HTTP client/server communication, JSON deserialization, and business logic, physical hardware constraints remain to be validated during physical hardware assembly:
1. **Physical RFID Radio Performance:** RF antenna coupling at 13.56 MHz, tag read angles, multi-tag collisions, and card read distances.
2. **I2C Bus Hardware Characteristics:** Bus pull-up resistor adequacy, clock stretching under 3.3V supply ripple, and OLED display contrast in sunlight.
3. **Wi-Fi RF Reliability:** Association delays with physical 2.4 GHz 802.11 b/g/n routers, WPA2 key handshake timing, and multi-path fading in deployment environments.
4. **Buzzer Driver Circuitry:** Transistor saturation voltage and acoustic sound pressure level (dB) of physical 5V active buzzers.

---

## 25. Recommendation for Stage 8.5

Milestone 2 (Software Verification and Simulation) has achieved complete coverage:
- Stage 8.1: Software Testing Architecture Audit.
- Stage 8.2: Mock Architecture & Header Shims.
- Stage 8.3: Comprehensive ESP32 Firmware Unit Testing (79 tests).
- Stage 8.4: End-to-End Firmware & Live Backend Simulation (18 tests).

**Recommended Next Step: STAGE 8.5 — PRE-HARDWARE FLIGHT CHECK & DEPLOYMENT PREPARATION**
1. Generate complete hardware pinout, jumper wiring, and power supply verification checklist.
2. Finalize firmware build profile and verify configuration instructions for physical Wi-Fi credentials and device API keys.
3. Review physical assembly guidelines before connecting hardware.
