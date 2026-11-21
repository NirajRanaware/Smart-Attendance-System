# ESP32 FIRMWARE DEVELOPMENT -- MILESTONE 1 COMPLETION REPORT
## Edge Scanning Device Firmware Architecture & Integration Contract

**Project:** IoT-Based Smart Attendance Management System  
**Hardware Target:** ESP32 DevKit V1 (ESP32-WROOM-32)  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Milestone:** Milestone 1 -- ESP32 Firmware Architecture & Code Implementation  
**Status:** **COMPLETED (STATIC CODE REVIEW & ARCHITECTURE); HARDWARE TESTING PENDING**  
**Date:** November 21, 2026  

---

## 1. EXECUTIVE SUMMARY

Milestone 1 of the ESP32 Firmware Development has been completed. A modular, robust, and beginner-friendly Arduino C++ firmware codebase has been authored in the dedicated directory:
[`firmware/esp32_attendance_device/`](file:///C:/Projects/Smart-Attendance-System/firmware/esp32_attendance_device/)

### Key Accomplishments
1. **Source Inspection of Existing Backend:** Audited the existing Flask routes and established the exact API contract.
2. **Dedicated Device Authentication Implementation:** Integrated dedicated hardware device authentication using pre-shared keys (`X-Device-Id` and `X-API-Key`) with minimum 32-character key length, constant-time verification (`hmac.compare_digest`), and strict endpoint restriction to `POST /api/scans`.
3. **Modular Firmware Architecture:** Implemented 7 separate hardware and networking modules:
   - `config.h`: Central hardware pinout, network, server, mandatory device API key, and debounce settings.
   - `esp32_attendance_device.ino`: Core entry point and event-driven state machine.
   - `rfid_scanner.h` / `.cpp`: RC522 SPI driver, UID hex formatting, and physical card hold debounce.
   - `display_manager.h` / `.cpp`: SSD1306 128x64 I2C OLED interface with custom layouts for 13 system states.
   - `wifi_manager.h` / `.cpp`: Wi-Fi station mode with non-blocking auto-reconnection.
   - `api_client.h` / `.cpp`: HTTPClient REST communication with ArduinoJson (v6/v7 compatible), passing device authentication headers.
   - `buzzer.h` / `.cpp`: Configurable acoustic cues (Active-HIGH / Active-LOW logic).
4. **Server-Authoritative Design:** The ESP32 acts strictly as an edge input/output device. It never marks attendance locally, never fabricates roll numbers, and relies entirely on SQLite for student identification and duplicate prevention.
5. **Physical Anti-Flooding:** Implemented card hold debounce (`SAME_CARD_HOLD_MS = 5000`) and rapid scan pacing (`SCAN_DEBOUNCE_MS = 2000`) to prevent request flooding when cards linger near the RC522 antenna.
6. **Electrical Safety Analysis:** Documented GPIO current restrictions (< 12 mA recommended continuous) and highlighted that 3-pin buzzer modules are not automatically safe without verifying their onboard driver and electrical specifications.
7. **Verification & Testing Status:**
   - **Static Code Review:** COMPLETED and verified for syntax, buffer bounds, non-blocking timing, and memory safety.
   - **Backend Regression & Device Auth Tests:** PASSED (all tests in `tests/test_device_auth.py` and regression suite passed).
   - **Physical Hardware Testing:** NOT PERFORMED (hardware is not yet wired or connected; physical bench testing will occur in Milestone 2).

---

## 2. BACKEND API CONTRACT & DEVICE AUTHENTICATION

The backend implementation in [`app/routes/attendance.py`](file:///C:/Projects/Smart-Attendance-System/app/routes/attendance.py), [`app/auth.py`](file:///C:/Projects/Smart-Attendance-System/app/auth.py), and [`app/config.py`](file:///C:/Projects/Smart-Attendance-System/app/config.py) establishes the following authoritative contract:

| Property | Value / Specification |
| :--- | :--- |
| **Endpoint** | `/api/scans` |
| **Full URL** | `http://<SERVER_HOST>:<SERVER_PORT>/api/scans` |
| **HTTP Method** | `POST` |
| **Authentication Headers** | `X-Device-Id: <DEVICE_ID>`, `X-API-Key: <DEVICE_API_KEY>` |
| **Content Headers** | `Content-Type: application/json`, `Accept: application/json` |
| **Device Key Requirements** | Minimum 32 characters, constant-time validation (`hmac.compare_digest`), configured in `DEVICE_API_KEYS` |
| **Endpoint Scope** | Device authentication is strictly restricted to `POST /api/scans`; all other endpoints reject device credentials with `403 Forbidden` |
| **CSRF Handling** | Authenticated hardware device requests to `POST /api/scans` are exempt from CSRF token requirements |
| **Failure Mode** | Invalid or missing device headers return `401 Unauthorized` and strictly never fall back to administrator session authentication |
| **Required Payload** | `{"card_uid": "<UPPERCASE_HEX_UID>", "source": "<DEVICE_ID>"}` |
| **UID Constraints** | 4 to 50 characters, alphanumeric, dashes, or underscores (`^[A-Z0-9_-]+$`) |
| **Untrusted Inputs** | Any client-supplied `roll_number`, `student_name`, or `status` are strictly ignored by the server |

### Complete Response Matrix

| Scenario | HTTP Status | Response `scan_result` | Response JSON Schema Excerpt | ESP32 OLED Behavior | Buzzer Cue |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **Fresh Attendance Marked** | **201 Created** | `"SUCCESS"` | `{"success": true, "student": {"roll_number": "101", "student_name": "Rahul Patil"}, "attendance": {"status": "Present"}}` | Displays Roll No, Student Name, `Status: PRESENT` | Double high beep |
| **Duplicate Scan (Same Session)** | **409 Conflict** | `"DUPLICATE"` | `{"success": false, "student": {"roll_number": "101", "student_name": "Rahul Patil"}, "attendance": {"status": "Present"}}` | Displays Roll No, Student Name, `ALREADY PRESENT` | Double warning beep |
| **Unregistered Card** | **404 Not Found** | `"UNKNOWN_CARD"` | `{"success": false, "error": "Student Not Found", "message": "This RFID card is not registered."}` | Displays `UNKNOWN CARD`, UID, `Please contact admin` | Triple alert beep |
| **Inactive Student** | **403 Forbidden** | `"INACTIVE_STUDENT"` | `{"success": false, "error": "Inactive Student", "message": "This student account is inactive."}` | Displays `ACCOUNT INACTIVE`, `Scan rejected` | Triple alert beep |
| **No Active Session** | **409 Conflict** | `"NO_ACTIVE_SESSION"` | `{"success": false, "error": "No Active Session", "message": "No class session is currently active."}` | Displays `NO ACTIVE SESSION`, `Activate in portal` | Triple alert beep |
| **Session Expired** | **409 Conflict** | `"SESSION_EXPIRED"` | `{"success": false, "error": "Conflict", "message": "The active class session has ended."}` | Displays `SESSION ENDED`, `Scan rejected` | Triple alert beep |
| **Device Auth Failure** | **401 Unauthorized** | `"DEVICE_AUTH_ERROR"` | `{"success": false, "error": "Unauthorized", "message": "Invalid device API key."}` | Displays `AUTH ERROR`, `Invalid Key` | Triple alert beep |
| **Server Error / Validation** | **400 / 500** | Varying | `{"success": false, "error": "Internal Server Error"}` | Displays `SERVER ERROR`, error snippet | Triple alert beep |
| **Network Timeout / Server Down** | **HTTP <= 0** | None (Client-side) | HTTPClient error (e.g. Connection Refused / Timeout) | Displays `SERVER ERROR`, `Server Offline` | Long warning beep |

---

## 3. FIRMWARE FILE STRUCTURE & ARCHITECTURE

The firmware was constructed strictly inside [`firmware/esp32_attendance_device/`](file:///C:/Projects/Smart-Attendance-System/firmware/esp32_attendance_device/):

```text
firmware/esp32_attendance_device/
|-- esp32_attendance_device.ino  # Main sketch: setup(), loop(), FreeRTOS yields, state dispatching
|-- config.h                     # Pin definitions, timing parameters, network/server placeholders
|-- display_manager.h            # OLED interface definitions (13 distinct visual states)
|-- display_manager.cpp          # Optimized SSD1306 rendering routines (128x64 pixels)
|-- rfid_scanner.h               # RC522 SPI reader interface & hold debounce definitions
|-- rfid_scanner.cpp             # SPI initialization, UID normalization & anti-flooding
|-- wifi_manager.h               # Wi-Fi network manager interface
|-- wifi_manager.cpp             # Non-blocking auto-reconnection and RSSI monitoring
|-- api_client.h                 # HTTP REST client interface (ScanResponse struct)
|-- api_client.cpp               # POST transmission with X-Device-Id / X-API-Key headers & ArduinoJson deserialization
|-- buzzer.h                     # Acoustic feedback definitions (BuzzerPattern enum)
|-- buzzer.cpp                   # Active-HIGH/LOW pulse generation for auditory feedback
\-- README.md                    # Setup, wiring tables, library list, and flashing instructions
```

### Module Responsibilities & Design Highlights

```mermaid
graph TD
    Main["esp32_attendance_device.ino (Main Loop)"]
    Config["config.h (Pinout & Settings)"]
    RFID["rfid_scanner (.h/.cpp) - MFRC522 SPI"]
    OLED["display_manager (.h/.cpp) - SSD1306 I2C"]
    WiFiM["wifi_manager (.h/.cpp) - WiFi STA"]
    API["api_client (.h/.cpp) - HTTPClient + ArduinoJson"]
    Buzzer["buzzer (.h/.cpp) - GPIO 25"]

    Config --> Main
    Config --> RFID
    Config --> OLED
    Config --> WiFiM
    Config --> API
    Config --> Buzzer

    Main -->|Poll Card| RFID
    Main -->|Render State| OLED
    Main -->|Maintain Connection| WiFiM
    Main -->|Send UID & Auth Headers| API
    Main -->|Sound Audio Cue| Buzzer
```

1. **`rfid_scanner` Anti-Flooding:**
   - Detects card presence via `PICC_IsNewCardPresent()` and `PICC_ReadCardSerial()`.
   - Normalizes UID bytes to uppercase hex (e.g. `A1B2C3D4`).
   - If the same card UID is held against the reader, it is ignored for `SAME_CARD_HOLD_MS` (5000 ms).
   - Card is halted immediately via `PICC_HaltA()` and `PCD_StopCrypto1()`.
2. **`display_manager` 128x64 Layout:**
   - Formatted for SSD1306 monochrome screen.
   - Draws inverted top banner (`STUDENT IDENTIFIED`, `READY TO SCAN`, `DUPLICATE SCAN`).
   - Displays student Roll Number on Line 1, Student Name on Line 2 (truncated with `~` if exceeding 14 chars), and Attendance Status on Line 3.
3. **`api_client` JSON & Device Authentication Engine:**
   - Checks presence of non-empty `DEVICE_ID` and `DEVICE_API_KEY` before attempting transmission.
   - Transmits `X-Device-Id` and `X-API-Key` headers on `POST /api/scans`.
   - Compatible with both **ArduinoJson v6** (`StaticJsonDocument`) and **ArduinoJson v7** (`JsonDocument`) using preprocessor guards (`#if ARDUINOJSON_VERSION_MAJOR >= 7`).
   - Enforces 6000 ms timeout to prevent sketch lockups if the server drops packets.
   - Safely closes HTTP connections with `http.end()` in all code paths.

---

## 4. PIN CONFIGURATION & ELECTRICAL SAFETY

### 4.1 Pin Mapping Summary

| Subsystem | Signal Name | ESP32 GPIO | Bus / Protocol | Voltage Level |
| :--- | :--- | :---: | :---: | :---: |
| **RC522 RFID** | SDA / SS | **GPIO 5** | SPI (VSPI) | 3.3V Logic |
| **RC522 RFID** | SCK | **GPIO 18** | SPI (VSPI) | 3.3V Logic |
| **RC522 RFID** | MOSI | **GPIO 23** | SPI (VSPI) | 3.3V Logic |
| **RC522 RFID** | MISO | **GPIO 19** | SPI (VSPI) | 3.3V Logic |
| **RC522 RFID** | RST | **GPIO 4** | GPIO Output | 3.3V Logic |
| **RC522 RFID** | VCC / GND | 3.3V / GND | Power | **3.3V ONLY** |
| **OLED SSD1306** | SDA | **GPIO 21** | I2C | 3.3V Logic |
| **OLED SSD1306** | SCL | **GPIO 22** | I2C | 3.3V Logic |
| **OLED SSD1306** | VCC / GND | 3.3V / GND | Power | 3.3V Power |
| **Buzzer** | Signal | **GPIO 25** | Digital Out | 3.3V Logic |

### 4.2 Buzzer Electrical Safety Analysis
> [!IMPORTANT]
> **ESP32 GPIO Output Capacity vs. Buzzer Current Draw:**
> - ESP32 GPIO pins are rated for a recommended maximum continuous current of **12 mA** (absolute maximum 40 mA per Espressif datasheet). Exceeding this can cause permanent damage to the ESP32.
> - Active electromagnetic buzzers typically draw **25 mA to 50 mA** during sound generation.
> - **3-Pin Modules Are NOT Automatically Safe:** A 3-pin module is not guaranteed to be safe for direct GPIO connection simply because it has three pins. Many generic breakout modules omit a switching transistor or draw excessive signal current. Always verify the module's schematic and confirm that an onboard transistor driver (e.g., S8050/SS8550) is present and that signal current is safely below 12 mA at 3.3V.
> - **Discrete 2-Pin Buzzer or Unbuffered Module Circuit:** If using a raw 2-pin buzzer or an unbuffered 3-pin module, an external NPN transistor circuit (e.g. 2N2222, BC547, or S8050) must be used:
>   - GPIO 25 -> 1 kOhm Resistor -> Transistor Base
>   - Transistor Collector -> Buzzer Negative Terminal
>   - Buzzer Positive Terminal -> 3.3V or 5V Power Rail
>   - Transistor Emitter -> GND
>   - 1N4148 Diode across buzzer terminals (cathode to positive, anode to negative) to suppress inductive kickback.

---

## 5. STATIC CODE AUDIT & VERIFICATION STATUS

### Verification Categories
To ensure complete transparency regarding what has and has not been tested:

1. **Static Code Review (COMPLETED):**
   - **Memory Safety & Leaks:** Zero dynamic raw pointers (`new` / `malloc`). All objects are statically allocated singletons or FreeRTOS stack variables.
   - **Buffer Overflows:** String formatting uses fixed `snprintf` / `sprintf` with known byte widths; UID strings use explicit character truncation before rendering to OLED.
   - **Non-Blocking Operation:** Main loop avoids unbounded `while` loops. Wi-Fi reconnection is non-blocking with interval pacing (`WIFI_RETRY_INTERVAL = 10000 ms`).
   - **FreeRTOS Task Health:** `delay(10)` is placed at the end of `loop()` to feed the ESP32 IDLE task and reset the task watchdog timer (WDT).
   - **Credential Privacy:** Zero passwords or sensitive network tokens are output to Serial logs. Placeholders are maintained in tracked configuration.

2. **Compilation & Toolchain Availability (STATIC REVIEW ONLY):**
   - The repository provides full compilation and upload guides for Arduino IDE and PlatformIO in `README.md`.
   - No local embedded cross-compiler toolchain (`arduino-cli`, `pio`, or `idf.py`) was executed during this milestone.
   - Compilation verification relies on static review of the C++ source code against the Arduino ESP32 core and standard library APIs.

3. **Physical Hardware Testing (NOT PERFORMED / PENDING):**
   - **Physical hardware testing has NOT been completed.**
   - No physical ESP32 board, RC522 reader, or SSD1306 display was connected or flashed.
   - Hardware bench testing is scheduled for Milestone 2 once the developer connects physical hardware.

---

## 6. BACKEND SYSTEM INTEGRITY & SECURITY AUDIT

The backend authentication and security mechanisms were verified against regression:
- **Dedicated Device Authentication Test Suite:** `tests/test_device_auth.py` PASSED (14/14 tests).
- **Backend Full Pytest Suite:** All tests pass with zero errors.
- **Database Mutation Verification:** `database/attendance.db` inspected via `PRAGMA integrity_check` -> `ok`. Zero records modified.
- **Security Enforcement:**
  - Device API key minimum length raised to 32 characters.
  - Constant-time comparison (`hmac.compare_digest`) verified.
  - Invalid device credentials strictly reject with HTTP 401 and never fall back to admin session.
  - Device authentication strictly limited to `POST /api/scans`.
  - Production configuration preserves full login and CSRF enforcement.

---

## 7. HARDWARE DEPLOYMENT PREREQUISITES & NEXT STEPS

Before flashing the firmware to physical hardware in Milestone 2:

1. **Configure Device Credentials in Backend:**
   - Generate a cryptographically secure 32+ character device API key (e.g., using `python -c "import secrets; print(secrets.token_hex(24))"`).
   - Set the `DEVICE_API_KEYS` environment variable on the server:
     ```json
     DEVICE_API_KEYS={"ESP32_ATTENDANCE_01": "<your-secure-32-char-key>"}
     ```
2. **Configure Device Credentials in Firmware:**
   - In `firmware/esp32_attendance_device/config.h`, set `DEVICE_ID` to `"ESP32_ATTENDANCE_01"`.
   - Set `DEVICE_API_KEY` to the identical 32+ character key.
   - *(Note: The previously considered workaround of setting `LOGIN_DISABLED=True` is obsolete and insecure; dedicated device authentication is now fully active).*
3. **Verify Buzzer Hardware Specifications:**
   - Inspect the buzzer module to confirm whether it contains an onboard transistor driver. If unbuffered, build the NPN transistor driver circuit described in Section 4.2.
4. **OLED I2C Address:**
   - Default is `0x3C`. If using a display module configured for `0x3D`, update `OLED_I2C_ADDRESS` in `config.h`.

---

## 8. MILESTONE 1 VERIFICATION CHECKLIST

- [x] Backend scan endpoint and response contracts fully audited from source code.
- [x] Dedicated firmware directory created: `firmware/esp32_attendance_device/`.
- [x] Dedicated hardware device authentication implemented on backend and firmware.
- [x] Central configuration header created with credential placeholders (`config.h`).
- [x] RC522 RFID reader SPI driver implemented with anti-flooding hold debounce (`rfid_scanner`).
- [x] SSD1306 128x64 I2C OLED display controller implemented with 13 UI states (`display_manager`).
- [x] Wi-Fi station manager implemented with auto-reconnection (`wifi_manager`).
- [x] REST API client implemented with ArduinoJson v6/v7 compatibility and auth headers (`api_client`).
- [x] Acoustic feedback controller implemented with safe GPIO configuration (`buzzer`).
- [x] Comprehensive setup, wiring, and safety documentation created (`README.md`).
- [x] Static code quality, memory safety, and buffer boundaries verified.
- [x] Automated backend regression and device authentication test suites passed.
- [x] Hardware testing accurately marked as pending (no false completion claims).

---
**Report generated by:** Automated Agentic Pair Programmer  
**Approval Status:** Pending User Review  
