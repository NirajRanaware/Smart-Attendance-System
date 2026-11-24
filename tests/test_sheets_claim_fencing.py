"""
Comprehensive unit and integration test suite for Google Sheets Outbox Claim-Token Fencing.
Stage 9.7B.13H Specification.

Validates:
- Test A: First claim increments token from 0 to 1
- Test B: Reclaim increments token from 1 to 2
- Test C: Multiple records receive independent tokens
- Test D: Stale worker mark_as_synced rejection
- Test E: Stale worker schedule_retry rejection
- Test F: Stale worker move_to_dead_letter rejection
- Test G: Stale worker release_claim rejection
- Test H: Current-token transitions succeed and execute exactly once
- Test I: Expired lease recovery selectively resets only expired in_progress records
- Test J: Concurrent recovery operations are idempotent and race-free
- Test K: Concurrent worker claim requests produce disjoint claims with unique tokens
- Test L: Same-record transition race allows only one winner
- Test M: Token monotonicity across claim, retry, requeue, and reclaim cycles
- Test N: Administrative requeue invalidates old claim generations safely
- Test O: Atomic transaction rollback leaves no partial token or status changes
- Test P: Supplied connection ownership and savepoint behavior
- Test Q: Internally created connection lifecycle and cleanup
- Test R: Missing and malformed tokens fail closed
"""

import shutil
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from app.services.sheets_outbox import (
    create_queue_record,
    get_queue_stats,
    get_pending_records,
    claim_records,
    recover_expired_leases,
    mark_as_synced,
    schedule_retry,
    mark_as_failed,
    move_to_dead_letter,
    release_claim,
    retry_dead_letter_records,
)
from app.services.sheets_row_allocator import get_next_sheet_row

PRIMARY_FENCING_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_fencing_test.db")
SOURCE_ALLOCATOR_DB = Path(r"C:\Projects\Smart-Attendance-System\database\manual_test\attendance_allocator_test.db")


def _get_utc_now_iso(dt: datetime = None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_synthetic_attendance_and_queue(
    conn: sqlite3.Connection,
    student_id: int = 1,
    course_name: str = "CS-101: Test Fencing",
    sync_status: str = "pending",
    claim_token: int = 0,
    sheet_row_number: int = None,
) -> tuple[int, int]:
    """Helper to insert valid class session, attendance, and queue records."""
    now_utc = _get_utc_now_iso()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES (?, '2026-11-24', '12:00:00', 0, ?);
        """,
        (course_name, now_utc),
    )
    session_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, ?, 'present', 'rfid');
        """,
        (student_id, session_id, now_utc),
    )
    att_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO sheets_sync_queue (
            attendance_id, sync_status, retry_count, claim_token, sheet_row_number, created_at, updated_at
        ) VALUES (?, ?, 0, ?, ?, ?, ?);
        """,
        (att_id, sync_status, claim_token, sheet_row_number, now_utc, now_utc),
    )
    queue_id = cursor.lastrowid
    conn.commit()
    return att_id, queue_id


# =============================================================================
# Test A: First Claim Increments Token
# =============================================================================
def test_test_a_first_claim_increments_token():
    """Test A: First claim of an eligible record increments claim_token from 0 to 1."""
    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    # Queue ID 6 is pending in PRIMARY_FENCING_DB with claim_token = 0
    claimed = claim_records(
        limit=1,
        lease_duration_seconds=60,
        now_dt=t0,
        db_path=PRIMARY_FENCING_DB
    )
    assert len(claimed) == 1
    rec = claimed[0]
    assert rec["sync_status"] == "in_progress"
    assert rec["claim_token"] == 1
    assert rec["lease_expires_at"] == "2026-11-24T10:01:00Z"

    # Verify directly in database
    conn = sqlite3.connect(str(PRIMARY_FENCING_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT claim_token, sync_status, lease_expires_at FROM sheets_sync_queue WHERE id = ?;", (rec["queue_id"],)).fetchone()
    conn.close()
    assert row["claim_token"] == 1
    assert row["sync_status"] == "in_progress"
    assert row["lease_expires_at"] == "2026-11-24T10:01:00Z"


# =============================================================================
# Test B: Reclaim Increments Token
# =============================================================================
def test_test_b_reclaim_increments_token():
    """Test B: Expiring lease, recovering it, and claiming again increments claim_token from 1 to 2."""
    t_expire = datetime(2026, 11, 24, 10, 2, 0, tzinfo=timezone.utc)
    # Recover expired lease (from Test A)
    recovered = recover_expired_leases(now_dt=t_expire, db_path=PRIMARY_FENCING_DB)
    assert recovered >= 1

    # Claim again
    t_reclaim = datetime(2026, 11, 24, 10, 3, 0, tzinfo=timezone.utc)
    claimed = claim_records(
        limit=1,
        lease_duration_seconds=60,
        now_dt=t_reclaim,
        db_path=PRIMARY_FENCING_DB
    )
    assert len(claimed) == 1
    rec = claimed[0]
    assert rec["claim_token"] == 2
    assert rec["sync_status"] == "in_progress"
    assert rec["lease_expires_at"] == "2026-11-24T10:04:00Z"


# =============================================================================
# Test C: Multiple Records Receive Independent Tokens
# =============================================================================
def test_test_c_multiple_records_receive_independent_tokens(tmp_path):
    """Test C: Batch claim assigns independent incremented tokens to all selected records."""
    tmp_db = tmp_path / "batch_tokens.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    # Claim first 3 pending records (they have claim_token=0)
    claimed = claim_records(limit=3, now_dt=t0, db_path=tmp_db)
    assert len(claimed) == 3
    for r in claimed:
        assert r["claim_token"] == 1

    # Update one of them to have claim_token=5
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("UPDATE sheets_sync_queue SET claim_token = 5 WHERE id = ?;", (claimed[0]["queue_id"],))
    conn.commit()
    conn.close()

    # Expire and recover
    t_exp = datetime(2026, 11, 24, 12, 5, 0, tzinfo=timezone.utc)
    recover_expired_leases(now_dt=t_exp, db_path=tmp_db)

    # Reclaim batch
    reclaimed = claim_records(limit=3, now_dt=t_exp, db_path=tmp_db)
    token_map = {r["queue_id"]: r["claim_token"] for r in reclaimed}
    assert token_map[claimed[0]["queue_id"]] == 6  # 5 + 1
    assert token_map[claimed[1]["queue_id"]] == 2  # 1 + 1
    assert token_map[claimed[2]["queue_id"]] == 2  # 1 + 1


# =============================================================================
# Test D: Stale Mark-As-Synced Rejection
# =============================================================================
def test_test_d_stale_mark_as_synced_rejection(tmp_path):
    """Test D: Worker A with stale token 1 is rejected after Worker B claims token 2."""
    tmp_db = tmp_path / "stale_synced.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed_a = claim_records(limit=1, lease_duration_seconds=30, now_dt=t0, db_path=tmp_db)
    qid = claimed_a[0]["queue_id"]
    token_a = claimed_a[0]["claim_token"]
    assert token_a == 1

    # Worker A pauses; lease expires at 10:00:30
    t_rec = datetime(2026, 11, 24, 10, 0, 35, tzinfo=timezone.utc)
    recover_expired_leases(now_dt=t_rec, db_path=tmp_db)

    # Worker B claims record with token 2
    claimed_b = claim_records(limit=1, lease_duration_seconds=60, now_dt=t_rec, db_path=tmp_db)
    assert claimed_b[0]["queue_id"] == qid
    assert claimed_b[0]["claim_token"] == 2

    # Worker A resumes and attempts mark_as_synced with stale token 1
    res_a = mark_as_synced(queue_id=qid, claim_token=token_a, db_path=tmp_db)
    assert res_a is False, "Stale worker mark_as_synced was not rejected!"

    # Verify record in DB: status remains in_progress with token 2 and no synced_at
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token, synced_at FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2
    assert row["synced_at"] is None


# =============================================================================
# Test E: Stale Retry Rejection
# =============================================================================
def test_test_e_stale_retry_rejection(tmp_path):
    """Test E: Worker A with stale token 1 cannot schedule retry after Worker B claims token 2."""
    tmp_db = tmp_path / "stale_retry.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed_a = claim_records(limit=1, lease_duration_seconds=30, now_dt=t0, db_path=tmp_db)
    qid = claimed_a[0]["queue_id"]
    token_a = claimed_a[0]["claim_token"]

    t_rec = datetime(2026, 11, 24, 10, 0, 35, tzinfo=timezone.utc)
    recover_expired_leases(now_dt=t_rec, db_path=tmp_db)

    claimed_b = claim_records(limit=1, lease_duration_seconds=60, now_dt=t_rec, db_path=tmp_db)
    token_b = claimed_b[0]["claim_token"]
    assert token_b == 2

    # Worker A attempts schedule_retry with stale token 1
    res_a = schedule_retry(queue_id=qid, claim_token=token_a, error_message="Stale error", db_path=tmp_db)
    assert res_a["success"] is False
    assert res_a["claim_lost"] is True

    # Verify DB: status is still in_progress, retry_count is 0, token is 2
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, retry_count, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "in_progress"
    assert row["retry_count"] == 0
    assert row["claim_token"] == 2


# =============================================================================
# Test F: Stale Dead-Letter Rejection
# =============================================================================
def test_test_f_stale_dead_letter_rejection(tmp_path):
    """Test F: Worker A with stale token 1 cannot move record to dead_letter."""
    tmp_db = tmp_path / "stale_dl.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed_a = claim_records(limit=1, lease_duration_seconds=30, now_dt=t0, db_path=tmp_db)
    qid = claimed_a[0]["queue_id"]
    token_a = claimed_a[0]["claim_token"]

    t_rec = datetime(2026, 11, 24, 10, 0, 35, tzinfo=timezone.utc)
    recover_expired_leases(now_dt=t_rec, db_path=tmp_db)
    claim_records(limit=1, lease_duration_seconds=60, now_dt=t_rec, db_path=tmp_db)

    # Worker A attempts move_to_dead_letter with token 1
    res_a = move_to_dead_letter(queue_id=qid, claim_token=token_a, error_message="Permanent failure", db_path=tmp_db)
    assert res_a["success"] is False
    assert res_a["claim_lost"] is True

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2


# =============================================================================
# Test G: Stale Release Rejection
# =============================================================================
def test_test_g_stale_release_rejection(tmp_path):
    """Test G: Worker A with stale token 1 cannot release a claim owned by Worker B."""
    tmp_db = tmp_path / "stale_rel.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed_a = claim_records(limit=1, lease_duration_seconds=30, now_dt=t0, db_path=tmp_db)
    qid = claimed_a[0]["queue_id"]
    token_a = claimed_a[0]["claim_token"]

    t_rec = datetime(2026, 11, 24, 10, 0, 35, tzinfo=timezone.utc)
    recover_expired_leases(now_dt=t_rec, db_path=tmp_db)
    claim_records(limit=1, lease_duration_seconds=60, now_dt=t_rec, db_path=tmp_db)

    # Worker A attempts release_claim with token 1
    res = release_claim(queue_id=qid, claim_token=token_a, db_path=tmp_db)
    assert res is False

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 2


# =============================================================================
# Test H: Current-Token Transitions Succeed
# =============================================================================
def test_test_h_current_token_transitions_succeed(tmp_path):
    """Test H: Active current worker transitions succeed and execute exactly once."""
    tmp_db = tmp_path / "current_token.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    t0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
    claimed = claim_records(limit=2, lease_duration_seconds=60, now_dt=t0, db_path=tmp_db)
    q1 = claimed[0]["queue_id"]
    tok1 = claimed[0]["claim_token"]
    q2 = claimed[1]["queue_id"]
    tok2 = claimed[1]["claim_token"]

    # 1. Successful mark_as_synced
    assert mark_as_synced(queue_id=q1, claim_token=tok1, now_dt=t0, db_path=tmp_db) is True
    # Attempting second mark_as_synced with same token returns False (already synced)
    assert mark_as_synced(queue_id=q1, claim_token=tok1, now_dt=t0, db_path=tmp_db) is False

    # 2. Successful schedule_retry
    res_retry = schedule_retry(queue_id=q2, claim_token=tok2, error_message="HTTP 500", now_dt=t0, db_path=tmp_db)
    assert res_retry["success"] is True
    assert res_retry["sync_status"] == "failed"
    assert res_retry["retry_count"] == 1


# =============================================================================
# Test I: Expired Lease Recovery
# =============================================================================
def test_test_i_expired_lease_recovery(tmp_path):
    """Test I: Recovery selectively resets only expired in_progress records."""
    tmp_db = tmp_path / "lease_selective.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    # Record 1: in_progress with lease in past (expired)
    _, q_exp = _insert_synthetic_attendance_and_queue(conn, sync_status="in_progress", claim_token=1)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = '2026-11-24T10:00:00Z' WHERE id = ?;", (q_exp,))

    # Record 2: in_progress with lease in future (valid)
    _, q_valid = _insert_synthetic_attendance_and_queue(conn, sync_status="in_progress", claim_token=1)
    conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = '2026-11-24T10:10:00Z' WHERE id = ?;", (q_valid,))

    # Record 3: pending
    _, q_pending = _insert_synthetic_attendance_and_queue(conn, sync_status="pending", claim_token=0)

    # Record 4: failed not yet due
    _, q_failed = _insert_synthetic_attendance_and_queue(conn, sync_status="failed", claim_token=1)
    conn.execute("UPDATE sheets_sync_queue SET next_retry_at = '2026-11-24T10:30:00Z' WHERE id = ?;", (q_failed,))
    conn.commit()
    conn.close()

    t_now = datetime(2026, 11, 24, 10, 5, 0, tzinfo=timezone.utc)
    recovered = recover_expired_leases(now_dt=t_now, db_path=tmp_db)
    assert recovered == 1

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, sync_status, lease_expires_at, claim_token FROM sheets_sync_queue;").fetchall()}
    conn.close()

    assert rows[q_exp]["sync_status"] == "pending"
    assert rows[q_exp]["lease_expires_at"] is None
    assert rows[q_exp]["claim_token"] == 1  # Token preserved!

    assert rows[q_valid]["sync_status"] == "in_progress"
    assert rows[q_valid]["lease_expires_at"] == "2026-11-24T10:10:00Z"

    assert rows[q_pending]["sync_status"] == "pending"
    assert rows[q_failed]["sync_status"] == "failed"


# =============================================================================
# Test J: Recovery Concurrency
# =============================================================================
def test_test_j_recovery_concurrency(tmp_path):
    """Test J: Concurrent recovery operations on separate connections are idempotent."""
    tmp_db = tmp_path / "rec_concurrency.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    # Insert 5 expired records
    for _ in range(5):
        _, qid = _insert_synthetic_attendance_and_queue(conn, sync_status="in_progress", claim_token=1)
        conn.execute("UPDATE sheets_sync_queue SET lease_expires_at = '2026-11-24T10:00:00Z' WHERE id = ?;", (qid,))
    conn.commit()
    conn.close()

    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait(timeout=5)
        t_now = datetime(2026, 11, 24, 10, 5, 0, tzinfo=timezone.utc)
        count = recover_expired_leases(now_dt=t_now, db_path=tmp_db)
        results.append(count)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sum(results) == 5
    # Database integrity check
    conn = sqlite3.connect(str(tmp_db))
    assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    conn.close()


# =============================================================================
# Test K: Claim Concurrency
# =============================================================================
def test_test_k_claim_concurrency(tmp_path):
    """Test K: Concurrent workers claiming from same pool receive disjoint sets and unique tokens."""
    tmp_db = tmp_path / "claim_concurrency.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    # In tmp_db, there are already 7 pending records (6..12). Let's insert 3 more so there are 10.
    conn = sqlite3.connect(str(tmp_db))
    for _ in range(3):
        _insert_synthetic_attendance_and_queue(conn, sync_status="pending", claim_token=0)
    conn.close()

    barrier = threading.Barrier(2)
    claimed_a = []
    claimed_b = []

    def worker_a():
        barrier.wait(timeout=5)
        res = claim_records(limit=10, db_path=tmp_db)
        claimed_a.extend(res)

    def worker_b():
        barrier.wait(timeout=5)
        res = claim_records(limit=10, db_path=tmp_db)
        claimed_b.extend(res)

    t1 = threading.Thread(target=worker_a)
    t2 = threading.Thread(target=worker_b)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    ids_a = {r["queue_id"] for r in claimed_a}
    ids_b = {r["queue_id"] for r in claimed_b}

    # Mutual exclusion: sets must be completely disjoint
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a) + len(ids_b) == 10
    # Every claimed record has claim_token == 1
    for r in claimed_a + claimed_b:
        assert r["claim_token"] == 1


# =============================================================================
# Test L: Same-Record Transition Race
# =============================================================================
def test_test_l_same_record_transition_race(tmp_path):
    """Test L: Two workers racing to transition the same claimed record allow only one winner."""
    tmp_db = tmp_path / "transition_race.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    claimed = claim_records(limit=1, lease_duration_seconds=60, db_path=tmp_db)
    qid = claimed[0]["queue_id"]
    token = claimed[0]["claim_token"]

    barrier = threading.Barrier(2)
    results = {}

    def worker_sync():
        barrier.wait(timeout=5)
        results["sync"] = mark_as_synced(queue_id=qid, claim_token=token, db_path=tmp_db)

    def worker_retry():
        barrier.wait(timeout=5)
        results["retry"] = schedule_retry(queue_id=qid, claim_token=token, error_message="Race err", db_path=tmp_db)

    t1 = threading.Thread(target=worker_sync)
    t2 = threading.Thread(target=worker_retry)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Exactly one operation succeeded; the other failed closed
    if results["sync"] is True:
        assert results["retry"]["success"] is False
    else:
        assert results["sync"] is False
        assert results["retry"]["success"] is True


# =============================================================================
# Test M: Token Monotonicity
# =============================================================================
def test_test_m_token_monotonicity(tmp_path):
    """Test M: Tokens strictly increase across claim, retry, requeue, and reclaim cycles."""
    tmp_db = tmp_path / "monotonicity.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    # Claim 1 record from pool
    c1 = claim_records(limit=1, lease_duration_seconds=60, db_path=tmp_db)
    qid = c1[0]["queue_id"]
    assert c1[0]["claim_token"] == 1

    # Retry 1 -> status failed, token remains 1
    res1 = schedule_retry(queue_id=qid, claim_token=1, error_message="Err 1", base_delay=0, jitter=False, db_path=tmp_db)
    assert res1["success"] is True
    assert res1["sync_status"] == "failed"

    # Claim 2 -> token 2
    c2 = claim_records(limit=1, lease_duration_seconds=60, db_path=tmp_db)
    assert c2[0]["queue_id"] == qid
    assert c2[0]["claim_token"] == 2

    # Retry 2 -> max_retries reached (max_retries=2) -> dead_letter
    res2 = schedule_retry(queue_id=qid, claim_token=2, error_message="Err 2", max_retries=2, db_path=tmp_db)
    assert res2["success"] is True
    assert res2["sync_status"] == "dead_letter"

    # Administrative requeue -> token increments to 3
    req = retry_dead_letter_records(queue_ids=[qid], db_path=tmp_db)
    assert req["requeued_count"] == 1

    # Claim 3 -> token 4
    c3 = claim_records(limit=1, lease_duration_seconds=60, db_path=tmp_db)
    assert c3[0]["queue_id"] == qid
    assert c3[0]["claim_token"] == 4


# =============================================================================
# Test N: Administrative Requeue
# =============================================================================
def test_test_n_administrative_requeue(tmp_path):
    """Test N: Admin requeue invalidates old claim tokens and preserves row numbers."""
    tmp_db = tmp_path / "admin_requeue.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    # Use sheet_row_number=99 to avoid conflicting with existing assigned rows 3..11
    _, qid = _insert_synthetic_attendance_and_queue(conn, sync_status="dead_letter", claim_token=5, sheet_row_number=99)
    conn.close()

    # Requeue record
    res = retry_dead_letter_records(queue_ids=[qid], reset_retries=True, db_path=tmp_db)
    assert res["requeued_count"] == 1

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token, sheet_row_number, retry_count FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()

    assert row["sync_status"] == "pending"
    assert row["claim_token"] == 6  # Incremented from 5 to 6
    assert row["sheet_row_number"] == 99  # Preserved!
    assert row["retry_count"] == 0

    # Stale worker attempting to mark synced with old token 5 is rejected
    assert mark_as_synced(queue_id=qid, claim_token=5, db_path=tmp_db) is False


# =============================================================================
# Test O: Transaction Rollback
# =============================================================================
def test_test_o_transaction_rollback(tmp_path):
    """Test O: Aborted transaction rolls back all ownership changes and token increments."""
    tmp_db = tmp_path / "tx_rollback.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    _, qid = _insert_synthetic_attendance_and_queue(conn, claim_token=0)
    # Trigger that aborts on status 'in_progress'
    conn.execute(
        """
        CREATE TRIGGER abort_claim
        BEFORE UPDATE OF sync_status ON sheets_sync_queue
        WHEN NEW.sync_status = 'in_progress'
        BEGIN
            SELECT RAISE(ABORT, 'Simulated claim failure');
        END;
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.DatabaseError) as exc_info:
        claim_records(limit=1, db_path=tmp_db)
    assert "Simulated claim failure" in str(exc_info.value)

    # Verify state rolled back: token remains 0, status remains pending
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "pending"
    assert row["claim_token"] == 0


# =============================================================================
# Test P: Supplied Connection Ownership & Savepoint
# =============================================================================
def test_test_p_supplied_connection_ownership(tmp_path):
    """Test P: Outbox functions respect external caller transaction without premature commit."""
    tmp_db = tmp_path / "conn_ownership.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    # Caller starts transaction
    conn.execute("BEGIN IMMEDIATE;")
    conn.execute("INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at) VALUES ('Caller Session', '2026-11-24', '12:00', 0, '2026-11-24T12:00:00Z');")

    # Call claim_records with supplied conn
    claimed = claim_records(limit=1, conn=conn)
    assert len(claimed) == 1
    qid = claimed[0]["queue_id"]
    tok = claimed[0]["claim_token"]

    # Fenced transition inside same caller transaction
    res = mark_as_synced(queue_id=qid, claim_token=tok, conn=conn)
    assert res is True

    # Caller decides to roll back entire transaction
    conn.rollback()

    # Verify everything rolled back
    row = conn.execute("SELECT sync_status FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    assert row["sync_status"] == "pending"
    session_row = conn.execute("SELECT COUNT(*) FROM class_sessions WHERE course_name = 'Caller Session';").fetchone()[0]
    assert session_row == 0
    conn.close()


# =============================================================================
# Test Q: Internally Created Connection Lifecycle
# =============================================================================
def test_test_q_internally_created_connection_lifecycle(tmp_path):
    """Test Q: Calling functions with db_path opens, commits, and closes connections cleanly."""
    tmp_db = tmp_path / "internal_conn.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    claimed = claim_records(limit=1, db_path=tmp_db)
    assert len(claimed) == 1
    qid = claimed[0]["queue_id"]
    tok = claimed[0]["claim_token"]

    # DB file should not be locked; a new connection can write immediately
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("PRAGMA integrity_check;")
    conn.close()

    res = mark_as_synced(queue_id=qid, claim_token=tok, db_path=tmp_db)
    assert res is True


# =============================================================================
# Test R: Missing and Malformed Tokens Fail Closed
# =============================================================================
def test_test_r_missing_and_malformed_tokens_fail_closed(tmp_path):
    """Test R: None, string, boolean, negative, and zero tokens raise ValueError without modifying DB."""
    tmp_db = tmp_path / "malformed_tokens.db"
    shutil.copyfile(str(SOURCE_ALLOCATOR_DB), str(tmp_db))

    claimed = claim_records(limit=1, db_path=tmp_db)
    qid = claimed[0]["queue_id"]

    for bad_token in (None, True, False, "1", -1, 0, 1.5):
        with pytest.raises(ValueError):
            mark_as_synced(queue_id=qid, claim_token=bad_token, db_path=tmp_db)
        with pytest.raises(ValueError):
            schedule_retry(queue_id=qid, claim_token=bad_token, error_message="err", db_path=tmp_db)
        with pytest.raises(ValueError):
            release_claim(queue_id=qid, claim_token=bad_token, db_path=tmp_db)
        with pytest.raises(ValueError):
            move_to_dead_letter(queue_id=qid, claim_token=bad_token, error_message="err", db_path=tmp_db)

    # Verify status in DB is untouched
    conn = sqlite3.connect(str(tmp_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT sync_status, claim_token FROM sheets_sync_queue WHERE id = ?;", (qid,)).fetchone()
    conn.close()
    assert row["sync_status"] == "in_progress"
    assert row["claim_token"] == 1
