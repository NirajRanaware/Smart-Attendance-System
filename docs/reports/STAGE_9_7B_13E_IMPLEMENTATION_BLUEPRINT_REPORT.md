# Stage 9.7B.13E — Google Sheets Synchronization Exactly-Once Implementation Blueprint & Migration Plan

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Current Branch:** `main` (HEAD: `889ea78 feat: advance attendance and Google Sheets integration`)
**Execution Date:** 2026-11-24
**Audit Mode:** STRICT READ-ONLY IMPLEMENTATION PLANNING
**Previous Milestones:** Stage 9.7B.13C (Empirical Concurrency Test), Stage 9.7B.13D (Architecture Review)
**Lead Systems Architect:** Antigravity

---

## 1. Executive Summary

This engineering blueprint establishes the definitive, implementation-ready technical specification for resolving the duplicate delivery race condition confirmed during Stage 9.7B.13C (Test C2).

Under the previous append-based architecture (`values().append`), any worker lease expiration occurring in the Time-Of-Check to Time-Of-Use (TOCTOU) window between reading Column A and executing the append caused duplicate rows when a resumed worker dispatched a stale append payload.

The blueprint formalizes a **Multi-Layered Defense-in-Depth Model** that achieves **destination idempotence** and **exactly-once logical row representation** across all network delay, process pause, and worker failure scenarios:
1. **Deterministic Destination Addressing:** Replaces `values().append` with targeted range updates (`values().update` targeting `'Attendance_Log'!A{row}:I{row}`).
2. **Generational Claim Fencing:** Introduces a monotonic `claim_token` on every outbox item to fence stale workers and guarantee atomic SQLite transitions.
3. **Pre-Dispatch Verification:** Enforces a local lease-validity check immediately prior to remote network dispatch.
4. **Coordinated Timeout & Lease Budgeting:** Establishes a 15-second client-side socket timeout against a 180-second lease duration, bounded by batch single-request execution.
5. **Historical Row Preservation:** Explicitly locks and preserves existing Google Sheet rows 1 through 4 (Header, `-92022`, `MANUAL-001`, `MANUAL-002`) and commences new allocations at Row 5.

**Status:** This document is an architectural blueprint only. **Zero source code or database modifications have been executed.**

**Final Verdict:** `READY — IMPLEMENTATION BLUEPRINT COMPLETE`

---

## 2. Verified Baseline & Environment State

An inspection of the workspace repository confirms the exact operational baseline:

### Git Repository State
- **Branch:** `main`
- **Tracking:** Up to date with `origin/main`
- **HEAD Commit:** `889ea78 feat: advance attendance and Google Sheets integration`
- **Working Tree:** Clean with respect to tracked files; untracked audit reports from Stages 12B through 13D exist in `docs/reports/`.
- **Git Safety:** Zero commits, zero pushes, zero staged changes.

### Database Migration Mechanism
- **File:** `app/database.py` (`migrate_db()`, lines 90–113).
- **Mechanism:** Idempotent Python/SQLite schema verification. Checks `PRAGMA table_info` and `sqlite_master` before issuing forward `ALTER TABLE` or `CREATE TABLE IF NOT EXISTS` DDL statements.

### Existing Database Schemas
- **`sheets_sync_queue` Table:**
  ```sql
  CREATE TABLE IF NOT EXISTS sheets_sync_queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      attendance_id INTEGER NOT NULL UNIQUE,
      sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending', 'in_progress', 'synced', 'failed', 'dead_letter')),
      retry_count INTEGER NOT NULL DEFAULT 0,
      next_retry_at TEXT,
      lease_expires_at TEXT,
      last_error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      synced_at TEXT,
      FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE
  );
  ```
- **`attendance` Table:**
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

### Existing Services & Worker State
- **Outbox Service:** `app/services/sheets_outbox.py` manages claims (`claim_records`), lease recovery (`recover_expired_leases`), retry backoff (`calculate_retry_delay`, `schedule_retry`), and telemetry (`get_queue_stats`).
- **Google Sheets API Service:** `app/services/google_sheets.py` provides credentials loading, `build("sheets", "v4")`, formula sanitization (`sanitize_sheet_cell`), header verification (`ensure_sheet_headers`), and `append_attendance_batch`.
- **Sync Worker:** `app/services/sheets_worker.py` provides `sync_pending_records_batch` and `SheetsSyncWorker` lifecycle loop.
- **Database Override Configuration:** `app/config.py` provides `resolve_development_database_path()` allowing `DATABASE_PATH` environment overrides without code alteration.

---

## 3. Existing Architecture Vulnerabilities

The Stage 9.7B.13D audit established the concrete failure modes of the current codebase:

1. **Non-Atomic Append Operations:** `append_attendance_batch()` issues `spreadsheets.values.append(..., insertDataOption="INSERT_ROWS")`. This always inserts a new row at the bottom of the worksheet.
2. **The TOCTOU Read/Write Window:** In `sheets_worker.py` (lines 259–298), querying existing IDs from Column A and appending new rows are separate, non-atomic HTTP API calls.
3. **Absence of Fencing in SQLite:** In `sheets_outbox.py`, `mark_as_synced()` (line 343) and `schedule_retry()` (line 451) filter with `WHERE id = ?;`. They lack a concurrency token check (`claim_token`), permitting a stale, revived worker to overwrite state established by a newer claim.
4. **Unbounded Network Client Timeout:** `google_sheets.py` (line 257) uses `googleapiclient.discovery.build()`, which does not configure an explicit socket timeout on the transport layer, allowing worker threads to stall indefinitely on unresponsive network sockets while SQLite leases expire.
5. **No Lease Renewal:** A long-running or stalled batch cannot renew its lease, making expiration inevitable if execution stalls.

---

## 4. Database Migration Design

To implement deterministic row addressing and generational claim fencing, two new columns and an audit metadata table will be introduced.

### 4.1 Schema Additions to `sheets_sync_queue`

```sql
-- Forward Schema Migration for Phase 3 Reliability
ALTER TABLE sheets_sync_queue ADD COLUMN sheet_row_number INTEGER;
ALTER TABLE sheets_sync_queue ADD COLUMN claim_token INTEGER NOT NULL DEFAULT 0;
```

#### Field Specifications:
1. **`sheet_row_number` (INTEGER, NULL during migration, UNIQUE once allocated):**
   - Represents the 1-based physical row coordinate in `'Attendance_Log'`.
   - Nullable upon initial table alteration to accommodate historical records before backfill.
   - Enforced by a partial unique index:
     ```sql
     CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_queue_sheet_row
     ON sheets_sync_queue(sheet_row_number)
     WHERE sheet_row_number IS NOT NULL;
     ```
   - Once assigned, a row number is **immutable and never recycled**, even if the record enters `failed` or `dead_letter`.
2. **`claim_token` (INTEGER, NOT NULL, DEFAULT 0):**
   - Represents the monotonic claim generation of the record.
   - Initialized to 0. Incremented by 1 every time the record is claimed under `BEGIN IMMEDIATE;`.
   - Carried in-memory by the worker thread as its claim identity.
   - Enforced on all subsequent state transitions (`WHERE id = ? AND claim_token = ?`).

#### Evaluated But Rejected Fields:
- `last_claimed_at`: Redundant; `updated_at` already captures transition timestamps.
- `last_claimed_by`: Process IDs in containerized or desktop environments are non-unique across restarts; `claim_token` provides mathematically strict monotonically increasing ownership without string parsing.

### 4.2 Metadata Storage: `sheets_sync_meta`

To guarantee that the row allocation counter never regresses—even if records are archived, purged, or deleted—a dedicated metadata table is specified:

```sql
CREATE TABLE IF NOT EXISTS sheets_sync_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```
- Holds the authoritative sequence counter: `meta_key = 'next_sheet_row'`.
- Initialized to `'5'` (protecting rows 1–4).

### 4.3 SQLite Concurrency & Locking Semantics
SQLite uses database-level write locks. Under Python's standard `sqlite3` driver:
- A regular `BEGIN` starts a deferred transaction that only acquires a shared read lock, upgrading to a write lock on the first `UPDATE`/`INSERT`. Two concurrent workers can both read, attempt to upgrade, and trigger `sqlite3.OperationalError: database is locked`.
- **Architectural Requirement:** All claiming and row-allocation routines MUST execute:
  ```python
  conn.execute("BEGIN IMMEDIATE;")
  ```
  `BEGIN IMMEDIATE` guarantees that the connection obtains a reserved write lock upfront. Other workers attempting to claim are queued or wait according to SQLite's `busy_timeout` (configured to 5,000 ms), completely eliminating concurrency race windows during allocation.

---

## 5. Historical Spreadsheet Row Mapping & Preservation

The live Google Spreadsheet (`1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY`) contains authoritative historical records verified across Stages 9.7B.12D through 13C:

| Row Number | Logical Content | Current Record Identity | Status |
| :---: | :--- | :--- | :--- |
| **Row 1** | Standard 9-Column Headers | `Attendance ID`, `Date`, `Time`, ... | **LOCKED / PROTECTED** |
| **Row 2** | Synthetic Baseline Record | Attendance ID `-92022` | **LOCKED / PROTECTED** |
| **Row 3** | Manual Test Record 1 | Attendance ID `1` (`MANUAL-001`) | **LOCKED / PROTECTED** |
| **Row 4** | Manual Test Record 2 | Attendance ID `2` (`MANUAL-002`) | **LOCKED / PROTECTED** |
| **Row 5+** | Future Synchronizations | Any new attendance scan | **ALLOCATABLE** |

### Historical Migration Procedure:
1. **Backfill Existing Queue Records:**
   ```sql
   UPDATE sheets_sync_queue SET sheet_row_number = 3 WHERE attendance_id = 1;
   UPDATE sheets_sync_queue SET sheet_row_number = 4 WHERE attendance_id = 2;
   ```
2. **Initialize Allocation Counter:**
   ```sql
   INSERT OR IGNORE INTO sheets_sync_meta (meta_key, meta_value, updated_at)
   VALUES ('next_sheet_row', '5', '2026-11-24T12:00:00Z');
   ```
3. **Pre-Flight Live Verification Constraint:**
   Before any future worker performs its first write to the spreadsheet, a verification check must query `'Attendance_Log'!A1:A4`. It must confirm:
   - Row 1 header exists.
   - Row 2 cell equals `-92022`.
   - Row 3 cell equals `1`.
   - Row 4 cell equals `2`.
   If any discrepancy is found, the worker must raise `GoogleSheetsConflictError` and abort without writing.

---

## 6. Deterministic Row Allocation Algorithm

### 6.1 Requirements
1. **Permanent Assignment:** Once assigned, `sheet_row_number` never changes for that attendance record.
2. **Atomic Reservation:** Allocation occurs inside `BEGIN IMMEDIATE;` before any remote API dispatch.
3. **Monotonic Progression:** Each new attendance record receives the next strictly increasing integer.
4. **Crash Invariance:** If a worker crashes after allocating a row but before remote write, the assigned row is preserved in SQLite. Subsequent retries reuse the exact same row.
5. **No Row Recycling:** If a record fails permanently (`dead_letter`), its row number remains reserved and is never reallocated to a different record, preventing semantic collisions.

### 6.2 Allocation Algorithm Pseudocode

```python
def allocate_sheet_rows_for_batch(conn: sqlite3.Connection, queue_ids: list[int], min_start_row: int = 5) -> dict[int, int]:
    """
    Allocates permanent, sequential spreadsheet row numbers for a batch of queue records.
    Must be executed inside an active BEGIN IMMEDIATE transaction.
    """
    cursor = conn.cursor()

    # 1. Fetch any already-allocated rows in this batch (e.g. retries)
    placeholders = ",".join("?" for _ in queue_ids)
    cursor.execute(
        f"SELECT id, sheet_row_number FROM sheets_sync_queue WHERE id IN ({placeholders});",
        queue_ids
    )
    allocations = {}
    unallocated_ids = []
    for row in cursor.fetchall():
        if row["sheet_row_number"] is not None:
            allocations[row["id"]] = row["sheet_row_number"]
        else:
            unallocated_ids.append(row["id"])

    if not unallocated_ids:
        return allocations

    # 2. Fetch current counter from sheets_sync_meta (or initialize)
    cursor.execute("SELECT meta_value FROM sheets_sync_meta WHERE meta_key = 'next_sheet_row';")
    meta_row = cursor.fetchone()
    if meta_row:
        next_row = max(int(meta_row["meta_value"]), min_start_row)
    else:
        # Fallback to MAX(sheet_row_number) + 1 if meta table not yet populated
        cursor.execute("SELECT MAX(sheet_row_number) AS max_row FROM sheets_sync_queue;")
        max_val = cursor.fetchone()["max_row"]
        next_row = max((max_val + 1) if max_val is not None else min_start_row, min_start_row)

    # 3. Assign sequential rows to unallocated queue records
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for q_id in unallocated_ids:
        allocations[q_id] = next_row
        cursor.execute(
            """
            UPDATE sheets_sync_queue
            SET sheet_row_number = ?, updated_at = ?
            WHERE id = ? AND sheet_row_number IS NULL;
            """,
            (next_row, now_utc, q_id)
        )
        next_row += 1

    # 4. Update the persistent sequence counter
    cursor.execute(
        """
        INSERT INTO sheets_sync_meta (meta_key, meta_value, updated_at)
        VALUES ('next_sheet_row', ?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value, updated_at = excluded.updated_at;
        """,
        (str(next_row), now_utc)
    )

    return allocations
```

---

## 7. Google Sheets Write Architecture

### 7.1 Replacement of `values().append` with `values().update`
Instead of appending dynamically to `'Attendance_Log'!A:I`, writes will target the exact rectangular grid range corresponding to the allocated row(s).

#### Single-Record Range Format:
```
'Attendance_Log'!A{row}:I{row}
```
*Example for Row 5:* `'Attendance_Log'!A5:I5`

#### Contiguous Batch Range Format:
When processing a batch of $N$ contiguous records starting at row $R_{start}$ and ending at $R_{end}$:
```
'Attendance_Log'!A{R_start}:I{R_end}
```
*Example for Rows 5 to 24:* `'Attendance_Log'!A5:I24`

### 7.2 Protocol & Parameters
- **HTTP Method:** `PUT`
- **Endpoint:**
  `https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/'Attendance_Log'!A{start}:I{end}?valueInputOption=RAW`
- **`valueInputOption` = `RAW`:** Enforced to ensure string values prepended with `'` (formula sanitization via `sanitize_sheet_cell`) and integer attendance IDs are written literally without Google Sheets re-evaluating equations or localized date formats.

### 7.3 Response Verification
The Google Sheets API returns an `UpdateValuesResponse`:
```json
{
  "spreadsheetId": "1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY",
  "updatedRange": "'Attendance_Log'!A5:I24",
  "updatedRows": 20,
  "updatedColumns": 9,
  "updatedCells": 180
}
```
The update service must verify:
1. `updatedRows == len(batch_records)`
2. `updatedRange` starts with the requested tab name and row coordinate.

### 7.4 Destination Idempotence vs Transport Delivery
- **Transport Delivery:** Remotely dispatched over HTTP/TCP, which is inherently **at-least-once**. Retries, network delays, or dropped TCP ACKs mean Google's backend may receive the request more than once.
- **Destination Idempotence:** Because the destination is a deterministic cell range (`A{R}:I{R}`), multiple identical writes overwrite the exact same 9 cells. The physical row count does not change, and no duplicate logical record is created.
- **Payload Immutability:** Payloads are reconstructed from the authoritative `attendance`, `students`, and `class_sessions` SQLite tables at dispatch time. Even if an older worker resumes, it overwrites with the identical authoritative attendance record.

---

## 8. Claim Fencing Design

### 8.1 Monotonic Claim Increment
When records are claimed, SQLite atomically increments `claim_token`:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'in_progress',
    claim_token = claim_token + 1,
    lease_expires_at = ?,
    updated_at = ?
WHERE id IN (...)
  AND sync_status IN ('pending', 'failed');
```
The returned record dictionary holds the exact `claim_token` generated during that transaction.

### 8.2 Fenced State Transitions

Every subsequent mutation MUST validate both the `id` and the `claim_token`:

#### 1. Mark as Synced:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'synced',
    synced_at = ?,
    lease_expires_at = NULL,
    last_error = NULL,
    updated_at = ?
WHERE id = ?
  AND claim_token = ?
  AND sync_status = 'in_progress';
```
If `cursor.rowcount == 0`:
- Indicates the claim was lost (lease expired and record was reclaimed by another worker).
- The worker MUST NOT raise an unhandled exception. It logs:
  `WARNING: Stale worker attempted to mark Queue ID %s as synced (token=%s). Claim was lost; skipping SQLite acknowledgement.`

#### 2. Schedule Retry:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'failed',
    retry_count = retry_count + 1,
    next_retry_at = ?,
    lease_expires_at = NULL,
    last_error = ?,
    updated_at = ?
WHERE id = ?
  AND claim_token = ?
  AND sync_status = 'in_progress';
```
If `cursor.rowcount == 0`:
- Ignored safely. Stale worker errors do not overwrite active claims.

#### 3. Transition to Dead Letter:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'dead_letter',
    retry_count = retry_count + 1,
    next_retry_at = NULL,
    lease_expires_at = NULL,
    last_error = ?,
    updated_at = ?
WHERE id = ?
  AND claim_token = ?
  AND sync_status = 'in_progress';
```

#### 4. Expired Lease Recovery:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'pending',
    lease_expires_at = NULL,
    updated_at = ?
WHERE sync_status = 'in_progress'
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at <= ?;
```
*Note:* Recovery resets status to `'pending'` but **does not decrement `claim_token`**. When the next worker claims the record, `claim_token` increments again, ensuring any in-flight requests from the previous worker are permanently fenced out.

---

## 9. Lease Duration and HTTP Timeout Coordination

### 9.1 Google API Client Transport Timeout Hardening
Inspection of `app/services/google_sheets.py` revealed that `build("sheets", "v4")` uses default socket timeouts. Under standard Python `httplib2` or `requests` transports, sockets can block indefinitely on dropped packets.

#### Proposed Hardening in `get_sheets_service()`:
To guarantee that HTTP socket calls never hang past our safety budget, `google_sheets.py` will configure an explicit HTTP transport timeout:
```python
import httplib2
from google_auth_httplib2 import AuthorizedHttp

def get_sheets_service(credentials=None, credentials_path=None, timeout_seconds=15.0):
    # Construct an httplib2 transport with explicit socket timeout
    http_transport = httplib2.Http(timeout=timeout_seconds)
    authorized_http = AuthorizedHttp(credentials, http=http_transport)
    return build("sheets", "v4", http=authorized_http, cache_discovery=False)
```

### 9.2 Budget Coordination Parameters

| Parameter | Recommended Value | Justification |
| :--- | :---: | :--- |
| **HTTP Socket Timeout** | **15 seconds** | Sufficient for Google Cloud API SLA; fails fast on network drops. |
| **Batch API Strategy** | **1 HTTP Call per Batch** | Using `values().update` with multi-row range (`A{start}:I{end}`) guarantees entire batch completes in a single 15s budget. |
| **Lease Duration** | **180 seconds (3 min)** | Provides a $12\times$ safety margin over the 15s socket timeout. |
| **Poll Interval** | **10 seconds** | Standard background poll frequency. |
| **Lease Recovery Interval** | **Every cycle (10s)** | Recovers expired records immediately on worker start. |

### 9.3 Invariance to Operating System Freezes
If an edge device is suspended (laptop lid closed, VM paused, OS CPU starvation) for >180 seconds:
- Even if the worker wakes up 30 minutes later, **Claim Fencing (Layer 2)** prevents it from modifying SQLite.
- Even if the worker dispatches its delayed HTTP packet, **Deterministic Row Addressing (Layer 1)** ensures it updates the identical row $R$, completely preventing duplicate row creation.

---

## 10. Worker Processing Flow & Transaction Boundaries

To prevent SQLite lock contention, SQLite write transactions must never be held open while waiting on Google API network calls.

```
[Start Batch]
      |
      v
+------------------------------------------------------------------+
| PHASE 1: SQLite Immediate Transaction (conn.execute("BEGIN IMMEDIATE;"))
| 1. Recover expired leases (lease_expires_at <= now_utc).         |
| 2. Claim up to batch_size records (increment claim_token).       |
| 3. Allocate sheet_row_number sequentially for new records.       |
| 4. Commit transaction & release SQLite lock.                     |
+------------------------------------------------------------------+
      |
      v
+------------------------------------------------------------------+
| PHASE 2: In-Memory Preparation (No DB Lock Held)                 |
| 1. Reconstruct 9-column attendance payloads from query data.     |
| 2. Group contiguous rows (e.g. A5:I24).                          |
+------------------------------------------------------------------+
      |
      v
+------------------------------------------------------------------+
| PHASE 3: Pre-Dispatch Lease Validation (Short Read Transaction)  |
| 1. Verify lease_expires_at > now_utc and status == 'in_progress'.|
| 2. If expired: abort dispatch, allow natural recovery.           |
+------------------------------------------------------------------+
      |
      v
+------------------------------------------------------------------+
| PHASE 4: Remote Network Dispatch (Zero SQLite Locks Held)        |
| 1. Dispatch PUT values().update to 'Attendance_Log'!A{start}:I{end}
| 2. Catch network exceptions (TimeoutError, HttpError).           |
+------------------------------------------------------------------+
      |
      v
+------------------------------------------------------------------+
| PHASE 5: SQLite Acknowledgement (conn.execute("BEGIN IMMEDIATE;"))
| If Success:                                                      |
|   UPDATE sheets_sync_queue SET sync_status='synced'              |
|   WHERE id=? AND claim_token=? AND sync_status='in_progress';    |
| If Failed:                                                       |
|   UPDATE sheets_sync_queue SET sync_status='failed'              |
|   WHERE id=? AND claim_token=? AND sync_status='in_progress';    |
| Commit transaction.                                              |
+------------------------------------------------------------------+
      |
      v
[End Batch / Return Telemetry]
```

### Partial Batch Failure Handling:
If Google Sheets returns an error on a batch update, all records in that batch update call are marked failed with exponential backoff scheduled. Because row numbers were already committed in Phase 1, the subsequent retry will update the exact same destination rows without gap creation.

---

## 11. Testing Blueprint (Isolated Regression Suite)

*Note: None of these tests are executed in Stage 13E. This blueprint specifies the test suite for future implementation stages.*

All tests will use a disposable copy of `attendance_manual_test.db` and an in-memory `ThreadSafeMockSheetsService`.

| Test ID | Scenario | Setup / Fixture | Execution | Expected Database State | Expected Mock Sheets State | Failure Criteria |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Test A** | Deterministic Allocation | Fresh disposable DB with 2 pending records. | Call allocator twice on same records. | Record 1 has Row 5; Record 2 has Row 6. Counter is 7. | N/A (SQLite only) | Row numbers change on second call, or non-sequential rows. |
| **Test B** | Concurrent Allocation | 2 threads with `threading.Barrier(2)` claiming records. | Both threads call `claim_records` simultaneously. | Disjoint sets; Record 1 has Row 5; Record 2 has Row 6. No duplicate rows. | N/A | Duplicate `sheet_row_number` allocated or `sqlite3.BusyError` unhandled. |
| **Test C** | Stale Worker Fencing | Queue ID 1 claimed with `claim_token = 1`. | Lease expires; Worker B claims with `claim_token = 2`. Worker A calls `mark_as_synced(token=1)`. | Record remains owned by Worker B (`claim_token = 2`). Worker A update affects 0 rows. | N/A | Worker A overwrites Worker B's status or token. |
| **Test D** | Stage 13C C2 Race Reproduction | Worker A allocates Row 5, pauses before update. | Worker B claims (Row 5 reused), writes to Row 5, marks synced. Worker A resumes and writes to Row 5. | Queue ID 1 `sync_status = 'synced'`. Worker A acknowledgement rejected by fencing token. | Mock sheet has exactly 1 row for ID 1 at Row 5 (`A5:I5`). Total rows in sheet: 5. | Total sheet rows = 6, or ID 1 appears twice. |
| **Test E** | Crash After Remote Update | Worker updates Row 5 in mock sheet, then process killed before SQLite ACK. | Lease expires. Worker B claims (Row 5 retained) and updates Row 5. | Queue ID 1 marked `synced`. `sheet_row_number = 5`. | Exactly 1 entry at Row 5. Overwritten idempotently. | Row 6 created, or duplicate entries. |
| **Test F** | Remote Timeout Outcome | Mock accepts update to Row 5, but raises `socket.timeout`. | Worker catches timeout, schedules retry. Worker retries on next cycle. | Record transitions `failed` $\rightarrow$ `synced`. Assigned row remains 5. | Row 5 contains correct attendance data. No duplicate row. | New row allocated on retry. |
| **Test G** | Historical Preservation | Sheet has Rows 1–4 populated. DB has records 1 & 2 mapped to rows 3 & 4. | Synchronize new Attendance ID 3. | Attendance ID 3 assigned `sheet_row_number = 5`. | Rows 1–4 untouched. ID 3 written to `A5:I5`. | Any modification to rows 1–4. |
| **Test H** | Concurrent Updates to Same ID | Two workers both attempt update to Row 5 with identical payload. | Concurrent thread execution. | Final DB state `synced`. | Row 5 updated with valid data. | Corrupted row or duplicate appends. |
| **Test I** | Retry & Dead-Letter Retention | Record fails 5 times, moves to `dead_letter`. | Induce 5 simulated failures. Requeue manually. | `sheet_row_number` is preserved across all 5 failures and requeue. | Row number remains constant throughout lifecycle. | Row number cleared or changed. |
| **Test J** | Migration & Rollback | Copy of `attendance_manual_test.db`. | Apply migration script. Verify. Apply rollback script. Verify. | Forward: columns exist, backfill verified. Rollback: columns removed/restored safely. | N/A | Data loss in `attendance` or `sheets_sync_queue`. |

---

## 12. Implementation Phasing

Implementation must be partitioned into seven distinct, sequential, independently reviewable milestones:

```
Stage 9.7B.13F: Database Migration & Schema Constraints
      |
      v
Stage 9.7B.13G: Transactional Deterministic Row Allocator Service
      |
      v
Stage 9.7B.13H: Claim-Token Fencing Across Outbox State Transitions
      |
      v
Stage 9.7B.13I: Deterministic Google Sheets Update Service & Client Timeout Hardening
      |
      v
Stage 9.7B.13J: Worker Pipeline Integration & Batch Timeout Budgeting
      |
      v
Stage 9.7B.13K: Isolated Failure Injection & Concurrency Regression Tests
      |
      v
Stage 9.7B.13L: Final Read-Only Security, Migration & Compatibility Preflight Audit
```

### Milestone Specifications:

#### Milestone 13F: Database Migration & Schema Constraints
- **Scope:** Add `sheet_row_number`, `claim_token` to `sheets_sync_queue`, create `sheets_sync_meta` table, and partial unique index.
- **Files Modified:** `app/models.py`, `app/database.py`.
- **Database Impact:** Forward schema addition; non-breaking.
- **Rollback:** Drop added index and columns (or restore backup).
- **Acceptance Criteria:** `init_db()` and `migrate_db()` apply cleanly to clean and populated databases; `PRAGMA integrity_check` passes.

#### Milestone 13G: Transactional Deterministic Row Allocator Service
- **Scope:** Implement `allocate_sheet_rows_for_batch()` in `app/services/sheets_outbox.py`.
- **Files Modified:** `app/services/sheets_outbox.py`.
- **Database Impact:** Writes to `sheets_sync_meta` and updates `sheet_row_number`.
- **Rollback:** Revert functions in `sheets_outbox.py`.
- **Acceptance Criteria:** Tests A and B pass with 100% concurrency safety.

#### Milestone 13H: Claim-Token Fencing Across Outbox State Transitions
- **Scope:** Update `claim_records()`, `mark_as_synced()`, and `schedule_retry()` to increment and check `claim_token`.
- **Files Modified:** `app/services/sheets_outbox.py`.
- **Database Impact:** Enforces conditional updates with `claim_token`.
- **Rollback:** Revert `WHERE claim_token = ?` clauses.
- **Acceptance Criteria:** Test C passes; stale worker acknowledgements affect 0 rows.

#### Milestone 13I: Deterministic Google Sheets Update Service & Client Timeout Hardening
- **Scope:** Implement `update_attendance_batch()` in `app/services/google_sheets.py`; configure 15s transport timeout in `get_sheets_service()`.
- **Files Modified:** `app/services/google_sheets.py`.
- **Database Impact:** None.
- **Rollback:** Revert to `append_attendance_batch()`.
- **Acceptance Criteria:** Unit tests verify PUT range formatting and timeout enforcement.

#### Milestone 13J: Worker Pipeline Integration & Batch Timeout Budgeting
- **Scope:** Wire deterministic update and pre-dispatch lease verification into `sync_pending_records_batch()`.
- **Files Modified:** `app/services/sheets_worker.py`.
- **Database Impact:** End-to-end integration of Phase 1–5 lifecycle.
- **Rollback:** Revert `sheets_worker.py` to previous batch pipeline.
- **Acceptance Criteria:** Worker executes end-to-end against mock sheets service.

#### Milestone 13K: Isolated Failure Injection & Concurrency Regression Tests
- **Scope:** Execute comprehensive test suite (Tests A through J) on disposable database.
- **Files Modified:** `tests/test_sheets_reliability_phase3.py` (new test file).
- **Database Impact:** Confined strictly to disposable test databases.
- **Rollback:** Delete test artifacts.
- **Acceptance Criteria:** All 10 tests (A–J) pass with 0 failures, including reproduction of Stage 13C C2 with 0 duplicate rows.

#### Milestone 13L: Final Read-Only Security, Migration & Compatibility Preflight Audit
- **Scope:** Verify production and manual-test database integrity, hash checks, and live spreadsheet pre-flight readiness before authorized live migration.
- **Files Modified:** None (Strict read-only).
- **Database Impact:** None.
- **Acceptance Criteria:** Clean audit report with zero discrepancies.

---

## 13. Rollback & Recovery Plan

1. **Pre-Migration Snapshot:**
   Before applying Milestone 13F to any existing database, a byte-for-byte backup is created in `backups/` and its SHA-256 hash recorded.
2. **Schema Reversibility:**
   SQLite 3.35.0+ supports `ALTER TABLE DROP COLUMN`. In the event of rollback:
   ```sql
   DROP INDEX IF EXISTS idx_sync_queue_sheet_row;
   DROP TABLE IF EXISTS sheets_sync_meta;
   -- Reverting worker code ensures added columns are simply ignored if drop column is deferred
   ```
3. **Data Protection Invariance:**
   The `attendance`, `students`, `class_sessions`, and `admin_users` tables are never modified by this subsystem. Their records remain 100% intact throughout all migrations and rollbacks.

---

## 14. Risks and Unresolved Questions

1. **Human Edits to Google Sheets:**
   - *Risk:* If a user manually inserts or deletes rows in Google Sheets via the browser, row coordinates will drift.
   - *Mitigation:* Document in operational runbooks that `'Attendance_Log'` is a machine-managed ledger. In Milestone 13I, add a header-check validator that compares Column A IDs against expected row numbers before writing.
2. **Google Sheets Maximum Dimensions:**
   - *Risk:* Google Sheets tabs default to 1,000 rows.
   - *Mitigation:* In Milestone 13I, the update service will inspect spreadsheet metadata and call `appendDimension` if the allocated row exceeds current sheet capacity.
3. **Google API Quota Consumption:**
   - *Quota:* 300 requests per minute per project; 60 requests per minute per user.
   - *Mitigation:* Milestone 13J aggregates all contiguous rows in a batch into a single HTTP update call, consuming exactly 1 quota token per 20-record batch.

---

## 15. Source-Code References

- `app/database.py`: Lines 19–46 (`get_db_connection`), lines 90–113 (`migrate_db`), lines 115–142 (`init_db`).
- `app/models.py`: Lines 19–44 (`class_sessions`, `attendance`), lines 57–72 (`sheets_sync_queue`), lines 74–109 (indexes).
- `app/services/sheets_outbox.py`: Lines 182–276 (`claim_records`), lines 278–316 (`recover_expired_leases`), lines 318–360 (`mark_as_synced`), lines 402–480 (`schedule_retry`).
- `app/services/google_sheets.py`: Lines 233–265 (`get_sheets_service`), lines 278–299 (`sanitize_sheet_cell`), lines 424–538 (`ensure_sheet_headers`), lines 618–686 (`append_attendance_batch`).
- `app/services/sheets_worker.py`: Lines 71–354 (`sync_pending_records_batch`), lines 356–485 (`SheetsSyncWorker`).
- `app/config.py`: Lines 110–142 (`resolve_development_database_path`), lines 166–178 (Google Sheets settings).

---

## 16. Final Verdict & Next Steps

`READY — IMPLEMENTATION BLUEPRINT COMPLETE`

**Justification:**
- An implementation-ready, production-grade blueprint has been formulated that completely eliminates duplicate delivery races.
- Deterministic row addressing, generational claim fencing, pre-dispatch verification, and transport timeout hardening have been designed in detail with exact SQL pseudocode and data flow diagrams.
- Existing Google Sheet rows 1 through 4 are explicitly protected and locked.
- A 7-stage phased implementation roadmap (Milestones 13F–13L) provides safe, independently verifiable increments.
- Zero source-code, database, or network modifications were performed during this stage.

As instructed, I have **NOT** proceeded to Stage 9.7B.13F. Standing by for your review and explicit approval.
