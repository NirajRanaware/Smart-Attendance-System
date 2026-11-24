# Stage 9.7B.13D — Google Sheets Exactly-Once Delivery Architecture Review

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Execution Date:** 2026-11-24
**Audit Mode:** STRICT READ-ONLY ARCHITECTURE AUDIT
**Target Systems:** Google Sheets Outbox Subsystem, Background Worker, and API Service Layer
**Lead Architect/Auditor:** Antigravity

---

## 1. Executive Summary

During Stage 9.7B.13C (Isolated Lease Recovery & Concurrent Worker Reliability Test), an empirical reliability audit identified an architectural race condition in the Google Sheets synchronization subsystem. Specifically, under **Test C2 (Slow Operation & Lease-Expiration Race in the TOCTOU Window)**, when a worker experiences severe network latency or thread suspension *after* querying existing IDs from Column A but *before* executing the remote append, its lease expires in SQLite. A recovering worker then claims and synchronizes the record. When the stalled worker eventually resumes, it dispatches its stale append payload, resulting in a duplicate row in Google Sheets.

This Stage 9.7B.13D review conducts an exhaustive, zero-write architectural investigation to:
1. Reconstruct the root causes of the C2 race condition in existing code.
2. Evaluate six competing architectural patterns (Options A through F) for resolving duplicate delivery.
3. Establish clear theoretical and practical distinctions between **at-least-once transport delivery**, **pre-write duplicate suppression**, and **exactly-once logical row representation**.
4. Recommend a production-grade, multi-layered architecture that guarantees zero duplicate rows in Google Sheets while preserving local SQLite autonomy, historical spreadsheet data, and offline IoT scanning.

**Final Audit Verdict:** `READY — RECOMMENDED ARCHITECTURE IDENTIFIED`

---

## 2. Existing Implementation Review & Code References

An exhaustive inspection of `app/services/sheets_worker.py`, `app/services/sheets_outbox.py`, `app/services/google_sheets.py`, `app/models.py`, and database initialization routines reveals the exact operational mechanics of the current outbox subsystem:

### 1. How a Record is Claimed
- **Function:** `claim_records()` in `app/services/sheets_outbox.py` (lines 182–276).
- **Caller:** `sync_pending_records_batch()` in `app/services/sheets_worker.py` (lines 178–182).
- **Mechanism:**
  - Begins an explicit immediate SQLite transaction (`conn.execute("BEGIN IMMEDIATE;")`).
  - Queries candidate rows:
    ```sql
    SELECT id FROM sheets_sync_queue
    WHERE sync_status IN ('pending', 'failed')
      AND (next_retry_at IS NULL OR next_retry_at <= ?)
    ORDER BY id ASC
    LIMIT ?;
    ```
  - Atomically updates candidate rows to `'in_progress'`:
    ```sql
    UPDATE sheets_sync_queue
    SET sync_status = 'in_progress',
        lease_expires_at = ?,
        updated_at = ?
    WHERE id IN (...)
      AND sync_status IN ('pending', 'failed');
    ```
  - Commits the transaction and returns record dictionaries.

### 2. How its Lease is Created and Renewed
- **Creation:** In `claim_records()` (lines 201–203 of `app/services/sheets_outbox.py`), the expiration timestamp is computed as `dt_now + timedelta(seconds=lease_duration_seconds)` (default `DEFAULT_LEASE_DURATION_SECONDS = 60`) and formatted as an ISO 8601 UTC string (`lease_expires_utc`).
- **Renewal / Heartbeating:** **NOT SUPPORTED.** No function exists in `sheets_outbox.py` or `sheets_worker.py` to extend, refresh, or heartbeat an active lease while a batch is executing. The lease duration is fixed at claim time.

### 3. How Expired Leases are Recovered
- **Function:** `recover_expired_leases()` in `app/services/sheets_outbox.py` (lines 278–316).
- **Caller:** `sync_pending_records_batch()` (line 123 of `sheets_worker.py`), executed at the entry point of every synchronization cycle.
- **Mechanism:**
  - Executes under `BEGIN IMMEDIATE;`:
    ```sql
    UPDATE sheets_sync_queue
    SET sync_status = 'pending',
        lease_expires_at = NULL,
        updated_at = ?
    WHERE sync_status = 'in_progress'
      AND lease_expires_at IS NOT NULL
      AND lease_expires_at <= ?;
    ```
  - Resets orphaned records to `'pending'` without incrementing `retry_count`.
  - Records with valid, unexpired leases (`lease_expires_at > now_utc`) remain completely untouched.

### 4. How Duplicate Detection Works
- **Functions:**
  - `get_existing_attendance_ids()` in `app/services/google_sheets.py` (lines 688–741).
  - Pre-check partitioning in `sync_pending_records_batch()` in `app/services/sheets_worker.py` (lines 256–285).
- **Mechanism:**
  - Queries the range `'Attendance_Log'!A2:A` via `service.spreadsheets().values().get(...)`.
  - Parses cell values into a Python set of integers (`existing_ids: Set[int]`).
  - Partitions the claimed batch in memory:
    ```python
    records_to_append = [r for r in claimed if r["attendance_id"] not in existing_ids]
    already_synced_records = [r for r in claimed if r["attendance_id"] in existing_ids]
    ```
  - Records already present in Column A bypass the remote append entirely and are marked `'synced'` in SQLite.
- **Limitation:** This is a **Check-Then-Act (Time-Of-Check to Time-Of-Use)** pattern. The query (`values().get`) and the subsequent write (`values().append`) are two decoupled, non-atomic HTTP API calls.

### 5. When the Remote Append Request is Dispatched
- **Function:** `append_attendance_batch()` in `app/services/google_sheets.py` (lines 618–686).
- **Caller:** `sync_pending_records_batch()` in `app/services/sheets_worker.py` (lines 292–298).
- **Timing:** Dispatched **after** Column A lookup and partitioning have completed, but **before** SQLite status is updated to `'synced'`.
- **API Call:** Sends an HTTP `POST` to `https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/'Attendance_Log'!A:I:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS`.

### 6. When SQLite is Updated to `synced`
- **Function:** `mark_as_synced()` in `app/services/sheets_outbox.py` (lines 318–360).
- **Caller:** `sync_pending_records_batch()` (lines 328–335 of `sheets_worker.py`).
- **Timing:** Executed sequentially in a loop for each record **only after** `append_attendance_batch()` returns `success=True`.
- **SQL Statement:**
  ```sql
  UPDATE sheets_sync_queue
  SET sync_status = 'synced',
      synced_at = ?,
      lease_expires_at = NULL,
      last_error = NULL,
      updated_at = ?
  WHERE id = ?;
  ```

### 7. What Happens if Remote Append Succeeds but SQLite Acknowledgement Fails
- If a crash, disk full error, or power interruption occurs after Google Sheets receives and commits the append but before `mark_as_synced()` executes in SQLite:
  - The record in `sheets_sync_queue` remains with `sync_status = 'in_progress'`.
  - When its lease expires, `recover_expired_leases()` resets it to `'pending'`.
  - A subsequent worker claims the record.
  - The subsequent worker queries Column A (`get_existing_attendance_ids()`).
  - Because Google Sheets already committed the row in the previous attempt, its Attendance ID is now present in Column A.
  - The subsequent worker partitions it into `already_synced_records`, skips the remote append, and marks it `'synced'` in SQLite.
- **Verdict on Post-Append Crash:** Under normal, non-racing worker retries, the existing duplicate check is **self-healing** for crashes occurring after remote write.

### 8. Whether a Stale Worker Can Continue After its Lease Expires
- **YES.** In `sheets_worker.py`, once records are returned by `claim_records()`, the worker retains those records in Python memory.
- There is **no re-validation** of lease validity or current clock time before calling `append_attendance_batch()`.
- If a worker process or thread stalls (e.g., OS page thrashing, prolonged garbage collection, thread preemption, or network socket freeze), it resumes execution at whatever instruction was paused and blindly dispatches the append payload regardless of elapsed time.

### 9. Whether Stale Workers Can Update Queue State After Reclaim
- **YES.** In `mark_as_synced()` (lines 343–352 of `sheets_outbox.py`) and `schedule_retry()` (lines 451–460 of `sheets_outbox.py`), the SQL statement updates the record using an unconditional filter:
  ```sql
  WHERE id = ?;
  ```
- Neither function checks `AND sync_status = 'in_progress'`, `AND lease_expires_at > ?`, or any ownership/token identifier.
- Consequently, if Worker B reclaims and processes a record, and stale Worker A subsequently wakes up and encounters an error or finishes, Worker A's update statement will execute blindly, overwriting Worker B's state.

---

## 3. Stage 9.7B.13C Test C2 Failure Analysis

### Exact Sequence Reconstruction

```
[Worker A]               [SQLite Outbox]             [Google Sheets]             [Worker B]
     |                          |                            |                        |
1. Claim Rec 1 (Lease=60s) ---->| (sync_status='in_progress')|                        |
2. Read Col A ---------------------------------------------->|                        |
3. Col A returns empty <-------------------------------------|                        |
4. Prepare append payload       |                            |                        |
5. STALL / PAUSE [Thread A]     |                            |                        |
     |                          |                            |                        |
     :                          |                            |                        |
     : [60s Lease Expires] ---->| (lease_expires_at <= now)  |                        |
     :                          |                            |                        |
6.   :                   Recover Leases <--------------------------------------- Run Worker B
     :                          | (sync_status='pending')    |                        |
7.   :                          |<---------------------------------------------- Claim Rec 1
     :                          | (sync_status='in_progress')|                        |
8.   :                          |                            |<----------------- Read Col A
     :                          |                            |-----------------> Empty
9.   :                          |                            |<----------------- values.append(Rec 1)
     :                          |                            | (Row 3 appended)       |
10.  :                          |<---------------------------------------------- Mark Synced
     :                          | (sync_status='synced')     |                        |
     :                          |                            |                        v
11. RESUME [Thread A]           |                            |                   (Completed)
12. values.append(Rec 1) ----------------------------------->|
     |                          |                            | (Row 4 appended: DUPLICATE!)
13. Mark Synced --------------->| (Overwrites Synced)        |
     v                          v                            v
```

### Breakdown of Specific Mechanism Failures
1. **Blind Append API:** Google Sheets `values().append` with `insertDataOption="INSERT_ROWS"` always appends a new physical row at the bottom of the worksheet. It treats every HTTP request as an independent insertion.
2. **The TOCTOU Race Window:** Worker A verified uniqueness at Step 2. However, the state of the remote spreadsheet changed at Step 9. Worker A had no mechanism to detect that its pre-check was invalidated.
3. **Absence of Stale-Worker Fencing:** Worker A possessed no token or lease check prior to calling Google Sheets API at Step 12.
4. **Non-Transactional Remote System:** SQLite and Google Sheets API cannot participate in a distributed two-phase commit (2PC) transaction. A write to Google Sheets cannot be rolled back if SQLite rejects the acknowledgement.

---

## 4. Architectural Solutions Evaluation

### Option A — Pre-Append Lease Verification
- **Description:** Immediately before calling `append_attendance_batch()`, the worker queries SQLite to verify that `sync_status == 'in_progress'` and `lease_expires_at > now_utc`.
- **Analysis:**
  - If the lease already expired while formatting rows, the worker aborts locally before touching the network.
  - **Why this does NOT eliminate the race:** The verification occurs at time $T_{check}$. The network packet travels across the Internet, negotiates TLS, and queues on Google's backend, arriving at $T_{commit}$. If the network stalls *after* the local verification check while the HTTP packet is in transit on the wire, the lease can expire in SQLite while the request is in-flight. Worker B recovers the lease and writes. Then Google's server processes Worker A's in-flight request.
- **Verdict:** Necessary defensive check, but insufficient on its own.

### Option B — Lease Fencing Tokens
- **Description:** Add a monotonically increasing integer column `claim_token INTEGER NOT NULL DEFAULT 0` to `sheets_sync_queue`. On each claim, `claim_token` is incremented. All database acknowledgements require `WHERE id = ? AND claim_token = ?`.
- **Analysis:**
  - Effectively prevents stale workers from corrupting SQLite state (`mark_as_synced` will affect 0 rows if Worker B reclaimed the record).
  - **Why this cannot prevent duplicate Google Sheets rows:** Google Sheets API v4 has **no concept of fencing tokens**. You cannot send a custom SQLite fencing token to `spreadsheets.values.append` and expect Google's servers to reject the append if a higher token was seen. The stale HTTP request will still create a row in the spreadsheet.
- **Verdict:** Solves local database acknowledgement races; does not solve remote append duplication.

### Option C — Lease Duration and HTTP Timeout Coordination
- **Description:** Enforce strict client-side HTTP socket timeouts (e.g., connect: 5s, read: 15s; total budget: 20s) and set `lease_duration_seconds` to a significantly larger value (e.g., 180s to 300s).
- **Analysis:**
  - If an HTTP request hangs, the client-side socket throws a `TimeoutError` at 20s, which is 160 seconds before the lease can expire.
  - The worker enters error handling and schedules a retry or fails safely long before any recovery worker can claim the record.
  - **Remaining Risks:** If Google received and committed the write at second 19, but the ACK was dropped, the client-side timeout still leaves write status ambiguous. Furthermore, process freezing (OS paging, VM suspension) can pause a process past the lease duration regardless of socket timeouts.
- **Verdict:** Highly effective operational hardening, but still probabilistic rather than deterministic.

### Option D — Deterministic Spreadsheet Row Assignment (Idempotent Overwrite)
- **Description:** Replace blind `values().append` with deterministic, targeted range updates (`values().update`). Each attendance record is assigned a stable, dedicated row number $R$ in the target worksheet.
- **Mechanism:**
  1. Each attendance record is mapped to a dedicated target row $R$ (e.g., Range `'Attendance_Log'!A{R}:I{R}`).
  2. The write operation uses HTTP `PUT` to `https://sheets.googleapis.com/v4/spreadsheets/{id}/values/'Attendance_Log'!A{R}:I{R}?valueInputOption=RAW`.
  3. If Worker A writes to row $R$, and Worker B writes to row $R$, both workers write to the **exact same logical and physical row**.
  4. Retries, duplicate dispatches, concurrent workers, and delayed packets all overwrite row $R$ with the identical payload.
- **Analysis of Deterministic Row Mapping Strategies:**
  - *Strategy D.1 (Mathematical Offset):* $R = \text{attendance\_id} + \text{offset}$.
    - *Risk:* If attendance IDs have gaps (deletions, manual tests), blank rows appear in the spreadsheet. Furthermore, historical rows (Row 2 `-92022`, Row 3 ID 1, Row 4 ID 2) must be preserved without collision.
  - *Strategy D.2 (Database-Managed Sequential Allocation):* Add `sheet_row_number INTEGER UNIQUE` to `sheets_sync_queue`.
    - When an outbox record is claimed or created, SQLite transactionally allocates the next available row number: `sheet_row_number = COALESCE(MAX(sheet_row_number), baseline_max) + 1`.
    - The assigned row number is persisted in SQLite before any remote API dispatch.
    - If Worker A stalls and Worker B takes over, Worker B reads the **already-assigned** `sheet_row_number` from SQLite. Both workers target `'Attendance_Log'!A{R}:I{R}`!
- **Failure Modes of Option D:**
  - *Manual User Editing:* If a human administrator deletes or inserts a row directly in Google Sheets, physical rows become desynchronized from SQLite's recorded row numbers.
  - *Sheet Dimension Limits:* Google Sheets has a limit of 10,000,000 cells (approx. 1,000,000 10-column rows), which must be expanded if writes exceed current sheet rows.
- **Verdict:** **The only architecture that guarantees exactly-once logical row representation** in Google Sheets despite network delays, worker interruptions, and duplicate HTTP requests.

### Option E — Dedicated Serialized Sheets Dispatcher (Single-Writer Pattern)
- **Description:** Enforce that only one worker thread or process can ever dispatch writes to Google Sheets (e.g., via a process-level mutex, file lock, or dedicated daemon).
- **Analysis:**
  - Completely prevents concurrent worker collisions.
  - **Why serialization alone fails:** If the single worker sends an append request, the network drops the TCP ACK, and the worker times out, the worker must retry. Upon retry, if it issues another `values().append`, it will append a duplicate row. Serialization prevents multi-worker overlap, but does not solve the unacknowledged write / retry problem.
- **Verdict:** Useful for concurrency simplification, but insufficient for exactly-once guarantees.

### Option F — External Idempotency Service / Intermediate Storage Layer
- **Description:** Interpose an external transactional service (e.g., Cloud Function with Redis / Firestore, or AWS Lambda with DynamoDB) that accepts idempotency keys, deduplicates requests, and proxies writes to Google Sheets.
- **Analysis:**
  - Provides true idempotency-key semantics (`Idempotency-Key: {attendance_id}`).
  - **Drawbacks:** Introduces external cloud hosting dependencies, monthly operational costs, secrets management overhead, and additional failure domains for what is designed to be a lightweight, local edge attendance system.
- **Verdict:** Architecturally clean, but disproportionately complex and costly for this project's requirements.

---

## 5. Required Comparison Matrix

| Architecture Option | Prevents Duplicate Remote Writes? | Handles Stale Workers? | Handles Crash After Successful Remote Write? | Handles Concurrent Workers? | Requires DB Migration? | Requires Spreadsheet Changes? | Implementation Complexity | Remaining Limitations |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Current Baseline (Check-Then-Append)** | **NO** | **NO** | **YES** (Self-healing on retry) | **PARTIAL** (Disjoint claims, but TOCTOU race) | None | None | Low | Stale worker causes duplicate rows in C2 race. |
| **Option A (Pre-Append Lease Verification)** | **PARTIAL** (Reduces window) | **PARTIAL** | **YES** | **PARTIAL** | None | None | Very Low | In-flight network stalls after check still cause duplicates. |
| **Option B (Lease Fencing Tokens)** | **NO** | **YES** (Protects SQLite) | **YES** | **YES** (Protects SQLite) | **YES** (`claim_token` column) | None | Low | Cannot prevent duplicate appends in Google Sheets. |
| **Option C (Timeout & Lease Budgeting)** | **PARTIAL** (Probabilistic) | **PARTIAL** | **YES** | **PARTIAL** | None | None | Very Low | OS process pauses or dropped ACKs can still produce duplicates. |
| **Option D (Deterministic Row Assignment)** | **YES** (Idempotent Overwrite) | **YES** (Target identical range) | **YES** (Target identical range) | **YES** (Target identical range) | **YES** (`sheet_row_number` column) | None (Standard grid) | Moderate | Manual row insertions by human users in Google Sheets cause offset drift. |
| **Option E (Serialized Dispatcher)** | **NO** | **PARTIAL** | **NO** (Retry appends duplicate) | **YES** (Eliminates concurrency) | None | None | Moderate | Dropped ACK on single writer causes duplicate append on retry. |
| **Option F (External Idempotency Service)** | **YES** | **YES** | **YES** | **YES** | None | Requires Cloud Service | High | External hosting costs, extra network hops, vendor lock-in. |

### Semantic Clarifications:
- **At-Least-Once Request Delivery:** The network transport layer ensures that every attendance record is dispatched to Google Sheets at least once. Duplicate dispatches across retries or recovering workers may occur on the wire.
- **Duplicate Suppression via Pre-Write Lookup:** The current mechanism queries existing IDs before appending. It prevents duplicates only when the spreadsheet state does not change between lookup and append (fails under TOCTOU races).
- **Exactly-Once Logical Row Representation:** The combination of at-least-once transport delivery with deterministic range updates (`values().update`). Even if multiple requests for the same attendance record reach Google Sheets, they overwrite the exact same physical cells, ensuring that each attendance record exists **exactly once** in the spreadsheet.

---

## 6. Recommended Architecture: The Multi-Layered Defense-in-Depth Model

To achieve absolute reliability without cloud service dependencies, the recommended architecture combines **Option D (Deterministic Row Assignment)** with **Option B (Fencing Tokens)**, **Option A (Pre-Dispatch Verification)**, and **Option C (Timeout Coordination)** into a unified **Defense-in-Depth Model**.

```
+---------------------------------------------------------------------------------------------------+
|                                 DEFENSE-IN-DEPTH ARCHITECTURE                                     |
|                                                                                                   |
|  [Layer 1: Pre-Dispatch Lease Check]   ---> Checks lease validity in SQLite before remote call.   |
|  [Layer 2: Socket Timeout Budgeting]   ---> Enforces 15s socket timeout << 180s lease duration.   |
|  [Layer 3: Fencing Tokens]             ---> Prevents stale workers from updating SQLite status.   |
|  [Layer 4: Deterministic Row Update]   ---> Replaces values().append with values().update(A{R}:I{R})|
|                                             Guarantees idempotent row overwrites on any retry.    |
+---------------------------------------------------------------------------------------------------+
```

### Why this is Safer than Append-Based Delivery
1. **Idempotence at the Destination:** By targeting a specific cell range (`'Attendance_Log'!A{R}:I{R}`) via HTTP `PUT` instead of `values().append`, duplicate HTTP requests become harmless. If Worker A and Worker B both write Attendance ID 1, Row $R$ simply reflects the data. No secondary row is created.
2. **Elimination of TOCTOU:** The worker no longer depends on a non-atomic read-then-write sequence to ensure uniqueness. Uniqueness is enforced by the deterministic destination coordinate.
3. **Fenced Database Updates:** Even if Worker A resumes after lease expiration and completes its HTTP update, SQLite rejects Worker A's `mark_as_synced` because its `claim_token` is obsolete.

### Required Schema Changes
Add two new columns and an index to `sheets_sync_queue`:
```sql
-- Forward migration for Phase 3 Reliability
ALTER TABLE sheets_sync_queue ADD COLUMN sheet_row_number INTEGER;
ALTER TABLE sheets_sync_queue ADD COLUMN claim_token INTEGER NOT NULL DEFAULT 0;

-- Ensure no two queue records target the same spreadsheet row
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_queue_sheet_row ON sheets_sync_queue(sheet_row_number);
```

### Required Service Changes
1. **`app/services/sheets_outbox.py`:**
   - In `claim_records()`: Increment `claim_token = claim_token + 1`. Persist `claim_token` and return it in the record dictionary.
   - Row Allocation: When a record is claimed (or inserted), assign `sheet_row_number`:
     ```sql
     sheet_row_number = (SELECT COALESCE(MAX(sheet_row_number), ?) + 1 FROM sheets_sync_queue)
     ```
   - In `mark_as_synced()` and `schedule_retry()`: Add `AND claim_token = ?` to the `WHERE` clause. Return boolean indicating if row was updated.
2. **`app/services/google_sheets.py`:**
   - Introduce `update_attendance_row(spreadsheet_id, sheet_name, row_number, row_values, service)` using `service.spreadsheets().values().update(range=f"'{sheet_name}'!A{row_number}:I{row_number}", valueInputOption="RAW", body={"values": [row_values]})`.
3. **`app/services/sheets_worker.py`:**
   - In `sync_pending_records_batch()`:
     - Check `lease_expires_at > now_utc` immediately before network dispatch.
     - Replace `append_attendance_batch` with deterministic batch updates or contiguous range updates.
     - Pass `claim_token` to `mark_as_synced`.

### Backward Compatibility & Existing Rows Preservation
The live spreadsheet `1G2kJ_i4i31Frk4ph5LmcF_RzaGpeStZMttla3bP07PY` currently contains exactly 4 rows:
- Row 1: Header Row
- Row 2: Baseline `-92022`
- Row 3: Attendance ID `1` (`MANUAL-001`)
- Row 4: Attendance ID `2` (`MANUAL-002`)

**Preservation Rules:**
1. **Baseline Offset:** The initial `sheet_row_number` baseline is configured to `4`. The next synchronized record will be deterministically assigned **Row 5**.
2. **Historical Backfill:** In SQLite, existing synchronized records are backfilled:
   - Queue ID 1 (Attendance ID 1) $\rightarrow$ `sheet_row_number = 3`
   - Queue ID 2 (Attendance ID 2) $\rightarrow$ `sheet_row_number = 4`
3. Existing rows 1 through 4 will **never be overwritten or altered**.

### Rollback Strategy
If any issue arises during future implementation:
1. Reverting `sync_pending_records_batch` to use `append_attendance_batch` is 100% backward compatible, as the schema columns `sheet_row_number` and `claim_token` are non-breaking additions with defaults.
2. The columns can remain in SQLite without affecting normal operation.
3. Database backup taken immediately prior to migration allows instant restoration.

---

## 7. Proposed Future Reliability Tests (Design Only)

The following test scenarios are designed for execution in future validation stages. **None of these tests were executed during this stage.**

1. **Test F1 — Concurrent Workers for Same Attendance Record:**
   - *Design:* Two workers simulate simultaneous claims for the same record. Verify SQLite `claim_records` serializes via `BEGIN IMMEDIATE` and awards ownership to exactly one worker with a unique `claim_token`.
2. **Test F2 — Stale Worker Resumption After Expiration (Test C2 Re-Test):**
   - *Design:* Worker A claims Record 1 (allocated Row $R$), pauses before write. Lease expires. Worker B claims Record 1 with higher `claim_token` and writes to Row $R$. Worker A resumes and writes to Row $R$.
   - *Success Criteria:* Row $R$ is overwritten with identical data; exactly 1 instance of Attendance ID exists in the sheet; Worker A's acknowledgement fails due to obsolete `claim_token`.
3. **Test F3 — Crash After Remote Write Before SQLite Acknowledgement:**
   - *Design:* Worker completes `values().update` to Row $R$, then crashes before `mark_as_synced`. Lease expires and recovers. Worker B claims record and re-dispatches write to Row $R$.
   - *Success Criteria:* Row $R$ remains valid; Worker B successfully marks record `synced` in SQLite; zero duplicate rows.
4. **Test F4 — Client-Side Socket Timeout with In-Flight Remote Commit:**
   - *Design:* Mock service accepts HTTP update to Row $R$ but simulates a dropped socket response. Worker catches `TimeoutError` and schedules retry.
   - *Success Criteria:* On retry, worker updates Row $R$ again; final spreadsheet state has exactly 1 row.
5. **Test F5 — Repeated Worker Execution Against Synchronized Queue:**
   - *Design:* Execute worker against fully synchronized queue. Verify 0 network calls, status `IDLE`, and zero row mutations.
6. **Test F6 — Contiguous Batch Row Allocation:**
   - *Design:* Claim batch of 10 records. Verify records are allocated contiguous rows $R$ through $R+9$ and written via a single batch range update.
7. **Test F7 — Historical Row Protection:**
   - *Design:* Verify row numbers $\le 4$ cannot be allocated or written to by the allocation engine.
8. **Test F8 — Database Migration and Rollback Verification:**
   - *Design:* Apply forward migration to a disposable copy of `attendance_manual_test.db`. Verify backfill of rows 3 and 4. Roll back and verify data integrity.
9. **Test F9 — Fencing Token Rejection:**
   - *Design:* Invoke `mark_as_synced(queue_id=1, claim_token=999)` where actual token is 1000. Verify update returns `False` and SQLite record is unchanged.

---

## 8. Risks and Unresolved Questions

1. **Human Manual Edits in Google Sheets:**
   - *Risk:* If a school administrator manually inserts or deletes rows directly in the Google Sheets web interface, the physical row positions will drift from SQLite's `sheet_row_number`.
   - *Mitigation:* Document administrative rules that the `'Attendance_Log'` sheet is machine-managed. Provide an admin audit utility to detect row drift by comparing Column A IDs against SQLite `sheet_row_number`.
2. **Sheet Maximum Row Limits:**
   - *Risk:* Google Sheets tabs default to 1,000 rows. When row allocations approach row 1,000, updates to non-existent rows will fail.
   - *Mitigation:* The worker must check sheet dimension metadata and call `appendDimension` or ensure capacity if `max(sheet_row_number) > current_grid_rows - 50`.
3. **Google Sheets API Rate Limits:**
   - *Quota:* Google Sheets API allows 300 requests per minute per project and 60 requests per minute per user per project.
   - *Mitigation:* Ensure workers update rows in contiguous batches (`values().batchUpdate` or multi-row range update `A{start}:I{end}`) rather than individual row HTTP calls.

---

## 9. Source-Code References

- `app/services/sheets_worker.py`: Lines 71–354 (`sync_pending_records_batch`), lines 178–196 (batch claim), lines 256–285 (duplicate check), lines 290–327 (append batch), lines 328–335 (mark synced).
- `app/services/sheets_outbox.py`: Lines 182–276 (`claim_records`), lines 278–316 (`recover_expired_leases`), lines 318–360 (`mark_as_synced`), lines 402–480 (`schedule_retry`).
- `app/services/google_sheets.py`: Lines 424–538 (`ensure_sheet_headers`), lines 544–616 (`format_attendance_row` and batch), lines 618–686 (`append_attendance_batch`), lines 688–741 (`get_existing_attendance_ids`).
- `app/models.py`: Lines 112–126 (`CREATE_SHEETS_SYNC_QUEUE_SQL`), lines 128–132 (`CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL`).
- `app/database.py`: Lines 19–46 (`get_db_connection`), lines 90–113 (`migrate_db`).
- `tests/test_sheets_worker.py`: Lines 1–717.
- `tests/test_sheets_outbox.py`: Lines 1–687.

---

## 10. Audit Verdict

`READY — RECOMMENDED ARCHITECTURE IDENTIFIED`

**Justification:**
- The root cause of the Stage 9.7B.13C C2 failure has been completely explained with exact source-code references.
- All six architectural options have been thoroughly evaluated against concrete failure modes.
- A robust, production-grade Defense-in-Depth model (Deterministic Row Assignment + Fencing Tokens + Timeout Budgeting) has been identified that completely eliminates duplicate rows without cloud dependencies.
- A migration and rollback plan that strictly protects existing historical spreadsheet data has been formulated.
- Zero source-code or database modifications were performed during this audit.
