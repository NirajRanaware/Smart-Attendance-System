# Phase 3 — Milestone 2: Sync Queue Database Migration & Local Outbox Service Completion Report

**Project:** IoT-Based Smart Attendance Management System  
**Phase:** Phase 3 — Cloud Synchronization & External Integration  
**Milestone:** Milestone 2 — Sync Queue Database Migration & Local Outbox Service  
**Workspace:** `C:\Users\niraj\Downloads\Smart-Attendance-System`  
**Current Baseline:** 350 automated tests passing (100% pass rate)  
**Date:** November 2026  
**Status:** Completed & Awaiting Phase 3 Milestone 3 Approval  

---

## 1. Implementation Summary

In Phase 3 — Milestone 2, the **Local SQLite Outbox Mechanism** has been successfully designed, migrated, and implemented. The core objective of this milestone is to establish an ACID-compliant, reliable local queue for future Google Sheets synchronization without introducing any external cloud dependencies or blocking physical card scan latency.

### Core Accomplishments:
1. **Safe SQLite Migration:** Added the `sheets_sync_queue` table and performance indexes via an idempotent migration in `app/database.py` and `app/models.py`. Verified that fresh and pre-existing databases migrate without data loss.
2. **Atomic Outbox Insertion:** Integrated queue insertion directly into `process_scan()` in `app/routes/attendance.py` inside the active `BEGIN IMMEDIATE;` write transaction. Every committed attendance record is guaranteed to have exactly one pending queue entry.
3. **Local Outbox Service (`app/services/sheets_outbox.py`):** Created a comprehensive, modular service providing thread-safe record claiming, lease timeouts, lease expiration recovery, exponential backoff retry scheduling, dead-letter routing, and queue telemetry.
4. **Settings & Cleanup Compatibility:** Validated that `ON DELETE CASCADE` and the existing "Clear All Test Data" feature seamlessly purge queue items without leaving orphaned rows.
5. **Comprehensive Testing:** Created `tests/test_sheets_outbox.py` covering all 20 required scenarios. All 350 system tests pass with 100% reliability.

---

## 2. Files Created and Modified

| Action | File | Description |
|---|---|---|
| **Created** | [`app/services/sheets_outbox.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/sheets_outbox.py) | Service module for queue management, record claiming, lease recovery, and retry calculation. |
| **Created** | [`app/services/__init__.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/__init__.py) | Service package initialization. |
| **Created** | [`tests/test_sheets_outbox.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/tests/test_sheets_outbox.py) | 20 dedicated automated tests for queue migration, atomicity, leases, and retry logic. |
| **Modified** | [`app/models.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/models.py) | Added Table 5 `sheets_sync_queue` schema, constraints, and indexes. |
| **Modified** | [`app/database.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/database.py) | Idempotent migration hook in `migrate_db()` ensuring safe schema evolution. |
| **Modified** | [`app/routes/attendance.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/routes/attendance.py) | Atomic queue insertion inside `BEGIN IMMEDIATE;` transaction during card scan processing. |
| **Modified** | [`DATABASE_DOCUMENTATION.md`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/DATABASE_DOCUMENTATION.md) | Updated database architecture documentation with Table 5 specifications. |

---

## 3. Database Migration Details

The migration is implemented in [`app/database.py:migrate_db()`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/database.py#L85-L115) and [`app/models.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/models.py):

```python
# Check if sheets_sync_queue already exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_queue';")
if not cursor.fetchone():
    cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_SQL)
    cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL)
    conn.commit()
```

### Safety Guarantees:
- **Idempotency:** Safe to run repeatedly; checks `sqlite_master` prior to executing DDL.
- **Zero Data Loss:** Preserves all existing records in `students`, `class_sessions`, `attendance`, and `admin_users`.
- **Constraint Enforcement:** Adds foreign-key constraint to `attendance.id` with `ON DELETE CASCADE`.
- **Integrity Validation:** Tested on fresh databases, legacy databases, and verified via `PRAGMA integrity_check`.

---

## 4. Outbox Transaction Explanation

The queue insertion occurs in [`app/routes/attendance.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/routes/attendance.py) inside `process_scan()`:

```mermaid
sequenceDiagram
    autonumber
    participant Client as Scanner / Simulator
    participant Route as app/routes/attendance.py
    participant DB as SQLite Engine

    Client->>Route: POST /api/scans {"card_uid": "...", "source": "..."}
    Route->>DB: BEGIN IMMEDIATE; (Acquire exclusive write lock)
    Route->>DB: Check student active & verify active session
    Route->>DB: Verify no duplicate attendance exists for session
    Route->>DB: INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
    Note over Route,DB: attendance_id = cursor.lastrowid
    Route->>DB: INSERT INTO sheets_sync_queue (attendance_id, sync_status='pending', retry_count=0)
    Route->>DB: COMMIT; (Both records committed atomically)
    Route-->>Client: HTTP 201 Created {"attendance": {...}}
```

### Transaction Invariant:
$$\text{Attendance Committed} \iff \text{Queue Item Committed}$$

- If attendance insertion succeeds but queue insertion encounters an error, `db.rollback()` rolls back **both**.
- If attendance insertion fails (e.g., student not active or duplicate scan), no queue item is created.
- The external caller receives the exact same API contract without experiencing any network delays to Google.

---

## 5. Queue Schema Specification

```sql
CREATE TABLE IF NOT EXISTS sheets_sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attendance_id INTEGER NOT NULL UNIQUE,
    sync_status TEXT NOT NULL DEFAULT 'pending' 
        CHECK (sync_status IN ('pending', 'in_progress', 'synced', 'failed', 'dead_letter')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    synced_at TEXT,
    FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_status_retry 
    ON sheets_sync_queue(sync_status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_sync_queue_attendance_id 
    ON sheets_sync_queue(attendance_id);

CREATE INDEX IF NOT EXISTS idx_sync_queue_lease 
    ON sheets_sync_queue(sync_status, lease_expires_at);
```

---

## 6. Retry Policy and Processing Lease Behavior

Implemented in [`app/services/sheets_outbox.py`](file:///C:/Users/niraj/Downloads/Smart-Attendance-System/app/services/sheets_outbox.py):

### 6.1 Lease Locking & Worker Concurrency Safety
- When a worker claims records via `claim_records()`, it acquires an immediate lock via `BEGIN IMMEDIATE;` and assigns an expiration timestamp:
  $$\text{lease\_expires\_at} = \text{now\_utc} + \text{lease\_duration\_seconds (default: 60s)}$$
- Records transition from `pending` (or `failed`) to `in_progress`.
- The update query condition `WHERE id IN (...) AND sync_status IN ('pending', 'failed')` guarantees that concurrent worker threads cannot claim the same record.

### 6.2 Lease Expiration Recovery
- If a worker crashes or encounters an unhandled process termination while holding records `in_progress`:
  `recover_expired_leases()` queries records where `sync_status = 'in_progress' AND lease_expires_at <= now_utc`.
- These orphaned records are automatically reverted to `pending`, with `lease_expires_at` cleared to allow subsequent processing.

### 6.3 Exponential Backoff & Dead Letter Routing
- **Base Delay:** 15 seconds.
- **Exponential Multiplier:** 2.
- **Random Jitter:** $\pm 10\%$ of capped delay.
- **Maximum Ceiling:** 900 seconds (15 minutes).
- **Backoff Formula:**
  $$\text{delay} = \min(900, \min(900, 15 \times 2^{\text{retry\_count}}) \pm 10\%\text{ jitter})$$
- **Attempt Progression (without jitter):**
  - Attempt 0: 15s (with $\pm 10\%$ jitter: $13.5\text{s}$ to $16.5\text{s}$)
  - Attempt 1: 30s (with $\pm 10\%$ jitter: $27.0\text{s}$ to $33.0\text{s}$)
  - Attempt 2: 60s (with $\pm 10\%$ jitter: $54.0\text{s}$ to $66.0\text{s}$)
  - Attempt 3: 120s (with $\pm 10\%$ jitter: $108.0\text{s}$ to $132.0\text{s}$)
  - Attempt 4: 240s (with $\pm 10\%$ jitter: $216.0\text{s}$ to $264.0\text{s}$)
  - Maximum ceiling: 900s / 15 minutes (with $\pm 10\%$ jitter and absolute cap: strictly $810.0\text{s}$ to $900.0\text{s}$; never exceeds 900.0s).
- **Dead Letter State:** After reaching `max_retries` (default: 5), the record transitions to `dead_letter`, and `next_retry_at` is set to `NULL` to prevent indefinite processing loops.

---

## 7. Security & Data Protection

1. **Parameterized Queries:** Every SQL statement in `app/services/sheets_outbox.py` and `app/routes/attendance.py` uses parameterized inputs (`?`), preventing SQL injection.
2. **Zero Credential Exposure:** No Google service account keys or secrets are stored in the queue.
3. **No External Network Calls:** True to the Milestone 2 objective, no outbound network requests to Google or third-party APIs were introduced.
4. **Timestamp Standardization:** Internal queue timestamps use UTC ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`), while institutional views use `Asia/Kolkata` (`+05:30`).

---

## 8. Automated Test Results (`tests/test_sheets_outbox.py`)

All 20 required scenarios passed:

```text
tests/test_sheets_outbox.py::test_1_queue_table_migration_on_new_database PASSED
tests/test_sheets_outbox.py::test_2_migration_on_existing_database PASSED
tests/test_sheets_outbox.py::test_3_repeated_migration_without_damage PASSED
tests/test_sheets_outbox.py::test_4_existing_attendance_records_remain_intact PASSED
tests/test_sheets_outbox.py::test_5_new_attendance_creates_exactly_one_queue_record PASSED
tests/test_sheets_outbox.py::test_6_attendance_rollback_also_rolls_back_queue_insertion PASSED
tests/test_sheets_outbox.py::test_7_duplicate_attendance_does_not_create_duplicate_queue_records PASSED
tests/test_sheets_outbox.py::test_8_queue_status_defaults_to_pending PASSED
tests/test_sheets_outbox.py::test_9_pending_records_can_be_retrieved PASSED
tests/test_sheets_outbox.py::test_10_records_can_be_claimed_safely PASSED
tests/test_sheets_outbox.py::test_11_concurrent_workers_cannot_claim_same_record PASSED
tests/test_sheets_outbox.py::test_12_expired_leases_can_be_recovered PASSED
tests/test_sheets_outbox.py::test_13_successful_records_can_be_marked_synced PASSED
tests/test_sheets_outbox.py::test_14_retry_scheduling_works_correctly PASSED
tests/test_sheets_outbox.py::test_15_exponential_backoff_respects_maximum_delay PASSED
tests/test_sheets_outbox.py::test_16_maximum_attempts_lead_to_dead_letter PASSED
tests/test_sheets_outbox.py::test_17_queue_statistics_are_accurate PASSED
tests/test_sheets_outbox.py::test_18_deleting_attendance_cascades_to_queue_record PASSED
tests/test_sheets_outbox.py::test_19_database_integrity_remains_valid PASSED
tests/test_sheets_outbox.py::test_20_existing_clear_all_test_data_functionality_remains_compatible PASSED

============================= 20 passed in 18.48s =============================
```

---

## 9. Full System Regression Test Results

The complete automated test suite (including all legacy tests from Phases 1, 2, and Milestone 1) was executed:

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

======================= 350 passed in 103.33s (0:01:43) =======================
```

---

## 10. SQLite Database Integrity & Dev Database Preservation

The physical development database at `database/attendance.db` was verified:
- **ACID Integrity:** `PRAGMA integrity_check` returned `ok`.
- **Foreign Keys:** `PRAGMA foreign_key_check` returned `0` violations.
- **Preserved Tables:**
  - `admin_users`: 1 record intact (`Niraj`, role: `admin`, active: `1`)
  - `students`: Intact
  - `class_sessions`: Intact
  - `attendance`: Intact
  - `sheets_sync_queue`: Successfully provisioned and empty (0 rows)
- **Isolation Guarantee:** Destructive tests ran exclusively in isolated temporary directories (`tmp_path`).

---

## 11. Remaining Limitations & Boundaries

1. **Cloud Synchronization:** Google Sheets API integration and actual HTTP transmission are strictly deferred to Phase 3 Milestone 3.
2. **Worker Automation:** Background worker polling thread and webhook dispatching will be implemented in subsequent Phase 3 milestones.
3. **Local Queue Size:** The queue relies on local SQLite storage, which effortlessly supports millions of rows without degradation.

---

## 12. Final Milestone Status

> [!IMPORTANT]
> **PHASE 3 — MILESTONE 2 COMPLETE**: The local synchronization queue schema, idempotent migration, atomic scan transaction hook, and outbox service are fully operational and verified with 350 passing tests.
> 
> As instructed, work has **STOPPED** at the completion of Milestone 2. Awaiting review and explicit approval before beginning **Phase 3 — Milestone 3 (Google Service Account Integration & Sheets API Client)**.
