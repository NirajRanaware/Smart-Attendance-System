# Beginner's Setup and Testing Guide

**Project:** IoT-Based Smart Attendance Management System  
**Environment:** Windows 11 PowerShell  
**Target Audience:** Students, evaluators, and developers demonstrating the Phase 1 backend.

This step-by-step walkthrough will guide you through running and demonstrating the complete Smart Attendance Management System backend on your Windows 11 machine without requiring physical RFID hardware.

---

## Preparation: Prerequisites Check

1. Open **PowerShell** as your standard user.
2. Navigate to the project root:
   ```powershell
   cd C:\Users\niraj\Downloads\Smart-Attendance-System
   ```
3. Activate the virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   *(You should see `(.venv)` appear at the beginning of your PowerShell prompt).*

---

## Step 0: Create the First Administrator Account

Before logging in to the web dashboard, create an administrator account using the secure interactive CLI command:

```powershell
.\.venv\Scripts\flask.exe create-admin
```

### Interactive Prompts:
```text
Administrator Username: admin
Password: <type hidden password>
Repeat for confirmation: <type hidden password>
Success: Administrator user 'admin' created successfully.
```
> [!NOTE]
> The password must be at least 8 characters long and contain at least one digit or special symbol. Passwords are cryptographically hashed using Werkzeug scrypt/pbkdf2 and stored securely in `admin_users` table in SQLite. No plaintext password is ever written to disk or printed to the terminal.

---

## Demonstration 1: Start the Backend Server

Start the Flask server using the centralized runner script:

```powershell
.\.venv\Scripts\python.exe run.py
```

### Expected Output
```text
============================================================
Smart Attendance Management System - Backend Server
Environment: development
Listening on: http://127.0.0.1:5000
Press Ctrl+C to stop the server.
============================================================
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
```
> [!NOTE]
> Keep this terminal window open. This is **Terminal 1** (the Server Terminal). For the remaining demonstrations, open a second PowerShell window: **Terminal 2** (the Client Terminal).

---

## Demonstration 2: Verify Server Health

In **Terminal 2**, verify that the server is responding and connected to the SQLite database:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health" -Method Get
```

### Expected Output
```json
{
  "database": "connected",
  "status": "healthy"
}
```

---

## Demonstration 3: Register a Student

Register a student named **Aarav Sharma** with roll number `CS-101` and RFID UID `A1B2C3D4`:

```powershell
$studentPayload = @{
    roll_number = "CS-101"
    full_name   = "Aarav Sharma"
    card_uid    = "A1B2C3D4"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/students" -Method Post -ContentType "application/json" -Body $studentPayload
```

### Expected Output (`HTTP 201 Created`)
```json
{
  "message": "Student registered successfully",
  "student": {
    "active": true,
    "card_uid": "A1B2C3D4",
    "full_name": "Aarav Sharma",
    "id": 1,
    "roll_number": "CS-101"
  },
  "success": true
}
```

---

## Demonstration 4: Create a Class Session

Create a scheduled class lecture for **Embedded Systems** on `2026-11-08` at `09:30`:

```powershell
$sessionPayload = @{
    course_name  = "Embedded Systems"
    session_date = "2026-11-08"
    start_time   = "09:30"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions" -Method Post -ContentType "application/json" -Body $sessionPayload
```

### Expected Output (`HTTP 201 Created`)
```json
{
  "message": "Class session created successfully",
  "session": {
    "active": false,
    "course_name": "Embedded Systems",
    "id": 1,
    "session_date": "2026-11-08",
    "start_time": "09:30"
  },
  "success": true
}
```
*(Notice that newly created sessions start as inactive (`active: false`) until activated).*

---

## Demonstration 5: Activate the Class Session

Activate Session ID `1` to begin receiving student RFID card scans:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions/1/activate" -Method Patch
```

### Expected Output (`HTTP 200 OK`)
```json
{
  "message": "Class session activated successfully",
  "session": {
    "active": true,
    "course_name": "Embedded Systems",
    "id": 1
  },
  "success": true
}
```

Verify that this session is currently active:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions/active" -Method Get
```

---

## Demonstration 6: Run the RFID Simulator

Launch the interactive RFID card scanner simulator:

```powershell
.\.venv\Scripts\python.exe scripts\simulate_scan.py
```

### Terminal Display
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

Type Aarav Sharma's card UID:
```text
RFID UID: A1B2C3D4
```

### Expected Output
```text
[ SUCCESS ] Attendance Recorded!

Student Name:      Aarav Sharma
Roll Number:       CS-101
Attendance Status: Present
Scan Source:       SIMULATOR
Course:            Embedded Systems
Timestamp (UTC):   2026-11-07T...Z
```

---

## Demonstration 7: Test Duplicate Scan Prevention

Without restarting the simulator, scan the exact same card UID again:

```text
RFID UID: A1B2C3D4
```

### Expected Output
```text
[ DUPLICATE ATTENDANCE ] Attendance has already been recorded for this student in this session.
```
> [!TIP]
> This confirms that the backend's atomic transaction lock and SQLite's `UNIQUE(student_id, session_id)` constraint actively protect the system against duplicate scans.

Close the simulator:
```text
RFID UID: EXIT
```

---

## Demonstration 8: Generate Daily Attendance Reports

Retrieve real-time attendance statistics for `2026-11-08`:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/reports/daily?date=2026-11-08" -Method Get
```

### Expected Output (`HTTP 200 OK`)
```json
{
  "date": "2026-11-08",
  "sessions": [
    {
      "absent": 0,
      "attendance_percentage": 100.0,
      "course_name": "Embedded Systems",
      "present": 1,
      "session_id": 1,
      "start_time": "09:30",
      "total_students": 1
    }
  ],
  "summary": {
    "overall_attendance_percentage": 100.0,
    "total_absent": 0,
    "total_enrollments": 1,
    "total_present": 1,
    "total_sessions": 1
  },
  "success": true
}
```

---

## Demonstration 9: Download and Inspect Attendance CSV

Download the official CSV attendance register for the session:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:5000/api/reports/export.csv?date=2026-11-08" -OutFile "attendance_2026-11-08.csv"
```

View the generated CSV in PowerShell:
```powershell
Get-Content attendance_2026-11-08.csv
```

### Expected Output
```csv
Date,Session ID,Course Name,Start Time,Roll Number,Student Name,RFID UID,Attendance Status,Scanned At (UTC),Source
2026-11-08,1,Embedded Systems,09:30,CS-101,Aarav Sharma,A1B2C3D4,Present,2026-11-07T...Z,SIMULATOR
```
You can also open `attendance_2026-11-08.csv` in Microsoft Excel or Google Sheets.

---

## Demonstration 10: Run All Automated Tests

To prove that the entire software suite meets all quality standards:

### 1. Run the Full pytest Suite (432 Tests)
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
Expected summary: `432 passed, 1 warning`.

### 2. Measure Statement Coverage
```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```
Expected summary: `>93%` statement coverage across all core application modules.

---

## Demonstration 11: Google Sheets Synchronization Worker & CLI

Phase 3 introduces asynchronous cloud synchronization using the Transactional Outbox pattern. Attendance records written to SQLite are automatically queued in `sheets_sync_queue` and exported to Google Sheets.

### 1. Configure Google Cloud Service Account (One-Time Setup)

To synchronize to a real Google Spreadsheet:
1. Create a Google Cloud Project with the **Google Sheets API** enabled.
2. Create a **Service Account** and download its JSON private key file.
3. Save the JSON key file as:
   ```text
   credentials/service_account.json
   ```
   *(Protected by `.gitignore`; never commit this file to version control).*
4. Create a target Google Sheet and share it with your Service Account's email address (e.g. `attendance-worker@project-id.iam.gserviceaccount.com`) with **Editor** permissions.
5. Copy the Spreadsheet ID from the browser URL (`https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`) and set it in your environment or `.env` file:
   ```powershell
   $env:GOOGLE_SHEETS_SPREADSHEET_ID = "your-google-spreadsheet-id"
   ```

### 2. Run the Worker in Single-Pass Batch Mode (`--once`)

For cron jobs, scheduled tasks, or automated testing, execute one batch iteration and exit cleanly:

```powershell
.\.venv\Scripts\flask.exe run-sheets-worker --once
```

#### Expected Terminal Output:
```text
[INFO] Starting Google Sheets Synchronization Worker (single-pass mode)...
[INFO] Polling sync queue (batch size: 20)...
[INFO] Successfully processed 5 record(s). Synced: 5, Failed: 0, Permanent/Dead-Letter: 0.
[INFO] Google Sheets sync worker completed cleanly.
```

### 3. Run the Worker in Continuous Polling Mode

For continuous classroom operation, run the worker daemon in a dedicated background terminal window:

```powershell
.\.venv\Scripts\flask.exe run-sheets-worker
```
- Continuously polls `sheets_sync_queue` every 5 seconds.
- Automatically claims pending rows using temporary lease locks (`lease_expires_at = 60s`).
- Implements exponential backoff retry with jitter (`2s`, `4s`, `8s` ... up to `300s`) for transient failures.
- Automatically diverts permanent errors (HTTP 401, 403, 404) directly to dead-letter status to protect Google API quotas.
- Gracefully shuts down on `Ctrl+C` (SIGINT / SIGTERM), completing the active in-flight batch before exiting.

### 4. Admin Telemetry & Dead-Letter Queue Management

Administrators can monitor sync health and recover failed records through the REST API or Web Dashboard:

#### A. Check Sync Status:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/status" -Method Get -WebSession $adminSession
```

#### B. Inspect Dead-Letter Records:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/dead-letter" -Method Get -WebSession $adminSession
```

#### C. Requeue Dead-Letter Records After Resolving Issues:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/dead-letter/retry" `
  -Method Post `
  -Headers @{ "X-CSRF-Token" = $csrfToken } `
  -WebSession $adminSession
```

---

## Production Deployment Environment Note

When deploying the application in production (`FLASK_ENV=production` or `APP_ENV=production`):
1. A cryptographically random `SECRET_KEY` environment variable is strictly required:
   ```powershell
   $env:FLASK_ENV = "production"
   $env:SECRET_KEY = "your-high-entropy-production-secret-key"
   .\.venv\Scripts\python.exe run.py
   ```
2. If `SECRET_KEY` is not provided or left as the development fallback, `ProductionConfig` will safely raise a `RuntimeError` and halt startup immediately to prevent session forgery vulnerabilities.
3. Cookies in production are strictly configured with `Secure`, `HttpOnly`, and `SameSite=Lax`.

---

## Summary of Demonstration

You have successfully demonstrated:
1. Flask backend lifecycle management.
2. SQLite health monitoring.
3. Student registration with RFID UID mapping.
4. Class session scheduling and activation.
5. Hardware scanning simulation.
6. Atomic duplicate attendance rejection.
7. Real-time statistical reporting.
8. Sanitized CSV export.
9. 100% passing automated test suite (432 tests, >93% coverage).
10. Google Sheets background synchronization worker (`--once` and continuous polling).
11. Transactional Outbox resilience, exponential retry backoff, and dead-letter recovery.
