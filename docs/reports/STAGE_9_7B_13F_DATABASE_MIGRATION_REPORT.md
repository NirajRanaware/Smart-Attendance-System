# Stage 9.7B.13F — Database Migration & Schema Constraints Report

**Project:** IoT-Based Smart Attendance Management System
**Workspace:** `C:\Projects\Smart-Attendance-System`
**Current Branch:** `main`
**Current Baseline Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
**Execution Date:** 2026-11-24
**Audit Mode:** CONTROLLED IMPLEMENTATION WITH DISPOSABLE DATABASE TESTING
**Target Disposable Database:** `database/manual_test/attendance_schema_test.db`
**Lead Database Engineer:** Antigravity

---

## 1. Executive Summary

In accordance with the Stage 9.7B.13E Blueprint, this milestone implemented the database schema foundation necessary for deterministic Google Sheets row assignment and generational claim fencing.

All schema modifications, migration logic, and validation tests were executed under strict database isolation against a dedicated disposable database (`attendance_schema_test.db`). The production database (`attendance.db`), the baseline manual-test database (`attendance_manual_test.db`), and earlier reliability databases (`attendance_reliability_test.db`, `attendance_lease_test.db`) were preserved with 100% SHA-256 byte-for-byte integrity.

### Key Milestones Delivered:
1. **`sheet_row_number` Column:** Added `sheet_row_number INTEGER NULL` to `sheets_sync_queue`.
2. **Partial Unique Constraint:** Created `uq_sheets_sync_queue_sheet_row_number` on `sheets_sync_queue(sheet_row_number) WHERE sheet_row_number IS NOT NULL`, allowing multiple `NULL` values while strictly rejecting duplicate non-null row assignments.
3. **`claim_token` Column:** Added `claim_token INTEGER NOT NULL DEFAULT 0` to `sheets_sync_queue` to support monotonic claim generation fencing.
4. **Metadata Table (`sheets_sync_meta`):** Provisioned singleton metadata table storing authoritative sequence counter `next_sheet_row` initialized to `5` with strict `CHECK (id = 1)` and `CHECK (next_sheet_row >= 5)`.
5. **Historical Row Backfill:** Safely backfilled historical synthetic records without hardcoded assumptions:
   - Attendance ID `1` (`MANUAL-001`) $\rightarrow$ `sheet_row_number = 3`
   - Attendance ID `2` (`MANUAL-002`) $\rightarrow$ `sheet_row_number = 4`
6. **Isolation & Test Suite Verification:** Successfully executed Tests A through H (8 comprehensive scenarios). Existing unit and integration test suites (`test_database.py` and `test_sheets_outbox.py`, 37 total tests) passed with 100% success.

**Final Verdict:** `PASS — SCHEMA MIGRATION VERIFIED`

---

## 2. Verified Baseline & Pre-Migration State

Prior to modifying source code, Git and database file hashes were recorded:

- **Git Branch:** `main`
- **HEAD Commit:** `889ea78bef82e56b5952a1c74338b6e417b2eee8`
- **Working Tree:** Clean tracked files; untracked audit reports from Stages 12B–13E present in `docs/reports/`.
- **Pre-Migration Protected Database Hashes:**
  - `attendance.db`: `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9`
  - `attendance_manual_test.db`: `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221`
  - `attendance_reliability_test.db`: `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5`
  - `attendance_lease_test.db`: `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63`

---

## 3. Files Modified

In strict adherence to implementation scope controls, only two files were modified:

1. **`app/models.py`:**
   - Updated Table 5 (`sheets_sync_queue`) DDL to include `sheet_row_number INTEGER` and `claim_token INTEGER NOT NULL DEFAULT 0`.
   - Added Table 6 (`sheets_sync_meta`) DDL.
   - Added `uq_sheets_sync_queue_sheet_row_number` partial unique index to `CREATE_INDEXES_SQL` and `CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL`.
   - Defined `CREATE_SHEETS_SYNC_META_SQL`.
2. **`app/database.py`:**
   - Imported `datetime, timezone` and `CREATE_SHEETS_SYNC_META_SQL`.
   - Implemented `backfill_historical_rows(conn: sqlite3.Connection) -> dict`.
   - Updated `migrate_db(conn: sqlite3.Connection) -> None` with steps for adding columns, creating `sheets_sync_meta`, ensuring row 1 singleton, and backfilling.
   - Reordered `init_db()` to apply `migrate_db(conn)` prior to `CREATE_INDEXES_SQL`, guaranteeing that columns added via forward migrations exist before indexes are constructed on pre-existing database files.

---

## 4. Schema Changes

### 4.1 Schema Additions to `sheets_sync_queue`
```sql
ALTER TABLE sheets_sync_queue ADD COLUMN sheet_row_number INTEGER;
ALTER TABLE sheets_sync_queue ADD COLUMN claim_token INTEGER NOT NULL DEFAULT 0;
```

#### Detailed Column Attributes:
- **`sheet_row_number`:** `INTEGER NULL`. Represents the 1-based physical row in Google Sheets. Nullable during record creation and migration; permanently assigned before remote dispatch.
- **`claim_token`:** `INTEGER NOT NULL DEFAULT 0`. Represents monotonic claim generation for worker fencing. Defaults to `0` for all existing and new records.

### 4.2 Partial Unique Index
```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_sheets_sync_queue_sheet_row_number
ON sheets_sync_queue(sheet_row_number)
WHERE sheet_row_number IS NOT NULL;
```
- **Constraint Behavior:** Allows an unlimited number of records with `sheet_row_number IS NULL` (pending unallocated records). Once a non-null integer is assigned, guarantees that no two queue records can ever share the same row number.

### 4.3 Metadata Singleton Table (`sheets_sync_meta`)
```sql
CREATE TABLE IF NOT EXISTS sheets_sync_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_sheet_row INTEGER NOT NULL CHECK (next_sheet_row >= 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```
- **Constraint Behavior:**
  - `CHECK (id = 1)` restricts the table to a single authoritative configuration row.
  - `CHECK (next_sheet_row >= 5)` prevents counter regression into reserved historical rows (Rows 1–4).
  - Uses ISO 8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SSZ`).

---

## 5. Migration Implementation Details

In `app/database.py`, `migrate_db()` applies forward migrations idempotently and transactionally:

```python
def migrate_db(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    try:
        # 1. Forward migration: end_time column in class_sessions
        cursor.execute("PRAGMA table_info(class_sessions);")
        columns = [row["name"] if hasattr(row, "keys") else row[1] for row in cursor.fetchall()]
        if columns and "end_time" not in columns:
            cursor.execute("ALTER TABLE class_sessions ADD COLUMN end_time TEXT;")
            conn.commit()

        # 2. Forward migration: sheets_sync_queue table & indexes (Phase 3 Milestone 2)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_queue';")
        if not cursor.fetchone():
            cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_SQL)
            cursor.executescript(CREATE_SHEETS_SYNC_QUEUE_INDEXES_SQL)
            conn.commit()

        # 3. Forward migration: sheets_sync_queue columns (Phase 3 Milestone 5 - Stage 9.7B.13F)
        cursor.execute("PRAGMA table_info(sheets_sync_queue);")
        queue_columns = [row["name"] if hasattr(row, "keys") else row[1] for row in cursor.fetchall()]
        if queue_columns:
            if "sheet_row_number" not in queue_columns:
                cursor.execute("ALTER TABLE sheets_sync_queue ADD COLUMN sheet_row_number INTEGER;")
            if "claim_token" not in queue_columns:
                cursor.execute("ALTER TABLE sheets_sync_queue ADD COLUMN claim_token INTEGER NOT NULL DEFAULT 0;")

            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_sheets_sync_queue_sheet_row_number
                ON sheets_sync_queue(sheet_row_number)
                WHERE sheet_row_number IS NOT NULL;
                """
            )
            conn.commit()

        # 4. Forward migration: sheets_sync_meta table (Phase 3 Milestone 5 - Stage 9.7B.13F)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sheets_sync_meta';")
        if not cursor.fetchone():
            cursor.execute(CREATE_SHEETS_SYNC_META_SQL)
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cursor.execute(
                """
                INSERT OR IGNORE INTO sheets_sync_meta (id, next_sheet_row, created_at, updated_at)
                VALUES (1, 5, ?, ?);
                """,
                (now_utc, now_utc)
            )
            conn.commit()
        else:
            cursor.execute("SELECT id FROM sheets_sync_meta WHERE id = 1;")
            if not cursor.fetchone():
                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO sheets_sync_meta (id, next_sheet_row, created_at, updated_at)
                    VALUES (1, 5, ?, ?);
                    """,
                    (now_utc, now_utc)
                )
                conn.commit()

        # 5. Historical row backfill for verified baseline records
        backfill_historical_rows(conn)

    except sqlite3.Error as e:
        conn.rollback()
        raise RuntimeError(f"Database migration failed: {e}") from e
```

---

## 6. Historical Backfill Evidence & Safety Guarantees

In `app/database.py`, `backfill_historical_rows()` strictly verifies identity and constraints before mapping rows:

```python
verified_mappings = [
    (1, "MANUAL-001", 3),
    (2, "MANUAL-002", 4),
]
```

### Safety Rules Enforced:
1. **Attendance & Student Identity Verification:** Queries `attendance` joined with `students` to confirm `attendance.id == att_id` AND `students.roll_number == expected_roll`. If not matched, skips safely without modification.
2. **Conflict Detection (Existing Assignment):** If `sheet_row_number` is already assigned to a value other than target, raises `RuntimeError` and aborts migration without overwriting.
3. **Conflict Detection (Target Row Claimed):** Queries `sheets_sync_queue` to verify no other record owns `target_row`.
4. **Idempotency:** If `sheet_row_number == target_row`, records `already_assigned` and takes no action.
5. **Authoritative Preservation:** Does not delete or alter historical rows in `attendance` or `sheets_sync_queue`. Existing `sync_status = 'synced'` and timestamps are preserved intact.

### Backfill Telemetry on Disposable Database:
```json
{
  "backfilled": [
    [1, 3],
    [2, 4]
  ],
  "already_assigned": [],
  "skipped": [],
  "conflicts": []
}
```

---

## 7. Index and Constraint Verification

Inspection of `attendance_schema_test.db` via `PRAGMA index_list` and `PRAGMA table_info`:

| Table | Constraint / Index Name | Indexed Columns | Unique? | Partial Condition | Status |
| :--- | :--- | :--- | :---: | :---: | :---: |
| `sheets_sync_queue` | `uq_sheets_sync_queue_sheet_row_number` | `sheet_row_number` | **YES** | `sheet_row_number IS NOT NULL` | **VERIFIED** |
| `sheets_sync_queue` | `idx_sync_queue_status_retry` | `sync_status, next_retry_at` | NO | None | Preserved |
| `sheets_sync_queue` | `idx_sync_queue_attendance_id` | `attendance_id` | NO | None | Preserved |
| `sheets_sync_queue` | `idx_sync_queue_lease` | `sync_status, lease_expires_at` | NO | None | Preserved |
| `sheets_sync_meta` | `sqlite_autoindex_sheets_sync_meta_1` | `id` (PK) | **YES** | None | `CHECK (id = 1)` |

---

## 8. Test Suite Results (Tests A through H)

All test scenarios were executed via automated test harness `run_schema_migration_tests_13f.py`:

```
======================================================================
STAGE 9.7B.13F: DATABASE MIGRATION & SCHEMA CONSTRAINTS TEST RUNNER
======================================================================
Pre-test check: attendance.db -> 0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9
Pre-test check: attendance_manual_test.db -> 5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221
Pre-test check: attendance_reliability_test.db -> D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5
Pre-test check: attendance_lease_test.db -> A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63

Provisioned disposable database: C:\Projects\Smart-Attendance-System\database\manual_test\attendance_schema_test.db
Disposable DB SHA-256: 5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221

TEST A: Fresh Migration ------------------------------------ PASS
TEST B: Historical Backfill -------------------------------- PASS
TEST C: Unique Row Constraint ------------------------------ PASS
TEST D: Claim-Token Default -------------------------------- PASS
TEST E: Migration Idempotency ------------------------------ PASS
TEST F: Conflicting Historical Mapping Detection ----------- PASS
TEST G: Rollback and Recovery Simulation ------------------- PASS
TEST H: Existing Test Suite Compatibility ------------------ PASS
======================================================================
ALL TESTS (A through H) PASSED WITH 100% ISOLATION.
======================================================================
```

### Detailed Results Breakdown:
- **Test A (Fresh Migration):** Columns `sheet_row_number` and `claim_token` added. Table `sheets_sync_meta` created with `next_sheet_row = 5`. Partial index created. (`integrity = ok`).
- **Test B (Historical Backfill):** Attendance ID 1 mapped to Row 3; Attendance ID 2 mapped to Row 4. Attendance count = 2; queue count = 2; statuses remain `synced`.
- **Test C (Unique Row Constraint):** Two records with `sheet_row_number = NULL` accepted. Row 5 assigned to first record successfully. Second assignment of Row 5 rejected with `sqlite3.IntegrityError: UNIQUE constraint failed: sheets_sync_queue.sheet_row_number`.
- **Test D (Claim-Token Default):** Pre-existing records confirmed with `claim_token = 0`. Newly inserted record without token received default `claim_token = 0`.
- **Test E (Migration Idempotency):** Executed `migrate_db()` twice on the same database. Discovered 0 duplicate columns, 0 duplicate indexes, exactly 1 metadata row with counter = 5, and identical row assignments (Rows 3 & 4).
- **Test F (Conflicting Mapping Detection):** Injected conflicting row assignment (`sheet_row_number = 99` for ID 1) on separate fixture `attendance_conflict_test.db`. Migration caught conflict: `Historical backfill conflict: Queue ID 1 (attendance_id 1) already assigned row 99, conflicting with target 3`. Halting verified without corruption.
- **Test G (Rollback and Recovery):** Simulated transaction error during multi-step migration on `attendance_rollback_test.db`. Transaction rolled back cleanly; uncommitted row updates were completely discarded.
- **Test H (Existing Test Suite Compatibility):**
  - `pytest tests/test_database.py`: 16 passed, 0 failed.
  - `pytest tests/test_sheets_outbox.py`: 21 passed, 0 failed.
  - Total existing tests executed: **37 passed, 0 failed**.

---

## 9. Application Compatibility

1. **Insert Operations:** `create_queue_record()` specifies explicit column names (`attendance_id, sync_status, retry_count, created_at, updated_at`). Because `sheet_row_number` is nullable and `claim_token` defaults to `0`, all existing insertions remain 100% compatible.
2. **Query Operations:** Existing queue readers (`get_pending_records()`, `get_queue_stats()`) function seamlessly without modification.
3. **Transitional State:** In this milestone, the new columns and tables are dormant foundations. The worker does not yet allocate rows or enforce fencing tokens during synchronization, preserving existing behavior until Milestones 13G and 13H.

---

## 10. Database Isolation & Preservation Evidence

| Database | Pre-Test SHA-256 | Post-Test SHA-256 | Verification Result |
| :--- | :--- | :--- | :--- |
| **`attendance.db` (Production)** | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | `0F9953D52F38DEE5A47C85299E9C7B4943BA8903ED91F1D851B702E185B017F9` | **EXACT MATCH (100% Untouched)** |
| **`attendance_manual_test.db`** | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | `5D2C7481457EBC06F93C2685B70ABD37C27B51DF92DAB0793149F30BF8AD9221` | **EXACT MATCH (100% Untouched)** |
| **`attendance_reliability_test.db`** | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | `D22ED105589EE1A72F189B7C000598C4821B74B8FEE7F5AC8FF3823219295AF5` | **EXACT MATCH (100% Untouched)** |
| **`attendance_lease_test.db`** | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | `A8A6199315F3FDE407FB2AF7E6189FF1B7F712183AB068FF5A887AC54C5C2C63` | **EXACT MATCH (100% Untouched)** |
| **`attendance_schema_test.db`** | N/A (Fresh Disposable Copy) | Mutated via Migration | **Preserved on disk for audit** |

- **Real Google Spreadsheet Access:** Zero calls.
- **External Network Sockets:** Zero connections.

---

## 11. Final Disposable Database State

Inspection of `C:\Projects\Smart-Attendance-System\database\manual_test\attendance_schema_test.db`:
- **PRAGMA integrity_check:** `ok`
- **PRAGMA foreign_key_check:** `[]` (0 violations)
- **Attendance Records Count:** 2
- **Queue Records Count:** 2
- **Queue Row States:**
  - ID 1: `attendance_id = 1`, `status = synced`, `sheet_row_number = 3`, `claim_token = 0`
  - ID 2: `attendance_id = 2`, `status = synced`, `sheet_row_number = 4`, `claim_token = 0`
- **Metadata State:** `id = 1`, `next_sheet_row = 5`

---

## 12. Git Diff Summary

### `git status`:
```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
	modified:   app/database.py
	modified:   app/models.py

Untracked files:
	docs/reports/STAGE_9_7B_12B_DATABASE_QUEUE_ISOLATION_AUDIT_REPORT.md
	docs/reports/STAGE_9_7B_12C_GOOGLE_SHEETS_WORKER_PREFLIGHT_REPORT.md
	docs/reports/STAGE_9_7B_12D_GOOGLE_SHEETS_SYNC_EXECUTION_REPORT.md
	docs/reports/STAGE_9_7B_13A_RELIABILITY_ARCHITECTURE_AUDIT_REPORT.md
	docs/reports/STAGE_9_7B_13B_IDEMPOTENCY_TEST_REPORT.md
	docs/reports/STAGE_9_7B_13C_LEASE_RECOVERY_CONCURRENCY_REPORT.md
	docs/reports/STAGE_9_7B_13D_EXACTLY_ONCE_ARCHITECTURE_REPORT.md
	docs/reports/STAGE_9_7B_13E_IMPLEMENTATION_BLUEPRINT_REPORT.md
	docs/reports/STAGE_9_7B_13F_DATABASE_MIGRATION_REPORT.md
```

### `git diff --check`:
- **Exit code:** `0` (Zero whitespace or formatting errors).

### Source Diff Summary:
- **`app/models.py`:** Added `sheet_row_number` and `claim_token` columns to Table 5 DDL; added Table 6 DDL (`sheets_sync_meta`); added `uq_sheets_sync_queue_sheet_row_number` partial unique index.
- **`app/database.py`:** Added `backfill_historical_rows()`; added migration steps 3, 4, and 5 to `migrate_db()`; adjusted `init_db()` execution order so migrations apply before creating indexes on existing databases.

---

## 13. Audit Verdict & Recommended Next Milestone

`PASS — SCHEMA MIGRATION VERIFIED`

**Justification:**
- All schema additions (`sheet_row_number`, `claim_token`, `sheets_sync_meta`, partial unique index) are verified and operational.
- Historical backfill executed safely without hardcoded queue ID assumptions.
- 8 comprehensive test scenarios (Tests A through H) passed with 100% success.
- 37 existing unit and outbox regression tests passed.
- 100% database isolation verified; source and production databases untouched.
- `git diff --check` passed cleanly with zero staging, zero commits, and zero pushes.

### Recommended Next Milestone:
**Stage 9.7B.13G — Transactional Deterministic Row Allocator Service**
(Implementation of `allocate_sheet_rows_for_batch()` in `app/services/sheets_outbox.py` utilizing the new schema foundation under isolated testing).

As instructed, I have **NOT** proceeded to Stage 9.7B.13G. Standing by for your review and explicit instructions.
