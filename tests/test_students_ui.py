"""
Automated Integration and Functional Tests for Student Management UI (Milestone 3).
Verifies:
1. Student management page loads successfully.
2. Required template and JavaScript assets are included.
3. Registration form elements are present.
4. Student table structure is present.
5. Search and filter controls are present.
6. Student API integration uses the correct endpoint.
7. Registration sends the correct request fields.
8. Registration success handling.
9. Duplicate roll number error handling.
10. Duplicate RFID UID error handling.
11. API failure handling.
12. Existing student records remain unchanged.
13. JavaScript asset contains client validation and defensive normalization.
"""

from unittest.mock import patch
import sqlite3
import pytest
from app import create_app
from app.database import init_db, get_db_connection


@pytest.fixture
def app_instance(tmp_path):
    """Creates an isolated test application instance."""
    db_path = tmp_path / "test_students_ui.db"
    app = create_app("testing")
    app.config["DATABASE_PATH"] = db_path
    init_db(db_path)
    yield app


@pytest.fixture
def client(app_instance):
    """Provides Flask test client."""
    return app_instance.test_client()


def test_1_student_management_page_loads_successfully(client):
    """Verify GET /students returns HTTP 200 with HTML content and correct page headers."""
    res = client.get("/students")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    html = res.get_data(as_text=True)

    assert "Student Management" in html
    assert "Register, view and manage student records" in html
    assert 'id="sidebar"' in html
    assert 'id="live-clock"' in html


def test_2_required_template_and_assets_included(client):
    """Verify that CSS and JS asset files specific to Student Management are linked and served."""
    res = client.get("/students")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Core scripts & student script
    assert "js/app.js" in html
    assert "js/students.js" in html

    # Core stylesheets & student stylesheet
    assert "css/style.css" in html
    assert "css/components.css" in html
    assert "css/students.css" in html

    # Verify student assets are actually served with HTTP 200
    css_res = client.get("/static/css/students.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.content_type

    js_res = client.get("/static/js/students.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.content_type or "text/plain" in js_res.content_type


def test_3_registration_form_elements_are_present(client):
    """Verify registration form markup, input elements, buttons, and inline alert container."""
    res = client.get("/students")
    html = res.get_data(as_text=True)

    # Form card and form tag
    assert 'id="student-registration-card"' in html
    assert 'id="form-register-student"' in html

    # Input fields
    assert 'id="student-roll-number"' in html
    assert 'id="student-full-name"' in html
    assert 'id="student-card-uid"' in html

    # Action buttons and alert container
    assert 'id="btn-submit-student"' in html
    assert 'id="btn-reset-form"' in html
    assert 'id="form-alert"' in html


def test_4_student_table_structure_is_present(client):
    """Verify table element, column headers, statistics cards, and empty state boxes."""
    res = client.get("/students")
    html = res.get_data(as_text=True)

    # 3 Stat Cards
    assert 'id="stat-total-students"' in html
    assert 'id="stat-active-students"' in html
    assert 'id="stat-inactive-students"' in html

    # Table markup
    assert 'id="student-table"' in html
    assert 'id="student-table-body"' in html

    # Required column headers
    assert "ID" in html
    assert "Roll Number" in html
    assert "Full Name" in html
    assert "RFID UID" in html
    assert "Status" in html
    assert "Registration Date" in html

    # State boxes
    assert 'id="table-empty-state"' in html
    assert 'id="search-empty-state"' in html
    assert 'id="table-error-state"' in html


def test_5_search_and_filter_controls_are_present(client):
    """Verify client-side search input, clear button, and status filter dropdown."""
    res = client.get("/students")
    html = res.get_data(as_text=True)

    assert 'id="search-students"' in html
    assert 'id="btn-clear-search"' in html
    assert 'id="filter-status"' in html
    assert 'value="all"' in html
    assert 'value="active"' in html
    assert 'value="inactive"' in html
    assert 'id="roster-count-badge"' in html


def test_6_student_api_integration_uses_correct_endpoint(client):
    """Verify GET /api/students returns valid JSON structure consumed by students.js."""
    res = client.get("/api/students")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "count" in data
    assert "students" in data
    assert isinstance(data["students"], list)


def test_7_registration_sends_correct_request_fields(client):
    """Verify POST /api/students processes roll_number, full_name, and card_uid correctly."""
    payload = {
        "roll_number": "CS-201",
        "full_name": "Riya Sen",
        "card_uid": "c1d2e3f4"  # lower case input
    }
    res = client.post("/api/students", json=payload)
    assert res.status_code == 201
    data = res.get_json()
    assert data["success"] is True
    student = data["student"]

    assert student["roll_number"] == "CS-201"
    assert student["full_name"] == "Riya Sen"
    assert student["card_uid"] == "C1D2E3F4"  # automatically uppercased
    assert student["active"] is True


def test_8_registration_success_handling(client):
    """Verify complete registration lifecycle and appearance in subsequent GET /api/students."""
    payload = {
        "roll_number": "CS-301",
        "full_name": "Vikram Malhotra",
        "card_uid": "9988AABB"
    }
    # 1. Register student
    reg_res = client.post("/api/students", json=payload)
    assert reg_res.status_code == 201

    # 2. Retrieve student list and verify record exists
    list_res = client.get("/api/students")
    assert list_res.status_code == 200
    students = list_res.get_json()["students"]

    matched = [s for s in students if s["roll_number"] == "CS-301"]
    assert len(matched) == 1
    assert matched[0]["full_name"] == "Vikram Malhotra"
    assert matched[0]["card_uid"] == "9988AABB"
    assert matched[0]["active"] is True


def test_9_duplicate_roll_number_error_handling(client):
    """Verify duplicate roll numbers return HTTP 409 Conflict with descriptive message."""
    client.post("/api/students", json={
        "roll_number": "CS-401",
        "full_name": "Original Student",
        "card_uid": "UID401A"
    })

    # Attempt to register second student with identical roll number
    dup_res = client.post("/api/students", json={
        "roll_number": "CS-401",
        "full_name": "Duplicate Student",
        "card_uid": "UID401B"
    })
    assert dup_res.status_code == 409
    data = dup_res.get_json()
    assert data["success"] is False
    assert data["error"] == "Conflict"
    assert "roll number 'CS-401' already exists" in data["message"]


def test_10_duplicate_rfid_uid_error_handling(client):
    """Verify duplicate RFID card UIDs return HTTP 409 Conflict with descriptive message."""
    client.post("/api/students", json={
        "roll_number": "CS-501",
        "full_name": "Card Owner",
        "card_uid": "UNIQUE_CARD_99"
    })

    # Attempt to register second student with different roll number but identical card UID
    dup_res = client.post("/api/students", json={
        "roll_number": "CS-502",
        "full_name": "Second Person",
        "card_uid": "unique_card_99"  # case-insensitive match
    })
    assert dup_res.status_code == 409
    data = dup_res.get_json()
    assert data["success"] is False
    assert data["error"] == "Conflict"
    assert "RFID card UID 'UNIQUE_CARD_99' already exists" in data["message"]


def test_11_api_failure_handling(client):
    """Verify client receives HTTP 400 Bad Request on invalid payloads and HTTP 500 on DB failure."""
    # Empty roll number
    res1 = client.post("/api/students", json={
        "roll_number": "   ",
        "full_name": "Valid Name",
        "card_uid": "CARD111"
    })
    assert res1.status_code == 400
    assert "cannot be empty" in res1.get_json()["message"]

    # Full name too short
    res2 = client.post("/api/students", json={
        "roll_number": "CS-601",
        "full_name": "X",
        "card_uid": "CARD222"
    })
    assert res2.status_code == 400
    assert "must be between 2 and 100 characters" in res2.get_json()["message"]

    # Invalid RFID card characters
    res3 = client.post("/api/students", json={
        "roll_number": "CS-602",
        "full_name": "Valid Name",
        "card_uid": "INVALID@CARD#!"
    })
    assert res3.status_code == 400
    assert "alphanumeric characters, dashes, or underscores" in res3.get_json()["message"]

    # Database internal error handled cleanly
    with patch("app.routes.students.get_db") as mock_db:
        mock_db.side_effect = sqlite3.OperationalError("Simulated database failure")
        res_error = client.post("/api/students", json={
            "roll_number": "CS-701",
            "full_name": "Error Student",
            "card_uid": "CARD701"
        })
        assert res_error.status_code == 500
        assert res_error.get_json()["error"] == "Internal Server Error"


def test_12_existing_student_records_remain_unchanged(client, app_instance):
    """Verify student registration preserves existing records without unintended mutations."""
    # Register 2 initial students
    client.post("/api/students", json={"roll_number": "ST-01", "full_name": "Student A", "card_uid": "UID_A"})
    client.post("/api/students", json={"roll_number": "ST-02", "full_name": "Student B", "card_uid": "UID_B"})

    # Register a 3rd student
    client.post("/api/students", json={"roll_number": "ST-03", "full_name": "Student C", "card_uid": "UID_C"})

    # Query all students
    res = client.get("/api/students")
    assert res.status_code == 200
    students = res.get_json()["students"]
    assert len(students) == 3

    # Check that ST-01 and ST-02 are unmodified
    s1 = next(s for s in students if s["roll_number"] == "ST-01")
    s2 = next(s for s in students if s["roll_number"] == "ST-02")
    assert s1["full_name"] == "Student A" and s1["card_uid"] == "UID_A"
    assert s2["full_name"] == "Student B" and s2["card_uid"] == "UID_B"


def test_13_static_js_contains_client_validation_and_normalization(client):
    """Verify students.js includes defensive normalization, client validation, and search/filter logic."""
    res = client.get("/static/js/students.js")
    assert res.status_code == 200
    js_content = res.get_data(as_text=True)

    # Active status normalization
    assert "Number(s.active) === 1" in js_content
    assert "s.active === true" in js_content

    # Functions
    assert "fetchStudents" in js_content
    assert "renderTable" in js_content
    assert "handleFormSubmit" in js_content
    assert "escapeHtml" in js_content
    assert "setFormAlert" in js_content

    # Form and input references
    assert "student-roll-number" in js_content
    assert "student-full-name" in js_content
    assert "student-card-uid" in js_content
    assert "search-students" in js_content
    assert "filter-status" in js_content
