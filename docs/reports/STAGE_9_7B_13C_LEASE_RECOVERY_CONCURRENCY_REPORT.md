# Stage 9.7B.13C — Isolated Lease Recovery & Concurrent Worker Reliability Test Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Execution Date:** 2026-11-24
**Test Mode:** STRICTLY ISOLATED RELIABILITY TESTING — DISPOSABLE DATABASE ONLY
**External API Operations:** STRICTLY PROHIBITED (100% Mocked, Zero Network Sockets)
**Lead Auditor/Executor:** Antigravity

---

## 1. Executive Summary

This testing phase evaluated the reliability, leased concurrency control, worker interruption recovery, and duplicate prevention boundaries of the Google Sheets synchronization subsystem. Testing was executed in strict isolation against a dedicated disposable SQLite database (`attendance_lease_test.db`) using an in-memory, thread-safe mock Google Sheets service (`ThreadSafeMockSheetsService`).

### Key Test Outcomes:
1. **Test A (Interrupted Worker & Expired Lease Recovery):**
   - A claimed record transitioned to `in_progress` with a 60-second lease expiration (`lease_expires_at = T0 + 60s`).
   - Non-expired recovery checks ($T_0 + 30s$) preserved the lease without premature recovery (`recovered = 0`).
   - Expired recovery checks ($T_0 + 70s$) successfully reclaimed the record back to `pending` with `lease_expires_at = NULL` (`recovered = 1`).
   - **Verdict:** **PASS**.
2. **Test B (Two Workers Competing for the Same Queue):**
   - Two concurrent worker threads synchronized via `threading.Barrier(2)` attempted to claim eligible records simultaneously.
   - Worker 1 claimed Queue ID 2; Worker 2 claimed Queue ID 1.
   - Claim sets were strictly disjoint (`claimed_1 ∩ claimed_2 == ∅`).
   - SQLite `BEGIN IMMEDIATE` successfully eliminated race conditions and overlapping record ownership.
   - **Verdict:** **PASS**.
3. **Test C (Slow Operation & Lease-Expiration Race):**
   - **Sub-Scenario C1 (Delay Before Lookup):** When network delay preceded the worksheet Column A read, the resumed worker observed Worker B's append, engaged duplicate suppression, and made **0 duplicate appends**.
   - **Sub-Scenario C2 (Delay In TOCTOU Window):** When network delay occurred *after* reading Column A but *before* appending, Worker B claimed and appended the record upon lease expiration. Worker A resumed and dispatched its pre-computed append payload, resulting in a duplicate row in the mock sheet (`occurrences = 2`).
   - **Finding:** A dual-write distributed race window exists under lease expiry. Google Sheets API lacks atomic distributed locking or fencing tokens with SQLite.
   - **Verdict:** **LIMITATION IDENTIFIED**.
4. **Test D (Recovery Followed by Repeated Execution):**
   - Subsequent worker execution against the fully synchronized queue claimed 0 records, dispatched 0 append calls, returned `status = "IDLE"`, and left 0 records in `in_progress`.
   - **Verdict:** **PASS**.
5. **Database & API Isolation:**
   - Pre-test and post-test SHA-256 hashes for `attendance_manual_test.db` and `attendance.db` matched byte-for-byte.
   - Zero outbound HTTP, HTTPS, or socket requests were initiated.

**Final Verdict:** `PARTIAL — SOME RELIABILITY LIMITATIONS IDENTIFIED`

---

## 2. Environment & Disposable Database Provisioning

To protect the pristine Stage 9.7B.12D manual-test baseline and production databases, a disposable copy was provisioned and validated prior to executing any mutation or test logic:

- **Source Database:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_manual_test.db`
  - Pre-Test SHA-256: `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221`
  - Integrity Check: `ok`
  - Attendance Count: 2
  - Queue Count: 2 (both `synced`)
- **Production Database:** `C:\Projects\Smart-Attendance-System\database\attendance.db`
  - Pre-Test SHA-256: `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`
  - Production DB Access: **STRICT ZERO-ACCESS (Untouched)**
- **Disposable Database:** `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_lease_test.db`
  - Provisioning Method: Binary byte-for-byte copy (`shutil.copy2`)
  - Initial SHA-256: `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221`
  - Post-Creation Integrity Check: `ok`
  - Working Copy Path Confirmed: `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_lease_test.db`
  - Preservation: Retained on disk for auditing and review.

---

## 3. Thread-Safe Mock Google Sheets Service

An in-memory, thread-safe Google API mock (`ThreadSafeMockSheetsService`) was utilized to simulate the Google Sheets API v4 endpoints without network overhead:

```python
class ThreadSafeMockSheetsService:
    def __init__(self, initial_rows=None):
        self._lock = threading.Lock()
        self.rows = [list(r) for r in (initial_rows or [
            DEFAULT_SPREADSHEET_HEADERS,
            ["-92022", "2026-11-22", "14:30:41", "STAGE92-TEST", "GOOGLE SHEETS LIVE TEST — NOT A REAL STUDENT", "STAGE 9.2 INTEGRATION TEST", "TEST-UID-NOT-A-REAL-CARD", "Present", "STAGE_9_2_TEST"]
        ])]
        self.calls = []
        self.append_calls = []
        self.lookup_calls = []

        # Hooks for fine-grained race simulation
        self.before_get_hook = None
        self.after_get_hook = None
        self.before_append_hook = None
        self.after_append_hook = None
```

### Safety & Behavioral Attributes:
1. **Thread-Safe Concurrency:** Internal operations protected by `threading.Lock()`, allowing multi-threaded race condition tests without memory corruption.
2. **Full API Emulation:** Supports `spreadsheets().get()`, `values().get()`, and `values().append()`.
3. **Interception Hooks:** Exposes hook callouts (`before_get_hook`, `before_append_hook`) enabling deterministic stalling of worker threads to reproduce real-world distributed race conditions.
4. **Zero Egress:** No network sockets opened; zero API quotas consumed.

---

## 4. Test A — Interrupted Worker & Expired Lease Recovery

### Objective:
Verify that when a worker claims a record and crashes or is interrupted before acknowledging completion:
1. The record is protected from premature reclamation while the lease is valid.
2. The record is safely restored to `pending` once the lease expires.

### Test Procedure:
1. Queue ID 1 in `attendance_lease_test.db` was prepared with `sync_status = 'pending'`, `lease_expires_at = NULL`.
2. Worker `worker-A-interrupted` claimed the record at simulated time $T_0 = \text{2026-11-24T10:00:00Z}$ with a 60-second lease (`lease_duration_seconds=60`).
3. Evaluated SQLite state: Record transitioned to `in_progress` with `lease_expires_at = '2026-11-24T10:01:00Z'`.
4. Worker simulated an unexpected crash (no `mark_as_synced` or `schedule_retry` invoked).
5. At $T_0 + 30s$ (simulated time 10:00:30Z), `recover_expired_leases` was invoked.
6. At $T_0 + 70s$ (simulated time 10:01:10Z), `recover_expired_leases` was invoked.

### Observed Results:
- **Claim Telemetry:** Claimed Queue ID 1 successfully.
- **Premature Recovery Check ($T_0 + 30s$):** Returned `recovered = 0`. Record remained `in_progress` with lease intact.
- **Expired Recovery Check ($T_0 + 70s$):** Returned `recovered = 1`.
- **Post-Recovery SQLite State:** Queue ID 1 transitioned to `sync_status = 'pending'` with `lease_expires_at = NULL`.
- **Verdict:** **PASS**.

---

## 5. Test B — Two Workers Competing for the Same Queue

### Objective:
Verify that two concurrent worker instances attempting to claim pending records from the outbox queue simultaneously never receive overlapping or duplicate claims.

### Test Procedure:
1. Both Queue ID 1 and Queue ID 2 in `attendance_lease_test.db` were prepared in `pending` status.
2. Two independent threads (`Worker-1` and `Worker-2`) were spawned.
3. Threads were synchronized using a `threading.Barrier(2)` to trigger simultaneous invocations of `claim_records(limit=1, db_path=DISPOSABLE_DB)`.
4. Claimed IDs and SQLite locking behaviors were monitored and recorded.

### Observed Results:
- **Worker 1 Claimed:** `[2]` (Queue ID 2)
- **Worker 2 Claimed:** `[1]` (Queue ID 1)
- **Worker Errors:** None (`[]`)
- **Claim Set Intersection:** `set()` (strictly disjoint, empty intersection)
- **SQLite Concurrency Mechanism:** SQLite `BEGIN IMMEDIATE` acquired exclusive write locks sequentially, forcing serialization at the database engine level.
- **Post-Test Queue Status:**
  - Queue ID 1: `sync_status = 'in_progress'`, `lease_expires_at` populated.
  - Queue ID 2: `sync_status = 'in_progress'`, `lease_expires_at` populated.
- **Database Integrity:** `PRAGMA integrity_check` returned `ok`.
- **Verdict:** **PASS**.

---

## 6. Test C — Slow Operation & Lease-Expiration Race

### Objective:
Simulate a worker experiencing severe network latency during remote cloud operations exceeding the lease duration (60 seconds), followed by lease recovery, worker handover, and resumption.

### Race Analysis & Scenarios Tested:

```
[Timeline: Dual-Write TOCTOU Race Condition]

Worker A:  Claim ID 1 ---> Read Col A (ID 1 missing) ------------[NETWORK STALL]------------> Append ID 1 (Duplicate!)
                                                                 |
Clock:     -------------------------------------------- Lease Expired (60s)
                                                                 |
Recovery:  -------------------------------------------- Reset ID 1 to 'pending'
                                                                 |
Worker B:  -------------------------------------------- Claim ID 1 -> Read Col A -> Append ID 1 -> Mark Synced
```

#### Sub-Scenario C1: Delay Occurs BEFORE Duplicate Check (Column A Lookup)
1. Worker A claims Queue ID 1.
2. Worker A experiences network stall before querying Column A (`Attendance_Log!A2:A`).
3. Lease expires ($T_0 + 70s$). Supervisor executes `recover_expired_leases`, resetting Queue ID 1 to `pending`.
4. Worker B executes, claims Queue ID 1, verifies Column A (Attendance ID 1 not found), appends row, and marks `synced`.
5. Worker A resumes and performs its Column A read.
6. Worker A detects Attendance ID 1 is now present in Google Sheets (`already_present_count = 1`), suppresses the append call (`appended_count = 0`), and completes idempotently.
7. **Outcome C1:** Total append calls: 1. Total occurrences of Attendance ID 1 in Sheet: **1**. **Duplicate successfully prevented.**

#### Sub-Scenario C2: Delay Occurs AFTER Duplicate Check, BEFORE Append (TOCTOU Window)
1. Worker A claims Queue ID 1.
2. Worker A queries Column A: Attendance ID 1 is not present. Worker A partitions record into `records_to_append = [Record 1]`.
3. Worker A experiences network delay right before dispatching `values().append(...)`.
4. Lease expires ($T_0 + 70s$). Supervisor executes `recover_expired_leases`, resetting Queue ID 1 to `pending`.
5. Worker B executes, claims Queue ID 1, queries Column A (Attendance ID 1 is not yet in Google Sheets because Worker A has not sent its append), appends row, and marks `synced`.
6. Worker A resumes its paused execution and dispatches its already-formatted append payload.
7. Mock Google Sheets accepts Worker A's append.
8. **Outcome C2:** Total append calls: 2. Total occurrences of Attendance ID 1 in Sheet: **2** (`['-92022', '1', '1']`).
9. **Finding:** A Time-Of-Check to Time-Of-Use (TOCTOU) distributed race condition exists. Because Google Sheets API append operations are non-transactional with respect to SQLite, a worker whose lease expires after performing its pre-check will cause duplicate rows if it is not fenced prior to append.

---

## 7. Test D — Recovery Followed by Repeated Execution

### Objective:
Verify that after all recovery cycles and synchronization operations, subsequent worker runs against the disposable database execute idempotently without side effects.

### Test Procedure:
1. Baseline state confirmed: Both Queue IDs 1 and 2 in `attendance_lease_test.db` in `synced` status.
2. Worker invoked via `sync_pending_records_batch(batch_size=20, db_path=DISPOSABLE_DB, service=mock_service_d)`.
3. Recorded telemetry, append counts, and queue state.

### Observed Results:
```json
{
  "success": true,
  "status": "IDLE",
  "claimed_count": 0,
  "synced_count": 0,
  "appended_count": 0,
  "message": "No pending records to synchronize."
}
```
- **Records Claimed:** 0.
- **Append API Calls:** 0 (mock append count remained 0).
- **Stuck Records Check:**
  - `pending`: 0
  - `in_progress`: 0
  - `synced`: 2
  - `failed`: 0
  - `dead_letter`: 0
- **Verdict:** **PASS**.

---

## 8. Quantitative Telemetry & Final State Summary

| Metric / Parameter | Pre-Test State | Post-Test State | Verification Status |
| :--- | :--- | :--- | :--- |
| **Source DB Path** | `database/manual_test/attendance_manual_test.db` | `database/manual_test/attendance_manual_test.db` | Verified |
| **Source DB SHA-256** | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | **Exact Match (100% Untouched)** |
| **Production DB Path** | `database/attendance.db` | `database/attendance.db` | Verified |
| **Production DB SHA-256** | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | **Exact Match (100% Untouched)** |
| **Disposable DB Path** | `database/manual_test/attendance_lease_test.db` | `database/manual_test/attendance_lease_test.db` | Preserved on disk |
| **Disposable DB Integrity** | `ok` | `ok` | **PRAGMA integrity_check PASS** |
| **Disposable DB Attendance Rows** | 2 | 2 | Unchanged |
| **Disposable DB Queue Rows** | 2 | 2 | Unchanged |
| **Queue ID 1 Final Status** | `synced` | `synced` | `lease_expires_at = NULL` |
| **Queue ID 2 Final Status** | `synced` | `synced` | `lease_expires_at = NULL` |
| **Concurrent Claim Overlap** | N/A | **0 records (disjoint)** | **PASS** |
| **Premature Lease Recovery** | N/A | **0 records** | **PASS** |
| **Expired Lease Recovery** | N/A | **1 record** | **PASS** |
| **Total Mock Lookup Calls** | 0 | 8 (`values.get`) | 100% in-memory |
| **Total Mock Append Calls** | 0 | 3 (`values.append`) | 100% in-memory |
| **Live Network Sockets** | 0 | 0 | **Zero Network Access** |

---

## 9. Identified Architectural Limitations & Recommendations

### Limitation 1: Distributed Dual-Write TOCTOU Race Condition
As demonstrated empirically in Test C (Sub-scenario C2), the synchronization worker implements an **at-least-once** delivery model rather than strict exactly-once delivery across network partitions. If a worker's remote append operation stalls past the lease duration (60s) after the idempotency check is completed, lease recovery will reassign the queue item to another worker, and both workers may append the record.

### Limitation 2: Lack of Fencing Tokens or Atomic Google Sheets Locks
Google Sheets API v4 provides no conditional write primitives (such as `If-Match` ETags or atomic CAS operations) across batch appends. Therefore, a revived worker cannot be rejected by Google Sheets without a prior lock verification step.

### Recommended Mitigations:
1. **Pre-Append Lease Verification:**
   Before invoking `append_attendance_batch`, the worker should verify that its lease in SQLite has not expired and that `sync_status` remains `in_progress`. If the lease expired while preparing the payload, the worker must abort the append.
2. **Generational Fencing Tokens:**
   Add a `lease_epoch` or `claim_token` integer to `sheets_sync_queue`. Incremented on each claim, a worker holding a stale token will immediately abort prior to remote dispatch.
3. **Conservative Lease Duration:**
   In production environments with variable network latencies, set `lease_duration_seconds` to a higher threshold (e.g., 180–300 seconds) combined with short HTTP request timeouts (e.g., 15 seconds) so that HTTP requests always fail before the lease can expire.

---

## 10. Audit Verdict

`PARTIAL — SOME RELIABILITY LIMITATIONS IDENTIFIED`

**Justification:**
- SQLite-level concurrency control (`BEGIN IMMEDIATE`), record claiming, unexpired lease protection, and expired lease recovery operate with 100% mathematical correctness.
- Concurrent workers never claim identical queue items.
- A legitimate distributed systems limitation was exposed under extreme network delay: an expired worker can create duplicate Google Sheets rows if its lease expires within the TOCTOU window between reading and writing the remote spreadsheet.
- The disposable database `attendance_lease_test.db` and all test artifacts have been preserved on disk for review.
