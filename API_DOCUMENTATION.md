# REST API Documentation

**Project:** IoT-Based Smart Attendance Management System  
**Base URL:** `http://127.0.0.1:5000`  
**Protocol:** HTTP/1.1  
**Default Content-Type:** `application/json`  
**Time Standard:** UTC (ISO 8601 strings ending in `Z`, e.g. `2026-11-07T10:00:00Z`)  
**Total Implemented Endpoints:** 20

This document provides exhaustive technical specifications, request/response schemas, PowerShell invocation examples, and error-handling contracts for all **20 implemented REST API endpoints** in the Smart Attendance Management System backend.

---

## Authentication & Authorization Model

Beginning in Phase 2 Milestone 7, the application implements **Administrator Session Authentication** and **Cross-Site Request Forgery (CSRF) Protection**:

1. **Public Endpoints:**
   - `GET /api/health`: Always accessible without authentication for infrastructure and uptime monitoring.
   - `POST /login`: Accessible to authenticate administrator credentials and establish a secure session.

2. **Protected Administrative Endpoints:**
   - All student, session, attendance, report, and export endpoints require an active administrator session cookie (`session`).
   - Unauthenticated API requests receive `HTTP 401 Unauthorized` with a standard JSON error response:
     ```json
     {
       "success": false,
       "error": "Unauthorized",
       "message": "Administrator authentication is required to access this endpoint."
     }
     ```

3. **CSRF Protection:**
   - State-changing browser requests (`POST`, `PATCH`, `PUT`, `DELETE`) require a valid `X-CSRF-Token` header matching the active session token.
   - The token is accessible from the `<meta name="csrf-token">` tag or `csrf_token()` template helper.

4. **Future Hardware (ESP32) Authentication Boundary:**
   - `POST /api/scans` currently accepts scans within an authenticated administrator session (such as the web browser RFID simulator).
   - In production deployment with physical ESP32 microcontrollers, device-to-backend authentication will utilize a dedicated hardware credential mechanism (such as an `X-Device-Token` header or mTLS certificate) before physical hardware goes live. Hardware secrets must never be exposed to browser JavaScript.

---

## Table of Implemented Endpoints (20 Total)

| # | Category | Method | Endpoint | Purpose |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **System Health** | `GET` | `/api/health` | Check API and SQLite database connectivity status (Public). |
| **2** | **Authentication** | `POST` | `/login` | Authenticate administrator credentials and initialize session. |
| **3** | | `POST` | `/logout` | Invalidate administrator session (requires CSRF token). |
| **4** | | `GET` | `/api/auth/status` | Check current administrator authentication state. |
| **5** | **Students** | `POST` | `/api/students` | Register a new student with a unique roll number and RFID UID. |
| **6** | | `GET` | `/api/students` | Retrieve all registered students sorted by roll number. |
| **7** | **Class Sessions** | `POST` | `/api/sessions` | Create a new class lecture session (inactive by default). |
| **8** | | `GET` | `/api/sessions` | Retrieve all class sessions sorted chronologically. |
| **9** | | `GET` | `/api/sessions/active` | Retrieve the currently active class session (if any). |
| **10** | | `PATCH` | `/api/sessions/<id>/activate` | Activate a class session (deactivates any previous active session). |
| **11** | | `PATCH` | `/api/sessions/<id>/deactivate` | Deactivate an active class session. |
| **12** | **Attendance** | `POST` | `/api/scans` | Record an RFID scan event against the active class session. |
| **13** | | `GET` | `/api/attendance` | Retrieve all attendance records for a specific date (`YYYY-MM-DD`). |
| **14** | **Reports** | `GET` | `/api/reports/daily` | Retrieve daily attendance statistics (present, absent, percentage). |
| **15** | | `GET` | `/api/reports/export.csv` | Download a sanitized CSV attendance register for a specific date. |
| **16** | **Administration** | `GET` | `/api/admin/database-stats` | Retrieve live database record counts across all operational tables. |
| **17** | **Cloud Sync** | `GET` | `/api/admin/sheets-sync/status` | Retrieve Google Sheets sync queue telemetry and credential status. |
| **18** | | `GET` | `/api/admin/sheets-sync/dead-letter` | Retrieve paginated dead-letter records with failure reasons. |
| **19** | | `POST` | `/api/admin/sheets-sync/dead-letter/retry` | Requeue dead-letter records back to pending (bulk or selective). |
| **20** | **Maintenance** | `POST` | `/api/admin/clear-data` | Safely clear operational test data after verified online ACID backup. |

---

## 1. System Health

### `GET /api/health`
Checks whether the Flask application is running and actively communicating with the SQLite database file.

- **Method:** `GET`
- **URL:** `/api/health`
- **Headers:** None required
- **Query Parameters:** None

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/health" -Method Get
```

#### Successful Response (`200 OK`)
```json
{
  "database": "connected",
  "status": "healthy"
}
```

#### Error Response (`503 Service Unavailable`)
Returned if the database file is inaccessible or locked.
```json
{
  "database": "disconnected",
  "error": "Database Unavailable",
  "status": "unhealthy"
}
```

---

## 2. Students API

### `POST /api/students`
Registers a new student. Enforces unique roll numbers and unique RFID card UIDs.

- **Method:** `POST`
- **URL:** `/api/students`
- **Headers:** `Content-Type: application/json`
- **Request Body Fields:**
  - `roll_number` *(string, required)*: Max 50 characters. Must be unique.
  - `full_name` *(string, required)*: 2 to 100 characters.
  - `card_uid` *(string, required)*: 4 to 50 characters. Alphanumeric, underscores, dashes (`^[A-Z0-9_-]+$`). Normalized to uppercase. Must be unique.

#### Example Request
```powershell
$body = @{
    roll_number = "101"
    full_name   = "Rahul Sharma"
    card_uid    = "A1B2C3D4"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/students" -Method Post -ContentType "application/json" -Body $body
```

#### Successful Response (`201 Created`)
```json
{
  "success": true,
  "message": "Student registered successfully",
  "student": {
    "id": 1,
    "roll_number": "101",
    "full_name": "Rahul Sharma",
    "card_uid": "A1B2C3D4",
    "active": true
  }
}
```

#### Error Responses
- **`400 Bad Request`** (Validation failure or missing fields):
  ```json
  {
    "success": false,
    "error": "Bad Request",
    "message": "Field 'card_uid' must contain only alphanumeric characters, dashes, or underscores."
  }
  ```
- **`409 Conflict`** (Duplicate roll number or RFID UID):
  ```json
  {
    "success": false,
    "error": "Conflict",
    "message": "A student with roll number '101' already exists."
  }
  ```

---

### `GET /api/students`
Retrieves all registered students sorted by roll number ascending.

- **Method:** `GET`
- **URL:** `/api/students`
- **Query Parameters:** None

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/students" -Method Get
```

#### Successful Response (`200 OK`)
```json
{
  "count": 2,
  "students": [
    {
      "id": 1,
      "roll_number": "101",
      "full_name": "Rahul Sharma",
      "card_uid": "A1B2C3D4",
      "active": true,
      "created_at": "2026-11-07T10:00:00"
    },
    {
      "id": 2,
      "roll_number": "102",
      "full_name": "Priya Patel",
      "card_uid": "E5F6A7B8",
      "active": true,
      "created_at": "2026-11-07T10:05:00"
    }
  ],
  "success": true
}
```

---

## 3. Class Sessions API

### `POST /api/sessions`
Creates a new scheduled class lecture session. Newly created sessions are inactive (`active: false`) by default.

- **Method:** `POST`
- **URL:** `/api/sessions`
- **Headers:** `Content-Type: application/json`
- **Request Body Fields:**
  - `course_name` *(string, required)*: 2 to 100 characters.
  - `session_date` *(string, required)*: Valid calendar date in `YYYY-MM-DD` format.
  - `start_time` *(string, required)*: Valid 24-hour time in `HH:MM` format (00:00 to 23:59).

#### Example Request
```powershell
$body = @{
    course_name  = "CS101 - Introduction to Programming"
    session_date = "2026-11-07"
    start_time   = "10:00"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions" -Method Post -ContentType "application/json" -Body $body
```

#### Successful Response (`201 Created`)
```json
{
  "message": "Class session created successfully",
  "session": {
    "active": false,
    "course_name": "CS101 - Introduction to Programming",
    "id": 1,
    "session_date": "2026-11-07",
    "start_time": "10:00"
  },
  "success": true
}
```

#### Error Response (`400 Bad Request`)
```json
{
  "error": "Bad Request",
  "message": "'2026-02-30' is not a valid calendar date.",
  "success": false
}
```

---

### `GET /api/sessions`
Retrieves all class sessions sorted chronologically by session date ascending, then start time ascending.

- **Method:** `GET`
- **URL:** `/api/sessions`

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions" -Method Get
```

#### Successful Response (`200 OK`)
```json
{
  "count": 1,
  "sessions": [
    {
      "active": false,
      "course_name": "CS101 - Introduction to Programming",
      "created_at": "2026-11-07T10:00:00Z",
      "id": 1,
      "session_date": "2026-11-07",
      "start_time": "10:00"
    }
  ],
  "success": true
}
```

---

### `GET /api/sessions/active`
Retrieves the currently active class session that is ready to accept RFID card scans.

- **Method:** `GET`
- **URL:** `/api/sessions/active`

#### Successful Response when Active Session Exists (`200 OK`)
```json
{
  "session": {
    "active": true,
    "course_name": "CS101 - Introduction to Programming",
    "id": 1,
    "session_date": "2026-11-07",
    "start_time": "10:00"
  },
  "success": true
}
```

#### Successful Response when No Session is Active (`200 OK`)
```json
{
  "message": "No active class session",
  "session": null,
  "success": true
}
```

---

### `PATCH /api/sessions/<id>/activate`
Activates the specified session. Employs atomic `BEGIN IMMEDIATE` transaction locking to automatically deactivate any previously active session, guaranteeing that only one session is active across the system.

- **Method:** `PATCH`
- **URL:** `/api/sessions/<int:session_id>/activate`

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions/1/activate" -Method Patch
```

#### Successful Response (`200 OK`)
```json
{
  "message": "Class session activated successfully",
  "session": {
    "active": true,
    "course_name": "CS101 - Introduction to Programming",
    "id": 1
  },
  "success": true
}
```

#### Error Responses
- **`404 Not Found`**:
  ```json
  {
    "error": "Not Found",
    "message": "Class session with ID 999 does not exist.",
    "success": false
  }
  ```

---

### `PATCH /api/sessions/<id>/deactivate`
Deactivates the specified class session. Once deactivated, subsequent card scans will be rejected until another session is activated.

- **Method:** `PATCH`
- **URL:** `/api/sessions/<int:session_id>/deactivate`

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/sessions/1/deactivate" -Method Patch
```

#### Successful Response (`200 OK`)
```json
{
  "message": "Class session deactivated successfully",
  "session": {
    "active": false,
    "course_name": "CS101 - Introduction to Programming",
    "id": 1
  },
  "success": true
}
```

---

## 4. Attendance API

### `POST /api/scans`
Receives an RFID scan event, validates student registration and active session status, prevents duplicate attendance, and records an official attendance record.

- **Method:** `POST`
- **URL:** `/api/scans`
- **Headers:** `Content-Type: application/json`
- **Request Body Fields:**
  - `card_uid` *(string, required)*: The RFID card's hardware UID.
  - `source` *(string, required)*: The scanning client identifier (e.g. `"ESP32"` or `"SIMULATOR"`).

> [!IMPORTANT]
> Clients must **not** provide an attendance timestamp. The backend generates the official server UTC timestamp (`scanned_at`) during insertion to prevent client clock tampering.

#### Example Request
```powershell
$body = @{
    card_uid = "A1B2C3D4"
    source   = "SIMULATOR"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/scans" -Method Post -ContentType "application/json" -Body $body
```

#### Successful Response (`201 Created`)
```json
{
  "attendance": {
    "course_name": "CS101 - Introduction to Programming",
    "id": 1,
    "roll_number": "101",
    "scanned_at": "2026-11-07T10:05:00Z",
    "session_id": 1,
    "source": "SIMULATOR",
    "status": "Present",
    "student_name": "Rahul Sharma"
  },
  "message": "Attendance recorded successfully",
  "success": true
}
```

#### Error Responses
- **`404 Not Found`** (Unregistered RFID card):
  ```json
  {
    "error": "Student Not Found",
    "message": "This RFID card is not registered.",
    "success": false
  }
  ```
- **`403 Forbidden`** (Student account is inactive/withdrawn):
  ```json
  {
    "error": "Inactive Student",
    "message": "This student account is inactive.",
    "success": false
  }
  ```
- **`409 Conflict`** (No session active):
  ```json
  {
    "error": "No Active Session",
    "message": "No class session is currently active. Attendance cannot be recorded.",
    "success": false
  }
  ```
- **`409 Conflict`** (Duplicate attendance attempt):
  ```json
  {
    "error": "Duplicate Attendance",
    "message": "Attendance has already been recorded for this student in this session.",
    "success": false
  }
  ```

---

### `GET /api/attendance`
Retrieves all attendance records for class sessions scheduled on a given calendar date.

- **Method:** `GET`
- **URL:** `/api/attendance?date=YYYY-MM-DD`
- **Query Parameters:**
  - `date` *(string, required)*: Target calendar date in `YYYY-MM-DD` format.

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/attendance?date=2026-11-07" -Method Get
```

#### Successful Response (`200 OK`)
```json
{
  "attendance": [
    {
      "course_name": "CS101 - Introduction to Programming",
      "id": 1,
      "roll_number": "101",
      "scanned_at": "2026-11-07T10:05:00Z",
      "session_date": "2026-11-07",
      "session_id": 1,
      "source": "SIMULATOR",
      "start_time": "10:00",
      "status": "Present",
      "student_name": "Rahul Sharma"
    }
  ],
  "count": 1,
  "date": "2026-11-07",
  "success": true
}
```

---

## 5. Reports & CSV Export API

### `GET /api/reports/daily`
Generates comprehensive attendance statistics per session and overall across all class sessions scheduled on the target date.

- **Method:** `GET`
- **URL:** `/api/reports/daily?date=YYYY-MM-DD`
- **Query Parameters:**
  - `date` *(string, required)*: Target calendar date in `YYYY-MM-DD` format.

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/reports/daily?date=2026-11-07" -Method Get
```

#### Successful Response (`200 OK`)
```json
{
  "date": "2026-11-07",
  "sessions": [
    {
      "absent": 1,
      "attendance_percentage": 50.0,
      "course_name": "CS101 - Introduction to Programming",
      "present": 1,
      "session_id": 1,
      "start_time": "10:00",
      "total_students": 2
    }
  ],
  "summary": {
    "overall_attendance_percentage": 50.0,
    "total_absent": 1,
    "total_enrollments": 2,
    "total_present": 1,
    "total_sessions": 1
  },
  "success": true
}
```

---

### `GET /api/reports/export.csv`
Generates a downloadable CSV attendance register. Performs a `LEFT JOIN` between all active registered students and attendance records to list both **Present** and **Absent** students.

- **Method:** `GET`
- **URL:** `/api/reports/export.csv?date=YYYY-MM-DD`
- **Query Parameters:**
  - `date` *(string, required)*: Target calendar date in `YYYY-MM-DD` format.
- **Response Content-Type:** `text/csv; charset=utf-8`
- **Content-Disposition:** `attachment; filename="attendance_YYYY-MM-DD.csv"`

#### Security Feature (CSV Formula Injection Mitigation)
All user-controlled fields (`student_name`, `roll_number`, `card_uid`, `course_name`, `source`) are checked against CSV formula injection (CWE-1236). Values starting with `=`, `+`, `-`, `@`, `\t`, or `\r` are prepended with a single quote (`'`), ensuring spreadsheet software renders them as safe text rather than executing formulas.

#### Example Request
```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:5000/api/reports/export.csv?date=2026-11-07" -OutFile "attendance_2026-11-07.csv"
```

#### Example CSV Output Format
```csv
Date,Session ID,Course Name,Start Time,Roll Number,Student Name,RFID UID,Attendance Status,Scanned At (UTC),Source
2026-11-07,1,CS101 - Introduction to Programming,10:00,101,Rahul Sharma,A1B2C3D4,Present,2026-11-07T10:05:00Z,SIMULATOR
2026-11-07,1,CS101 - Introduction to Programming,10:00,102,Priya Patel,E5F6A7B8,Absent,,
```
*(Notice that absent students are clearly included with blank timestamp and source columns).*

---

## 6. Administration & Cloud Synchronization API

All administrative and synchronization endpoints require an authenticated administrator session (`@login_required`). State-changing requests (`POST`) also require a valid `X-CSRF-Token` header.

---

### `GET /api/admin/database-stats`
Returns live, dynamic counts of all records currently stored in the SQLite database. Also accessible via alias `GET /api/admin/stats`.

- **Method:** `GET`
- **URL:** `/api/admin/database-stats`
- **Security:** Requires administrator authentication (`@login_required`).

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/database-stats" -Method Get -WebSession $adminSession
```

#### Successful Response (`200 OK`)
```json
{
  "data": {
    "active_students": 45,
    "admins": 1,
    "attendance_records": 128,
    "class_sessions": 6,
    "database_size_bytes": 49152,
    "sheets_sync_queue": 128,
    "total_students": 45
  },
  "message": "Database statistics retrieved successfully.",
  "success": true
}
```

---

### `GET /api/admin/sheets-sync/status`
Returns live synchronization telemetry, queue counters partitioned by status, Google Cloud configuration presence flags, and the timestamp of the most recent successful sync.

- **Method:** `GET`
- **URL:** `/api/admin/sheets-sync/status`
- **Security:** Requires administrator authentication (`@login_required`).
- **Data Privacy Guarantee:** Strictly returns boolean configuration flags (`credentials_configured`, `spreadsheet_id_configured`). Never exposes local filesystem paths, client secrets, private keys, or API tokens.

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/status" -Method Get -WebSession $adminSession
```

#### Successful Response (`200 OK`)
```json
{
  "credentials_configured": true,
  "last_sync_timestamp": "2026-11-08T14:30:15Z",
  "queue_stats": {
    "dead_letter": 0,
    "failed": 0,
    "in_progress": 0,
    "pending": 2,
    "synced": 126,
    "total": 128
  },
  "spreadsheet_id_configured": true,
  "success": true,
  "sync_enabled": true
}
```

---

### `GET /api/admin/sheets-sync/dead-letter`
Retrieves a paginated list of synchronization records that exceeded the maximum retry threshold (5 attempts) or encountered unrecoverable permanent errors (e.g. HTTP 403 Forbidden, HTTP 404 Not Found). Includes student and session metadata to assist administrator troubleshooting.

- **Method:** `GET`
- **URL:** `/api/admin/sheets-sync/dead-letter?limit=50&offset=0`
- **Security:** Requires administrator authentication (`@login_required`).
- **Query Parameters:**
  - `limit` *(integer, optional, default: 50, min: 1, max: 200)*: Number of records to return per page.
  - `offset` *(integer, optional, default: 0, min: 0)*: Number of records to skip for pagination.

#### Example Request
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/dead-letter?limit=10&offset=0" -Method Get -WebSession $adminSession
```

#### Successful Response (`200 OK`)
```json
{
  "limit": 10,
  "message": "Retrieved 1 dead-letter record(s).",
  "offset": 0,
  "records": [
    {
      "attendance_id": 42,
      "course_name": "CS101 - Introduction to Programming",
      "created_at": "2026-11-08T10:00:00Z",
      "error_category": "PERMANENT",
      "id": 42,
      "last_error": "HTTP 403 Forbidden: Google Sheets service account lacks edit permissions on spreadsheet.",
      "retry_count": 0,
      "roll_number": "101",
      "scanned_at": "2026-11-08T10:05:00Z",
      "session_date": "2026-11-08",
      "session_id": 1,
      "source": "ESP32",
      "student_name": "Rahul Sharma",
      "sync_status": "dead_letter"
    }
  ],
  "success": true,
  "total_dead_letter": 1
}
```

---

### `POST /api/admin/sheets-sync/dead-letter/retry`
Requeues dead-letter synchronization records back to `pending` status so the background worker can re-process them after the underlying configuration or permission issue has been resolved.

- **Method:** `POST`
- **URL:** `/api/admin/sheets-sync/dead-letter/retry`
- **Security:** Requires administrator authentication (`@login_required`) and CSRF validation header (`X-CSRF-Token`).
- **Request Body (JSON, Optional):**
  - To requeue **all** dead-letter records: Omit the `queue_ids` field entirely or supply `{}`.
  - To requeue **specific** records: Provide an array of integer queue IDs: `{"queue_ids": [42, 43]}`.
  - Supplying an empty list `{"queue_ids": []}` is rejected with `HTTP 400 Bad Request` to prevent accidental bulk requeues.

#### Example Request (Selective Requeue)
```powershell
$body = @{ queue_ids = @(42) } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/sheets-sync/dead-letter/retry" `
  -Method Post `
  -Headers @{ "X-CSRF-Token" = $csrfToken } `
  -ContentType "application/json" `
  -Body $body `
  -WebSession $adminSession
```

#### Successful Response (`200 OK`)
```json
{
  "message": "Successfully requeued 1 dead-letter record(s) for synchronization.",
  "requeued_count": 1,
  "requeued_ids": [42],
  "success": true
}
```

#### Validation Error Responses (`400 Bad Request`)
- **Empty `queue_ids` array:**
  ```json
  {
    "error": "Invalid Payload",
    "message": "'queue_ids' must not be empty. Omit 'queue_ids' to requeue all records, or provide specific IDs.",
    "success": false
  }
  ```
- **Non-integer element:**
  ```json
  {
    "error": "Invalid Payload",
    "message": "All elements in 'queue_ids' must be integers.",
    "success": false
  }
  ```

---

### `POST /api/admin/clear-data`
Safely purges operational test data (attendance records, class sessions, and student enrollments) after creating and verifying an ACID-consistent SQLite online backup. Also accessible via alias `POST /api/admin/clear-test-data`.

- **Method:** `POST`
- **URL:** `/api/admin/clear-data`
- **Security:** Requires administrator authentication (`@login_required`) and CSRF validation header (`X-CSRF-Token`).
- **Safety Protections:**
  1. Requires explicit JSON field `"confirmation_phrase": "DELETE ALL DATA"`.
  2. Executes an online SQLite backup (`VACUUM INTO` or SQLite Backup API) into the configured `backups/` directory before any deletion begins.
  3. Verifies backup integrity with `PRAGMA integrity_check` before proceeding. If backup or verification fails, aborts with no data touched.
  4. Preserves administrator accounts, credentials, schema tables, and indexes intact.
  5. Executes within an immediate database transaction (`BEGIN IMMEDIATE`).

#### Example Request
```powershell
$body = @{ confirmation_phrase = "DELETE ALL DATA" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/admin/clear-data" `
  -Method Post `
  -Headers @{ "X-CSRF-Token" = $csrfToken } `
  -ContentType "application/json" `
  -Body $body `
  -WebSession $adminSession
```

#### Successful Response (`200 OK`)
```json
{
  "backup_file": "backups/attendance_backup_20261108_143500.db",
  "deleted_attendance": 128,
  "deleted_sessions": 6,
  "deleted_students": 45,
  "message": "All operational test data has been safely cleared. Backup verified.",
  "success": true
}
```

#### Error Response (`400 Bad Request`)
Returned if `confirmation_phrase` is omitted or does not match exactly:
```json
{
  "error": "Invalid Confirmation Phrase",
  "message": "Invalid confirmation phrase. You must type exactly 'DELETE ALL DATA' to proceed.",
  "success": false
}
```
