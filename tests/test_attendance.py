"""
Automated unit and integration tests for RFID Attendance Scan & Validation API.
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
    test_db_path = tmp_path / "test_attendance_app.db"
    
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's test client."""
    return app.test_client()


@pytest.fixture
def active_session_and_student(client):
    """
    Helper fixture:
    1. Registers an active student (roll_number: '101', full_name: 'Rahul Sharma', card_uid: 'A1B2C3D4').
    2. Creates and activates a class session (course_name: 'Engineering Mathematics').
    Returns dict with student and session details.
    """
    # Register student
    student_res = client.post("/api/students", json={
        "roll_number": "101",
        "full_name": "Rahul Sharma",
        "card_uid": "A1B2C3D4"
    })
    student = student_res.get_json()["student"]
    
    # Create session
    session_res = client.post("/api/sessions", json={
        "course_name": "Engineering Mathematics",
        "session_date": "2026-11-07",
        "start_time": "10:00",
        "end_time": "12:00"
    })
    session = session_res.get_json()["session"]
    
    # Activate session
    client.patch(f"/api/sessions/{session['id']}/activate")
    
    return {"student": student, "session": session}


def test_1_valid_attendance_scan_succeeds(client, active_session_and_student):
    """TEST 1: Verify valid RFID scan returns HTTP 201 and expected JSON response."""
    response = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "ESP32"
    })
    assert response.status_code == 201
    assert response.content_type == "application/json"
    data = response.get_json()
    assert data["success"] is True
    assert data["message"] == "Attendance recorded successfully"
    assert "attendance" in data


def test_2_attendance_record_is_saved_in_sqlite(client, app, active_session_and_student):
    """TEST 2: Verify attendance record is persisted in SQLite."""
    res = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "ESP32"
    })
    attendance_id = res.get_json()["attendance"]["id"]
    
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM attendance WHERE id = ?;", (attendance_id,))
    row = cursor.fetchone()
    close_db_connection(conn)
    
    assert row is not None
    assert row["status"] == "Present"
    assert row["source"] == "ESP32"


def test_3_correct_student_is_identified(client, active_session_and_student):
    """TEST 3: Verify the student details in response match registered student."""
    response = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "SIMULATOR"
    })
    att = response.get_json()["attendance"]
    assert att["roll_number"] == "101"
    assert att["student_name"] == "Rahul Sharma"


def test_4_correct_active_session_is_identified(client, active_session_and_student):
    """TEST 4: Verify attendance links to the active session."""
    data_setup = active_session_and_student
    response = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "SIMULATOR"
    })
    att = response.get_json()["attendance"]
    assert att["session_id"] == data_setup["session"]["id"]
    assert att["course_name"] == "Engineering Mathematics"


def test_5_unknown_rfid_uid_is_rejected(client, active_session_and_student):
    """TEST 5: Verify scanning an unregistered RFID card returns HTTP 404."""
    response = client.post("/api/scans", json={
        "card_uid": "UNKNOWN_CARD",
        "source": "ESP32"
    })
    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Student Not Found"
    assert "not registered" in data["message"].lower()


def test_6_inactive_student_is_rejected(client, app, active_session_and_student):
    """TEST 6: Verify scanning an inactive student card returns HTTP 403."""
    # Deactivate student in database
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET active = 0 WHERE card_uid = 'A1B2C3D4';")
    conn.commit()
    close_db_connection(conn)
    
    response = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "ESP32"
    })
    assert response.status_code == 403
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Inactive Student"
    assert "inactive" in data["message"].lower()


def test_7_scan_without_active_session_is_rejected(client):
    """TEST 7: Verify scan returns HTTP 409 when no session is active."""
    # Register student
    client.post("/api/students", json={
        "roll_number": "102",
        "full_name": "Priya Patel",
        "card_uid": "CARD_PRIYA"
    })
    
    # Do not activate any session
    response = client.post("/api/scans", json={
        "card_uid": "CARD_PRIYA",
        "source": "ESP32"
    })
    assert response.status_code == 409
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "No Active Session"
    assert "no class session is currently active" in data["message"].lower()


def test_8_duplicate_attendance_is_rejected(client, active_session_and_student):
    """TEST 8: Verify scanning the same card twice in the same session returns HTTP 409."""
    # First scan -> succeeds
    res1 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert res1.status_code == 201
    
    # Second scan -> rejected
    res2 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert res2.status_code == 409
    data = response = res2.get_json()
    assert data["success"] is False
    assert data["error"] == "Duplicate Attendance"
    assert "already been recorded" in data["message"].lower()


def test_9_missing_card_uid_is_rejected(client):
    """TEST 9: Verify missing card_uid returns HTTP 400."""
    response = client.post("/api/scans", json={"source": "ESP32"})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "card_uid" in data["message"]


def test_10_missing_source_is_rejected(client):
    """TEST 10: Verify missing source returns HTTP 400."""
    response = client.post("/api/scans", json={"card_uid": "A1B2C3D4"})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "source" in data["message"]


def test_11_empty_card_uid_is_rejected(client):
    """TEST 11: Verify empty or whitespace-only card_uid returns HTTP 400."""
    response = client.post("/api/scans", json={"card_uid": "   ", "source": "ESP32"})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "empty" in data["message"].lower()


def test_12_empty_source_is_rejected(client):
    """TEST 12: Verify empty or whitespace-only source returns HTTP 400."""
    response = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "   "})
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "empty" in data["message"].lower()


def test_13_invalid_json_is_rejected(client):
    """TEST 13: Verify malformed or non-JSON request body returns HTTP 400."""
    response = client.post("/api/scans", data="not json", content_type="text/plain")
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Bad Request"


def test_14_rfid_uid_normalization_works(client, active_session_and_student):
    """TEST 14: Verify lowercase input 'a1b2c3d4' matches registered uppercase 'A1B2C3D4'."""
    response = client.post("/api/scans", json={
        "card_uid": "a1b2c3d4",
        "source": "SIMULATOR"
    })
    assert response.status_code == 201
    assert response.get_json()["success"] is True


def test_15_invalid_rfid_uid_format_is_rejected(client):
    """TEST 15: Verify RFID UIDs with invalid characters or bad length are rejected with HTTP 400."""
    # Special symbols
    res1 = client.post("/api/scans", json={"card_uid": "A1@B2#C3", "source": "ESP32"})
    assert res1.status_code == 400
    
    # Too short (< 4 chars)
    res2 = client.post("/api/scans", json={"card_uid": "AB", "source": "ESP32"})
    assert res2.status_code == 400


def test_16_server_generated_timestamp_is_used(client, active_session_and_student):
    """TEST 16: Verify server-generated timestamp is in UTC ISO 8601 ending in Z."""
    response = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    scanned_at = response.get_json()["attendance"]["scanned_at"]
    assert scanned_at.endswith("Z")
    assert "T" in scanned_at


def test_17_client_cannot_override_scanned_at(client, active_session_and_student):
    """TEST 17: Verify client cannot inject a custom scanned_at timestamp."""
    response = client.post("/api/scans", json={
        "card_uid": "A1B2C3D4",
        "source": "ESP32",
        "scanned_at": "1999-01-01T00:00:00Z"
    })
    scanned_at = response.get_json()["attendance"]["scanned_at"]
    assert not scanned_at.startswith("1999-01-01")


def test_18_correct_attendance_status_is_stored(client, active_session_and_student):
    """TEST 18: Verify attendance status is 'Present'."""
    response = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert response.get_json()["attendance"]["status"] == "Present"


def test_19_correct_source_is_stored(client, active_session_and_student):
    """TEST 19: Verify source field ('ESP32' or 'SIMULATOR') is recorded."""
    response = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "HARDWARE_ESP32_DEV"})
    assert response.get_json()["attendance"]["source"] == "HARDWARE_ESP32_DEV"


def test_20_database_unique_constraint_remains_effective(app, active_session_and_student):
    """TEST 20: Verify database UNIQUE(student_id, session_id) constraint blocks duplicates at SQL level."""
    setup = active_session_and_student
    student_id = setup["student"]["id"]
    session_id = setup["session"]["id"]
    
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    
    # Direct insert 1
    cursor.execute("""
        INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
        VALUES (?, ?, '2026-11-07T10:05:00Z', 'Present', 'test');
    """, (student_id, session_id))
    conn.commit()
    
    # Direct insert 2 (must raise IntegrityError)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO attendance (student_id, session_id, scanned_at, status, source)
            VALUES (?, ?, '2026-11-07T10:06:00Z', 'Present', 'test');
        """, (student_id, session_id))
        conn.commit()
    
    close_db_connection(conn)


def test_21_database_errors_handled_safely(client, active_session_and_student):
    """TEST 21: Verify database errors return HTTP 500 without leaking stack traces."""
    with patch("app.routes.attendance.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Disk locked")
        response = client.post("/api/scans", json={
            "card_uid": "A1B2C3D4",
            "source": "ESP32"
        })
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "Internal Server Error"
        assert "Disk locked" not in data["message"]


def test_22_multiple_different_students_can_attend_same_session(client, active_session_and_student):
    """TEST 22: Verify different students can all be marked present in the same active session."""
    # Register second student
    client.post("/api/students", json={
        "roll_number": "102",
        "full_name": "Priya Patel",
        "card_uid": "CARD_PRIYA_002"
    })
    
    # Scan Student 1
    res1 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert res1.status_code == 201
    
    # Scan Student 2
    res2 = client.post("/api/scans", json={"card_uid": "CARD_PRIYA_002", "source": "ESP32"})
    assert res2.status_code == 201
    assert res2.get_json()["attendance"]["roll_number"] == "102"


def test_23_same_student_can_attend_different_session(client, active_session_and_student):
    """TEST 23: Verify a student can attend a morning session and an afternoon session."""
    # 1. Attend session 1
    res1 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert res1.status_code == 201
    
    # 2. Create and activate session 2
    sess2_res = client.post("/api/sessions", json={
        "course_name": "Data Structures",
        "session_date": "2026-11-07",
        "start_time": "14:00",
        "end_time": "15:00"
    })
    sess2_id = sess2_res.get_json()["session"]["id"]
    client.patch(f"/api/sessions/{sess2_id}/activate")
    
    # 3. Attend session 2 (must succeed)
    res2 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "ESP32"})
    assert res2.status_code == 201
    assert res2.get_json()["attendance"]["session_id"] == sess2_id
    assert res2.get_json()["attendance"]["course_name"] == "Data Structures"


def test_24_non_string_scan_payload_fields_rejected(client):
    """TEST 24: Verify non-string scan payload fields are rejected with HTTP 400."""
    res1 = client.post("/api/scans", json={"card_uid": 12345, "source": "ESP32"})
    assert res1.status_code == 400
    assert "must be a string" in res1.get_json()["message"]

    res2 = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": 999})
    assert res2.status_code == 400
    assert "must be a string" in res2.get_json()["message"]


def test_25_source_length_boundary_exceeded(client):
    """TEST 25: Verify source field longer than 50 characters is rejected with HTTP 400."""
    res = client.post("/api/scans", json={"card_uid": "A1B2C3D4", "source": "S" * 51})
    assert res.status_code == 400
    assert "cannot exceed 50 characters" in res.get_json()["message"]


def test_26_json_array_scan_payload_rejected(client):
    """TEST 26: Verify JSON array payload is rejected as malformed JSON."""
    res = client.post("/api/scans", json=[{"card_uid": "A1B2C3D4"}])
    assert res.status_code == 400
    assert "Malformed or invalid JSON payload" in res.get_json()["message"]


def test_27_empty_date_in_daily_attendance_query_rejected(client):
    """TEST 27: Verify empty date query parameter is rejected with HTTP 400."""
    res = client.get("/api/attendance?date=")
    assert res.status_code == 400
    assert "cannot be empty" in res.get_json()["message"]


def test_28_database_error_on_get_attendance_handled(client):
    """TEST 28: Verify database errors during GET /api/attendance return HTTP 500."""
    with patch("app.routes.attendance.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Disk fault")
        res = client.get("/api/attendance?date=2026-11-07")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"

