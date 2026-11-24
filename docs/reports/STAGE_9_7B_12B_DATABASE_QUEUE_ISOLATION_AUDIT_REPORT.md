# Stage 9.7B.12B — Strict Read-Only Database & Google Sheets Queue Isolation Audit Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Execution Date:** 2026-11-24
**Audit Mode:** STRICT READ-ONLY AUDIT
**Lead Auditor:** Antigravity

---

## 1. Executive Summary

During Stage 9.7B.12A, the Google Sheets monitoring dashboard on the application settings page (`/settings`) displayed the following telemetry:
- **PENDING:** 2
- **SYNCED:** 0
- **IN-FLIGHT:** 0
- **DEAD-LETTER:** 0
- **PERMANENT FAILURE:** 0
- **TOTAL QUEUED:** 2

This raised immediate concern because earlier audits of the isolated manual-test database reported 0 queue records, whereas the production database was known to hold 3 pending queue records. The objective of this audit was to definitively determine which database the running Flask application, dashboard UI, and telemetry endpoint are accessing, verify database isolation, and identify the exact origin of the 2 pending queue records.

### Key Audit Findings:
1. **The dashboard is querying the isolated manual-test database (`database/manual_test/attendance_manual_test.db`).**
2. **The 2 pending queue records in the manual-test database were legitimately created at 22:41 IST on 2026-11-23** via the Web Scanner Simulator (`POST /api/scans`), which recorded attendance for Student 1 (`MANUAL-001`) and Student 2 (`MANUAL-002`) against active Session 3 (`CS-101: Test Lecture One`). The application logic atomically enqueued both attendance events into `sheets_sync_queue` with `sync_status = 'pending'`.
3. **The production database (`database/attendance.db`) has 3 pending queue records and was completely untouched.** Its SHA-256 hash (`0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`) matches its pre-audit baseline exactly.
4. **There is zero database path mismatch** between the Flask application and the queue telemetry API. Both uniformly bind to `current_app.config["DATABASE_PATH"]`.

**Final Verdict:** `MANUAL DATABASE CONFIRMED — SAFE TO PREPARE ISOLATED SYNC TEST`

---

## 2. Audit Scope & Methodology

- **Inspection Constraints:** Strict zero-write mode. All database connections opened with SQLite URI `mode=ro`.
- **Target Databases:**
  - Manual-Test Database: `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
  - Production Database: `C:\Projects\Smart-Attendance-System\database\attendance.db`
- **Application Files Audited:**
  - Configuration: `app/config.py`, `.env`
  - Routes: `app/routes/admin.py`, `app/routes/api.py`
  - Services: `app/services/sheets_outbox.py`
  - Frontend: `app/static/js/settings.js`, `app/templates/settings.html`
- **Integrity Verification:** SQLite `PRAGMA integrity_check`, SHA-256 file hashing, and exact row-level record analysis.

---

## 3. Application Database Configuration

The application database path resolution was examined in `app/config.py`:

```python
class Config:
    ...
    DATABASE_PATH = os.environ.get(
        "DATABASE_PATH",
        os.path.join(basedir, "../database/attendance.db")
    )
```

In the active project root `.env` file:
```ini
DATABASE_PATH=database/manual_test/attendance_manual_test.db
```

When Flask initializes via `create_app()`:
1. `app.config.from_object(Config)` binds `DATABASE_PATH` to `database/manual_test/attendance_manual_test.db`.
2. All database utility functions in `app/database.py` and `app/services/sheets_outbox.py` retrieve the database path from `current_app.config.get("DATABASE_PATH")`.
3. As long as `DATABASE_PATH` points to the manual-test database, all route handlers and background queue queries interact solely with the isolated test database.

---

## 4. Dashboard Telemetry Data Flow

The data flow powering the Google Sheets monitoring card was traced from client UI to the underlying SQLite database:

1. **User Interface (`app/templates/settings.html`):**
   Displays the Google Sheets Sync Outbox monitor card with elements:
   `#sync-pending`, `#sync-synced`, `#sync-inflight`, `#sync-deadletter`, `#sync-failed`, and `#sync-total`.

2. **Frontend Script (`app/static/js/settings.js`):**
   The `loadSyncStatus()` function periodically executes an HTTP request:
   ```javascript
   fetch('/api/admin/sheets-sync/status')
     .then(response => response.json())
     .then(data => {
         document.getElementById('sync-pending').textContent = data.pending ?? 0;
         document.getElementById('sync-synced').textContent = data.synced ?? 0;
         document.getElementById('sync-inflight').textContent = data.in_flight ?? 0;
         document.getElementById('sync-deadletter').textContent = data.dead_letter ?? 0;
         document.getElementById('sync-failed').textContent = data.permanent_failure ?? 0;
         document.getElementById('sync-total').textContent = data.total_queued ?? 0;
     });
   ```

3. **Backend Route (`app/routes/admin.py`):**
   The `/api/admin/sheets-sync/status` endpoint retrieves the configured database path directly from application context:
   ```python
   @admin_bp.route("/api/admin/sheets-sync/status", methods=["GET"])
   def sheets_sync_status():
       db_path = current_app.config.get("DATABASE_PATH")
       stats = get_queue_stats(db_path)
       return jsonify(stats)
   ```

4. **Telemetry Service (`app/services/sheets_outbox.py`):**
   `get_queue_stats(db_path)` executes an aggregated query against the passed database path:
   ```python
   def get_queue_stats(db_path: str = None) -> Dict[str, int]:
       with get_db_connection(db_path) as conn:
           cursor = conn.cursor()
           cursor.execute("""
               SELECT sync_status, COUNT(*)
               FROM sheets_sync_queue
               GROUP BY sync_status
           """)
           ...
   ```

**Architectural Confirmation:** There is no hardcoded path, no bypass, and no divergence. The dashboard telemetry endpoint strictly queries the exact database configured in `current_app.config["DATABASE_PATH"]`.

---

## 5. Manual-Test Database Results

- **Target Path:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
- **File Size:** 94,208 bytes
- **SHA-256 Hash:** `ABEFCE5140AF1F24E1A5CDFF712454AC3BABFA264608EFD44C7B09B6BF46E57F`
- **Integrity Check:** `ok`
- **Table Record Counts:**
  - `students`: 2
  - `class_sessions`: 3 (Session 3 is active)
  - `attendance`: 2
  - `sheets_sync_queue`: 2

### Detailed `sheets_sync_queue` Records in Manual-Test Database:
| id | attendance_id | sync_status | retry_count | next_retry_at | last_error | created_at | updated_at |
|:--:|:-------------:|:-----------:|:-----------:|:-------------:|:----------:|:----------:|:----------:|
| 1 | 1 | pending | 0 | NULL | NULL | 2026-11-23 17:11:28 | 2026-11-23 17:11:28 |
| 2 | 2 | pending | 0 | NULL | NULL | 2026-11-23 17:11:32 | 2026-11-23 17:11:32 |

### Corresponding `attendance` Records in Manual-Test Database:
| id | student_id | session_id | timestamp | status | scan_source |
|:--:|:----------:|:----------:|:---------:|:------:|:-----------:|
| 1 | 1 (MANUAL-001) | 3 | 2026-11-23 17:11:28 | Present | SIMULATOR |
| 2 | 2 (MANUAL-002) | 3 | 2026-11-23 17:11:32 | Present | SIMULATOR |

---

## 6. Production Database Results

- **Target Path:** `C:\Projects\Smart-Attendance-System\database\attendance.db`
- **File Size:** 94,208 bytes
- **SHA-256 Hash:** `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`
- **Integrity Check:** `ok`
- **Table Record Counts:**
  - `students`: 2
  - `class_sessions`: 3 (0 active sessions)
  - `attendance`: 3
  - `sheets_sync_queue`: 3

### Detailed `sheets_sync_queue` Records in Production Database:
| id | attendance_id | sync_status | retry_count | next_retry_at | last_error | created_at | updated_at |
|:--:|:-------------:|:-----------:|:-----------:|:-------------:|:----------:|:----------:|:----------:|
| 3 | 3 | pending | 0 | NULL | NULL | 2026-11-08 06:09:42 | 2026-11-08 06:09:42 |
| 4 | 4 | pending | 0 | NULL | NULL | 2026-11-20 17:48:47 | 2026-11-20 17:48:47 |
| 5 | 5 | pending | 0 | NULL | NULL | 2026-11-22 08:58:36 | 2026-11-22 08:58:36 |

---

## 7. Dashboard-versus-Database Comparison Table

| Metric / Telemetry Field | Monitoring Dashboard Display | Manual-Test Database (`attendance_manual_test.db`) | Production Database (`attendance.db`) | State Match Evaluation |
|:---|:---:|:---:|:---:|:---|
| **PENDING** | **2** | **2** | 3 | **Exact match with Manual-Test DB** |
| **SYNCED** | **0** | **0** | 0 | Matches |
| **IN-FLIGHT** | **0** | **0** | 0 | Matches |
| **DEAD-LETTER** | **0** | **0** | 0 | Matches |
| **PERMANENT FAILURE** | **0** | **0** | 0 | Matches |
| **TOTAL QUEUED** | **2** | **2** | 3 | **Exact match with Manual-Test DB** |
| **Active Session State** | Session 3 Active | Session 3 Active (`CS-101`) | No Active Sessions | **Matches Manual-Test DB** |

---

## 8. Root Cause of the 2-Pending-Record Discrepancy

1. **Origin of the Records:**
   Following Stage 9.7B.11F (activation of Session 3 `CS-101: Test Lecture One`), the web attendance scanner simulator (`POST /api/scans`) was used at approximately 22:41 IST on 2026-11-23 to test attendance capture:
   - Scan 1 at `17:11:28 UTC`: Student `MANUAL-001` (Card UID `04A1B2C3D4`)
   - Scan 2 at `17:11:32 UTC`: Student `MANUAL-002` (Card UID `04E5F6A7B8`)

2. **Automatic Outbox Queueing:**
   The `record_attendance()` service atomically writes each scan into the `attendance` table and invokes `enqueue_attendance()` in `app/services/sheets_outbox.py`. This created Queue ID 1 and Queue ID 2 with `sync_status = 'pending'`.

3. **Absence of Background Sync Worker:**
   Because manual testing strictly adhered to safety protocols, the Google Sheets synchronization worker (`flask run-sheets-worker`) was never executed. Consequently, the two queued rows remained in `pending` status.

4. **Dashboard Accuracy:**
   When the user accessed the settings page, `GET /api/admin/sheets-sync/status` faithfully counted the 2 pending rows in `attendance_manual_test.db` and rendered `PENDING: 2`. The perceived discrepancy was due to assuming the test database still had 0 queue records, rather than any database misconfiguration or leakage.

---

## 9. Safety Verification

- **Production Database Immutability:** The SHA-256 hash of `database/attendance.db` (`0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`) is identical to the baseline recorded prior to testing. Zero bytes have been modified.
- **Zero-Write Execution:** All database inspections were performed using read-only connections (`mode=ro`).
- **No Worker Execution:** The background sync worker was not invoked.
- **No Remote Calls:** No Google Sheets API calls or modifications were made.
- **Credential Protection:** No credentials, service-account keys, or sensitive configuration details were logged or exposed.

---

## 10. Findings & Recommendations

### Findings:
1. **Flawless Isolation:** The Flask application, UI dashboard, and queue telemetry endpoints are strictly isolated to `database/manual_test/attendance_manual_test.db`.
2. **Zero Contamination:** The production database has experienced zero read or write bleed-through.
3. **Queue Mechanism Verified:** The transactional outbox queue correctly captured attendance events generated from the web simulator.

### Recommendations for Stage 9.7B.12C (Sync Execution Preparation):
1. **CLI Worker Environment:** When invoking `flask run-sheets-worker` during sync testing, ensure that the active shell loads the test `.env` file so the worker operates against `attendance_manual_test.db`.
2. **Test Spreadsheet Verification:** Re-verify that `GOOGLE_SHEETS_SPREADSHEET_ID` points to an isolated manual-testing Google Sheet before triggering any synchronization run.

---

## 11. Final Verdict

# `MANUAL DATABASE CONFIRMED — SAFE TO PREPARE ISOLATED SYNC TEST`
