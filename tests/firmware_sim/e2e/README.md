# Stage 8.4 — End-to-End ESP32 Firmware & Live Flask Backend Simulation

## Architecture Overview

This suite automates end-to-end integration testing between the **actual production C++ ESP32 firmware modules** (`AttendanceApiClient::sendScan()`, `DisplayManager`, `BuzzerController`) and the **actual live Flask backend** (`POST /api/scans`) over real localhost TCP sockets.

```
+-------------------------------------------------------------------------------+
|                             TEST RUNNER (pytest)                             |
|                                                                               |
|  1. Allocates ephemeral port on 127.0.0.1                                     |
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
|     -> Reads raw HTTP/1.1 response                                            |
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

## Scenarios Covered

| Scenario | Trigger / Payload | Expected HTTP | Expected C++ Outcome | Hardware Feedback | DB State |
|---|---|---|---|---|---|
| **Scenario A** | Valid card UID & active session | 201 Created | `SCAN_OUTCOME_SUCCESS` | Display "ATTENDANCE", Buzzer Success (240ms) | Attendance record & sync queue record created |
| **Scenario B** | Duplicate scan in same session | 409 Conflict | `SCAN_OUTCOME_DUPLICATE` | Display "ALREADY MARKED", Buzzer Duplicate (380ms) | Attendance count unchanged (1 record) |
| **Scenario C** | Unregistered card UID | 404 Not Found | `SCAN_OUTCOME_UNKNOWN_CARD` | Display "ACCESS DENIED", Buzzer Error (280ms) | 0 records created |
| **Scenario D** | Inactive student account (`active=0`) | 403 Forbidden | `SCAN_OUTCOME_INACTIVE_STUDENT` | Display "CARD INACTIVE", Buzzer Error (280ms) | 0 records created |
| **Scenario E1**| Wrong 32-character API key | 401 Unauthorized | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | Display "Device Auth Error", Buzzer Error | 0 records created |
| **Scenario E2**| Empty API key (`""`) | Local 401 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | Display "Device Auth Error", Buzzer Error | Pre-check aborts locally before network |
| **Scenario E3**| Empty device ID (`""`) | Local 401 | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | Display "Device Auth Error", Buzzer Error | Pre-check aborts locally before network |
| **Scenario E4**| Short API key (< 32 chars) | 401 Unauthorized | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | Display "Device Auth Error", Buzzer Error | Dispatched; rejected by backend |
| **Scenario E5**| Unknown device ID | 401 Unauthorized | `SCAN_OUTCOME_DEVICE_AUTH_ERROR` | Display "Device Auth Error", Buzzer Error | 0 records created |
| **Scenario E6**| Valid credentials without CSRF token | 201 Created | `SCAN_OUTCOME_SUCCESS` | Display "ATTENDANCE", Buzzer Success | Device auth successfully bypasses CSRF |
| **Scenario F** | No active class session | 409 Conflict | `SCAN_OUTCOME_NO_SESSION` | Display "NO ACTIVE SESSION", Buzzer Error | 0 records created |
| **Scenario G** | Expired class session (`end_time` in past)| 409 Conflict | `SCAN_OUTCOME_SESSION_EXPIRED` | Display "SESSION EXPIRED", Buzzer Error | Session auto-deactivated, 0 records |
| **Scenario H** | Server unavailable (port down) | -1 (Refused) | `SCAN_OUTCOME_NETWORK_ERROR` | Display "Server Offline", Buzzer Network Error (400ms) | Connection refused handled safely |
| **Offline Guard**| Wi-Fi disconnected locally | -1 (Local) | `SCAN_OUTCOME_NETWORK_ERROR` | Display "Server Offline", Buzzer Network Error | Local offline guard suppresses scan |

---

## Running the E2E Tests

### Via Pytest (Recommended)
```powershell
python -m pytest tests/firmware_sim/e2e -v
```

### Direct Standalone Binary Execution
```powershell
# Display help & options
tests\firmware_sim\native\firmware_e2e_runner.exe --help

# Execute single scan against live server
tests\firmware_sim\native\firmware_e2e_runner.exe --host 127.0.0.1 --port 5000 --uid A1B2C3D4 --device ESP32_ATTENDANCE_01 --key <valid_key>
```
