"""
Unit and integration test suite for Google Sheets Deterministic Row Allocator.

Covers Tests A through K as defined in Stage 9.7B.13G specifications:
- Test A: Initial database state
- Test B: Single new allocation
- Test C: Multiple-record deterministic allocation
- Test D: Idempotency & duplicate inputs
- Test E: Unique-index protection
- Test F: Unknown and invalid queue IDs
- Test G: Metadata inconsistency & safe failure
- Test H: Atomic transaction rollback
- Test I: Concurrent distinct allocation (threading + barrier)
- Test J: Concurrent same-record allocation (threading + barrier)
- Test K: Existing outbox & database compatibility
"""

import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.services.sheets_row_allocator import (
    allocate_sheet_rows,
    get_next_sheet_row,
    get_allocated_sheet_row,
    AllocatorError,
    AllocatorInputError,
    AllocatorRecordNotFoundError,
    AllocatorMetadataError,
    AllocatorConsistencyError,
    AllocatorInvariantError,
    AllocatorConcurrencyError,
    AllocatorLockTimeoutError,
)
from app.services.sheets_outbox import create_queue_record

PRIMARY_TEST_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_allocator_test.db")
SOURCE_SCHEMA_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_schema_test.db")


def _get_utc_now_iso() -> str:
    """Return current UTC ISO string ending in 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_synthetic_record(
    conn: sqlite3.Connection,
    student_id: int = 1,
    session_id: int = None,
    sync_status: str = "pending",
    sheet_row_number: int = None,
) -> tuple[int, int]:
    """
    Inserts a valid class session, attendance, and sheets_sync_queue record.
    Returns (attendance_id, queue_id).
    """
    now_utc = _get_utc_now_iso()
    cursor = conn.cursor()
    if session_id is None:
        cursor.execute(
            """
            INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
            VALUES ('CS-101: Test Synthetic', '2026-11-24', '12:00:00', 0, ?);
            """,
            (now_utc,),
        )
        session_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, ?, 'present', 'rfid');
        """,
        (student_id, session_id, now_utc),
    )
    attendance_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (
            attendance_id, sync_status, retry_count, sheet_row_number, created_at, updated_at
        ) VALUES (?, ?, 0, ?, ?, ?);
        """,
        (attendance_id, sync_status, sheet_row_number, now_utc, now_utc),
    )
    queue_id = cursor.lastrowid
    conn.commit()
    return attendance_id, queue_id


# =============================================================================
# Test A: Initial Database State
# =============================================================================
def test_test_a_initial_database_state():
    """Test A: Verify initial disposable database integrity, historical rows, and counter."""
    assert PRIMARY_TEST_DB.exists(), f"Disposable database not found at {PRIMARY_TEST_DB}"

    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        # 1. Integrity check
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        integrity = cursor.fetchall()
        assert len(integrity) == 1 and integrity[0][0] == "ok", f"Integrity check failed: {integrity}"

        # 2. Foreign keys check
        cursor.execute("PRAGMA foreign_key_check;")
        fk_violations = cursor.fetchall()
        assert len(fk_violations) == 0, f"Foreign key violations found: {fk_violations}"

        # 3. Historical records: Queue 1 -> Row 3, Queue 2 -> Row 4
        cursor.execute("SELECT id, sheet_row_number, sync_status FROM sheets_sync_queue ORDER BY id;")
        queue_rows = cursor.fetchall()
        assert len(queue_rows) >= 2, "Expected at least 2 historical records"
        assert queue_rows[0]["id"] == 1 and queue_rows[0]["sheet_row_number"] == 3
        assert queue_rows[0]["sync_status"] == "synced"
        assert queue_rows[1]["id"] == 2 and queue_rows[1]["sheet_row_number"] == 4
        assert queue_rows[1]["sync_status"] == "synced"

        # 4. Metadata counter equals 5
        cursor.execute("SELECT id, next_sheet_row FROM sheets_sync_meta WHERE id = 1;")
        meta_row = cursor.fetchone()
        assert meta_row is not None, "Missing sheets_sync_meta row id=1"
        assert meta_row["next_sheet_row"] == 5, f"Expected counter 5, got {meta_row['next_sheet_row']}"

        # 5. Helper function verification
        assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 5
        assert get_allocated_sheet_row(1, db_path=PRIMARY_TEST_DB) == 3
        assert get_allocated_sheet_row(2, db_path=PRIMARY_TEST_DB) == 4
    finally:
        conn.close()


# =============================================================================
# Test B: Single New Allocation
# =============================================================================
def test_test_b_single_new_allocation():
    """Test B: Allocate a single new queue record; verify row 5 and counter advance to 6."""
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        # Create synthetic queue record
        _, qid = _insert_synthetic_record(conn, student_id=1)
    finally:
        conn.close()

    # Allocate row for new record
    result = allocate_sheet_rows(queue_ids=qid, db_path=PRIMARY_TEST_DB)
    assert result == {qid: 5}, f"Expected {{{qid}: 5}}, got {result}"

    # Verify database state
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, sheet_row_number FROM sheets_sync_queue WHERE id IN (1, 2, ?) ORDER BY id;", (qid,))
        rows = {r["id"]: r["sheet_row_number"] for r in cursor.fetchall()}
        assert rows[1] == 3, "Historical row 3 altered!"
        assert rows[2] == 4, "Historical row 4 altered!"
        assert rows[qid] == 5, f"New record {qid} not assigned row 5!"

        # Metadata counter advanced to 6
        assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 6
    finally:
        conn.close()


# =============================================================================
# Test C: Multiple-Record Deterministic Allocation
# =============================================================================
def test_test_c_multiple_record_deterministic_allocation():
    """Test C: Pass multiple IDs in shuffled order; verify ascending order assignment and counter."""
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        _, qid_c1 = _insert_synthetic_record(conn, student_id=2)
        _, qid_c2 = _insert_synthetic_record(conn, student_id=1)
        _, qid_c3 = _insert_synthetic_record(conn, student_id=2)
        assert qid_c1 < qid_c2 < qid_c3
    finally:
        conn.close()

    # Deliberately shuffled order: [qid_c3, qid_c1, qid_c2]
    result = allocate_sheet_rows(queue_ids=[qid_c3, qid_c1, qid_c2], db_path=PRIMARY_TEST_DB)
    expected = {qid_c1: 6, qid_c2: 7, qid_c3: 8}
    assert result == expected, f"Expected {expected}, got {result}"

    # Verify sequential assignment and counter = 9
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 9

    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, sheet_row_number FROM sheets_sync_queue WHERE id IN (?, ?, ?) ORDER BY id;",
            (qid_c1, qid_c2, qid_c3),
        )
        rows = {r["id"]: r["sheet_row_number"] for r in cursor.fetchall()}
        assert rows == expected
    finally:
        conn.close()


# =============================================================================
# Test D: Idempotency
# =============================================================================
def test_test_d_idempotency():
    """Test D: Repeated requests, duplicate inputs, mixture of allocated and unallocated."""
    initial_counter = get_next_sheet_row(db_path=PRIMARY_TEST_DB)
    assert initial_counter == 9

    # 1. Previously allocated single record
    res1 = allocate_sheet_rows(queue_ids=1, db_path=PRIMARY_TEST_DB)
    assert res1 == {1: 3}
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 9

    # 2. Previously allocated multiple records
    res2 = allocate_sheet_rows(queue_ids=[1, 2], db_path=PRIMARY_TEST_DB)
    assert res2 == {1: 3, 2: 4}
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 9

    # 3. Mixture: create new record
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    try:
        _, qid_d = _insert_synthetic_record(conn, student_id=1)
    finally:
        conn.close()

    # Call with duplicate IDs and mixture of allocated + unallocated: [1, qid_d, 2, qid_d, 1]
    res3 = allocate_sheet_rows(queue_ids=[1, qid_d, 2, qid_d, 1], db_path=PRIMARY_TEST_DB)
    assert res3 == {1: 3, 2: 4, qid_d: 9}

    # Counter should advance only by 1 (for qid_d)
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 10
    assert get_allocated_sheet_row(qid_d, db_path=PRIMARY_TEST_DB) == 9


# =============================================================================
# Test E: Unique-Index Protection
# =============================================================================
def test_test_e_unique_index_protection():
    """Test E: Direct attempt to violate partial unique index is rejected by SQLite."""
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    conn.row_factory = sqlite3.Row
    try:
        # Create unassigned record
        _, qid_e = _insert_synthetic_record(conn, student_id=2)

        # Direct SQL update trying to assign duplicate row 3 (owned by Queue 1)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute("UPDATE sheets_sync_queue SET sheet_row_number = 3 WHERE id = ?;", (qid_e,))
        assert "UNIQUE constraint failed" in str(exc_info.value)

        # Rollback the failed statement's transaction
        conn.rollback()

        # Verify Queue 1 still owns row 3 and qid_e still has NULL
        cursor = conn.cursor()
        cursor.execute("SELECT id, sheet_row_number FROM sheets_sync_queue WHERE id IN (1, ?) ORDER BY id;", (qid_e,))
        rows = {r["id"]: r["sheet_row_number"] for r in cursor.fetchall()}
        assert rows[1] == 3
        assert rows[qid_e] is None
    finally:
        conn.close()

    # Allocator behavior remains completely correct after failed constraint attempt
    res = allocate_sheet_rows(queue_ids=qid_e, db_path=PRIMARY_TEST_DB)
    assert res == {qid_e: 10}
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 11


# =============================================================================
# Test F: Unknown and Invalid Queue IDs
# =============================================================================
def test_test_f_unknown_and_invalid_queue_ids():
    """Test F: Unknown queue ID, negative ID, non-int, empty list, and atomic batch failure."""
    # 1. Unknown ID raises AllocatorRecordNotFoundError
    with pytest.raises(AllocatorRecordNotFoundError) as exc_nf:
        allocate_sheet_rows(99999, db_path=PRIMARY_TEST_DB)
    assert "99999" in str(exc_nf.value)

    # 2. Atomic failure on batch with mixture of valid and unknown IDs
    conn = sqlite3.connect(str(PRIMARY_TEST_DB))
    try:
        _, qid_f = _insert_synthetic_record(conn, student_id=1)
    finally:
        conn.close()

    with pytest.raises(AllocatorRecordNotFoundError):
        allocate_sheet_rows([1, qid_f, 99999], db_path=PRIMARY_TEST_DB)

    # Verify atomic failure: qid_f was NOT allocated a row
    assert get_allocated_sheet_row(qid_f, db_path=PRIMARY_TEST_DB) is None
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 11

    # 3. Negative ID
    with pytest.raises(AllocatorInputError):
        allocate_sheet_rows(-1, db_path=PRIMARY_TEST_DB)

    # 4. Zero ID
    with pytest.raises(AllocatorInputError):
        allocate_sheet_rows(0, db_path=PRIMARY_TEST_DB)

    # 5. Boolean ID
    with pytest.raises(AllocatorInputError):
        allocate_sheet_rows(True, db_path=PRIMARY_TEST_DB)

    # 6. Non-integer in sequence
    with pytest.raises(AllocatorInputError):
        allocate_sheet_rows([1, "two"], db_path=PRIMARY_TEST_DB)

    # 7. Non-sequence input
    with pytest.raises(AllocatorInputError):
        allocate_sheet_rows("invalid_string", db_path=PRIMARY_TEST_DB)

    # 8. Empty input returns {} immediately without touching DB
    assert allocate_sheet_rows([], db_path=PRIMARY_TEST_DB) == {}
    assert allocate_sheet_rows((), db_path=PRIMARY_TEST_DB) == {}

    # Now allocate qid_f cleanly so primary test DB stays consistent
    res_f = allocate_sheet_rows(qid_f, db_path=PRIMARY_TEST_DB)
    assert res_f == {qid_f: 11}
    assert get_next_sheet_row(db_path=PRIMARY_TEST_DB) == 12


# =============================================================================
# Test G: Metadata Inconsistency (Separate Disposable Fixture)
# =============================================================================
def test_test_g_metadata_inconsistency(tmp_path):
    """Test G: Missing metadata, duplicate metadata, invalid counter, inconsistency."""
    tmp_db = tmp_path / "metadata_test.db"
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))

    # Case 1: Missing metadata row
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("DELETE FROM sheets_sync_meta WHERE id = 1;")
    conn.commit()
    conn.close()

    with pytest.raises(AllocatorMetadataError):
        allocate_sheet_rows(1, db_path=tmp_db)

    # Case 2: Duplicate metadata row (recreate table without PK to simulate corruption)
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("DROP TABLE sheets_sync_meta;")
    conn.execute("CREATE TABLE sheets_sync_meta (id INTEGER, next_sheet_row INTEGER, created_at TEXT, updated_at TEXT);")
    conn.execute("INSERT INTO sheets_sync_meta VALUES (1, 5, '2026-11-24T12:00:00Z', '2026-11-24T12:00:00Z');")
    conn.execute("INSERT INTO sheets_sync_meta VALUES (2, 6, '2026-11-24T12:00:00Z', '2026-11-24T12:00:00Z');")
    conn.commit()
    conn.close()

    with pytest.raises(AllocatorMetadataError):
        allocate_sheet_rows(1, db_path=tmp_db)

    # Case 3: Invalid counter below 5 (recreate table without CHECK constraint to test allocator safeguard)
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("DROP TABLE sheets_sync_meta;")
    conn.execute("CREATE TABLE sheets_sync_meta (id INTEGER PRIMARY KEY, next_sheet_row INTEGER, created_at TEXT, updated_at TEXT);")
    conn.execute("INSERT INTO sheets_sync_meta VALUES (1, 4, '2026-11-24T12:00:00Z', '2026-11-24T12:00:00Z');")
    conn.commit()
    conn.close()

    with pytest.raises(AllocatorMetadataError):
        allocate_sheet_rows(1, db_path=tmp_db)

    # Case 4: Counter <= max allocated row (max assigned is 5, counter is 5)
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))
    conn = sqlite3.connect(str(tmp_db))
    _insert_synthetic_record(conn, student_id=1, sync_status="synced", sheet_row_number=5)
    conn.close()

    with pytest.raises(AllocatorConsistencyError):
        allocate_sheet_rows(1, db_path=tmp_db)

    # Case 5: Synced record with NULL sheet_row_number -> AllocatorInvariantError
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))
    conn = sqlite3.connect(str(tmp_db))
    _, qid_inv = _insert_synthetic_record(conn, student_id=1, sync_status="synced", sheet_row_number=None)
    conn.close()

    with pytest.raises(AllocatorInvariantError):
        allocate_sheet_rows(qid_inv, db_path=tmp_db)


# =============================================================================
# Test H: Transaction Rollback (Separate Disposable Fixture)
# =============================================================================
def test_test_h_transaction_rollback(tmp_path):
    """Test H: Forced failure mid-transaction rolls back assignments and preserves counter."""
    tmp_db = tmp_path / "rollback_test.db"
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    _, qid_h1 = _insert_synthetic_record(conn, student_id=1)
    _, qid_h2 = _insert_synthetic_record(conn, student_id=2)

    # Install trigger that aborts update specifically on second record
    conn.execute(
        f"""
        CREATE TRIGGER fail_on_second
        BEFORE UPDATE ON sheets_sync_queue
        WHEN NEW.id = {qid_h2}
        BEGIN
            SELECT RAISE(ABORT, 'Simulated failure during batch allocation');
        END;
        """
    )
    conn.commit()
    conn.close()

    # Attempt allocation of batch [qid_h1, qid_h2]
    with pytest.raises(AllocatorConcurrencyError) as exc_info:
        allocate_sheet_rows([qid_h1, qid_h2], db_path=tmp_db)
    assert "Simulated failure" in str(exc_info.value)

    # Verify rollback: qid_h1 was NOT assigned row 5, counter remains 5
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, sheet_row_number FROM sheets_sync_queue WHERE id IN (?, ?);", (qid_h1, qid_h2))
        rows = {r["id"]: r["sheet_row_number"] for r in cursor.fetchall()}
        assert rows[qid_h1] is None, f"Queue {qid_h1} assignment survived failed transaction!"
        assert rows[qid_h2] is None, f"Queue {qid_h2} assignment survived failed transaction!"

        cursor.execute("SELECT next_sheet_row FROM sheets_sync_meta WHERE id = 1;")
        assert cursor.fetchone()["next_sheet_row"] == 5, "Counter advanced despite rollback!"

        # Drop trigger and verify later allocation succeeds
        conn.execute("DROP TRIGGER fail_on_second;")
        conn.commit()
    finally:
        conn.close()

    res = allocate_sheet_rows([qid_h1, qid_h2], db_path=tmp_db)
    assert res == {qid_h1: 5, qid_h2: 6}
    assert get_next_sheet_row(db_path=tmp_db) == 7


# =============================================================================
# Test I: Concurrent Distinct Allocation (Separate Disposable Fixture)
# =============================================================================
def test_test_i_concurrent_distinct_allocation(tmp_path):
    """Test I: Concurrent threads with barrier allocating disjoint records produce disjoint rows."""
    tmp_db = tmp_path / "concurrency_distinct.db"
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    # Insert 10 unassigned records
    created_ids = []
    for _ in range(10):
        _, qid = _insert_synthetic_record(conn, student_id=1)
        created_ids.append(qid)
    conn.close()

    assert len(created_ids) == 10

    batch_a = created_ids[:5]
    batch_b = created_ids[5:]

    barrier = threading.Barrier(2)
    results_a = {}
    results_b = {}
    errors = []

    def worker_a():
        try:
            barrier.wait(timeout=5)
            res = allocate_sheet_rows(batch_a, db_path=tmp_db)
            results_a.update(res)
        except Exception as e:
            errors.append(("worker_a", e))

    def worker_b():
        try:
            barrier.wait(timeout=5)
            res = allocate_sheet_rows(batch_b, db_path=tmp_db)
            results_b.update(res)
        except Exception as e:
            errors.append(("worker_b", e))

    t1 = threading.Thread(target=worker_a)
    t2 = threading.Thread(target=worker_b)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"Errors in concurrent workers: {errors}"
    assert len(results_a) == 5
    assert len(results_b) == 5

    # Check for disjoint sets
    all_assigned_rows = list(results_a.values()) + list(results_b.values())
    assert len(all_assigned_rows) == 10
    assert len(set(all_assigned_rows)) == 10, f"Duplicate rows detected: {all_assigned_rows}"
    assert set(all_assigned_rows) == set(range(5, 15))

    # Verify final counter
    assert get_next_sheet_row(db_path=tmp_db) == 15

    # Verify database integrity
    conn = sqlite3.connect(str(tmp_db))
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        assert cursor.fetchall()[0][0] == "ok"
    finally:
        conn.close()


# =============================================================================
# Test J: Concurrent Same-Record Allocation (Separate Disposable Fixture)
# =============================================================================
def test_test_j_concurrent_same_record_allocation(tmp_path):
    """Test J: Concurrent threads allocating the same unassigned record return identical row."""
    tmp_db = tmp_path / "concurrency_same.db"
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    _, qid_j = _insert_synthetic_record(conn, student_id=1)
    conn.close()

    barrier = threading.Barrier(2)
    results_a = {}
    results_b = {}
    errors = []

    def worker(store):
        try:
            barrier.wait(timeout=5)
            res = allocate_sheet_rows(qid_j, db_path=tmp_db)
            store.update(res)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker, args=(results_a,))
    t2 = threading.Thread(target=worker, args=(results_b,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, f"Errors in concurrent workers: {errors}"
    assert results_a == {qid_j: 5}
    assert results_b == {qid_j: 5}
    assert get_next_sheet_row(db_path=tmp_db) == 6
    assert get_allocated_sheet_row(qid_j, db_path=tmp_db) == 5


# =============================================================================
# Test K: Existing Outbox Compatibility
# =============================================================================
def test_test_k_existing_outbox_compatibility(tmp_path):
    """Test K: Verify outbox record creation and subsequent allocation."""
    tmp_db = tmp_path / "outbox_compat.db"
    shutil.copyfile(str(SOURCE_SCHEMA_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
            VALUES ('CS-101: Test K', '2026-11-24', '12:00:00', 0, '2026-11-24T12:00:00Z');
            """
        )
        session_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (1, ?, '2026-11-24T12:00:00Z', 'present', 'rfid');
            """,
            (session_id,),
        )
        att_id = cursor.lastrowid

        # Use existing outbox service function to create queue record
        queue_id = create_queue_record(conn, att_id)
        conn.commit()

        # Check default column values
        cursor.execute("SELECT sheet_row_number, claim_token, sync_status FROM sheets_sync_queue WHERE id = ?;", (queue_id,))
        row = cursor.fetchone()
        assert row["sheet_row_number"] is None, "Newly inserted record should have NULL sheet_row_number"
        assert row["claim_token"] == 0, "Newly inserted record should have claim_token=0"
        assert row["sync_status"] == "pending"
    finally:
        conn.close()

    # Now allocate sheet row using new allocator
    alloc_res = allocate_sheet_rows(queue_id, db_path=tmp_db)
    assert alloc_res == {queue_id: 5}
    assert get_next_sheet_row(db_path=tmp_db) == 6
