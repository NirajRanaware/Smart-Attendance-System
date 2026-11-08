# IoT-Based Smart Attendance Management System

**Phase 3: Cloud Synchronization & External Integration (Completed)**

[![Tests](https://img.shields.io/badge/tests-432%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.10.11-blue.svg)]()
[![Framework](https://img.shields.io/badge/framework-Flask%203.1.3-lightgrey.svg)]()
[![Database](https://img.shields.io/badge/database-SQLite%203-blue.svg)]()
[![Auth](https://img.shields.io/badge/security-admin%20session%20%2B%20csrf-blueviolet.svg)]()
[![Google Sheets](https://img.shields.io/badge/sync-Google%20Sheets%20API%20v4-success.svg)]()

---

## 1. Project Introduction

The **IoT-Based Smart Attendance Management System** is an automated attendance recording solution designed for college classrooms and laboratories.

### Why RFID-Based Attendance?
In conventional classroom environments, taking manual attendance via paper roll sheets or verbal roll calls is time-consuming, prone to human error, and vulnerable to proxy attendance. 

This project solves those challenges:
- **Instant Identification:** Every student is issued an RFID smart card with a unique hardware ID (UID).
- **Contactless & Fast:** Students tap their RFID card against a reader upon entering the classroom. Attendance is recorded within milliseconds.
- **Accurate & Secure:** Duplicate attendance within the same lecture session is physically prevented by database constraints.
- **Immediate Insights:** Instructors and administrators can retrieve real-time attendance statistics and download official CSV attendance registers.
- **Resilient Cloud Backup:** Local SQLite outbox asynchronously synchronizes records to Google Sheets without blocking scans or risking data loss during Internet dropouts.

---

## 2. Project Objectives

1. **Automate Classroom Attendance:** Eliminate paper attendance sheets and reduce wasted lecture time.
2. **Contactless Student Identification:** Instantly map RFID card hardware UIDs to student roll numbers and names.
3. **Secure SQLite Storage (Primary Source of Truth):** Store students, class sessions, and attendance events with full ACID compliance, transactional atomicity, and foreign-key integrity.
4. **Prevent Duplicate Scans:** Reject repeated card taps in the same class session with clear feedback.
5. **Real-Time Attendance Reports:** Generate daily attendance statistics including total enrolled, present count, absent count, and attendance percentages.
6. **Downloadable CSV Registers:** Export comprehensive attendance sheets with formula-injection security (CWE-1236 protection).
7. **Simulated Hardware Testing:** Provide a terminal-based RFID simulator to thoroughly verify the backend before deploying physical hardware.
8. **Decoupled Google Sheets Synchronization:** Replicate attendance records to Google Sheets via a transactional SQLite outbox queue, background worker process, exponential retry backoff, and idempotent duplicate prevention.
9. **Future-Ready Hardware Architecture:** Designed for deployment with ESP32 microcontrollers and RC522 RFID readers in Phase 4.

---

## 3. Technology Stack

| Technology | Purpose | Implementation Status |
| :--- | :--- | :--- |
| **Python 3.10.11** | Backend programming language | **Implemented (Phase 1)** |
| **Flask 3.1.3** | Modular REST API backend & application factory | **Implemented (Phase 1)** |
| **SQLite 3** | Primary ACID attendance database (foreign keys, partial indexes, outbox queue) | **Implemented (Phases 1–3)** |
| **pytest 9.2.0** | Automated unit, API, and integration test framework | **Implemented (Phases 1–3)** |
| **pytest-cov 7.1.0** | Statement coverage measurement tool | **Implemented (Phase 1)** |
| **Python requests 2.34.2** | HTTP client powering the simulated RFID scanner | **Implemented (Phase 1)** |
| **HTML5 / CSS3 / JavaScript** | Responsive web dashboard for administrators and teachers | **Implemented (Phase 2)** |
| **Google Sheets API v4** | Cloud reporting and external synchronization destination | **Implemented (Phase 3)** |
| **Google Service Account** | Secure headless authentication for Google Cloud integration | **Implemented (Phase 3)** |
| **ESP32 Microcontroller** | IoT Wi-Fi controller for classroom card scanning | *Planned (Phase 4)* |
| **RC522 RFID Reader** | 13.56 MHz RFID reader module (SPI interface, 3.3V) | *Planned (Phase 4)* |

> [!NOTE]
> Phases 1, 2, and 3 are **100% complete and verified**. SQLite remains the authoritative database, and Google Sheets serves strictly as a secondary synchronized reporting destination. Physical hardware (ESP32, RC522) is scheduled for Phase 4.

---

## 4. System Architecture

### Phase 1–3 Complete Architecture (Current Status)

The system is fully decoupled into an authoritative SQLite edge persistence tier and an asynchronous cloud synchronization pipeline:

```text
+-------------------------+
|   RFID SCANNER          |
|   SIMULATOR / BROWSER   |  (POST /api/scans)
+-------------------------+
             |
             | HTTP POST /api/scans
             v
+-------------------------------------------------------+
|   FLASK REST API ENGINE                               |
|   - Student verification & Session active check       |
|   - Atomic Attendance insert + Outbox queue insert    |
+-------------------------------------------------------+
             |
             +-------------------+-------------------+
             | (Atomic ACID)     |                   |
             v                   v                   v
+-------------------------+  +---------------+  +--------------------------+
|   PRIMARY SQLITE DB     |  | WEB DASHBOARD |  | SHEETS SYNC QUEUE        |
|   (Authoritative Truth) |  | (HTML5/CSS/JS)|  | (Local Outbox Table)     |
|   - students            |  +---------------+  +--------------------------+
|   - class_sessions                                         |
|   - attendance                                             | Polling / Leased Claim
|   - admin_users                                            v
+-------------------------+                     +--------------------------+
                                                | BACKGROUND SYNC WORKER   |
                                                | (flask run-sheets-worker)|
                                                +--------------------------+
                                                             |
                                                             | Google Sheets API v4
                                                             | (RAW append, Column A check)
                                                             v
                                                +--------------------------+
                                                | GOOGLE SHEETS            |
                                                | (Secondary Cloud Backup) |
                                                +--------------------------+
```

### Planned Phase 4 Hardware Integration (Next Phase)

```text
+-----------------------+
|  RC522 RFID Reader    |
|  (13.56 MHz, SPI, 3.3V)
+-----------------------+
            |
            | SPI Protocol
            v
+-----------------------+
|  ESP32 Microcontroller|
|  (Wi-Fi Enabled)      |
+-----------------------+
            |
            | HTTP POST /api/scans (Wi-Fi)
            v
+-------------------------------------------------------+
|  FLASK REST API BACKEND (Existing & Verified)         |
+-------------------------------------------------------+
```

---

## 5. Installation Instructions (Windows 11 PowerShell)

Follow these steps to set up the Smart Attendance System backend on your local machine:

### Step 1: Prerequisites
- **Operating System:** Windows 11 (or Windows 10)
- **Python Version:** Python 3.10 (64-bit) installed and added to your system `PATH`.

### Step 2: Open PowerShell in the Project Directory
```powershell
cd C:\Users\niraj\Downloads\Smart-Attendance-System
```

### Step 3: Activate the Virtual Environment
The virtual environment `.venv` is already configured with Python 3.10:
```powershell
.\.venv\Scripts\Activate.ps1
```
*(If PowerShell restricts script execution, run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once).*

### Step 4: Install Dependencies
```powershell
python -m pip install -r requirements.txt
```

### Step 5: Verify Database Initialization
Run the database diagnostic tool to ensure the SQLite schema and indexes are provisioned:
```powershell
.\.venv\Scripts\python.exe scripts\check_database.py
```
Expected output: `ALL DATABASE CHECKS PASSED SUCCESSFULLY!`.

---

## 6. Running the Application

### Start the Flask Server
```powershell
.\.venv\Scripts\python.exe run.py
```

- **Server URL:** `http://127.0.0.1:5000`
- **Host Binding:** `127.0.0.1` (port `5000`)

### Verify Server Health
In a separate PowerShell window, check that the backend is responding and connected to SQLite:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health"
```
**Expected Response:**
```json
{
  "database": "connected",
  "status": "healthy"
}
```

### Stopping the Server
Press `Ctrl + C` in the terminal where `run.py` is running.

---

## 7. Running the Google Sheets Background Synchronization Worker

The system includes a dedicated asynchronous worker that processes pending records from the local SQLite outbox and synchronizes them to Google Sheets.

### Step 1: Configuration Requirements
1. Place your Google Cloud Service Account private key file at:
   `credentials/service_account.json`
2. Configure your target Google Spreadsheet ID in `.env` or application config:
   `GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id_here`
3. Share your Google Spreadsheet with the service account client email as **Editor**.

### Step 2: Running the Worker (Continuous Loop)
```powershell
.\.venv\Scripts\flask.exe run-sheets-worker
```
- **Poll Interval:** Default 10 seconds.
- **Batch Size:** Default 20 records per batch.
- **Lease Duration:** 60-second leased concurrency lock.
- **Graceful Termination:** Press `Ctrl + C` (SIGINT/SIGTERM) to finish the active batch and stop cleanly.

### Step 3: Running Single-Shot Batch Execution (CLI Option)
To process one batch of pending records and exit immediately:
```powershell
.\.venv\Scripts\flask.exe run-sheets-worker --once
```

### Step 4: Retry Policy & Dead-Letter Recovery
- **Exponential Backoff:** Base delay of 15 seconds, exponential multiplier of 2, and $\pm 10\%$ jitter.
- **Strict Delay Cap:** The final retry delay mathematically never exceeds 900 seconds (15 minutes).
- **Dead-Letter State:** Records failing 5 consecutive attempts transition to `dead_letter`.
- **Administrative Recovery:** Administrators can inspect dead-letter records via `GET /api/admin/sheets-sync/dead-letter` and safely requeue them using `POST /api/admin/sheets-sync/dead-letter/retry`.

---

## 8. Running Automated Tests & Code Coverage

The system includes a comprehensive test suite covering database ACID constraints, REST API endpoints, simulator mocking, worker synchronization, duplicate prevention, and dead-letter recovery.

### Run All Automated Tests
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Measure Actual Code Coverage
```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov=scripts --cov-report=term-missing
```

### Verified Test Results (Phase 3 Milestone 6)
- **Total Tests Passed:** **432 passed, 0 failed, 1 warning**
- **Statement Coverage:** **93% Statement Coverage** across `app/` and `scripts/`
- **Safety Guarantee:** All tests use isolated temporary databases and mocked Google APIs; zero live spreadsheet writes occur.

---

## 9. Running the Simulated RFID Scanner

The Python simulator (`scripts/simulate_scan.py`) emulates an ESP32 hardware scanner. It communicates with the backend via `POST /api/scans`.

### Interactive Mode (Continuous Terminal Prompt)
```powershell
.\.venv\Scripts\python.exe scripts\simulate_scan.py
```

**Terminal Display:**
```text
========================================
       SMART ATTENDANCE SYSTEM          
        RFID SCANNER SIMULATOR          
========================================

Backend:        http://127.0.0.1:5000
Scanner Status: Ready

Enter RFID Card UID to scan.
Type EXIT to close the simulator.

RFID UID: 
```
- Type any card UID (e.g. `A1B2C3D4`) and press Enter.
- Type `EXIT` or `QUIT` to close the simulator.

### One-Shot Mode (Single Scan)
```powershell
.\.venv\Scripts\python.exe scripts\simulate_scan.py --uid A1B2C3D4
```

### Supported Scanner Responses
| Status | Condition | Terminal Display |
| :--- | :--- | :--- |
| **HTTP 201** | Successful Scan | `[ SUCCESS ] Attendance Recorded!` with student name, roll number, status, and course. |
| **HTTP 409** | Duplicate Scan | `[ DUPLICATE ATTENDANCE ] Attendance has already been recorded for this student in this session.` |
| **HTTP 409** | Inactive Session | `[ NO ACTIVE SESSION ] No class session is currently active. Attendance cannot be recorded.` |
| **HTTP 404** | Unregistered Card | `[ ERROR ] Unknown RFID Card! This RFID UID is not registered in the system.` |
| **HTTP 403** | Inactive Student | `[ ERROR ] Student Account Inactive! Attendance cannot be recorded for this student.` |
| **Connection Error** | Server Offline | `[ CONNECTION ERROR ] Cannot connect to attendance server. Please check whether Flask is running.` |

---

## 9. Administrator Authentication & Authorization (Phase 2 Milestone 7)

The system features single-administrator authentication to secure the web dashboard and administrative REST endpoints:

### 1. Creating the First Administrator Account
Execute the CLI command via Windows PowerShell:
```powershell
.\.venv\Scripts\flask.exe create-admin
```
Follow the interactive prompts to enter a unique username and strong password (minimum 8 characters with at least one digit or symbol). Passwords are cryptographically hashed using Werkzeug (`scrypt`/`pbkdf2`) and stored in SQLite `admin_users` table.

### 2. Logging In & Session Management
- **Login URL:** `http://127.0.0.1:5000/login`
- **Session Security:** Cookies configured with `HttpOnly`, `SameSite=Lax`, and `PERMANENT_SESSION_LIFETIME = 2 hours`.
- **Session Fixation Prevention:** Session is cleared and re-generated upon successful authentication.
- **Cache-Control:** All authenticated views serve `Cache-Control: no-cache, no-store, must-revalidate` to prevent back-button browsing after logout.
- **CSRF Protection:** State-changing requests (`POST`, `PATCH`, `PUT`, `DELETE`) require a valid `X-CSRF-Token` header.
- **POST-Only Logout:** Logout strictly requires `POST /logout` with a valid CSRF token, mitigating cross-site session termination via image tags or prefetching.
- **Login Abuse Rate Limiting:** Thread-safe in-memory rate limiting enforces maximum 5 failed attempts per 5-minute window (HTTP 429) per client IP (ignores untrusted `X-Forwarded-For`).
- **Production Configuration:** `ProductionConfig` strictly validates `SECRET_KEY` from the environment, aborting startup if unconfigured or matching development fallback keys.

---

## 10. Project Documentation Index

For in-depth documentation, please refer to the following companion guides:

- [API Documentation (`API_DOCUMENTATION.md`)](file:///c:/Users/niraj/Downloads/Smart-Attendance-System/API_DOCUMENTATION.md): Complete specifications for all 15 REST endpoints with example payloads and status codes.
- [Database Documentation (`DATABASE_DOCUMENTATION.md`)](file:///c:/Users/niraj/Downloads/Smart-Attendance-System/DATABASE_DOCUMENTATION.md): Schema tables (`students`, `class_sessions`, `attendance`, `admin_users`), partial indexes, and ER diagram.
- [Setup & Testing Guide (`SETUP_AND_TESTING_GUIDE.md`)](file:///c:/Users/niraj/Downloads/Smart-Attendance-System/SETUP_AND_TESTING_GUIDE.md): Complete step-by-step administrator creation and testing walkthrough.
- [Future Development Roadmap (`FUTURE_DEVELOPMENT_ROADMAP.md`)](file:///c:/Users/niraj/Downloads/Smart-Attendance-System/FUTURE_DEVELOPMENT_ROADMAP.md): Blueprints for Web Dashboard (Phase 2), n8n & Google Sheets (Phase 3), and ESP32 hardware (Phase 4).

---

## 11. License & Academic Attribution
Developed as part of the **IoT-Based Smart Attendance Management System** college project.
Academic and educational use only.
