"""
RFID Scanner Simulator for the Smart Attendance Management System.

Simulates an RFID card scanner (such as an ESP32 + RC522 module) sending scan
events over HTTP to the Flask backend's POST /api/scans endpoint.

Supports:
1. Interactive Terminal Mode: Continuous prompt for scanning RFID cards.
2. One-Shot Mode (--uid <UID>): Scans a single card and exits immediately.
3. Custom URL (--url <URL>): Overrides the default backend base URL.
"""

import argparse
import re
import sys
from typing import Any, Dict, Optional, Tuple
import requests


# Default backend server address
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_SOURCE = "SIMULATOR"

# UID validation rules matching backend constraints
UID_MIN_LENGTH = 4
UID_MAX_LENGTH = 50
UID_REGEX = re.compile(r"^[A-Z0-9_-]+$")


def validate_uid(raw_uid: Optional[str]) -> Tuple[bool, str, str]:
    """
    Validates and normalizes an RFID card UID locally before sending.

    Rules:
    - Must be a non-empty string.
    - Trims leading and trailing whitespace.
    - Converts to uppercase.
    - Must be between 4 and 50 characters in length.
    - Must only contain alphanumeric characters, dashes, or underscores.

    :param raw_uid: The raw input string entered by the user or passed via CLI.
    :return: (is_valid: bool, normalized_uid: str, error_message: str)
    """
    if raw_uid is None:
        return False, "", "RFID UID cannot be empty."

    trimmed = raw_uid.strip()
    if not trimmed:
        return False, "", "RFID UID cannot be empty or whitespace only."

    normalized = trimmed.upper()

    if len(normalized) < UID_MIN_LENGTH or len(normalized) > UID_MAX_LENGTH:
        return (
            False,
            normalized,
            f"RFID UID must be between {UID_MIN_LENGTH} and {UID_MAX_LENGTH} characters (got {len(normalized)}).",
        )

    if not UID_REGEX.match(normalized):
        return (
            False,
            normalized,
            "RFID UID must contain only alphanumeric characters, dashes, or underscores.",
        )

    return True, normalized, ""


def send_scan(
    base_url: str,
    card_uid: str,
    source: str = DEFAULT_SOURCE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Sends an HTTP POST request to the backend attendance scanning endpoint.

    Endpoint: POST {base_url}/api/scans
    Payload: {"card_uid": "<normalized_uid>", "source": "SIMULATOR"}

    Note: The simulator NEVER generates an attendance timestamp. The backend
    is strictly authoritative for generating the official UTC timestamp.

    :param base_url: Base URL of the Flask backend (e.g. 'http://127.0.0.1:5000').
    :param card_uid: Normalized RFID UID to scan.
    :param source: Hardware/client source identifier.
    :param timeout: HTTP request timeout in seconds.
    :return: A structured result dictionary with outcome details.
    """
    normalized_base = base_url.rstrip("/")
    endpoint = f"{normalized_base}/api/scans"
    payload = {
        "card_uid": card_uid,
        "source": source,
    }

    try:
        response = requests.post(
            endpoint,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "status_code": None,
            "error_type": "CONNECTION_ERROR",
            "message": (
                f"[ CONNECTION ERROR ] Cannot connect to attendance server at {normalized_base}.\n"
                "Please check whether the Flask backend is running."
            ),
            "data": None,
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "status_code": None,
            "error_type": "TIMEOUT_ERROR",
            "message": (
                f"[ TIMEOUT ERROR ] Request timed out after {timeout} seconds.\n"
                "The attendance server took too long to respond."
            ),
            "data": None,
        }
    except requests.exceptions.RequestException as exc:
        return {
            "success": False,
            "status_code": None,
            "error_type": "NETWORK_ERROR",
            "message": f"[ NETWORK ERROR ] Communication failed: {exc}",
            "data": None,
        }

    status_code = response.status_code

    # Attempt to parse response as JSON
    try:
        response_json = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        raw_snippet = response.text[:200].strip() if response.text else "<empty response>"
        return {
            "success": False,
            "status_code": status_code,
            "error_type": "INVALID_JSON",
            "message": (
                f"[ SERVER ERROR ] Received non-JSON response from server (HTTP {status_code}).\n"
                f"Response preview: {raw_snippet}"
            ),
            "data": None,
        }

    # Interpret HTTP Status Codes according to the API contract
    if status_code == 201:
        att = response_json.get("attendance", {})
        return {
            "success": True,
            "status_code": 201,
            "error_type": None,
            "message": "Attendance recorded successfully",
            "data": response_json,
            "attendance": att,
        }

    error_label = response_json.get("error", "Error")
    detail_msg = response_json.get("message", "No details provided.")

    if status_code == 404:
        return {
            "success": False,
            "status_code": 404,
            "error_type": "UNKNOWN_CARD",
            "message": (
                "[ ERROR ] Unknown RFID Card!\n"
                "This RFID UID is not registered in the system."
            ),
            "data": response_json,
        }

    if status_code == 403:
        return {
            "success": False,
            "status_code": 403,
            "error_type": "INACTIVE_STUDENT",
            "message": (
                "[ ERROR ] Student Account Inactive!\n"
                "Attendance cannot be recorded for this student."
            ),
            "data": response_json,
        }

    if status_code == 409:
        combined_text = f"{error_label} {detail_msg}".lower()
        if "duplicate" in combined_text:
            return {
                "success": False,
                "status_code": 409,
                "error_type": "DUPLICATE_ATTENDANCE",
                "message": (
                    "[ DUPLICATE ATTENDANCE ] Attendance has already been recorded "
                    "for this student in this session."
                ),
                "data": response_json,
            }
        if "active session" in combined_text or "session" in combined_text:
            return {
                "success": False,
                "status_code": 409,
                "error_type": "NO_ACTIVE_SESSION",
                "message": (
                    "[ NO ACTIVE SESSION ] No class session is currently active.\n"
                    "Attendance cannot be recorded."
                ),
                "data": response_json,
            }
        return {
            "success": False,
            "status_code": 409,
            "error_type": "CONFLICT",
            "message": f"[ CONFLICT ] {detail_msg}",
            "data": response_json,
        }

    if status_code == 400:
        return {
            "success": False,
            "status_code": 400,
            "error_type": "VALIDATION_ERROR",
            "message": f"[ VALIDATION ERROR ] {detail_msg}",
            "data": response_json,
        }

    # Fallback for other status codes (500, 502, 503, etc.)
    return {
        "success": False,
        "status_code": status_code,
        "error_type": "HTTP_ERROR",
        "message": f"[ ERROR {status_code} ] {error_label}: {detail_msg}",
        "data": response_json,
    }


def format_scan_result(result: Dict[str, Any]) -> str:
    """
    Formats the structured scan result dictionary into human-readable terminal output.

    :param result: Result dictionary returned by send_scan.
    :return: Formatted string ready to display to the terminal.
    """
    if result.get("success"):
        att = result.get("attendance", {})
        student_name = att.get("student_name", "N/A")
        roll_number = att.get("roll_number", "N/A")
        status = att.get("status", "Present")
        source = att.get("source", DEFAULT_SOURCE)
        course_name = att.get("course_name")
        scanned_at = att.get("scanned_at")

        lines = [
            "",
            "[ SUCCESS ] Attendance Recorded!",
            "",
            f"Student Name:      {student_name}",
            f"Roll Number:       {roll_number}",
            f"Attendance Status: {status}",
            f"Scan Source:       {source}",
        ]
        if course_name:
            lines.append(f"Course:            {course_name}")
        if scanned_at:
            lines.append(f"Timestamp (UTC):   {scanned_at}")
        lines.append("")
        return "\n".join(lines)

    # Error case
    lines = ["", result.get("message", "[ ERROR ] Unknown scan failure."), ""]
    return "\n".join(lines)


def process_single_uid(
    base_url: str,
    raw_uid: str,
    source: str = DEFAULT_SOURCE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Validates a UID locally and, if valid, sends the scan request to the backend.

    :param base_url: Base URL of the Flask backend.
    :param raw_uid: Raw UID entered by user.
    :param source: Scan source identifier.
    :param timeout: HTTP request timeout.
    :return: (is_success: bool, result_dict: dict)
    """
    is_valid, normalized_uid, error_msg = validate_uid(raw_uid)
    if not is_valid:
        error_result = {
            "success": False,
            "status_code": None,
            "error_type": "LOCAL_VALIDATION_ERROR",
            "message": f"[ VALIDATION ERROR ] {error_msg}",
            "data": None,
        }
        return False, error_result

    result = send_scan(
        base_url=base_url,
        card_uid=normalized_uid,
        source=source,
        timeout=timeout,
    )
    return bool(result.get("success")), result


def run_interactive(
    base_url: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """
    Runs the interactive terminal scanner loop.

    Continuously prompts the user for RFID UIDs, validates and submits each scan,
    displays the immediate result, and returns to the prompt until the user types EXIT.
    """
    print("========================================")
    print("       SMART ATTENDANCE SYSTEM          ")
    print("        RFID SCANNER SIMULATOR          ")
    print("========================================")
    print()
    print(f"Backend:        {base_url}")
    print("Scanner Status: Ready")
    print()
    print("Enter RFID Card UID to scan.")
    print("Type EXIT to close the simulator.")
    print()

    while True:
        try:
            raw_input_val = input("RFID UID: ")
        except (KeyboardInterrupt, EOFError):
            print("\n\nSimulator stopped by user. Goodbye!")
            break

        cleaned = raw_input_val.strip()
        if cleaned.upper() in ("EXIT", "QUIT"):
            print("\nExiting simulator. Goodbye!")
            break

        if not cleaned:
            print("\n[ VALIDATION ERROR ] RFID UID cannot be empty. Please enter a valid UID.\n")
            continue

        _, result = process_single_uid(
            base_url=base_url,
            raw_uid=cleaned,
            source=DEFAULT_SOURCE,
            timeout=timeout,
        )
        print(format_scan_result(result))


def build_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser for the simulator."""
    parser = argparse.ArgumentParser(
        description="RFID Scanner Simulator for IoT-Based Smart Attendance System."
    )
    parser.add_argument(
        "--uid",
        type=str,
        default=None,
        help="Optional RFID card UID. If provided, performs a single scan and exits.",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_SERVER_URL,
        help=f"Base URL of the Flask backend (default: {DEFAULT_SERVER_URL}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    return parser


def main(args: Optional[list] = None) -> int:
    """
    Main entry point for the simulator script.

    :param args: Optional argument list for testing; defaults to sys.argv[1:].
    :return: Exit status code (0 for success, 1 for failure in one-shot mode).
    """
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    base_url = parsed_args.url.rstrip("/")
    timeout = parsed_args.timeout

    # One-shot mode: --uid was supplied
    if parsed_args.uid is not None:
        success, result = process_single_uid(
            base_url=base_url,
            raw_uid=parsed_args.uid,
            source=DEFAULT_SOURCE,
            timeout=timeout,
        )
        print(format_scan_result(result))
        return 0 if success else 1

    # Interactive mode: No --uid supplied
    run_interactive(base_url=base_url, timeout=timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
