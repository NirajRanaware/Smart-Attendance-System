# STAGE 9.7B.13H — CLAIM-TOKEN FENCING & LEASE EXPIRATION VERIFICATION REPORT

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Branch:** `main`
**Baseline Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
**Previous Milestone:** Stage 9.7B.13G — Transactional Deterministic Row Allocator (PASS)
**Current Milestone:** Stage 9.7B.13H — Claim-Token Fencing & Lease Expiration Verification
**Date:** 2026-11-24
**Verdict:** **PASS — CLAIM-TOKEN FENCING VERIFIED**

---

## 1. Executive Summary

Stage 9.7B.13H implements and formally verifies **Claim-Token Fencing and Safe Lease Expiration** in the SQLite outbox service (`app/services/sheets_outbox.py`).

During Stage 13C (Test C2), a critical concurrency vulnerability was confirmed: when Worker A claims a record and pauses while processing it, Worker A's lease can expire, allowing Worker B to reclaim the record and synchronize it to Google Sheets. If Worker A subsequently resumes without fencing, it performs a duplicate remote write or overwrites Worker B's newer state in SQLite.

Stage 9.7B.13H solves this race condition at the SQLite database layer by implementing fencing tokens:
1. **Monotonically Increasing Claim Tokens:** Every successful lease acquisition atomically increments `claim_token = claim_token + 1`. Tokens are permanent integers that never decrease or reset.
2. **Strictly Fenced State Transitions:** All subsequent worker state transitions (`mark_as_synced`, `schedule_retry`, `move_to_dead_letter`, `mark_as_failed`, `release_claim`) require explicit `queue_id: int` and `claim_token: int` arguments. The transition executes conditionally only if `id = ? AND claim_token = ? AND sync_status = 'in_progress' AND lease_expires_at >= ?`.
3. **Stale Worker Rejection (Fail Closed):** If Worker A's lease expires and Worker B reclaims the record, Worker B receives an incremented `claim_token`. When Worker A attempts to transition the record with its stale token, the database update matches zero rows. The service returns `False` or `claim_lost=True`, completely rejecting the stale worker.
4. **Non-Destructive Selective Lease Recovery:** `recover_expired_leases()` selectively resets only records with `sync_status = 'in_progress'` and `lease_expires_at <= now_utc` back to `pending`. It preserves existing `claim_token`, permanent `sheet_row_number`, and `retry_count`.
5. **Administrative Invalidation on Requeue:** `retry_dead_letter_records()` transitions `dead_letter` records back to `pending` while atomically incrementing `claim_token = claim_token + 1`, immediately invalidating any stranded worker thread from earlier claim generations.
6. **Robust Transaction Ownership:** Implemented `_managed_transaction()` supporting SQLite `SAVEPOINT`s when an external connection with an active transaction is provided, and strict `BEGIN IMMEDIATE;` when managing internal connections.
7. **Complete Database Safety:** All 6 protected databases (`attendance.db`, `attendance_manual_test.db`, `attendance_reliability_test.db`, `attendance_lease_test.db`, `attendance_schema_test.db`, and `attendance_allocator_test.db`) maintained 100% byte-for-byte SHA-256 parity before and after execution.
8. **Scope & Worker Boundary:** Zero modifications were made to `sheets_worker.py` (deferred to Stage 13J). Zero Google Sheets API calls, zero network sockets, zero credentials accessed, and zero Git commits/pushes occurred.

---

## 2. Verified Source Baseline

Prior to implementation, the starting codebase was audited against Stage 13G deliverables:
- Schema columns in `sheets_sync_queue`:
  - `sheet_row_number INTEGER NULL`
  - `claim_token INTEGER NOT NULL DEFAULT 0`
- Table `sheets_sync_meta`:
  - Singleton row `id = 1`, `next_sheet_row = 12`
- Partial unique index:
  - `uq_sheets_sync_queue_sheet_row_number ON sheets_sync_queue(sheet_row_number) WHERE sheet_row_number IS NOT NULL`
- Existing allocator service:
  - `app/services/sheets_row_allocator.py` intact and verified.
- Protected database baseline hashes:
  - `attendance.db`: `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`
  - `attendance_manual_test.db`: `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221`
  - `attendance_reliability_test.db`: `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5`
  - `attendance_lease_test.db`: `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63`
  - `attendance_schema_test.db`: `A502E79CFEE895F5BA758CB45F6EE05418F35C5D26CF3B5E0D7E13116190FF48`
  - `attendance_allocator_test.db`: `1D633E93F55C3F55DB7BFC25B51C4C0FD4680A57450EDB2F797EA20835BDD256`

---

## 3. Existing Unfenced Transition Inventory

An initial audit of `app/services/sheets_outbox.py` identified the following unfenced operations that were vulnerable to stale workers:

| Function | Old Signature | Vulnerability Prior to Stage 13H |
|---|---|---|
| `claim_records` | `claim_records(limit, lease_duration_seconds, now_dt, conn, db_path)` | Incremented retry timestamp but did NOT increment `claim_token`. Returned records without fencing tokens. |
| `mark_as_synced` | `mark_as_synced(queue_id, now_dt, conn, db_path)` | Accepted bare `queue_id`. Updated `sync_status = 'synced'` regardless of whether the lease had expired or another worker had claimed the record. |
| `schedule_retry` | `schedule_retry(queue_id, error_message, ...)` | Accepted bare `queue_id`. Incremented retry count and updated status without checking token or active lease. |
| `mark_as_failed` | `mark_as_failed(queue_id, error_message, ...)` | Accepted bare `queue_id`. Failed records without verifying worker lease ownership. |
| `move_to_dead_letter` | `move_to_dead_letter(queue_id, error_message, ...)` | Accepted bare `queue_id`. Sent records to dead-letter without verifying active claim token. |
| `release_claim` | `release_claim(queue_id, ...)` | Accepted bare `queue_id`. Released records even if claimed by another worker. |
| `recover_expired_leases` | `recover_expired_leases(...)` | Reclaimed expired leases, but left claim tokens static, failing to invalidate stale workers. |
| `retry_dead_letter_records` | `retry_dead_letter_records(...)` | Requeued dead-letter records with static tokens, leaving them susceptible to late writes from previous worker attempts. |

---

## 4. Claim-Token Design

### 4.1 Token Semantics
- **Column:** `sheets_sync_queue.claim_token INTEGER NOT NULL DEFAULT 0`
- **Initial State:** Upon queue record creation via `create_queue_record()`, `claim_token` is initialized to `0`.
- **Atomic Increment:** Whenever a record is claimed via `claim_records()`, the token is incremented atomically:
  ```sql
  UPDATE sheets_sync_queue
  SET sync_status = 'in_progress',
      claim_token = claim_token + 1,
      lease_expires_at = ?,
      updated_at = ?
  WHERE id = ?;
  ```
- **Monotonicity Guarantee:** `claim_token` only increases. It is never decremented, never cleared, and never reset to 0 upon lease recovery or retry.
- **Administrative Invalidation:** In `retry_dead_letter_records()`, `claim_token = claim_token + 1` is applied to ensure any stranded worker from prior processing attempts immediately fails closed.

---

## 5. Claim Transaction Implementation

### 5.1 Conditional Claim Query
In `app/services/sheets_outbox.py`, `claim_records()` executes within an immediate transaction (`BEGIN IMMEDIATE;` or unique savepoint). It selects eligible records:
```sql
SELECT id, attendance_id, retry_count, claim_token, sheet_row_number
FROM sheets_sync_queue
WHERE (
    sync_status = 'pending'
    OR (
        sync_status = 'failed'
        AND (next_retry_at IS NULL OR next_retry_at <= ?)
    )
)
ORDER BY id ASC
LIMIT ?;
```
For each eligible record, it updates the record using a conditional write verifying it remains in an eligible state:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'in_progress',
    claim_token = claim_token + 1,
    lease_expires_at = ?,
    updated_at = ?
WHERE id = ?
  AND (
      sync_status = 'pending'
      OR (
          sync_status = 'failed'
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
      )
  );
```
Only records whose `UPDATE` affected exactly 1 row are returned to the caller.

### 5.2 Returned Claim Data Structure
`claim_records()` returns a dictionary containing the new `claim_token` and `sheet_row_number`:
```python
{
    "queue_id": r["id"],
    "attendance_id": r["attendance_id"],
    "retry_count": r["retry_count"],
    "claim_token": current_token + 1,
    "sheet_row_number": r["sheet_row_number"],
    "sync_status": "in_progress",
    "lease_expires_at": lease_expires_at_iso,
    "student_id": r["student_id"],
    "roll_number": r["roll_number"],
    "student_name": r["student_name"],
    "course_name": r["course_name"],
    "session_date": r["session_date"],
    "scanned_at": r["scanned_at"],
    "attendance_status": r["attendance_status"],
}
```

---

## 6. Fenced Transition Implementation

All worker state transitions enforce strict argument validation (`_validate_queue_and_token(queue_id, claim_token)`):
- Non-integer or boolean types raise `ValueError`.
- `queue_id <= 0` or `claim_token <= 0` raise `ValueError`.

### 6.1 `mark_as_synced()`
Executes an atomic fenced update:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'synced',
    synced_at = ?,
    lease_expires_at = NULL,
    updated_at = ?,
    last_error = NULL
WHERE id = ?
  AND claim_token = ?
  AND sync_status = 'in_progress'
  AND lease_expires_at >= ?;
```
- **Return Value:** Returns `True` if `cursor.rowcount == 1`. Returns `False` if the token is stale, the status is not `in_progress`, or the lease expired.

### 6.2 `schedule_retry()`
Evaluates retry count against `max_retries`:
- If `new_retry_count >= max_retries`:
  ```sql
  UPDATE sheets_sync_queue
  SET sync_status = 'dead_letter',
      retry_count = ?,
      next_retry_at = NULL,
      lease_expires_at = NULL,
      last_error = ?,
      updated_at = ?
  WHERE id = ?
    AND claim_token = ?
    AND sync_status = 'in_progress'
    AND lease_expires_at >= ?;
  ```
- Else transitions to `failed` with exponential backoff:
  ```sql
  UPDATE sheets_sync_queue
  SET sync_status = 'failed',
      retry_count = ?,
      next_retry_at = ?,
      lease_expires_at = NULL,
      last_error = ?,
      updated_at = ?
  WHERE id = ?
    AND claim_token = ?
    AND sync_status = 'in_progress'
    AND lease_expires_at >= ?;
  ```
- **Return Value:** Returns dictionary:
  - Success: `{"success": True, "claim_lost": False, "status": "failed"|"dead_letter", "retry_count": ..., "next_retry_at": ...}`
  - Stale/Lost Claim: `{"success": False, "claim_lost": True, "status": current_db_status, "retry_count": ..., "next_retry_at": ...}`

### 6.3 `move_to_dead_letter()` and `mark_as_failed()`
Enforces matching `claim_token`, `sync_status = 'in_progress'`, and `lease_expires_at >= now_utc`. Returns `True` on success, `False` on stale worker.

### 6.4 `release_claim()`
Resets claimed record back to `pending`:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'pending',
    lease_expires_at = NULL,
    updated_at = ?
WHERE id = ?
  AND claim_token = ?
  AND sync_status = 'in_progress'
  AND lease_expires_at >= ?;
```
Returns `True` on success, `False` on stale claim.

---

## 7. Lease Recovery Behavior

### 7.1 Selective Lease Recovery
`recover_expired_leases(now_dt, conn, db_path)` strictly targets expired records without affecting active leases or other statuses:
```sql
SELECT id, claim_token, sheet_row_number, retry_count
FROM sheets_sync_queue
WHERE sync_status = 'in_progress'
  AND lease_expires_at <= ?;
```
For each expired record, it executes:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'pending',
    lease_expires_at = NULL,
    updated_at = ?
WHERE id = ?
  AND sync_status = 'in_progress'
  AND lease_expires_at <= ?;
```
- **Field Preservation:**
  - `claim_token`: Preserved unchanged during recovery. (It is incremented upon the next claim by Worker B).
  - `sheet_row_number`: Preserved permanently.
  - `retry_count`: Preserved without decrement.
- **Active Lease Protection:** Any record where `lease_expires_at > now_utc` is completely ignored by recovery.

---

## 8. Administrative Requeue Behavior

`retry_dead_letter_records(queue_ids, conn, db_path)` allows operators to requeue poisoned or exhausted records:
```sql
UPDATE sheets_sync_queue
SET sync_status = 'pending',
    retry_count = 0,
    claim_token = claim_token + 1,
    next_retry_at = NULL,
    lease_expires_at = NULL,
    updated_at = ?
WHERE id = ?
  AND sync_status = 'dead_letter';
```
- **Generational Token Invalidation:** Incrementing `claim_token = claim_token + 1` invalidates any lingering worker thread from before the record entered `dead_letter`.
- **Permanent Row Preservation:** Existing `sheet_row_number` assignments remain permanently attached to the queue record.

---

## 9. Transaction Ownership Findings

Stage 13G audit identified transaction nesting risks when an external `conn` was passed. In Stage 13H, `sheets_outbox.py` implements a robust transaction manager `_managed_transaction`:
```python
@contextmanager
def _managed_transaction(conn: Optional[sqlite3.Connection], db_path: Optional[Union[str, Path]] = None):
    owns_connection = (conn is None)
    if owns_connection:
        active_conn = _get_active_db_connection(db_path)
        active_conn.execute("BEGIN IMMEDIATE;")
        try:
            yield active_conn
            active_conn.commit()
        except Exception:
            active_conn.rollback()
            raise
        finally:
            active_conn.close()
    else:
        # Supplied external connection
        in_external_tx = getattr(conn, "in_transaction", False)
        if in_external_tx:
            sp_name = f"sp_outbox_{uuid.uuid4().hex[:8]}"
            conn.execute(f"SAVEPOINT {sp_name};")
            try:
                yield conn
                conn.execute(f"RELEASE SAVEPOINT {sp_name};")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                raise
        else:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
```
- **Supplied Connection Ownership:** External transactions are never prematurely committed or rolled back by outbox methods; partial operations are isolated within savepoints.
- **Internal Connection Ownership:** When `conn=None`, connections are opened with busy timeouts, managed under `BEGIN IMMEDIATE;`, committed, and reliably closed in a `finally` block.

---

## 10. Exception and Failure Behavior

1. **Validation Failures:** Non-integer, negative, or boolean `queue_id` or `claim_token` arguments immediately raise `ValueError` without touching the database.
2. **Missing Records:** Updating non-existent queue IDs returns `False` or `claim_lost=True` (fail closed) without raising unhandled exceptions.
3. **Mismatched Tokens:** Mismatched `claim_token` values return `False` or `claim_lost=True` without mutating the database.
4. **Expired Leases:** Attempts to transition records whose leases expired return `False` or `claim_lost=True`.
5. **Database Errors:** SQLite locking timeouts or constraint violations roll back the transaction/savepoint cleanly and propagate the exception to the caller.

---

## 11. Tests A–S Results Summary

All tests executed via pytest. Tests A through R ran against `tests/test_sheets_claim_fencing.py` using isolated disposable database fixtures and `attendance_fencing_test.db`. Test S verified regression suites across `test_sheets_outbox.py` and `test_database.py`.

| Test | Objective | Target Database / Fixture | Result | Details |
|---|---|---|:---:|---|
| **Test A** | First claim increments token | `attendance_fencing_test.db` | **PASSED** | Queue ID 6 claimed at $T_0$: `claim_token` incremented from 0 to 1; status $\rightarrow$ `in_progress`; lease set. |
| **Test B** | Reclaim increments token | `attendance_fencing_test.db` | **PASSED** | Lease expired at $T_0 + 61s$; recovered to `pending` (token preserved at 1); reclaimed at $T_0 + 120s$: `claim_token` incremented to 2. |
| **Test C** | Multiple records independent tokens | Isolated disposable copy | **PASSED** | Batch claim of 3 records: each incremented independently from 0 to 1 with identical lease expirations. |
| **Test D** | Stale mark-as-synced rejection | Isolated disposable copy | **PASSED** | Worker A claimed with token 1. Worker B reclaimed with token 2. Worker A called `mark_as_synced(qid, claim_token=1)`: returned `False`; DB status remained `in_progress` under Worker B. |
| **Test E** | Stale retry rejection | Isolated disposable copy | **PASSED** | Worker A with token 1 attempted `schedule_retry`: returned `claim_lost=True`; DB status and retry count untouched. |
| **Test F** | Stale dead-letter rejection | Isolated disposable copy | **PASSED** | Worker A with token 1 attempted `move_to_dead_letter`: returned `False`; DB status untouched. |
| **Test G** | Stale release rejection | Isolated disposable copy | **PASSED** | Worker A with token 1 attempted `release_claim`: returned `False`; DB status untouched. |
| **Test H** | Current-token transitions succeed | Isolated disposable copy | **PASSED** | Worker B with token 2 called `mark_as_synced(qid, claim_token=2)`: returned `True`; record updated to `synced`. |
| **Test I** | Expired lease recovery selectivity | Isolated disposable copy | **PASSED** | DB with 3 records (expired `in_progress`, active `in_progress`, pending). Recovery only reset the expired record; active lease and pending records remained untouched. |
| **Test J** | Recovery concurrency | Isolated disposable copy | **PASSED** | 2 concurrent recovery threads operating on 5 expired records with thread barrier: recovered exactly 5 records; zero deadlocks; integrity `ok`. |
| **Test K** | Claim concurrency | Isolated disposable copy | **PASSED** | 2 concurrent worker threads claiming disjoint batches from 10 pending records: claimed exactly 10 distinct records with zero overlap or collisions. |
| **Test L** | Same-record transition race | Isolated disposable copy | **PASSED** | Worker A and Worker B raced to transition the same record (Worker A using stale token 1, Worker B using valid token 2). Worker A rejected (`False`); Worker B succeeded (`True`). |
| **Test M** | Token monotonicity | Isolated disposable copy | **PASSED** | Record cycled through claim $\rightarrow$ recovery $\rightarrow$ reclaim $\rightarrow$ failure $\rightarrow$ reclaim. Tokens advanced strictly monotonically: $0 \rightarrow 1 \rightarrow 2 \rightarrow 3$. |
| **Test N** | Administrative requeue | Isolated disposable copy | **PASSED** | Dead-letter record with token 5 requeued: token incremented to 6; retry count reset to 0; permanent `sheet_row_number` preserved. |
| **Test O** | Transaction rollback | Isolated disposable copy | **PASSED** | Aborted transaction within savepoint rolled back claim status and token increment completely. |
| **Test P** | Supplied connection ownership | Isolated disposable copy | **PASSED** | Outbox operations using external transaction operated via `SAVEPOINT`. Rolling back caller transaction rolled back outbox operations cleanly. |
| **Test Q** | Internally created connection lifecycle | Isolated disposable copy | **PASSED** | Verified internal connection is committed, rolled back on error, and closed in `finally`. Zero connection leaks. |
| **Test R** | Missing/malformed tokens fail closed | Isolated disposable copy | **PASSED** | Passed `None`, negative ints, strings, and booleans as tokens/IDs: all raised `ValueError` or failed closed. |
| **Test S** | Existing test compatibility | Regression suites | **PASSED** | `test_database.py` (16 passed) and `test_sheets_outbox.py` (21 passed) executed cleanly. Worker tests skipped as deferred to Stage 13J. |

---

## 12. Concurrency Evidence

### 12.1 Concurrent Recovery (Test J)
- **Fixture:** 5 records inserted with expired leases (`sync_status = 'in_progress'`, lease in past).
- **Execution:** Two threads (Thread-1 and Thread-2) synchronized at `threading.Barrier(2)` called `recover_expired_leases()`.
- **Result:**
  - Total recovered records across both threads: exactly 5.
  - Zero SQLite locked errors or deadlocks.
  - Final database integrity check: `ok`.
  - All 5 records successfully returned to `pending` with tokens and rows intact.

### 12.2 Concurrent Claiming (Test K)
- **Fixture:** 10 records pending in queue.
- **Execution:** Worker A and Worker B synchronized at `threading.Barrier(2)` called `claim_records(limit=5)`.
- **Result:**
  - Worker A claimed 5 records; Worker B claimed 5 records.
  - Set intersection of claimed queue IDs: `set()`. Zero overlapping claims.
  - All 10 records transitioned to `in_progress` with `claim_token = 1`.
  - Zero lock timeout errors.

---

## 13. Stale-Worker Rejection Evidence

Test D, Test E, Test F, Test G, and Test L conclusively demonstrated that stale workers are completely prevented from modifying database state:

```
[Simulation Timeline]
T0: Record 6 is pending (claim_token = 0).
T1: Worker A claims Record 6 -> receives claim_token = 1, lease expires T0 + 60s.
T2: Worker A pauses / network delay occurs.
T3: Clock advances to T0 + 65s (lease expires).
T4: recover_expired_leases() resets Record 6 to 'pending' (claim_token remains 1).
T5: Worker B claims Record 6 -> receives claim_token = 2, lease expires T0 + 125s.
T6: Worker B marks Record 6 as 'in_progress' with token = 2.
T7: Worker A resumes and calls mark_as_synced(queue_id=6, claim_token=1).
    -> SQL: UPDATE sheets_sync_queue SET sync_status = 'synced'
            WHERE id = 6 AND claim_token = 1 AND lease_expires_at >= now_utc;
    -> Rows Affected: 0.
    -> Result: False (REJECTED).
T8: Database inspection confirms Record 6 is STILL 'in_progress' under Worker B.
T9: Worker B calls mark_as_synced(queue_id=6, claim_token=2).
    -> Rows Affected: 1.
    -> Result: True (ACCEPTED).
T10: Record 6 transitions to 'synced'.
```

Stale updates are rejected without altering the record or corrupting the newer worker's state.

---

## 14. Rollback Evidence

Test O and Test P verified atomic rollback behavior:

### 14.1 Savepoint Rollback on Injected Failure
- In Test O, an update was performed within an active external connection transaction.
- An exception was simulated after claiming a record.
- The transaction manager executed `conn.rollback()`.
- Inspection confirmed:
  - `claim_token` reverted to 0.
  - `sync_status` remained `pending`.
  - `lease_expires_at` remained `NULL`.
  - Zero ghost tokens or partial increments were committed to disk.

### 14.2 Caller Transaction Ownership (Test P)
- Test P initiated `conn.execute("BEGIN;")`, called `mark_as_synced()` with a valid token, and subsequently called `conn.rollback()`.
- The outbox operation created a nested savepoint (`sp_outbox_*`) which released cleanly into the caller's transaction.
- The caller's `conn.rollback()` rolled back the entire transaction, leaving the record `in_progress`.
- A subsequent `mark_as_synced()` in a new transaction succeeded.

---

## 15. Database Isolation and Hash Verification

All protected databases were verified before and after test execution:

| Database File | Pre-Test SHA-256 | Post-Test SHA-256 | Verification Status |
|---|---|---|:---:|
| `database/attendance.db` | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_manual_test.db` | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_reliability_test.db` | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_lease_test.db` | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_schema_test.db` | `A502E79CFEE895F5BA758CB45F6EE05418F35C5D26CF3B5E0D7E13116190FF48` | `A502E79CFEE895F5BA758CB45F6EE05418F35C5D26CF3B5E0D7E13116190FF48` | **MATCH (UNTOUCHED)** |
| `database/manual_test/attendance_allocator_test.db` | `1D633E93F55C3F55DB7BFC25B51C4C0FD4680A57450EDB2F797EA20835BDD256` | `1D633E93F55C3F55DB7BFC25B51C4C0FD4680A57450EDB2F797EA20835BDD256` | **MATCH (UNTOUCHED)** |

`attendance_fencing_test.db` was the exclusive target for test mutations.

---

## 16. Worker Compatibility Boundary

- `app/services/sheets_worker.py`: **Unmodified.**
- No worker threads or loops were executed during this milestone.
- Outbox state transitions now enforce required `claim_token: int` parameters without silent fallback defaults, deliberately failing closed if called without a token.
- `test_sheets_worker.py` was intentionally deferred to Stage 13J to prevent incompatible unfenced worker executions.

---

## 17. Required Stage 13J Worker Changes

An audit of `app/services/sheets_worker.py` identifies the exact modifications required for integration in Stage 13J:

1. **Token Extraction in Worker Loop:**
   In `process_queue_batch()`:
   ```python
   # Current:
   queue_id = record["queue_id"]
   # Required in Stage 13J:
   queue_id = record["queue_id"]
   claim_token = record["claim_token"]
   sheet_row_number = record.get("sheet_row_number")
   ```

2. **Passing Token to `mark_as_synced()`:**
   Around line 150 of `sheets_worker.py`:
   ```python
   # Current:
   mark_as_synced(queue_id=queue_id, db_path=db_path)
   # Required in Stage 13J:
   synced_ok = mark_as_synced(queue_id=queue_id, claim_token=claim_token, db_path=db_path)
   if not synced_ok:
       logger.warning("Claim lost or lease expired for Queue ID %s (token %s). Aborting acknowledgment.", queue_id, claim_token)
   ```

3. **Passing Token to `schedule_retry()`:**
   Around line 185 of `sheets_worker.py`:
   ```python
   # Current:
   schedule_retry(queue_id=queue_id, error_message=err_msg, db_path=db_path)
   # Required in Stage 13J:
   retry_result = schedule_retry(queue_id=queue_id, claim_token=claim_token, error_message=err_msg, db_path=db_path)
   if retry_result.get("claim_lost"):
       logger.warning("Claim lost for Queue ID %s during retry scheduling. Superseded by newer worker.", queue_id)
   ```

4. **Passing Token to `move_to_dead_letter()`:**
   When non-retryable errors occur:
   ```python
   # Required in Stage 13J:
   move_to_dead_letter(queue_id=queue_id, claim_token=claim_token, error_message=err_msg, db_path=db_path)
   ```

5. **Passing Token to `release_claim()`:**
   During graceful worker shutdown or interruption:
   ```python
   # Required in Stage 13J:
   release_claim(queue_id=queue_id, claim_token=claim_token, db_path=db_path)
   ```

6. **Stale Worker Abort Logic:**
   If pre-dispatch lease verification indicates `lease_expires_at <= now_utc`, the worker must abort remote Google Sheets calls immediately without attempting an update.

---

## 18. Known Limitations

1. **Worker Not Yet Connected:** The claim-token fencing logic is complete and verified at the database/outbox layer, but `sheets_worker.py` has not yet been updated (scheduled for Stage 13J).
2. **Deterministic Remote Writes Pending:** Stage 13H provides local fencing and lease recovery. Deterministic cell-level row writing (`Attendance_Log!A{row}:I{row}`) with coordinated HTTP timeouts will be implemented in Stage 13I.
3. **No Network Synchronization:** Zero real spreadsheet operations or remote API calls were made.

---

## 19. Git Diff Summary

### 19.1 Git Status
```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   app/database.py
	modified:   app/models.py
	modified:   app/services/sheets_outbox.py
	modified:   tests/test_sheets_outbox.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	app/services/sheets_row_allocator.py
	docs/reports/STAGE_9_7B_12B_DATABASE_QUEUE_ISOLATION_AUDIT_REPORT.md
	docs/reports/STAGE_9_7B_12C_GOOGLE_SHEETS_WORKER_PREFLIGHT_REPORT.md
	docs/reports/STAGE_9_7B_12D_GOOGLE_SHEETS_SYNC_EXECUTION_REPORT.md
	docs/reports/STAGE_9_7B_13A_RELIABILITY_ARCHITECTURE_AUDIT_REPORT.md
	docs/reports/STAGE_9_7B_13B_IDEMPOTENCY_TEST_REPORT.md
	docs/reports/STAGE_9_7B_13C_LEASE_RECOVERY_CONCURRENCY_REPORT.md
	docs/reports/STAGE_9_7B_13D_EXACTLY_ONCE_ARCHITECTURE_REPORT.md
	docs/reports/STAGE_9_7B_13E_IMPLEMENTATION_BLUEPRINT_REPORT.md
	docs/reports/STAGE_9_7B_13F_DATABASE_MIGRATION_REPORT.md
	docs/reports/STAGE_9_7B_13G_TRANSACTIONAL_ROW_ALLOCATOR_REPORT.md
	docs/reports/STAGE_9_7B_13H_CLAIM_TOKEN_FENCING_REPORT.md
	tests/test_sheets_claim_fencing.py
	tests/test_sheets_row_allocator.py

no changes added to commit (use "git add" and/or "git commit -a")
```

### 19.2 Git Diff Stat
```
 app/database.py               | 171 +++++++++++++-
 app/models.py                 |  31 +++
 app/services/sheets_outbox.py | 509 +++++++++++++++++++++++++++++-------------
 tests/test_sheets_outbox.py   |  15 +-
 4 files changed, 563 insertions(+), 163 deletions(-)
```

### 19.3 Git Diff Check
`git diff --check` returned exit code 0 (zero whitespace or formatting errors). Zero Git commits or pushes were performed.

---

## 20. Recommended Next Milestone

Proceed to **Stage 9.7B.13I — Deterministic Google Sheets Writer & HTTP Timeout Coordination**.
In Stage 13I, the Google Sheets client service (`app/services/google_sheets.py`) will be enhanced with deterministic row updates (`Attendance_Log!A{row}:I{row}`) utilizing the permanently allocated `sheet_row_number`, coupled with pre-dispatch lease verification and strict socket timeouts shorter than the lease duration.

---

## Final Verdict

# `PASS — CLAIM-TOKEN FENCING VERIFIED`

All 16 acceptance criteria for Stage 9.7B.13H have been fully met:
1. Every successful claim increments `claim_token` monotonically.
2. Every worker-owned transition requires matching token, status `in_progress`, and active lease.
3. Stale workers are rejected cleanly without altering state.
4. Expired leases are recovered selectively without touching active leases.
5. Claim tokens never decrease or reset.
6. Administrative requeue invalidates earlier worker generations.
7. Transaction ownership and savepoints are strictly maintained.
8. Rollbacks leave no orphaned token increments or partial state changes.
9. Concurrent claims and recoveries execute safely without conflict.
10. Permanent `sheet_row_number` assignments remain untouched.
11. All 55 test cases across `test_sheets_claim_fencing.py` (18), `test_sheets_outbox.py` (21), and `test_database.py` (16) passed.
12. All 6 protected databases maintained 100% SHA-256 match.
13. Zero Google Sheets remote calls occurred.
14. Zero worker loops were executed.
15. Zero Git commits or pushes were made.

---

> [!IMPORTANT]
> **STOP CONDITION ENFORCED:** Execution halts here. Stage 9.7B.13I will not proceed without explicit user instruction.
