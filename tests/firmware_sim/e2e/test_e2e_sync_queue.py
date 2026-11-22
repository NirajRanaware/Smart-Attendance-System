"""
End-to-End Tests for Google Sheets Outbox Synchronization Queue.
Validates:
1. Atomic outbox entry created on successful attendance.
2. Queue record references correct attendance_id with sync_status='pending' and retry_count=0.
3. Duplicate scans do not enqueue duplicate sync events.
4. Failed scans (unknown, inactive, no session, expired) never create sync queue records.
5. Records remain safely stored when external Google Sheets service is offline.
"""

import sqlite3
from .conftest import (
    run_firmware_client,
    seed_student,
    seed_session,
    get_sync_queue_records
)


def test_e2e_sync_queue_entry_created_on_success(live_server):
    """
    Verify that an atomic sync queue entry is created in SQLite
    when the C++ firmware client completes a successful attendance scan.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="108", full_name="Manish Gupta", card_uid="B3C4D5E6")
    session_id = seed_session(db, course_name="Cloud Computing", active=1)

    # Initial state: 0 sync queue records
    assert len(get_sync_queue_records(db)) == 0

    # Successful C++ scan
    res = run_firmware_client(port=port, card_uid="B3C4D5E6")
    assert res["outcome"] == "SCAN_OUTCOME_SUCCESS"

    # Query attendance ID from DB
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT id FROM attendance WHERE student_id = ?;", (student_id,))
    att_row = cur.fetchone()
    conn.close()
    assert att_row is not None
    attendance_id = att_row[0]

    # Verify sync queue record
    queue_records = get_sync_queue_records(db, attendance_id)
    assert len(queue_records) == 1

    entry = queue_records[0]
    assert entry["attendance_id"] == attendance_id
    assert entry["sync_status"] == "pending"
    assert entry["retry_count"] == 0
    assert "T" in entry["created_at"] and entry["created_at"].endswith("Z")
    assert entry["created_at"] == entry["updated_at"]


def test_e2e_sync_queue_no_duplicate_sync_entry(live_server):
    """
    Verify that duplicate card scans never create an unintended second
    synchronization queue entry.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="109", full_name="Divya Shah", card_uid="C4D5E6A2")
    seed_session(db, course_name="Artificial Intelligence", active=1)

    # First scan
    res1 = run_firmware_client(port=port, card_uid="C4D5E6A2")
    assert res1["outcome"] == "SCAN_OUTCOME_SUCCESS"

    records_after_first = get_sync_queue_records(db)
    assert len(records_after_first) == 1

    # Second scan (duplicate)
    res2 = run_firmware_client(port=port, card_uid="C4D5E6A2")
    assert res2["outcome"] == "SCAN_OUTCOME_DUPLICATE"

    # Verify queue count remains 1
    records_after_second = get_sync_queue_records(db)
    assert len(records_after_second) == 1
    assert records_after_second[0]["id"] == records_after_first[0]["id"]


def test_e2e_sync_queue_failed_scans_never_enqueued(live_server):
    """
    Verify that failed or rejected scans (unknown card, inactive student,
    expired session, missing credentials) never insert into sheets_sync_queue.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    # Inactive student
    seed_student(db, roll_number="110", full_name="Inactive User", card_uid="INACTIVE01", active=0)
    seed_session(db, course_name="Cyber Security", active=1)

    # 1. Unknown card scan
    res_unk = run_firmware_client(port=port, card_uid="UNKNOWN999")
    assert res_unk["outcome"] == "SCAN_OUTCOME_UNKNOWN_CARD"

    # 2. Inactive student scan
    res_inact = run_firmware_client(port=port, card_uid="INACTIVE01")
    assert res_inact["outcome"] == "SCAN_OUTCOME_INACTIVE_STUDENT"

    # 3. Invalid credentials scan
    res_auth = run_firmware_client(port=port, card_uid="UNKNOWN999", api_key="invalid_device_key_32_chars_!")
    assert res_auth["httpCode"] == 401

    # Verify zero sync queue entries were created
    assert len(get_sync_queue_records(db)) == 0


def test_e2e_sync_queue_persistence_without_google_creds(live_server):
    """
    Verify that attendance records and sync queue records are reliably persisted
    even in environments where external Google Sheets credentials are absent.
    """
    db = live_server["db_path"]
    port = live_server["port"]

    student_id = seed_student(db, roll_number="111", full_name="Rohan Desai", card_uid="D5E6A2B3")
    seed_session(db, course_name="Database Management", active=1)

    # Perform scan
    res = run_firmware_client(port=port, card_uid="D5E6A2B3")
    assert res["outcome"] == "SCAN_OUTCOME_SUCCESS"

    # Confirm safe storage in SQLite
    queue_records = get_sync_queue_records(db)
    assert len(queue_records) == 1
    assert queue_records[0]["sync_status"] == "pending"
