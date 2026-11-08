"""
Automated unit and integration tests for Class Session Management API.
Runs against an isolated temporary SQLite database.
"""

from unittest.mock import patch
import sqlite3
import pytest
from app import create_app
from app.database import init_db, get_db_connection, close_db_connection


@pytest.fixture
def app(tmp_path):
    """
    Creates an instance of the Flask application configured for testing.
    Uses an isolated temporary SQLite database for each test function.
    """
    test_db_path = tmp_path / "test_sessions_app.db"
    
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's test client."""
    return app.test_client()


def test_1_session_creation_succeeds(client):
    """TEST 1: Verify session creation succeeds with HTTP 201 and valid JSON."""
    payload = {
        "course_name": "Engineering Mathematics",
        "session_date": "2026-11-07",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 201
    assert response.content_type == "application/json"
    
    data = response.get_json()
    assert data["success"] is True
    assert data["message"] == "Class session created successfully"
    assert data["session"]["course_name"] == "Engineering Mathematics"
    assert data["session"]["session_date"] == "2026-11-07"
    assert data["session"]["start_time"] == "10:00"
    assert data["session"]["end_time"] == "11:00"
    assert data["session"]["active"] is False
    assert isinstance(data["session"]["id"], int)


def test_2_session_is_saved_correctly_in_sqlite(client, app):
    """TEST 2: Verify created session actually exists in SQLite database."""
    payload = {
        "course_name": "Data Structures & Algorithms",
        "session_date": "2026-11-08",
        "start_time": "11:30",
        "end_time": "12:30"
    }
    res = client.post("/api/sessions", json=payload)
    session_id = res.get_json()["session"]["id"]
    
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM class_sessions WHERE id = ?;", (session_id,))
    row = cursor.fetchone()
    close_db_connection(conn)
    
    assert row is not None
    assert row["course_name"] == "Data Structures & Algorithms"
    assert row["session_date"] == "2026-11-08"
    assert row["start_time"] == "11:30"
    assert row["end_time"] == "12:30"
    assert row["active"] == 0


def test_3_new_session_is_inactive_by_default(client):
    """TEST 3: Verify newly created session has active = False."""
    payload = {
        "course_name": "Operating Systems",
        "session_date": "2026-11-09",
        "start_time": "14:00",
        "end_time": "15:00"
    }
    response = client.post("/api/sessions", json=payload)
    data = response.get_json()
    assert data["session"]["active"] is False


def test_4_missing_course_name_is_rejected(client):
    """TEST 4: Verify missing course_name returns HTTP 400."""
    payload = {
        "session_date": "2026-11-07",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "course_name" in data["message"]


def test_5_missing_session_date_is_rejected(client):
    """TEST 5: Verify missing session_date returns HTTP 400."""
    payload = {
        "course_name": "Database Systems",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "session_date" in data["message"]


def test_6_missing_start_time_is_rejected(client):
    """TEST 6: Verify missing start_time returns HTTP 400."""
    payload = {
        "course_name": "Database Systems",
        "session_date": "2026-11-07",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "start_time" in data["message"]


def test_7_empty_values_are_rejected(client):
    """TEST 7: Verify whitespace-only values return HTTP 400."""
    payload = {
        "course_name": "   ",
        "session_date": "2026-11-07",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "empty" in data["message"].lower()


def test_8_invalid_json_is_rejected(client):
    """TEST 8: Verify malformed or non-JSON request body returns HTTP 400."""
    response = client.post("/api/sessions", data="non-json data", content_type="text/plain")
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Bad Request"


def test_9_invalid_date_format_is_rejected(client):
    """TEST 9: Verify wrong date format (e.g. DD-MM-YYYY) returns HTTP 400."""
    payload = {
        "course_name": "Digital Logic",
        "session_date": "07-11-2026",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert "YYYY-MM-DD" in data["message"]


def test_10_impossible_calendar_date_is_rejected(client):
    """TEST 10: Verify impossible calendar date (e.g. 2026-02-30) returns HTTP 400."""
    payload = {
        "course_name": "Digital Logic",
        "session_date": "2026-02-30",
        "start_time": "10:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert "not a valid calendar date" in data["message"]


def test_11_invalid_time_format_is_rejected(client):
    """TEST 11: Verify invalid time format (e.g. '10 AM' or '10:00:00') returns HTTP 400."""
    payload = {
        "course_name": "Digital Logic",
        "session_date": "2026-11-07",
        "start_time": "10:00:00",
        "end_time": "11:00"
    }
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert "HH:MM" in data["message"]


def test_12_invalid_hour_or_minute_is_rejected(client):
    """TEST 12: Verify impossible hours (>23) or minutes (>59) return HTTP 400."""
    payload1 = {"course_name": "AI", "session_date": "2026-11-07", "start_time": "25:00", "end_time": "11:00"}
    res1 = client.post("/api/sessions", json=payload1)
    assert res1.status_code == 400

    payload2 = {"course_name": "AI", "session_date": "2026-11-07", "start_time": "10:75", "end_time": "11:00"}
    res2 = client.post("/api/sessions", json=payload2)
    assert res2.status_code == 400


def test_13_retrieve_all_sessions_successfully(client):
    """TEST 13: Verify GET /api/sessions returns all registered sessions."""
    client.post("/api/sessions", json={"course_name": "Class A", "session_date": "2026-11-07", "start_time": "09:00", "end_time": "10:00"})
    client.post("/api/sessions", json={"course_name": "Class B", "session_date": "2026-11-07", "start_time": "11:00", "end_time": "12:00"})
    
    response = client.get("/api/sessions")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] >= 2
    assert len(data["sessions"]) >= 2


def test_14_empty_session_list_returned_correctly(app):
    """TEST 14: Verify GET /api/sessions returns empty list when no sessions exist."""
    fresh_client = app.test_client()
    response = fresh_client.get("/api/sessions")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["sessions"] == []


def test_15_multiple_sessions_are_sorted_consistently(client):
    """TEST 15: Verify sessions are sorted newest session_date and start_time first."""
    client.post("/api/sessions", json={"course_name": "Older", "session_date": "2026-11-05", "start_time": "09:00", "end_time": "10:00"})
    client.post("/api/sessions", json={"course_name": "Newer", "session_date": "2026-11-08", "start_time": "14:00", "end_time": "15:00"})
    client.post("/api/sessions", json={"course_name": "Mid", "session_date": "2026-11-08", "start_time": "10:00", "end_time": "11:00"})
    
    response = client.get("/api/sessions")
    data = response.get_json()
    assert response.status_code == 200
    sessions = data["sessions"]
    
    # Verify newest date/time first
    assert sessions[0]["course_name"] == "Newer"
    assert sessions[1]["course_name"] == "Mid"
    assert sessions[2]["course_name"] == "Older"


def test_16_active_session_retrieval_works(client):
    """TEST 16: Verify GET /api/sessions/active returns the active session."""
    res = client.post("/api/sessions", json={"course_name": "IoT Fundamentals", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_id = res.get_json()["session"]["id"]
    
    # Activate session
    client.patch(f"/api/sessions/{session_id}/activate")
    
    # Query active session
    active_res = client.get("/api/sessions/active")
    assert active_res.status_code == 200
    data = active_res.get_json()
    assert data["success"] is True
    assert data["session"] is not None
    assert data["session"]["id"] == session_id
    assert data["session"]["course_name"] == "IoT Fundamentals"
    assert data["session"]["active"] is True


def test_17_no_active_session_returns_null(client):
    """TEST 17: Verify GET /api/sessions/active returns null when no session is active."""
    # Create inactive session
    client.post("/api/sessions", json={"course_name": "IoT Fundamentals", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    
    response = client.get("/api/sessions/active")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["session"] is None
    assert "No active class session" in data["message"]


def test_18_session_activation_works(client):
    """TEST 18: Verify PATCH /api/sessions/<id>/activate activates a session."""
    res = client.post("/api/sessions", json={"course_name": "Cybersecurity", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_id = res.get_json()["session"]["id"]
    
    activate_res = client.patch(f"/api/sessions/{session_id}/activate")
    assert activate_res.status_code == 200
    data = activate_res.get_json()
    assert data["success"] is True
    assert data["session"]["active"] is True
    assert data["message"] == "Class session activated successfully"


def test_19_activating_another_session_deactivates_previous_one(client):
    """TEST 19: Verify activating session B automatically deactivates session A."""
    res1 = client.post("/api/sessions", json={"course_name": "Session A", "session_date": "2026-11-07", "start_time": "09:00", "end_time": "11:00"})
    session_a = res1.get_json()["session"]["id"]
    
    res2 = client.post("/api/sessions", json={"course_name": "Session B", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_b = res2.get_json()["session"]["id"]
    
    # 1. Activate Session A
    client.patch(f"/api/sessions/{session_a}/activate")
    
    # Verify Session A is active
    active1 = client.get("/api/sessions/active").get_json()
    assert active1["session"]["id"] == session_a
    
    # 2. Activate Session B
    client.patch(f"/api/sessions/{session_b}/activate")
    
    # Verify Session B is now active and Session A is no longer active
    active2 = client.get("/api/sessions/active").get_json()
    assert active2["session"]["id"] == session_b
    
    # Check all sessions
    all_sessions = client.get("/api/sessions").get_json()["sessions"]
    sess_a_record = next(s for s in all_sessions if s["id"] == session_a)
    sess_b_record = next(s for s in all_sessions if s["id"] == session_b)
    assert sess_a_record["active"] is False
    assert sess_b_record["active"] is True


def test_20_activating_already_active_session_handled_safely(client):
    """TEST 20: Verify activating an already active session returns 200 safely."""
    res = client.post("/api/sessions", json={"course_name": "Machine Learning", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_id = res.get_json()["session"]["id"]
    
    # First activation
    client.patch(f"/api/sessions/{session_id}/activate")
    # Second activation of the same session
    res_repeat = client.patch(f"/api/sessions/{session_id}/activate")
    assert res_repeat.status_code == 200
    data = res_repeat.get_json()
    assert data["success"] is True
    assert "already active" in data["message"].lower()


def test_21_session_deactivation_works(client):
    """TEST 21: Verify PATCH /api/sessions/<id>/deactivate deactivates an active session."""
    res = client.post("/api/sessions", json={"course_name": "Robotics", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_id = res.get_json()["session"]["id"]
    
    # Activate
    client.patch(f"/api/sessions/{session_id}/activate")
    
    # Deactivate
    deact_res = client.patch(f"/api/sessions/{session_id}/deactivate")
    assert deact_res.status_code == 200
    data = deact_res.get_json()
    assert data["success"] is True
    assert data["session"]["active"] is False
    assert data["message"] == "Class session deactivated successfully"
    
    # Confirm active endpoint returns null
    active_check = client.get("/api/sessions/active").get_json()
    assert active_check["session"] is None


def test_22_repeated_deactivation_handled_safely(client):
    """TEST 22: Verify deactivating an already inactive session returns 200 safely."""
    res = client.post("/api/sessions", json={"course_name": "Cloud Computing", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    session_id = res.get_json()["session"]["id"]
    
    # Deactivate while already inactive
    res_deact = client.patch(f"/api/sessions/{session_id}/deactivate")
    assert res_deact.status_code == 200
    data = res_deact.get_json()
    assert data["success"] is True
    assert "already inactive" in data["message"].lower()


def test_23_invalid_session_id_returns_404(client):
    """TEST 23: Verify activating or deactivating non-existent session ID returns HTTP 404."""
    res_act = client.patch("/api/sessions/9999/activate")
    assert res_act.status_code == 404
    assert res_act.get_json()["error"] == "Not Found"
    
    res_deact = client.patch("/api/sessions/9999/deactivate")
    assert res_deact.status_code == 404
    assert res_deact.get_json()["error"] == "Not Found"


def test_24_database_errors_handled_safely(client):
    """TEST 24: Verify database errors return HTTP 500 without leaking stack traces."""
    with patch("app.routes.sessions.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Database locked")
        response = client.post("/api/sessions", json={
            "course_name": "Fault Tolerance",
            "session_date": "2026-11-07",
            "start_time": "18:00",
            "end_time": "19:00"
        })
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "Internal Server Error"
        assert "Database locked" not in data["message"]


def test_25_database_level_protection_prevents_multiple_active_sessions(app):
    """
    TEST 25: Verify database-level constraint (partial unique index idx_single_active_session)
    prevents two rows from having active = 1 simultaneously.
    """
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    
    # Insert two inactive sessions
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Session 1', '2026-11-07', '10:00', 0, '2026-11-07T10:00:00Z');
    """)
    s1 = cursor.lastrowid
    
    cursor.execute("""
        INSERT INTO class_sessions (course_name, session_date, start_time, active, created_at)
        VALUES ('Session 2', '2026-11-07', '11:00', 0, '2026-11-07T11:00:00Z');
    """)
    s2 = cursor.lastrowid
    conn.commit()
    
    # Activate Session 1 in database directly
    cursor.execute("UPDATE class_sessions SET active = 1 WHERE id = ?;", (s1,))
    conn.commit()
    
    # Attempting to activate Session 2 WITHOUT deactivating Session 1 must trigger IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("UPDATE class_sessions SET active = 1 WHERE id = ?;", (s2,))
        conn.commit()
    
    close_db_connection(conn)


def test_26_non_string_session_payload_fields_rejected(client):
    """TEST 26: Verify non-string session payload fields are rejected with HTTP 400."""
    res1 = client.post("/api/sessions", json={"course_name": 12345, "session_date": "2026-11-07", "start_time": "10:00", "end_time": "11:00"})
    assert res1.status_code == 400
    assert "must be a string" in res1.get_json()["message"]

    res2 = client.post("/api/sessions", json={"course_name": "CS101", "session_date": True, "start_time": "10:00", "end_time": "11:00"})
    assert res2.status_code == 400
    assert "must be a string" in res2.get_json()["message"]


def test_27_course_name_length_boundaries_enforced(client):
    """TEST 27: Verify course_name shorter than 2 or longer than 100 characters is rejected."""
    res1 = client.post("/api/sessions", json={"course_name": "C", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "11:00"})
    assert res1.status_code == 400
    assert "between 2 and 100 characters" in res1.get_json()["message"]

    res2 = client.post("/api/sessions", json={"course_name": "C" * 101, "session_date": "2026-11-07", "start_time": "10:00", "end_time": "11:00"})
    assert res2.status_code == 400
    assert "between 2 and 100 characters" in res2.get_json()["message"]


def test_28_json_array_session_payload_rejected(client):
    """TEST 28: Verify JSON list session payload is rejected as malformed JSON."""
    res = client.post("/api/sessions", json=[{"course_name": "CS101"}])
    assert res.status_code == 400
    assert "Malformed or invalid JSON payload" in res.get_json()["message"]


def test_29_session_id_zero_or_negative_rejected(client):
    """TEST 29: Verify non-positive session IDs are rejected with HTTP 400."""
    res1 = client.patch("/api/sessions/0/activate")
    assert res1.status_code == 400
    assert "positive integer" in res1.get_json()["message"]

    res2 = client.patch("/api/sessions/0/deactivate")
    assert res2.status_code == 400
    assert "positive integer" in res2.get_json()["message"]


def test_30_database_error_on_get_sessions_handled(client):
    """TEST 30: Verify database error on GET /api/sessions returns HTTP 500."""
    with patch("app.routes.sessions.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Table unavailable")
        res = client.get("/api/sessions")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"


def test_31_database_error_on_get_active_session_handled(client):
    """TEST 31: Verify database error on GET /api/sessions/active returns HTTP 500."""
    with patch("app.routes.sessions.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Disk failure")
        res = client.get("/api/sessions/active")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"


def test_32_database_error_on_activate_session_handled(client):
    """TEST 32: Verify database error during session activation returns HTTP 500."""
    res_create = client.post("/api/sessions", json={"course_name": "CS50", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    sess_id = res_create.get_json()["session"]["id"]
    
    with patch("app.routes.sessions.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Transaction lock timeout")
        res = client.patch(f"/api/sessions/{sess_id}/activate")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"


def test_33_database_error_on_deactivate_session_handled(client):
    """TEST 33: Verify database error during session deactivation returns HTTP 500."""
    res_create = client.post("/api/sessions", json={"course_name": "CS50", "session_date": "2026-11-07", "start_time": "10:00", "end_time": "12:00"})
    sess_id = res_create.get_json()["session"]["id"]
    
    with patch("app.routes.sessions.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Transaction lock timeout")
        res = client.patch(f"/api/sessions/{sess_id}/deactivate")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"

