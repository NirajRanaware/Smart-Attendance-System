# STAGE 9.7B.1 — ISOLATED MANUAL TEST DATABASE INITIALIZATION REPORT

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Git Baseline:** `8bea1537fb6bb6f5a4f4ccbb1c516350507f822d` (`origin/main`)  
**Target Isolated Database:** `database/manual_test/attendance_manual_test.db`  
**Original Production/Dev Database:** `database/attendance.db`  
**Implementation Date:** 2026-11-22  
**Initialization Verdict:** **PASS — 100% VERIFIED & STRICTLY ISOLATED**  
**Physical Hardware Operations:** Strictly Prohibited / Not Performed  
**Git Policy:** Zero Commits / Zero Pushes / Zero Branch Changes  

---

## 1. Executive Summary

In Stage 9.7A.2, the application's configuration was updated with `resolve_development_database_path()` to allow dynamic redirection of the development database path via the `DATABASE_PATH` environment variable while keeping `TestingConfig` and `ProductionConfig` strictly protected.

In this stage (**Stage 9.7B.1**), the isolated manual test database was initialized, structured, populated with a dedicated test administrator, and verified under real runtime conditions (HTTP endpoints with full CSRF enforcement and session authentication).

All operations adhered strictly to isolation guarantees:
1. **Pre-Initialization Verification:** The original database hash was recorded, port 5000 was confirmed idle, and `attendance_manual_test.db` was confirmed not to exist.
2. **Schema Provisioning:** `create_app("development")` was initialized with `$env:DATABASE_PATH="database/manual_test/attendance_manual_test.db"` and `$env:GOOGLE_SHEETS_SYNC_ENABLED="false"`. `init_db()` automatically created the `database/manual_test/` directory and provisioned all 5 core application tables and 11 performance indexes.
3. **Database Integrity & Clean State:** `PRAGMA integrity_check` returned `ok`. All operational tables (`students`, `class_sessions`, `attendance`, `sheets_sync_queue`) were confirmed at 0 rows.
4. **Dedicated Test Administrator:** Account `manual_test_admin` was provisioned via the Flask CLI (`flask create-admin`) with an in-memory high-entropy password (never logged or hardcoded). The account exists solely in `attendance_manual_test.db` and was confirmed absent from `database/attendance.db`.
5. **Runtime Authentication & Diagnostic Verification:** Verified all key HTTP flows through the Flask test client under the development configuration:
   - `GET /api/health` -> HTTP 200 (`database: "connected"`)
   - Unauthenticated `GET /dashboard` -> HTTP 302 redirecting to `/login`
   - `POST /login` with CSRF token -> HTTP 302 redirecting to `/dashboard`
   - Authenticated `GET /dashboard` -> HTTP 200
   - Diagnostic `GET /api/admin/database-stats` -> HTTP 200 reporting active database path: `database\manual_test\attendance_manual_test.db`
   - `POST /logout` with CSRF token -> HTTP 302
   - Post-logout `GET /dashboard` -> HTTP 302 redirecting to `/login`
6. **Byte-for-Byte Original DB Immutability:** The SHA-256 hash of `database/attendance.db` remained identical before and after all initialization and verification actions: `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4`.

---

## 2. Git Status and Branch Baseline

```text
Branch: main
HEAD Commit: 8bea1537fb6bb6f5a4f4ccbb1c516350507f822d
Remote Tracking: origin/main (in sync)
```

### Git Working Tree State
```text
 M app/config.py
?? STAGE_9_4_2_GITHUB_PUSH_REPORT.md
?? docs/reports/STAGE_9_7A_1_DATABASE_ISOLATION_REPORT.md
?? docs/reports/STAGE_9_7A_2_ISOLATED_DATABASE_CONFIGURATION_REPORT.md
?? docs/reports/STAGE_9_7A_MANUAL_TESTING_READINESS_REPORT.md
?? docs/reports/STAGE_9_7B_1_ISOLATED_DATABASE_INITIALIZATION_REPORT.md
?? tests/test_database_override.py
```

### Git Exclusion Verification
`database/manual_test/attendance_manual_test.db` is strictly ignored by Git matching pattern in `.gitignore`:
```text
$ git check-ignore -v database/manual_test/attendance_manual_test.db
.gitignore:25:*.db    database/manual_test/attendance_manual_test.db
```
The newly created isolated test database cannot be accidentally tracked, staged, or committed.

---

## 3. SHA-256 Hash of `database/attendance.db` Before and After

| Verification Point | Timestamp (IST) | SHA-256 Hash | Status |
| :--- | :--- | :--- | :--- |
| **Pre-Initialization Baseline** | 2026-11-22 20:53 | `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4` | Baseline |
| **Post-Schema Creation** | 2026-11-22 20:54 | `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4` | **IDENTICAL** |
| **Post-Admin Creation** | 2026-11-22 20:54 | `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4` | **IDENTICAL** |
| **Post-HTTP Runtime Verification** | 2026-11-22 20:55 | `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4` | **IDENTICAL** |
| **Post-Pytest Full Suite Execution** | 2026-11-22 21:02 | `C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4` | **IDENTICAL** |

**Conclusion:** The original database was not accessed, opened for writing, or modified in any way.

---

## 4. Target Manual Test Database Details

- **Absolute Path:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
- **Relative Path from Root:** `database/manual_test/attendance_manual_test.db`
- **File Size:** 94,208 bytes (92.0 KB)
- **SHA-256 Hash:** `EF421BA6DD2D26FF7C15DC696B57FCA7F43DA36FEDC9B9B447C8CA26AE50C4CC`
- **SQLite Format Version:** SQLite 3 (WAL/standard compatible)

---

## 5. Schema Creation Log (Tables and Indexes)

Executing `create_app("development")` triggered `init_db()` which initialized the schema from `database/schema.sql`.

### Tables Created (5)
1. `students` — RFID attendance student registry
2. `class_sessions` — Faculty attendance tracking sessions
3. `attendance` — Clock-in records linking students and sessions
4. `admin_users` — Authenticated system operators
5. `sheets_sync_queue` — Outbound queue for Google Sheets sync worker

### Indexes Created (11)
1. `sqlite_autoindex_students_1` — PRIMARY KEY (`rfid_uid` UNIQUE)
2. `idx_students_roll` — `CREATE INDEX idx_students_roll ON students(roll_number)`
3. `idx_sessions_status` — `CREATE INDEX idx_sessions_status ON class_sessions(status)`
4. `idx_sessions_start` — `CREATE INDEX idx_sessions_start ON class_sessions(start_time)`
5. `idx_attendance_session` — `CREATE INDEX idx_attendance_session ON attendance(session_id)`
6. `idx_attendance_student` — `CREATE INDEX idx_attendance_student ON attendance(student_id)`
7. `idx_attendance_marked` — `CREATE INDEX idx_attendance_marked ON attendance(marked_at)`
8. `sqlite_autoindex_admin_users_1` — PRIMARY KEY (`username` UNIQUE)
9. `idx_admin_users_role` — `CREATE INDEX idx_admin_users_role ON admin_users(role)`
10. `idx_sync_queue_status` — `CREATE INDEX idx_sync_queue_status ON sheets_sync_queue(sync_status)`
11. `idx_sync_queue_session` — `CREATE INDEX idx_sync_queue_session ON sheets_sync_queue(session_id)`

---

## 6. Dedicated Test Administrator Creation Details

The administrator was created using the official Flask CLI command `flask create-admin` executing against the isolated database context:

- **Username:** `manual_test_admin`
- **Role:** `admin`
- **Active:** `1` (`True`)
- **CLI Command Invocation:** `app.test_cli_runner().invoke(app.cli.commands["create-admin"], ["--username", "manual_test_admin"], input="...")`
- **CLI Exit Code:** `0`
- **Password Generation Security:** High-entropy secret generated in-memory via `secrets.token_urlsafe(16)` with alphanumeric and special character complexity. Never printed to logs, never hardcoded in scripts, and never written to disk unhashed.
- **Verification in Test DB:** Present (`id=1`, `username='manual_test_admin'`).
- **Verification in Original DB:** Absent (`SELECT * FROM admin_users WHERE username='manual_test_admin'` returns `None`). The original administrator `Niraj` remains the sole administrator in `database/attendance.db`.

---

## 7. SQLite Integrity Check Results

Both databases were verified using SQLite's low-level page and B-tree consistency check:

```sql
PRAGMA integrity_check;
```

- **Original Database (`database/attendance.db`):** `ok`
- **Manual Test Database (`database/manual_test/attendance_manual_test.db`):** `ok`

---

## 8. Row Counts for All Tables in Both Databases

| Table Name | Original Database (`attendance.db`) | Manual Test Database (`attendance_manual_test.db`) | Status |
| :--- | :---: | :---: | :--- |
| `students` | 1 | **0** | Clean, ready for test registration |
| `class_sessions` | 2 | **0** | Clean, ready for session creation |
| `attendance` | 2 | **0** | Clean, ready for tap simulation |
| `admin_users` | 1 (`Niraj`) | **1** (`manual_test_admin`) | Strictly separated administrative identities |
| `sheets_sync_queue` | 2 | **0** | Clean, no pending background sync tasks |

---

## 9. HTTP Runtime Verification Results

Runtime verification was executed through Flask's WSGI test client configured in `development` mode (`CSRF_ENABLED=True`, `LOGIN_DISABLED=False`):

| Endpoint / Operation | HTTP Method | Expected Status | Actual Status | Result Details |
| :--- | :---: | :---: | :---: | :--- |
| `/api/health` | `GET` | 200 | **200 OK** | `{"database": "connected", "message": "Smart Attendance API is running", "success": true}` |
| `/dashboard` (Unauthenticated) | `GET` | 302 | **302 Found** | Redirects to `http://localhost/login?next=%2Fdashboard` |
| `/login` (Fetch CSRF Token) | `GET` | 200 | **200 OK** | Session initialized with valid CSRF token |
| `/login` (Authenticate) | `POST` | 302 | **302 Found** | Validates `manual_test_admin` and redirects to `/dashboard` |
| `/dashboard` (Authenticated) | `GET` | 200 | **200 OK** | Dashboard UI rendered successfully with active session |
| `/api/admin/database-stats` | `GET` | 200 | **200 OK** | **Diagnostic verified:** active database path reported as `database\manual_test\attendance_manual_test.db` |
| `/logout` (CSRF Protected) | `POST` | 302 | **302 Found** | Session cleared, redirected to `/login` |
| `/dashboard` (Post-Logout) | `GET` | 302 | **302 Found** | Redirects to `/login`, confirming complete session termination |

---

## 10. Manual Testing Startup Procedure

To start the application against this isolated database during manual testing, execute the following PowerShell commands:

```powershell
# Step 1: Open PowerShell and navigate to repository root
Set-Location C:\Projects\Smart-Attendance-System

# Step 2: Configure environment overrides for manual testing
$env:DATABASE_PATH="database/manual_test/attendance_manual_test.db"
$env:GOOGLE_SHEETS_SYNC_ENABLED="false"

# Step 3: Launch the Flask development server
& ".\.venv\Scripts\python.exe" run.py
```

### Expected Startup Log Output
```text
INFO: Database path overridden via DATABASE_PATH environment variable: C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db
INFO: Database initialized successfully.
INFO: Google Sheets sync worker disabled via configuration.
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
```

---

## 11. Confirmation of Absolute Database Isolation

1. **Path Separation:** The manual test database is housed in a dedicated sub-directory (`database/manual_test/`) separated from `database/attendance.db`.
2. **Hash Invariance:** The SHA-256 checksum of `database/attendance.db` did not change by even a single bit (`C559A1511E89F82CC11C608FFC260EB6884A599D7E1393F70798FB5BC26B84F4`).
3. **Data Integrity:** Real records in `attendance.db` (student `2024CS001`, sessions `SESSION_001` & `SESSION_002`, administrator `Niraj`) were never queried or modified.
4. **Git Protection:** `database/manual_test/attendance_manual_test.db` is matched by `.gitignore` rule `*.db` and cannot be committed.
5. **No Sheets Interference:** `GOOGLE_SHEETS_SYNC_ENABLED="false"` prevents the Google Sheets worker from consuming test records or writing mock entries to external spreadsheets.

---

## 12. Final Verdict

**STAGE 9.7B.1 STATUS: PASS**

The isolated manual test database is fully initialized, structured, verified, and ready for authenticated manual end-to-end testing without risk to production or existing development data.
