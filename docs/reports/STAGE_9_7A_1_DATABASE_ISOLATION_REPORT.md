# STAGE 9.7A.1 — ISOLATED TEST DATABASE VERIFICATION REPORT
**Feasibility Audit of Database Path Overrides, WAL Safety, and Google Sheets Decoupling**

**Project:** IoT-Based Smart Attendance Management System  
**Workspace:** `C:\Projects\Smart-Attendance-System`  
**Git Baseline:** `8bea1537fb6bb6f5a4f4ccbb1c516350507f822d` (`origin/main`)  
**Audit Execution Date:** 2026-11-22  
**Audit Mode:** Strict Read-Only Feasibility Audit (Zero Source Modifications, Zero Database Writes)  
**Final Decision:** **BLOCKED: Source-code or configuration changes are required before manual testing.**

---

## 1. Executive Summary

This feasibility audit investigated whether the Smart Attendance Management System can be executed against a completely isolated test database located at:
`database/manual_test/attendance_manual_test.db`
without modifying the original primary database (`database/attendance.db`) and without modifying any application source code.

### Summary of Critical Findings:
1. **Hardcoded Database Path in Development (BLOCKED):** `run.py` explicitly hardcodes `app = create_app("development")`, which loads `DevelopmentConfig`. In `DevelopmentConfig`, `DATABASE_PATH` is a static class attribute hardcoded to `DATABASE_DIR / "attendance.db"`. It does **not** check `os.environ` for any override variable.
2. **TestingConfig Is Unsuitable for Manual Testing (BLOCKED):** `TestingConfig` targets `test_attendance.db` (not `database/manual_test/attendance_manual_test.db`), but it explicitly sets `LOGIN_DISABLED = True` and `CSRF_ENABLED = False`. If run under `TestingConfig`, all administrative route protection and CSRF validation are bypassed, making it impossible to manually test user login, logout, session expiration, and authentication security.
3. **No CLI or Environment Override Currently Exists (BLOCKED):** Across all application files, no environment variable (such as `DATABASE_PATH` or `SQLITE_PATH`) is queried to override SQLite storage. Setting an environment variable currently has zero effect on the runtime database connection.
4. **SQLite Journal Mode & Online Backup Safety (VERIFIED):** The current active database uses `delete` journal mode with `synchronous = 2` (`FULL`), and integrity check passes. SQLite's online backup API (`source_conn.backup(target_conn)`, as implemented in `app/backup.py`) is proven to be the transaction-consistent method for creating snapshot copies without torn pages or WAL corruption.
5. **Google Sheets Safety & Decoupling (VERIFIED):** Cloud synchronization is fully decoupled through an SQLite outbox queue (`sheets_sync_queue`). Attendance scanning never invokes Google APIs in the HTTP request path. Setting `GOOGLE_SHEETS_SYNC_ENABLED="false"` guarantees fail-closed isolation during testing.
6. **Final Decision (BLOCKED):** Because no existing mechanism allows redirecting `run.py` to `database/manual_test/attendance_manual_test.db` without modifying source code, isolated manual testing is **BLOCKED** pending a minimal, controlled configuration update.

---

## 2. Current Database Configuration

The application defines its configurations in `app/config.py`:

```python
# Root directory and directory constants (app/config.py:12-18)
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_DIR = BASE_DIR / "database"
BACKUP_DIR = BASE_DIR / "backups"
CREDENTIALS_DIR = BASE_DIR / "credentials"

class Config:
    """Base configuration settings."""
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-smart-attendance-2026")
    DATABASE_PATH = DATABASE_DIR / "attendance.db"
    ...

class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True
    DATABASE_PATH = DATABASE_DIR / "attendance.db"
    SESSION_COOKIE_SECURE = False

class TestingConfig(Config):
    """Testing environment configuration using an isolated test database."""
    TESTING = True
    DEBUG = True
    DATABASE_PATH = DATABASE_DIR / "test_attendance.db"
    LOGIN_DISABLED = True
    CSRF_ENABLED = False
    WTF_CSRF_ENABLED = False
```

### Inspected Active State:
- `run.py` (Line 7):
  ```python
  app = create_app("development")
  ```
- Because `"development"` is explicitly passed as an argument to `create_app()`, `CONFIG_MAP["development"]` is returned, binding `DATABASE_PATH` to `C:\Projects\Smart-Attendance-System\database\attendance.db`.

---

## 3. Actual Database Path Resolution Mechanism

The application connects to SQLite through `app/database.py`:

```python
def get_db_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_config().DATABASE_PATH
    path_obj = Path(db_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    ...

def get_db(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    try:
        from flask import g, current_app, has_app_context
        if has_app_context():
            if "db" not in g:
                target_path = db_path or current_app.config.get("DATABASE_PATH")
                g.db = get_db_connection(target_path)
            return g.db
    except ImportError:
        pass
    return get_db_connection(db_path)
```

### Resolution Flow Analysis:
1. Every web route (`POST /login`, `GET /students`, `POST /api/students`, `POST /api/sessions`, `PATCH /api/sessions/<id>/activate`, `POST /api/scans`, `GET /api/reports/daily`) calls `get_db()`.
2. Inside the Flask request context, `get_db()` reads `current_app.config.get("DATABASE_PATH")`.
3. `current_app.config["DATABASE_PATH"]` is populated during `create_app()` by `app.config.from_object(config_class)`.
4. Because `run.py` passes `"development"`, `current_app.config["DATABASE_PATH"]` is permanently set to `database/attendance.db`.
5. No helper or decorator dynamically inspects request headers or environment variables to redirect queries to any other database file.

---

## 4. TestingConfig Security Limitations

The existing `TestingConfig` cannot be substituted for manual testing due to three deliberate architectural configurations:

| Parameter in `TestingConfig` | Value | Impact on Manual Testing | Status |
| :--- | :--- | :--- | :--- |
| `DATABASE_PATH` | `database/test_attendance.db` | Targets `test_attendance.db`, NOT `manual_test/attendance_manual_test.db`. | **UNSUITABLE** |
| `LOGIN_DISABLED` | `True` | **Completely bypasses authentication.** The `@login_required` decorator immediately executes the protected route. The login screen (`/login`) is bypassed and cannot be tested. | **CRITICAL LIMITATION** |
| `CSRF_ENABLED` / `WTF_CSRF_ENABLED` | `False` | **Completely disables CSRF protection.** CSRF token verification middleware exits immediately. Simulated scans and form submissions are not tested against real security middleware. | **CRITICAL LIMITATION** |
| `admin_users` table content | 0 rows | No administrative accounts exist in `test_attendance.db`. | **BLOCKED** |

**Conclusion:** `TestingConfig` is designed strictly for headless automated unit regression tests (where mock authentication is convenient). It is completely inappropriate for manual verification of real user authentication, session security, and CSRF protection.

---

## 5. Evaluation of Isolation Options (Gate B)

We evaluated the four options specified in Gate B in order:

### Option 1: Existing Supported Database-Path Environment Variable
- **Code Search:** Scanned all occurrences of `os.environ` across the entire codebase (`app/config.py`, `app/__init__.py`, `app/database.py`, `app/cli.py`, `run.py`).
- **Result:** Found 14 environment variable readers (`DEVICE_API_KEYS`, `SECRET_KEY`, `DEFAULT_TIMEZONE`, `SESSION_COOKIE_SECURE`, `GOOGLE_SHEETS_*`, `FLASK_ENV`, `APP_ENV`, `WERKZEUG_RUN_MAIN`).
- **Finding:** **ZERO** code references `os.environ.get("DATABASE_PATH")`. Setting `$env:DATABASE_PATH` has no effect.
- **Classification:** **NOT VERIFIED / NOT SUPPORTED**.

### Option 2: Existing Configuration Override Without Modifying Source Files
- **Code Inspection:** Evaluated whether an external `.env` or configuration file is loaded.
- **Result:** Neither `python-dotenv` nor any external YAML/JSON configuration loader is implemented in `run.py` or `create_app()`.
- **Finding:** Even if a `.env` file were created, `app/config.py` does not read `DATABASE_PATH` from it.
- **Classification:** **NOT VERIFIED / NOT SUPPORTED**.

### Option 3: Existing Supported CLI Configuration
- **Code Inspection:** Checked Flask CLI commands in `app/cli.py` (`flask create-admin`, `flask run-sheets-worker`).
- **Result:** When running `flask run` with `FLASK_APP=run.py`, it imports `app` from `run.py` (`create_app("development")`).
- **Alternative:** Running `flask --app "app:create_app()"` defaults `config_name` to `None`, which reads `FLASK_ENV`. If `FLASK_ENV=development` (or unset), it loads `DevelopmentConfig` (which hardcodes `attendance.db`). If `FLASK_ENV=testing`, it loads `TestingConfig` (which disables login and CSRF).
- **Finding:** No CLI option allows passing an arbitrary database path into `run.py` or Flask CLI.
- **Classification:** **NOT VERIFIED / NOT SUPPORTED**.

### Option 4: Source-Code Changes Required
- **Result:** Because Options 1, 2, and 3 are unsupported in the current codebase, the application **cannot** be pointed to `database/manual_test/attendance_manual_test.db` with authentication enabled without modifying source code.
- **Classification:** **SOURCE-CODE CHANGES REQUIRED**.

---

## 6. SQLite WAL Safety & Consistent Snapshot Assessment (Gate C)

### 6.1 Current Database State & Journal Mode Inspection
An inspection of `database/attendance.db` revealed:
- **Journal Mode (`PRAGMA journal_mode;`):** `delete` (standard rollback journal, not write-ahead log).
- **Synchronous Mode (`PRAGMA synchronous;`):** `2` (`FULL`).
- **WAL / SHM File Presence:** Neither `attendance.db-wal` nor `attendance.db-shm` exists on disk.
- **Integrity Check (`PRAGMA integrity_check;`):** Returned `ok`.
- **Active Connections:** 0 open lock holders when the server is stopped.

### 6.2 Raw Filesystem Copy vs. SQLite Online Backup API
The audit evaluated whether copying the `.db` file via filesystem commands (`Copy-Item`) is safe:

1. **Why Raw Filesystem Copy Can Be Hazardous:**
   - If any background process, IDE tool, or SQLite connection has uncommitted pages or holds open write transactions, a sequential block-level copy can capture torn pages (partially written sectors), yielding a corrupted destination database.
   - If SQLite were in WAL mode (`journal_mode=wal`), committed transactions frequently reside in `-wal` memory-mapped files until checkpointed. Copying only `.db` drops those transactions or produces corruption upon recovery.
2. **The ACID-Safe Method: SQLite Online Backup API:**
   - The application already includes a production-grade implementation of SQLite's Online Backup API in `app/backup.py` (`create_database_backup`):
     ```python
     source_conn = sqlite3.connect(str(src_path))
     target_conn = sqlite3.connect(str(target_path))
     try:
         source_conn.backup(target_conn)
     finally:
         target_conn.close()
         source_conn.close()
     ```
   - `sqlite3.backup()` operates at the database page level under SQLite's internal shared read lock. It automatically incorporates uncheckpointed WAL frames and guarantees an exact, transactionally consistent snapshot without stopping readers or risking torn pages.
   - `app/backup.py` also runs `PRAGMA integrity_check;` and verifies that table counts match before declaring the copy valid.

---

## 7. Google Sheets Isolation Assessment (Gate D)

### 7.1 Architectural Decoupling
- **Outbox Architecture:** In `app/routes/attendance.py`, `POST /api/scans` writes an attendance row to `attendance` and an outbox row to `sheets_sync_queue` (status: `pending`). It **never** invokes Google APIs in the HTTP request path.
- **Separate Process:** The sync worker (`SheetsSyncWorker`) is never started by `create_app()` or `run.py`. It runs strictly when manually invoked as `python -m flask run-sheets-worker`.
- **Credential Presence:** Service account credentials and `GOOGLE_SHEETS_SPREADSHEET_ID` are not configured in active environment variables during testing.

### 7.2 Fail-Closed Environment Configuration
To guarantee 100% cloud isolation during testing, the following environment settings provide a fail-closed guarantee:

```powershell
$env:GOOGLE_SHEETS_SYNC_ENABLED="false"
$env:GOOGLE_SHEETS_SPREADSHEET_ID=""
$env:GOOGLE_APPLICATION_CREDENTIALS="NUL"
```
Under this configuration:
- `current_app.config["GOOGLE_SHEETS_SYNC_ENABLED"]` is `False`.
- Even if a user were to execute `flask run-sheets-worker` in a separate terminal by accident, the worker immediately aborts:
  `"Google Sheets synchronization is currently disabled by configuration."`
- Real Google spreadsheets cannot be contacted or modified.

---

## 8. Verification Plan for Isolated Manual Testing (Gate E)

When isolated testing is unblocked, the following 7-point verification protocol must be executed to prove complete database isolation:

| Item | Condition to Prove | Exact Verification Procedure | Success Criteria |
| :--- | :--- | :--- | :--- |
| **V-01** | Original DB remains untouched | Calculate SHA-256 hash before and after testing:<br>`Get-FileHash database/attendance.db` | Hashes match exactly before and after manual testing. |
| **V-02** | Application connects to isolated test DB | Call `GET /api/admin/database-stats` in running app.<br>Check `database_path` property in returned JSON. | Returns path containing `attendance_manual_test.db`. |
| **V-03** | Creating admin affects only test DB | Create admin `manual_test_admin`. Inspect both DBs directly:<br>`SELECT username FROM admin_users;` | Present in `attendance_manual_test.db`; absent in `attendance.db`. |
| **V-04** | Creating student affects only test DB | Register `TEST-STU-001`. Inspect both DBs directly:<br>`SELECT roll_number FROM students;` | Present in `attendance_manual_test.db`; absent in `attendance.db`. |
| **V-05** | Creating session affects only test DB | Create session `TEST-CS101`. Inspect both DBs directly:<br>`SELECT course_name FROM class_sessions;` | Present in `attendance_manual_test.db`; absent in `attendance.db`. |
| **V-06** | Recording attendance affects only test DB | Simulate scan for `TEST-STU-001`. Inspect both DBs directly:<br>`SELECT student_id FROM attendance;` | New row in `attendance_manual_test.db`; row count in `attendance.db` unchanged. |
| **V-07** | Google Sheets worker cannot transmit test records | Query `sheets_sync_queue` in test DB:<br>`SELECT sync_status FROM sheets_sync_queue;` | Status remains `'pending'`; zero API requests logged. |

---

## 9. Remaining Blockers & Proposed Resolution

### Blocker Statement:
The application codebase currently lacks environment-variable support for `DATABASE_PATH` in `DevelopmentConfig` and `run.py`. As a result, running `python run.py` will unconditionally connect to `database/attendance.db`.

### Minimal Proposed Resolution (For Future Authorization):
In a future implementation stage (e.g. Stage 9.7A.2), a clean, 2-line non-breaking enhancement can unblock this:

1. **In `app/config.py` (line 105 & 140):**
   ```python
   # Support optional environment override for DATABASE_PATH
   DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", DATABASE_DIR / "attendance.db"))
   ```
2. **In `app/database.py` (line 33-35):**
   Ensure `path_obj.parent.mkdir(parents=True, exist_ok=True)` automatically creates `database/manual_test/` if it does not already exist (already implemented in line 35).
3. **Usage:**
   Setting `$env:DATABASE_PATH="database/manual_test/attendance_manual_test.db"` before running `python run.py` would dynamically and safely point the entire application to the isolated test database, with full authentication, CSRF, and development mode active.

---

## 10. Audit Classification & Final Decision

| Audit Criterion | Classification | Notes |
| :--- | :--- | :--- |
| **Existing Database Path Override** | **NOT VERIFIED** | No environment variable or CLI override exists in current code. |
| **TestingConfig Applicability** | **UNSUITABLE** | Disables login and CSRF; cannot be used for manual UI testing. |
| **SQLite Backup Safety** | **VERIFIED** | Online Backup API (`sqlite3.backup`) provides transaction consistency. |
| **Google Sheets Isolation** | **VERIFIED** | Decoupled outbox queue + `GOOGLE_SHEETS_SYNC_ENABLED=false` guarantee safety. |
| **Feasibility Without Code Changes** | **BLOCKED** | Running against `database/manual_test/` requires source code changes. |

### FINAL READINESS DECISION:

**BLOCKED:** Source-code or configuration changes are required before safe isolated manual testing can target `database/manual_test/attendance_manual_test.db`.

---
*Report generated under strict Read-Only audit rules. Zero application files or databases were modified.*
