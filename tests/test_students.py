"""
Automated unit and integration tests for Student Registration and Retrieval API.
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
    Uses an isolated temporary SQLite database for each test session.
    """
    test_db_path = tmp_path / "test_students_app.db"
    
    test_app = create_app("testing")
    test_app.config["DATABASE_PATH"] = test_db_path
    init_db(test_db_path)
    
    yield test_app


@pytest.fixture
def client(app):
    """Provides Flask's test client."""
    return app.test_client()


def test_1_student_registration_succeeds(client):
    """TEST 1: Verify student registration succeeds with HTTP 201 and valid JSON."""
    payload = {
        "roll_number": "101",
        "full_name": "Rahul Sharma",
        "card_uid": "A1B2C3D4"
    }
    response = client.post("/api/students", json=payload)
    assert response.status_code == 201
    assert response.content_type == "application/json"
    
    data = response.get_json()
    assert data["success"] is True
    assert data["message"] == "Student registered successfully"
    assert data["student"]["roll_number"] == "101"
    assert data["student"]["full_name"] == "Rahul Sharma"
    assert data["student"]["card_uid"] == "A1B2C3D4"
    assert data["student"]["active"] is True
    assert isinstance(data["student"]["id"], int)


def test_2_registered_student_is_saved_in_sqlite(client, app):
    """TEST 2: Verify registered student actually exists in the SQLite database."""
    payload = {
        "roll_number": "102",
        "full_name": "Priya Patel",
        "card_uid": "E5F6G7H8"
    }
    client.post("/api/students", json=payload)
    
    # Query database directly
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM students WHERE roll_number = '102';")
    row = cursor.fetchone()
    close_db_connection(conn)
    
    assert row is not None
    assert row["full_name"] == "Priya Patel"
    assert row["card_uid"] == "E5F6G7H8"
    assert row["active"] == 1


def test_3_duplicate_roll_number_is_rejected(client):
    """TEST 3: Verify registering a duplicate roll number returns HTTP 409 Conflict."""
    payload1 = {
        "roll_number": "103",
        "full_name": "Amit Kumar",
        "card_uid": "CARD_001"
    }
    response1 = client.post("/api/students", json=payload1)
    assert response1.status_code == 201
    
    # Same roll number, different card UID
    payload2 = {
        "roll_number": "103",
        "full_name": "Amit Kumar Jr",
        "card_uid": "CARD_002"
    }
    response2 = client.post("/api/students", json=payload2)
    assert response2.status_code == 409
    data = response2.get_json()
    assert data["success"] is False
    assert data["error"] == "Conflict"
    assert "roll number" in data["message"].lower()


def test_4_duplicate_rfid_uid_is_rejected(client):
    """TEST 4: Verify registering a duplicate RFID UID returns HTTP 409 Conflict."""
    payload1 = {
        "roll_number": "104",
        "full_name": "Sneha Roy",
        "card_uid": "SHARED_CARD_UID"
    }
    response1 = client.post("/api/students", json=payload1)
    assert response1.status_code == 201
    
    # Different roll number, same card UID
    payload2 = {
        "roll_number": "105",
        "full_name": "Vikram Singh",
        "card_uid": "SHARED_CARD_UID"
    }
    response2 = client.post("/api/students", json=payload2)
    assert response2.status_code == 409
    data = response2.get_json()
    assert data["success"] is False
    assert data["error"] == "Conflict"
    assert "card uid" in data["message"].lower()


def test_5_missing_roll_number_is_rejected(client):
    """TEST 5: Verify missing roll_number returns HTTP 400 Bad Request."""
    payload = {
        "full_name": "Incomplete Student",
        "card_uid": "CARD_106"
    }
    response = client.post("/api/students", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "roll_number" in data["message"]


def test_6_missing_full_name_is_rejected(client):
    """TEST 6: Verify missing full_name returns HTTP 400 Bad Request."""
    payload = {
        "roll_number": "107",
        "card_uid": "CARD_107"
    }
    response = client.post("/api/students", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "full_name" in data["message"]


def test_7_missing_card_uid_is_rejected(client):
    """TEST 7: Verify missing card_uid returns HTTP 400 Bad Request."""
    payload = {
        "roll_number": "108",
        "full_name": "Ananya Sen"
    }
    response = client.post("/api/students", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "card_uid" in data["message"]


def test_8_empty_values_are_rejected(client):
    """TEST 8: Verify empty or whitespace-only values return HTTP 400 Bad Request."""
    payload = {
        "roll_number": "   ",
        "full_name": "Rahul Sharma",
        "card_uid": "CARD_109"
    }
    response = client.post("/api/students", json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert "empty" in data["message"].lower()


def test_9_invalid_json_is_rejected(client):
    """TEST 9: Verify non-JSON requests return HTTP 400 Bad Request."""
    response = client.post("/api/students", data="plain text not json", content_type="text/plain")
    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False
    assert data["error"] == "Bad Request"


def test_10_student_retrieval_succeeds(client):
    """TEST 10: Verify GET /api/students retrieves registered students."""
    client.post("/api/students", json={
        "roll_number": "110",
        "full_name": "Kavita Reddy",
        "card_uid": "CARD_110"
    })
    response = client.get("/api/students")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] >= 1
    assert any(s["roll_number"] == "110" for s in data["students"])


def test_11_empty_student_list_returns_correctly(app):
    """TEST 11: Verify GET /api/students returns empty list when no students exist."""
    fresh_client = app.test_client()
    response = fresh_client.get("/api/students")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["students"] == []


def test_12_multiple_students_retrieved_in_sorted_order(client):
    """TEST 12: Verify multiple students are retrieved sorted by roll_number ASC."""
    client.post("/api/students", json={"roll_number": "300", "full_name": "Student Three", "card_uid": "CARD_300"})
    client.post("/api/students", json={"roll_number": "100", "full_name": "Student One", "card_uid": "CARD_100"})
    client.post("/api/students", json={"roll_number": "200", "full_name": "Student Two", "card_uid": "CARD_200"})
    
    response = client.get("/api/students")
    data = response.get_json()
    assert response.status_code == 200
    
    roll_numbers = [s["roll_number"] for s in data["students"]]
    # Verify sorted order
    assert roll_numbers == sorted(roll_numbers)


def test_13_inactive_student_status_represented_correctly(client, app):
    """TEST 13: Verify inactive student status (active=0 in DB) is returned as active=False in JSON."""
    client.post("/api/students", json={"roll_number": "400", "full_name": "Inactive Student", "card_uid": "CARD_400"})
    
    # Manually deactivate student in DB
    conn = get_db_connection(app.config["DATABASE_PATH"])
    cursor = conn.cursor()
    cursor.execute("UPDATE students SET active = 0 WHERE roll_number = '400';")
    conn.commit()
    close_db_connection(conn)
    
    response = client.get("/api/students")
    data = response.get_json()
    inactive_student = next(s for s in data["students"] if s["roll_number"] == "400")
    assert inactive_student["active"] is False


def test_14_database_insertion_failures_handled_safely(client):
    """TEST 14: Verify database error during registration returns HTTP 500 without leaking stack traces."""
    with patch("app.routes.students.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Disk I/O error")
        response = client.post("/api/students", json={
            "roll_number": "500",
            "full_name": "Crash Test Student",
            "card_uid": "CARD_500"
        })
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "Internal Server Error"
        # Ensure raw DB stack trace is not exposed
        assert "Disk I/O error" not in data["message"]


def test_15_non_string_field_types_rejected(client):
    """TEST 15: Verify non-string field types (integers, booleans, lists) are rejected with HTTP 400."""
    res1 = client.post("/api/students", json={"roll_number": 101, "full_name": "Test Student", "card_uid": "CARD_101"})
    assert res1.status_code == 400
    assert "must be a string" in res1.get_json()["message"]

    res2 = client.post("/api/students", json={"roll_number": "101", "full_name": True, "card_uid": "CARD_101"})
    assert res2.status_code == 400
    assert "must be a string" in res2.get_json()["message"]

    res3 = client.post("/api/students", json={"roll_number": "101", "full_name": "Test Student", "card_uid": 12345})
    assert res3.status_code == 400
    assert "must be a string" in res3.get_json()["message"]


def test_16_very_long_input_values_rejected(client):
    """TEST 16: Verify input values exceeding maximum length boundaries are rejected."""
    # roll_number > 50 chars
    res1 = client.post("/api/students", json={"roll_number": "R" * 51, "full_name": "Test Student", "card_uid": "CARD_101"})
    assert res1.status_code == 400
    assert "exceeds maximum length of 50" in res1.get_json()["message"]

    # full_name > 100 chars
    res2 = client.post("/api/students", json={"roll_number": "101", "full_name": "N" * 101, "card_uid": "CARD_101"})
    assert res2.status_code == 400
    assert "between 2 and 100 characters" in res2.get_json()["message"]

    # card_uid > 50 chars
    res3 = client.post("/api/students", json={"roll_number": "101", "full_name": "Test Student", "card_uid": "C" * 51})
    assert res3.status_code == 400
    assert "between 4 and 50 characters" in res3.get_json()["message"]


def test_17_full_name_too_short_rejected(client):
    """TEST 17: Verify full_name shorter than 2 characters is rejected."""
    res = client.post("/api/students", json={"roll_number": "101", "full_name": "A", "card_uid": "CARD_101"})
    assert res.status_code == 400
    assert "between 2 and 100 characters" in res.get_json()["message"]


def test_18_json_array_payload_rejected(client):
    """TEST 18: Verify JSON list payload is rejected as malformed JSON."""
    res = client.post("/api/students", json=[{"roll_number": "101"}])
    assert res.status_code == 400
    assert "Malformed or invalid JSON payload" in res.get_json()["message"]


def test_19_special_characters_in_card_uid_rejected(client):
    """TEST 19: Verify invalid characters in card_uid (spaces, dots, @) are rejected."""
    res = client.post("/api/students", json={"roll_number": "101", "full_name": "Test Student", "card_uid": "CARD@123"})
    assert res.status_code == 400
    assert "alphanumeric characters, dashes, or underscores" in res.get_json()["message"]


def test_20_case_insensitive_card_uid_uniqueness(client):
    """TEST 20: Verify card_uid uniqueness is case-insensitive (card_abc and CARD_ABC collide)."""
    res1 = client.post("/api/students", json={"roll_number": "101", "full_name": "First Student", "card_uid": "card_abc"})
    assert res1.status_code == 201

    res2 = client.post("/api/students", json={"roll_number": "102", "full_name": "Second Student", "card_uid": "CARD_ABC"})
    assert res2.status_code == 409
    assert res2.get_json()["error"] == "Conflict"


def test_21_database_error_on_get_students_handled(client):
    """TEST 21: Verify database errors during GET /api/students return HTTP 500."""
    with patch("app.routes.students.get_db") as mock_get_db:
        mock_get_db.side_effect = sqlite3.OperationalError("Table locked")
        res = client.get("/api/students")
        assert res.status_code == 500
        assert res.get_json()["error"] == "Internal Server Error"

