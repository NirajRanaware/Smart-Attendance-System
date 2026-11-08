# Phase 3 — Milestone 2: Targeted Verification & Hardening Report

**Project:** IoT-Based Smart Attendance Management System  
**Phase:** Phase 3 — Cloud Synchronization & External Integration  
**Milestone:** Milestone 2 — Sync Queue Database Migration & Local Outbox Service  
**Workspace:** `C:\Users\niraj\Downloads\Smart-Attendance-System`  
**Date:** November 2026  
**Status:** Verified, Aligned & Approved Baseline Intact  

---

## 1. Retry Policy: Before and After Alignment

### Before Targeted Verification
- **Configuration Defaults in Code:** `DEFAULT_BASE_DELAY_SECONDS = 15`, `DEFAULT_MAX_DELAY_SECONDS = 900` (15 minutes), multiplier 2.
- **Identified Deviation in Code:** Jitter calculation in `calculate_retry_delay()` used a flat uniform range of $1.0\text{s}$ to $5.0\text{s}$ (`random.uniform(1.0, 5.0)`).
- **Identified Deviation in Previous Turn Narrative:** The assistant conversational summary inadvertently noted "$30\text{s}$ initial delay and $30\text{m}$ maximum", causing a descriptive discrepancy with the approved 15s / 15m requirement.

### After Targeted Verification
The retry calculation in [`app/services/sheets_outbox.py:calculate_retry_delay()`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/sheets_outbox.py#L363-L395) has been explicitly updated and aligned:
- **Base Delay:** $15\text{ seconds}$ (`DEFAULT_BASE_DELAY_SECONDS = 15`).
- **Exponential Multiplier:** $2$ (`base_delay * (2 ** retry_count)`).
- **Jitter Range:** Strictly $\pm 10\%$ of the capped exponential delay:
  $$\text{added\_jitter} \in [-0.10 \times D, +0.10 \times D]$$
- **Maximum Ceiling:** $900\text{ seconds}$ (15 minutes) (`DEFAULT_MAX_DELAY_SECONDS = 900`).
- **Deterministic Testing:** Fully preserved via `jitter_seconds` override (e.g., `jitter_seconds=3.5` or `jitter_seconds=-2.0`) and `jitter=False` for zero-jitter test assertions.
- **Configurable Max Retries:** Unchanged (`DEFAULT_MAX_RETRIES = 5`, configurable via `schedule_retry(..., max_retries=N)`).

#### Detailed Delay Progression Table:
| Attempt (`retry_count`) | Pure Exponential Delay | $\pm 10\%$ Jitter Range | Actual Delay Window |
|---|---|---|---|
| **0** | $15.0\text{s}$ | $[-1.50\text{s}, +1.50\text{s}]$ | $13.50\text{s}$ – $16.50\text{s}$ |
| **1** | $30.0\text{s}$ | $[-3.00\text{s}, +3.00\text{s}]$ | $27.00\text{s}$ – $33.00\text{s}$ |
| **2** | $60.0\text{s}$ | $[-6.00\text{s}, +6.00\text{s}]$ | $54.00\text{s}$ – $66.00\text{s}$ |
| **3** | $120.0\text{s}$ | $[-12.00\text{s}, +12.00\text{s}]$ | $108.00\text{s}$ – $132.00\text{s}$ |
| **4** | $240.0\text{s}$ | $[-24.00\text{s}, +24.00\text{s}]$ | $216.00\text{s}$ – $264.00\text{s}$ |
| **5** | $480.0\text{s}$ | $[-48.00\text{s}, +48.00\text{s}]$ | $432.00\text{s}$ – $528.00\text{s}$ |
| **$\ge 6$ (Capped)** | $900.0\text{s}$ ($15\text{m}$) | $[-90.00\text{s}, +90.00\text{s}]$ | $810.00\text{s}$ – $990.00\text{s}$ ($13.5\text{m}$ – $16.5\text{m}$) |

---

## 2. Attendance Source Field Verification

An exhaustive inspection of [`app/routes/attendance.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/routes/attendance.py) and [`app/models.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/models.py) was performed:

### 2.1 Table Schema Integrity
The physical `attendance` table schema retains the mandatory `source TEXT NOT NULL` column:
```sql
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    scanned_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Present',
    source TEXT NOT NULL,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES class_sessions(id) ON DELETE CASCADE,
    UNIQUE(student_id, session_id)
);
```

### 2.2 Source Value Preservation During Scan Processing
In [`app/routes/attendance.py:process_scan()`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/routes/attendance.py#L108-L208):
- The `source` value is extracted from the incoming request payload (`source = payload["source"].strip()`).
- The attendance record is inserted with the exact source:
  ```python
  cursor.execute(
      """
      INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
      VALUES (?, ?, ?, 'Present', ?);
      """,
      (student_id, session_id, scanned_at, source)
  )
  ```
- No hardcoded or invented source values are substituted.

### 2.3 Non-Interference by Outbox Queue Insertion
The subsequent outbox queue insertion operates exclusively on `sheets_sync_queue`:
```python
cursor.execute(
    """
    INSERT INTO sheets_sync_queue (
        attendance_id, sync_status, retry_count, created_at, updated_at
    ) VALUES (?, 'pending', 0, ?, ?);
    """,
    (attendance_id, scanned_at, scanned_at)
)
```
- It does not modify, update, or overwrite any column of the newly inserted `attendance` row.
- The `attendance.source` value remains completely untouched.

### 2.4 Google Sheets Service Retrieval via Database Join
In [`app/services/sheets_outbox.py:claim_records()`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/sheets_outbox.py#L242-L267), the worker fetch query explicitly retrieves `a.source`:
```sql
SELECT 
    q.id AS queue_id,
    q.attendance_id,
    q.sync_status,
    q.retry_count,
    q.lease_expires_at,
    a.scanned_at,
    a.status AS attendance_status,
    a.source,
    s.roll_number,
    s.full_name AS student_name,
    s.card_uid,
    c.course_name,
    c.session_date
FROM sheets_sync_queue q
INNER JOIN attendance a ON q.attendance_id = a.id
INNER JOIN students s ON a.student_id = s.id
INNER JOIN class_sessions c ON a.session_id = c.id
WHERE q.id IN (...)
ORDER BY q.id ASC;
```
This confirms that the future Google Sheets synchronization worker (Milestone 3/4) has direct access to `attendance.source` without requiring any schema changes.

### 2.5 API Contract Immutability
The JSON response returned by `POST /api/scans` remains 100% identical to the approved Phase 1 & 2 contract:
```json
{
  "success": true,
  "message": "Attendance recorded successfully",
  "attendance": {
    "id": 1,
    "roll_number": "CS-2024-001",
    "student_name": "Aarav Sharma",
    "course_name": "Embedded Systems",
    "session_id": 1,
    "scanned_at": "2026-11-08T10:00:00Z",
    "status": "Present",
    "source": "rfid"
  }
}
```

---

## 3. Files Modified During Targeted Verification

| File | Nature of Modification |
|---|---|
| [`app/services/sheets_outbox.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/sheets_outbox.py) | Aligned `calculate_retry_delay()` to use exact $\pm 10\%$ jitter formula (`random.uniform(-0.10 * capped_delay, 0.10 * capped_delay)`). |
| [`tests/test_sheets_outbox.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/tests/test_sheets_outbox.py) | Enhanced `test_15_exponential_backoff_respects_maximum_delay()` with tests for deterministic positive/negative jitter and statistical distribution checks over 50 iterations. |
| [`PHASE_3_MILESTONE_2_COMPLETION_REPORT.md`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/PHASE_3_MILESTONE_2_COMPLETION_REPORT.md) | Synchronized Section 6.3 documentation with the 15-second base / $\pm 10\%$ jitter / 15-minute maximum ceiling specification. |

---

## 4. Test Results

### 4.1 Dedicated Outbox Tests
```text
============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\niraj\Downloads\Smart-Attendance-System
plugins: cov-7.1.0
collected 20 items

tests\test_sheets_outbox.py ....................                         [100%]

============================= 20 passed in 18.54s =============================
```

### 4.2 Full System Regression Suite
```text
============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\niraj\Downloads\Smart-Attendance-System
plugins: cov-7.1.0
collected 350 items

tests\test_attendance.py ............................                    [  8%]
tests\test_attendance_ui.py .................                            [ 12%]
tests\test_auth.py ...................................                   [ 22%]
tests\test_clear_test_data.py ..................                         [ 28%]
tests\test_dashboard_integration.py ...........                          [ 31%]
tests\test_database.py ................                                  [ 35%]
tests\test_health.py ........                                            [ 38%]
tests\test_integration.py .....                                          [ 39%]
tests\test_milestone9_integration.py ..........                          [ 42%]
tests\test_reports.py .................................                  [ 51%]
tests\test_reports_ui.py ..............                                  [ 55%]
tests\test_responsive_ui.py ..........                                   [ 58%]
tests\test_session_end_time.py .............                             [ 62%]
tests\test_sessions.py .................................                 [ 71%]
tests\test_sessions_ui.py ...............                                [ 76%]
tests\test_sheets_outbox.py ....................                         [ 81%]
tests\test_simulator.py .......................                          [ 88%]
tests\test_students.py .....................                             [ 94%]
tests\test_students_ui.py .............                                  [ 98%]
tests\test_views.py .......                                              [100%]

======================= 350 passed in 110.49s (0:01:50) =======================
```
- **Total Tests:** 350
- **Passed:** 350 (100%)
- **Failed:** 0
- **Regressions:** 0

---

## 5. Development Database & SQLite Integrity Results

Executed direct diagnostic inspection on the persistent development database (`database/attendance.db`):

| Diagnostic Check | SQL Command Executed | Result | Status |
|---|---|---|---|
| **Database File Existence** | `Path("database/attendance.db").exists()` | `True` | PASS |
| **Physical Storage Integrity** | `PRAGMA integrity_check;` | `[{'integrity_check': 'ok'}]` | PASS |
| **Foreign Key Constraints** | `PRAGMA foreign_key_check;` | `[]` (0 violations) | PASS |
| **Table Manifest** | `SELECT name FROM sqlite_master WHERE type='table';` | `admin_users`, `attendance`, `class_sessions`, `sheets_sync_queue`, `students` | PASS |
| **Admin User Persistence** | `SELECT id, username, role, active FROM admin_users;` | `[{'id': 1, 'username': 'Niraj', 'role': 'admin', 'active': 1}]` | PASS |
| **Clear All Data Run** | *Verification check* | **NOT executed** | PASS |

---

## 6. Confirmation of Boundary Adherence

In strict accordance with the instructions:
1. **No Google API calls:** Zero external network calls were made to Google Sheets or any other remote service.
2. **No Google packages installed:** No dependencies were installed or added to `requirements.txt`.
3. **No credentials added:** No private keys or service account credentials were created or referenced.
4. **No unnecessary schema changes:** The database schema remains identical to Table 5 as designed.
5. **No unrelated changes:** Application routes, frontend templates, and authentication mechanisms were untouched.
6. **Milestone 3 paused:** No work on Milestone 3 has commenced.

---

**Targeted verification is complete. The system is stopped and awaiting user review.**
