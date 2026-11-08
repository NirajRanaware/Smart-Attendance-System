"""
Automated Integration and Functional Tests for Session End Time & Automatic Deactivation.
Covers:
1. Valid session creation with end time (HTTP 201).
2. Missing end time rejected (HTTP 400).
3. Invalid time format rejected (HTTP 400).
4. End time equal to start time rejected (HTTP 400).
5. End time earlier than start time rejected (HTTP 400).
6. Automatic deactivation at expiry using frozen time (session becomes inactive).
7. Attendance rejection after expiry (HTTP 409 Conflict).
8. Previously recorded attendance remains unchanged after expiry.
9. Browser-independent backend deactivation via scheduler.
10. Session activation and switching remain correct with end_time.
11. Existing sessions without end_time remain readable (returns None / Not Set).
12. Scheduler concurrency and application lifecycle (deduplication, stop/start).
13. Full regression check (all endpoints behave reliably).
"""

from datetime import datetime
import pytest
from app import create_app
from app.database import init_db, get_db_connection, close_db_connection
from app.time_utils import set_frozen_time, clear_frozen_time, KOLKATA_TZ
from app.scheduler import check_and_deactivate_expired_sessions, SessionExpiryScheduler


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_session_end_time.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


# --------------------------------------------------------------------------
# TEST 1: Valid Session Creation with End Time (HTTP 201)
# --------------------------------------------------------------------------
def test_1_valid_session_creation_with_end_time(client, app_instance):
    """TEST 1: Verify valid session creation succeeds with end_time stored in SQLite."""
    payload = {
        "course_name": "CS-501: Advanced Cloud Computing",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:30"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 201
    data = res.get_json()
    assert data["success"] is True
    assert data["session"]["course_name"] == "CS-501: Advanced Cloud Computing"
    assert data["session"]["session_date"] == "2026-11-10"
    assert data["session"]["start_time"] == "10:00"
    assert data["session"]["end_time"] == "11:30"
    assert data["session"]["active"] is False
    sess_id = data["session"]["id"]

    # Verify directly in SQLite
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT id, course_name, start_time, end_time, active FROM class_sessions WHERE id = ?;", (sess_id,))
    row = cursor.fetchone()
    close_db_connection(conn)

    assert row is not None
    assert row["end_time"] == "11:30"
    assert row["active"] == 0


# --------------------------------------------------------------------------
# TEST 2: Missing End Time Rejected (HTTP 400)
# --------------------------------------------------------------------------
def test_2_missing_end_time_rejected(client):
    """TEST 2: Verify missing end_time returns HTTP 400 with descriptive error."""
    payload = {
        "course_name": "CS-501: Advanced Cloud Computing",
        "session_date": "2026-11-10",
        "start_time": "10:00"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
    assert "end_time" in data["message"].lower()


# --------------------------------------------------------------------------
# TEST 3: Invalid Time Format Rejected (HTTP 400)
# --------------------------------------------------------------------------
def test_3_invalid_time_format_rejected(client):
    """TEST 3: Verify malformed or non-calendar end_time returns HTTP 400."""
    # Malformed string
    res1 = client.post("/api/sessions", json={
        "course_name": "CS-501", "session_date": "2026-11-10", "start_time": "10:00", "end_time": "noon"
    })
    assert res1.status_code == 400
    assert "24-hour" in res1.get_json()["message"]

    # Hour out of range (> 23)
    res2 = client.post("/api/sessions", json={
        "course_name": "CS-501", "session_date": "2026-11-10", "start_time": "10:00", "end_time": "25:00"
    })
    assert res2.status_code == 400
    assert "not a valid 24-hour time" in res2.get_json()["message"]

    # Minute out of range (> 59)
    res3 = client.post("/api/sessions", json={
        "course_name": "CS-501", "session_date": "2026-11-10", "start_time": "10:00", "end_time": "11:75"
    })
    assert res3.status_code == 400
    assert "not a valid 24-hour time" in res3.get_json()["message"]


# --------------------------------------------------------------------------
# TEST 4: End Time Equal to Start Time Rejected (HTTP 400)
# --------------------------------------------------------------------------
def test_4_end_time_equal_to_start_time_rejected(client):
    """TEST 4: Verify end_time equal to start_time returns HTTP 400."""
    payload = {
        "course_name": "Zero Duration Session",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "10:00"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
    assert "later than start time" in data["message"].lower()


# --------------------------------------------------------------------------
# TEST 5: End Time Earlier Than Start Time Rejected (HTTP 400)
# --------------------------------------------------------------------------
def test_5_end_time_earlier_than_start_time_rejected(client):
    """TEST 5: Verify end_time before start_time returns HTTP 400."""
    payload = {
        "course_name": "Reverse Duration Session",
        "session_date": "2026-11-10",
        "start_time": "11:00",
        "end_time": "09:30"
    }
    res = client.post("/api/sessions", json=payload)
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False
    assert "later than start time" in data["message"].lower()


# --------------------------------------------------------------------------
# TEST 6: Automatic Deactivation at Expiry Using Frozen Time
# --------------------------------------------------------------------------
def test_6_automatic_deactivation_at_expiry(client, app_instance):
    """TEST 6: Verify session is active before end_time and deactivated when time >= end_time."""
    # Set time to 2026-11-10 10:00:00 (during lecture)
    set_frozen_time(datetime(2026, 11, 10, 10, 0, 0, tzinfo=KOLKATA_TZ))

    # Create and activate session ending at 11:00
    create_res = client.post("/api/sessions", json={
        "course_name": "Automated Expiry Lecture",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:00"
    })
    sess_id = create_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Fast forward to 10:45:00 -> Session should still be active
    set_frozen_time(datetime(2026, 11, 10, 10, 45, 0, tzinfo=KOLKATA_TZ))
    active_res = client.get("/api/sessions/active")
    assert active_res.status_code == 200
    assert active_res.get_json()["session"] is not None
    assert active_res.get_json()["session"]["id"] == sess_id

    # Fast forward to 11:00:01 (expiry point reached)
    set_frozen_time(datetime(2026, 11, 10, 11, 0, 1, tzinfo=KOLKATA_TZ))

    # Query active session -> JIT check deactivates session
    active_after = client.get("/api/sessions/active")
    assert active_after.status_code == 200
    assert active_after.get_json()["session"] is None

    # Verify directly in SQLite that active is now 0
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT active FROM class_sessions WHERE id = ?;", (sess_id,))
    row = cursor.fetchone()
    close_db_connection(conn)
    assert row["active"] == 0


# --------------------------------------------------------------------------
# TEST 7: Attendance Rejection After Expiry (HTTP 409 Conflict)
# --------------------------------------------------------------------------
def test_7_attendance_rejection_after_expiry(client):
    """TEST 7: Verify card scan is rejected with HTTP 409 when session has reached end_time."""
    # Register student
    client.post("/api/students", json={
        "roll_number": "EXPIRE-01",
        "full_name": "Test Student Expiry",
        "card_uid": "EXPIRE99"
    })

    # Set time to 2026-11-10 10:15:00
    set_frozen_time(datetime(2026, 11, 10, 10, 15, 0, tzinfo=KOLKATA_TZ))

    # Create and activate session ending at 11:00
    create_res = client.post("/api/sessions", json={
        "course_name": "Expiry Test Session",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:00"
    })
    sess_id = create_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Fast forward time to 11:05:00 (past end time)
    set_frozen_time(datetime(2026, 11, 10, 11, 5, 0, tzinfo=KOLKATA_TZ))

    # Submit RFID scan
    scan_res = client.post("/api/scans", json={
        "card_uid": "EXPIRE99",
        "source": "ESP32_Scanner"
    })
    assert scan_res.status_code == 409
    data = scan_res.get_json()
    assert data["success"] is False
    assert "ended" in data["message"].lower()

    # Verify session is now inactive
    active_res = client.get("/api/sessions/active")
    assert active_res.get_json()["session"] is None


# --------------------------------------------------------------------------
# TEST 8: Previously Recorded Attendance Preserved After Expiry
# --------------------------------------------------------------------------
def test_8_past_attendance_preserved_after_expiry(client, app_instance):
    """TEST 8: Verify attendance recorded during session remains intact in DB after session expires."""
    # Register student
    client.post("/api/students", json={
        "roll_number": "PRESERVE-01",
        "full_name": "Preserved Student",
        "card_uid": "PRESERVE01"
    })

    # Set time to 10:15:00
    set_frozen_time(datetime(2026, 11, 10, 10, 15, 0, tzinfo=KOLKATA_TZ))

    # Create & activate session ending at 11:00
    s_res = client.post("/api/sessions", json={
        "course_name": "Preservation Test Session",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:00"
    })
    sess_id = s_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess_id}/activate")

    # Valid scan during session
    scan_res = client.post("/api/scans", json={"card_uid": "PRESERVE01", "source": "ESP32"})
    assert scan_res.status_code == 201

    # Fast forward time past expiry (11:30:00)
    set_frozen_time(datetime(2026, 11, 10, 11, 30, 0, tzinfo=KOLKATA_TZ))

    # Trigger deactivation check via GET /api/sessions/active
    client.get("/api/sessions/active")

    # Verify past attendance record is completely intact
    conn = get_db_connection(app_instance.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT id, student_id, session_id, status FROM attendance WHERE session_id = ?;", (sess_id,))
    rows = cursor.fetchall()
    close_db_connection(conn)

    assert len(rows) == 1
    assert rows[0]["status"] == "Present"


# --------------------------------------------------------------------------
# TEST 9: Browser-Independent Backend Deactivation via Scheduler
# --------------------------------------------------------------------------
def test_9_browser_independent_backend_deactivation(app_instance):
    """TEST 9: Verify check_and_deactivate_expired_sessions operates directly without HTTP requests."""
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Insert an active session directly into SQLite
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Scheduler Direct Test', '2026-11-10', '09:00', '10:00', 1, '2026-11-10T09:00:00Z');
        """
    )
    conn.commit()
    sess_id = cursor.lastrowid
    close_db_connection(conn)

    # Set frozen time to 10:01:00 (after session end time)
    set_frozen_time(datetime(2026, 11, 10, 10, 1, 0, tzinfo=KOLKATA_TZ))

    # Invoke scheduler worker function directly
    deactivated = check_and_deactivate_expired_sessions(db_path)
    assert deactivated is not None
    assert deactivated["id"] == sess_id
    assert deactivated["course_name"] == "Scheduler Direct Test"

    # Verify session is now inactive in SQLite
    conn2 = get_db_connection(db_path)
    cursor2 = conn2.cursor()
    cursor2.execute("SELECT active FROM class_sessions WHERE id = ?;", (sess_id,))
    row = cursor2.fetchone()
    close_db_connection(conn2)
    assert row["active"] == 0

    # Running check again should return None (no active expired sessions)
    assert check_and_deactivate_expired_sessions(db_path) is None


# --------------------------------------------------------------------------
# TEST 10: Session Activation & Switching with End Time
# --------------------------------------------------------------------------
def test_10_session_activation_and_expired_activation_rejected(client):
    """TEST 10: Verify activating a valid session works, and activating an expired session is rejected."""
    # Set current time to 10:00:00
    set_frozen_time(datetime(2026, 11, 10, 10, 0, 0, tzinfo=KOLKATA_TZ))

    # Session 1: Future session (10:30 to 12:00)
    res1 = client.post("/api/sessions", json={
        "course_name": "Active Session 1",
        "session_date": "2026-11-10",
        "start_time": "10:30",
        "end_time": "12:00"
    })
    id1 = res1.get_json()["session"]["id"]

    # Session 2: Already expired session (08:00 to 09:30)
    res2 = client.post("/api/sessions", json={
        "course_name": "Expired Session 2",
        "session_date": "2026-11-10",
        "start_time": "08:00",
        "end_time": "09:30"
    })
    id2 = res2.get_json()["session"]["id"]

    # Activate valid session 1 -> Should succeed
    act_res1 = client.patch(f"/api/sessions/{id1}/activate")
    assert act_res1.status_code == 200
    assert act_res1.get_json()["success"] is True

    # Attempt to activate expired session 2 -> Should return HTTP 400 Bad Request
    act_res2 = client.patch(f"/api/sessions/{id2}/activate")
    assert act_res2.status_code == 400
    assert "expired" in act_res2.get_json()["message"].lower()

    # Session 1 should still be the active session
    active_res = client.get("/api/sessions/active")
    assert active_res.get_json()["session"]["id"] == id1


# --------------------------------------------------------------------------
# TEST 11: Existing Sessions Without End Time Remain Readable
# --------------------------------------------------------------------------
def test_11_historical_sessions_without_end_time_readable(client, app_instance):
    """TEST 11: Verify historical sessions with end_time = NULL return end_time: None gracefully."""
    db_path = app_instance.config["DATABASE_PATH"]
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Directly insert historical session with NULL end_time
    cursor.execute(
        """
        INSERT INTO class_sessions (course_name, session_date, start_time, end_time, active, created_at)
        VALUES ('Historical Session', '2026-11-01', '10:00', NULL, 0, '2026-11-01T10:00:00Z');
        """
    )
    conn.commit()
    hist_id = cursor.lastrowid
    close_db_connection(conn)

    # Fetch sessions via API
    res = client.get("/api/sessions")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    hist_sess = next((s for s in data["sessions"] if s["id"] == hist_id), None)
    assert hist_sess is not None
    assert hist_sess["end_time"] is None


# --------------------------------------------------------------------------
# TEST 12: Scheduler Concurrency and Lifecycle Management
# --------------------------------------------------------------------------
def test_12_scheduler_concurrency_and_lifecycle(app_instance):
    """TEST 12: Verify scheduler starts, stops, and handles duplicate start calls safely."""
    db_path = app_instance.config["DATABASE_PATH"]
    scheduler = SessionExpiryScheduler(db_path=db_path, interval=0.1)

    # 1. Start scheduler
    assert scheduler.start(db_path=db_path) is True
    assert scheduler.is_running() is True

    # 2. Duplicate start call (idempotent; should remain running without second thread)
    assert scheduler.start(db_path=db_path) is True
    assert scheduler.is_running() is True

    # 3. Stop scheduler
    assert scheduler.stop(timeout=1.0) is True
    assert scheduler.is_running() is False

    # 4. Repeated stop is safe
    assert scheduler.stop(timeout=1.0) is True


# --------------------------------------------------------------------------
# TEST 13: Full Regression Check (End Time in API Responses)
# --------------------------------------------------------------------------
def test_13_full_regression_end_time_in_all_session_views(client):
    """TEST 13: Verify end_time is consistently present across all session endpoints."""
    set_frozen_time(datetime(2026, 11, 10, 10, 0, 0, tzinfo=KOLKATA_TZ))

    # 1. Create session
    create_res = client.post("/api/sessions", json={
        "course_name": "Regression Session",
        "session_date": "2026-11-10",
        "start_time": "10:00",
        "end_time": "11:00"
    })
    sess_id = create_res.get_json()["session"]["id"]

    # 2. GET /api/sessions
    list_res = client.get("/api/sessions")
    assert list_res.status_code == 200
    sess_item = next(s for s in list_res.get_json()["sessions"] if s["id"] == sess_id)
    assert sess_item["end_time"] == "11:00"

    # 3. PATCH /api/sessions/<id>/activate
    act_res = client.patch(f"/api/sessions/{sess_id}/activate")
    assert act_res.status_code == 200
    assert act_res.get_json()["session"]["end_time"] == "11:00"

    # 4. GET /api/sessions/active
    active_res = client.get("/api/sessions/active")
    assert active_res.status_code == 200
    assert active_res.get_json()["session"]["end_time"] == "11:00"

    # 5. PATCH /api/sessions/<id>/deactivate
    deact_res = client.patch(f"/api/sessions/{sess_id}/deactivate")
    assert deact_res.status_code == 200
    assert deact_res.get_json()["session"]["end_time"] == "11:00"
