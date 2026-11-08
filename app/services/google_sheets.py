"""
Google Sheets API Client Service.

Provides secure service-account authentication, spreadsheet connection verification,
header provisioning, attendance row formatting with formula injection protection (CWE-1236),
and robust error classification for Google Sheets integration.

Architecture & Design Principles:
- SQLite is the authoritative primary database; Google Sheets is a secondary reporting destination.
- All credentials are kept private and never exposed to logs, client responses, or version control.
- Safe configuration fallback: if credentials are not configured, the application functions normally.
- Error handling maps HTTP status codes and network faults into retryable vs non-retryable categories
  compatible with the Local Outbox Service (Phase 3 Milestone 2).
"""

import os
import json
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union, Any, List, Dict, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError
from flask import current_app, has_app_context

from app.config import get_config
from app.time_utils import KOLKATA_TZ

logger = logging.getLogger(__name__)

# Standard 10-column spreadsheet header layout (Phase 3 Milestone 3 Specification)
DEFAULT_SPREADSHEET_HEADERS = [
    "Attendance ID",
    "Date",
    "Time",
    "Roll Number",
    "Student Name",
    "Course / Session",
    "RFID UID",
    "Attendance Status",
    "Source / Device",
    "Sync Timestamp"
]

# Minimum required Google API scope (Read/Write spreadsheets only)
DEFAULT_SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

# Singleton client caching
_cached_service: Optional[Resource] = None
_cached_credentials_path: Optional[str] = None


# ==============================================================================
# 1. Custom Exception Hierarchy
# ==============================================================================

class GoogleSheetsError(Exception):
    """Base exception for Google Sheets operations."""
    def __init__(
        self,
        message: str,
        error_type: str = "UNKNOWN_ERROR",
        status_code: Optional[int] = None,
        retryable: bool = False
    ):
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error_type": self.error_type,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "message": self.message
        }


class GoogleSheetsConfigError(GoogleSheetsError):
    """Raised when configuration or credential file is missing, unreadable, or invalid."""
    def __init__(self, message: str):
        super().__init__(message, error_type="CONFIG_ERROR", retryable=False)


class GoogleSheetsAuthError(GoogleSheetsError):
    """Raised when service account authentication fails (HTTP 401)."""
    def __init__(self, message: str, status_code: Optional[int] = 401):
        super().__init__(message, error_type="AUTH_ERROR", status_code=status_code, retryable=False)


class GoogleSheetsPermissionError(GoogleSheetsError):
    """Raised when service account lacks permission to the spreadsheet (HTTP 403)."""
    def __init__(self, message: str):
        super().__init__(message, error_type="PERMISSION_DENIED", status_code=403, retryable=False)


class GoogleSheetsNotFoundError(GoogleSheetsError):
    """Raised when the spreadsheet ID is not found (HTTP 404)."""
    def __init__(self, message: str):
        super().__init__(message, error_type="NOT_FOUND", status_code=404, retryable=False)


class GoogleSheetsRateLimitError(GoogleSheetsError):
    """Raised when Google API quota or rate limits are exceeded (HTTP 429)."""
    def __init__(self, message: str):
        super().__init__(message, error_type="RATE_LIMITED", status_code=429, retryable=True)


class GoogleSheetsServerError(GoogleSheetsError):
    """Raised when Google backend returns HTTP 5xx."""
    def __init__(self, message: str, status_code: Optional[int] = 500):
        super().__init__(message, error_type="SERVER_ERROR", status_code=status_code, retryable=True)


class GoogleSheetsNetworkError(GoogleSheetsError):
    """Raised on socket timeout, connection reset, or DNS failure."""
    def __init__(self, message: str):
        super().__init__(message, error_type="NETWORK_ERROR", retryable=True)


# ==============================================================================
# 2. Configuration & Credential Management
# ==============================================================================

def get_google_config() -> dict:
    """
    Retrieves Google Sheets configuration from Flask app context or Config class.
    
    :return: Dictionary with 'credentials_path', 'spreadsheet_id', 'sheet_name', 'scopes'.
    """
    if has_app_context():
        cfg = current_app.config
        creds_path = cfg.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        sheet_id = cfg.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
        sheet_name = cfg.get("GOOGLE_SHEETS_SHEET_NAME", "Attendance")
        scopes = cfg.get("GOOGLE_SHEETS_SCOPES", DEFAULT_SHEETS_SCOPES)
    else:
        cfg = get_config()
        creds_path = getattr(cfg, "GOOGLE_SERVICE_ACCOUNT_FILE", None)
        sheet_id = getattr(cfg, "GOOGLE_SHEETS_SPREADSHEET_ID", "")
        sheet_name = getattr(cfg, "GOOGLE_SHEETS_SHEET_NAME", "Attendance")
        scopes = getattr(cfg, "GOOGLE_SHEETS_SCOPES", DEFAULT_SHEETS_SCOPES)

    return {
        "credentials_path": str(creds_path) if creds_path else None,
        "spreadsheet_id": str(sheet_id).strip() if sheet_id else "",
        "sheet_name": str(sheet_name).strip() if sheet_name else "Attendance",
        "scopes": tuple(scopes) if scopes else DEFAULT_SHEETS_SCOPES
    }


def load_credentials(
    credentials_path: Optional[Union[str, Path]] = None,
    scopes: Optional[Tuple[str, ...]] = None
) -> service_account.Credentials:
    """
    Loads Google Cloud Service Account credentials securely from disk.
    Enforces minimum scope requirements and validates file format without exposing keys.
    
    :param credentials_path: Optional path to JSON key file.
    :param scopes: Optional scopes tuple (defaults to DEFAULT_SHEETS_SCOPES).
    :return: Authenticated service_account.Credentials instance.
    :raises GoogleSheetsConfigError: If file is missing or contains invalid JSON structure.
    :raises GoogleSheetsAuthError: If authentication initialization fails.
    """
    if credentials_path is None:
        cfg = get_google_config()
        credentials_path = cfg["credentials_path"]

    if not credentials_path:
        raise GoogleSheetsConfigError(
            "Service account credential path is not configured. "
            "Please configure GOOGLE_SERVICE_ACCOUNT_FILE."
        )

    cred_file = Path(credentials_path)
    if not cred_file.exists():
        raise GoogleSheetsConfigError(
            f"Service account credential file was not found at '{cred_file}'. "
            "Please place your downloaded JSON key in 'credentials/service_account.json'."
        )

    # Validate JSON syntax and required service account fields
    try:
        with open(cred_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise GoogleSheetsConfigError(f"Service account file contains invalid JSON: {e}")
    except Exception as e:
        raise GoogleSheetsConfigError(f"Failed to read service account credential file: {e}")

    if not isinstance(data, dict):
        raise GoogleSheetsConfigError("Service account key file root must be a JSON object.")

    if data.get("type") != "service_account":
        raise GoogleSheetsConfigError(
            f"Expected key type 'service_account', but found '{data.get('type')}'. "
            "Ensure you downloaded a Service Account key (not an OAuth client ID)."
        )

    if not data.get("client_email") or not data.get("private_key"):
        raise GoogleSheetsConfigError(
            "Service account key file is missing required fields ('client_email' or 'private_key')."
        )

    target_scopes = scopes or DEFAULT_SHEETS_SCOPES

    try:
        credentials = service_account.Credentials.from_service_account_file(
            str(cred_file),
            scopes=target_scopes
        )
        return credentials
    except Exception as e:
        logger.warning(f"Failed to initialize service account credentials: {e}")
        raise GoogleSheetsAuthError(f"Service account authentication failed: {e}")


def get_sheets_service(
    credentials: Optional[service_account.Credentials] = None,
    credentials_path: Optional[Union[str, Path]] = None,
    scopes: Optional[Tuple[str, ...]] = None,
    cache: bool = True
) -> Resource:
    """
    Initializes and caches the Google Sheets API client (Resource).
    
    :param credentials: Optional pre-loaded credentials.
    :param credentials_path: Optional path to JSON key file.
    :param scopes: Optional scopes tuple.
    :param cache: Whether to reuse cached singleton client instance.
    :return: Google API discovery Resource for Sheets v4.
    """
    global _cached_service, _cached_credentials_path

    if cache and _cached_service is not None:
        return _cached_service

    if credentials is None:
        credentials = load_credentials(credentials_path=credentials_path, scopes=scopes)

    try:
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        if cache:
            _cached_service = service
            _cached_credentials_path = str(credentials_path) if credentials_path else None
        return service
    except Exception as e:
        logger.error(f"Failed to build Google Sheets service: {e}")
        raise GoogleSheetsError(f"Failed to build Google Sheets service: {e}")


def reset_sheets_service() -> None:
    """Resets cached service singleton (used for testing or reconfiguration)."""
    global _cached_service, _cached_credentials_path
    _cached_service = None
    _cached_credentials_path = None


# ==============================================================================
# 3. Formula Injection Protection (CWE-1236)
# ==============================================================================

def sanitize_sheet_cell(value: Any) -> Any:
    """
    Sanitizes values against Spreadsheet Formula Injection (CWE-1236).
    If a string value starts with '=', '+', '-', '@', '\\t', '\\r',
    a leading single quote (') is prepended to ensure Google Sheets
    treats the cell as literal plain text rather than an active formula.
    
    Numeric types (integers, floats) are preserved intact without quotes.
    
    :param value: Cell value to sanitize.
    :return: Sanitized value safe for Google Sheets insertion.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value

    val_str = str(value)
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


# ==============================================================================
# 4. Connection Verification
# ==============================================================================

def map_http_error(e: HttpError, spreadsheet_id: str = "") -> GoogleSheetsError:
    """
    Translates Google HttpError into specific domain exceptions with retryability flags.
    
    :param e: Google HttpError instance.
    :param spreadsheet_id: Target spreadsheet identifier.
    :return: Typed GoogleSheetsError subclass.
    """
    status_code = getattr(e.resp, "status", 500)
    details = ""
    try:
        content_json = json.loads(e.content.decode("utf-8"))
        details = content_json.get("error", {}).get("message", "")
    except Exception:
        details = str(e)

    if status_code == 401:
        return GoogleSheetsAuthError(
            f"Authentication failed (HTTP 401): {details or 'Invalid credentials or expired token.'}",
            status_code=401
        )
    elif status_code == 403:
        return GoogleSheetsPermissionError(
            f"Permission denied (HTTP 403): The service account does not have access to spreadsheet '{spreadsheet_id}'. "
            "Please share the Google Spreadsheet with the service account email as Editor."
        )
    elif status_code == 404:
        return GoogleSheetsNotFoundError(
            f"Spreadsheet not found (HTTP 404): No spreadsheet found with ID '{spreadsheet_id}'. "
            "Please verify GOOGLE_SHEETS_SPREADSHEET_ID."
        )
    elif status_code == 429:
        return GoogleSheetsRateLimitError(
            f"Google Sheets API rate limit exceeded (HTTP 429): {details or 'Quota limit reached.'} Retry with backoff."
        )
    elif status_code >= 500:
        return GoogleSheetsServerError(
            f"Google backend server error (HTTP {status_code}): {details or 'Internal error.'}",
            status_code=status_code
        )
    else:
        return GoogleSheetsError(
            f"Google Sheets API error (HTTP {status_code}): {details}",
            error_type="HTTP_ERROR",
            status_code=status_code,
            retryable=False
        )


def verify_sheet_connection(
    spreadsheet_id: Optional[str] = None,
    credentials_path: Optional[Union[str, Path]] = None,
    service: Optional[Resource] = None
) -> dict:
    """
    Verifies read access to the designated Google Spreadsheet.
    
    :param spreadsheet_id: Target spreadsheet ID (or resolved from config).
    :param credentials_path: Optional path to JSON key file.
    :param service: Optional mock/pre-built service instance.
    :return: Structured result dictionary.
    """
    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]

    if not target_id or not target_id.strip():
        return {
            "success": False,
            "error_type": "MISSING_SPREADSHEET_ID",
            "status_code": None,
            "retryable": False,
            "message": "Google Spreadsheet ID is not configured. Set GOOGLE_SHEETS_SPREADSHEET_ID."
        }

    target_id = target_id.strip()

    try:
        if service is None:
            service = get_sheets_service(credentials_path=credentials_path)

        result = service.spreadsheets().get(
            spreadsheetId=target_id,
            fields="spreadsheetId,properties.title,sheets.properties(sheetId,title)"
        ).execute()

        title = result.get("properties", {}).get("title", "Untitled Spreadsheet")
        sheets = [
            s.get("properties", {}).get("title", "")
            for s in result.get("sheets", [])
        ]

        return {
            "success": True,
            "spreadsheet_id": target_id,
            "title": title,
            "sheets": sheets,
            "message": f"Successfully connected to spreadsheet '{title}'."
        }

    except GoogleSheetsError as e:
        return e.to_dict()
    except HttpError as e:
        domain_err = map_http_error(e, spreadsheet_id=target_id)
        return domain_err.to_dict()
    except (socket.timeout, TimeoutError) as e:
        err = GoogleSheetsNetworkError(f"Network timeout connecting to Google Sheets API: {e}")
        return err.to_dict()
    except (socket.error, ConnectionError) as e:
        err = GoogleSheetsNetworkError(f"Network connection failure: {e}")
        return err.to_dict()
    except Exception as e:
        err = GoogleSheetsError(f"Unexpected error verifying spreadsheet access: {e}")
        return err.to_dict()


# ==============================================================================
# 5. Header Provisioning
# ==============================================================================

def ensure_sheet_headers(
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = "Attendance",
    service: Optional[Resource] = None,
    expected_headers: Optional[List[str]] = None
) -> dict:
    """
    Ensures that the first row of the designated spreadsheet tab contains the required 10 headers.
    - If headers are already present in row 1: does not duplicate or overwrite.
    - If row 1 is completely empty: writes the 10 standard headers.
    - If row 1 contains other data: prevents accidental overwriting and reports conflict.
    - Never clears existing attendance data.
    
    :param spreadsheet_id: Target spreadsheet ID.
    :param sheet_name: Target sheet/tab name (default: "Attendance").
    :param service: Optional service instance.
    :param expected_headers: List of 10 header strings (defaults to DEFAULT_SPREADSHEET_HEADERS).
    :return: Structured result dictionary.
    """
    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]
    if not target_id or not target_id.strip():
        return {
            "success": False,
            "error_type": "MISSING_SPREADSHEET_ID",
            "retryable": False,
            "message": "Google Spreadsheet ID is not configured."
        }

    headers_to_check = expected_headers or DEFAULT_SPREADSHEET_HEADERS
    target_sheet = sheet_name or cfg["sheet_name"] or "Attendance"
    read_range = f"'{target_sheet}'!A1:J1"

    try:
        if service is None:
            service = get_sheets_service()

        # 1. Read existing row 1
        resp = service.spreadsheets().values().get(
            spreadsheetId=target_id,
            range=read_range
        ).execute()

        values = resp.get("values", [])

        # Case A: Row 1 already matches expected headers exactly
        if values and len(values) > 0 and values[0] == headers_to_check:
            return {
                "success": True,
                "created": False,
                "status": "ALREADY_PRESENT",
                "headers": values[0],
                "message": "Headers already present and verified in row 1."
            }

        # Case B: Row 1 is empty -> write headers
        is_empty = (
            not values or
            len(values) == 0 or
            not values[0] or
            all(str(cell).strip() == "" for cell in values[0])
        )

        if is_empty:
            service.spreadsheets().values().update(
                spreadsheetId=target_id,
                range=read_range,
                valueInputOption="USER_ENTERED",
                body={"values": [headers_to_check]}
            ).execute()

            return {
                "success": True,
                "created": True,
                "status": "CREATED",
                "headers": headers_to_check,
                "message": f"Successfully provisioned standard headers in '{target_sheet}'!A1:J1."
            }

        # Case C: Row 1 has content that differs from expected headers
        # Check if it matches case-insensitively
        row1_clean = [str(c).strip().lower() for c in values[0]]
        expected_clean = [h.strip().lower() for h in headers_to_check]
        if row1_clean == expected_clean:
            return {
                "success": True,
                "created": False,
                "status": "ALREADY_PRESENT",
                "headers": values[0],
                "message": "Headers already present (case-insensitive match)."
            }

        # Protection: Row 1 contains unrecognized data; do not overwrite existing data
        return {
            "success": False,
            "created": False,
            "status": "CONFLICT",
            "error_type": "HEADER_CONFLICT",
            "existing_headers": values[0],
            "message": (
                f"Row 1 in sheet '{target_sheet}' contains existing non-header data or different columns. "
                "Aborting write to preserve existing spreadsheet data."
            )
        }

    except HttpError as e:
        domain_err = map_http_error(e, spreadsheet_id=target_id)
        return domain_err.to_dict()
    except (socket.timeout, TimeoutError) as e:
        err = GoogleSheetsNetworkError(f"Network timeout provisioning headers: {e}")
        return err.to_dict()
    except Exception as e:
        err = GoogleSheetsError(f"Failed to provision headers: {e}")
        return err.to_dict()


# ==============================================================================
# 6. Batch Row Formatting (Ten-Column Mapping)
# ==============================================================================

def format_attendance_row(
    record: Dict[str, Any],
    sync_timestamp: Optional[str] = None
) -> List[Any]:
    """
    Formats a single attendance record into the standard 10-column spreadsheet row:
    1. Attendance ID (int, numeric preserved)
    2. Date (YYYY-MM-DD)
    3. Time (HH:MM:SS)
    4. Roll Number (sanitized text)
    5. Student Name (sanitized text)
    6. Course / Session (sanitized text)
    7. RFID UID (sanitized text)
    8. Attendance Status (sanitized text)
    9. Source / Device (sanitized text)
    10. Sync Timestamp (ISO 8601 UTC timestamp string)
    
    :param record: Attendance record dictionary (from claim_records or query).
    :param sync_timestamp: Optional ISO timestamp of the sync execution.
    :return: List of 10 sanitized values.
    """
    # 1. Attendance ID (must remain numeric int)
    raw_id = record.get("attendance_id") if "attendance_id" in record else record.get("id")
    try:
        att_id = int(raw_id) if raw_id is not None else 0
    except (ValueError, TypeError):
        att_id = sanitize_sheet_cell(raw_id)

    # 2 & 3. Date and Time resolution
    date_val = record.get("date") or record.get("session_date") or ""
    time_val = record.get("time") or ""

    scanned_at = record.get("scanned_at") or ""
    if scanned_at:
        try:
            # Parse ISO 8601 string (e.g. '2026-11-08T04:30:00Z')
            clean_ts = scanned_at.replace("Z", "+00:00")
            dt_utc = datetime.fromisoformat(clean_ts)
            dt_ist = dt_utc.astimezone(KOLKATA_TZ)
            if not date_val:
                date_val = dt_ist.strftime("%Y-%m-%d")
            if not time_val:
                time_val = dt_ist.strftime("%H:%M:%S")
        except Exception:
            if not date_val:
                date_val = str(scanned_at)[:10]

    # 10. Sync Timestamp resolution
    sync_ts = sync_timestamp or record.get("sync_timestamp") or record.get("synced_at")
    if not sync_ts:
        sync_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Map the 10 columns applying formula injection sanitization
    return [
        att_id,                                                                         # 1. Attendance ID
        sanitize_sheet_cell(date_val),                                                  # 2. Date
        sanitize_sheet_cell(time_val),                                                  # 3. Time
        sanitize_sheet_cell(record.get("roll_number", "")),                             # 4. Roll Number
        sanitize_sheet_cell(record.get("student_name") or record.get("full_name", "")), # 5. Student Name
        sanitize_sheet_cell(record.get("course_name") or record.get("session_name", "")), # 6. Course / Session
        sanitize_sheet_cell(record.get("card_uid", "")),                                # 7. RFID UID
        sanitize_sheet_cell(record.get("attendance_status") or record.get("status", "Present")), # 8. Attendance Status
        sanitize_sheet_cell(record.get("source", "")),                                  # 9. Source / Device
        sanitize_sheet_cell(sync_ts)                                                    # 10. Sync Timestamp
    ]


def format_attendance_batch(
    records: List[Dict[str, Any]],
    sync_timestamp: Optional[str] = None
) -> List[List[Any]]:
    """
    Formats multiple attendance records into a 2D matrix of 10-column rows.
    
    :param records: List of record dictionaries.
    :param sync_timestamp: Optional sync execution timestamp.
    :return: List of 10-element lists.
    """
    return [format_attendance_row(rec, sync_timestamp=sync_timestamp) for rec in records]


def append_attendance_batch(
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = "Attendance",
    rows: Optional[List[List[Any]]] = None,
    service: Optional[Resource] = None,
    value_input_option: str = "RAW"
) -> dict:
    """
    Appends pre-formatted rows to the designated spreadsheet tab using RAW input.
    Reusable API helper for Milestone 4 synchronization worker.
    
    :param spreadsheet_id: Target spreadsheet ID.
    :param sheet_name: Target tab name.
    :param rows: 2D list of row values.
    :param service: Optional service instance.
    :param value_input_option: "RAW" (enforced for attendance data to prevent unexpected parsing).
    :return: Structured result dictionary.
    """
    if not rows:
        return {"success": True, "appended_count": 0, "message": "No rows to append."}

    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]
    if not target_id or not target_id.strip():
        return {
            "success": False,
            "error_type": "MISSING_SPREADSHEET_ID",
            "retryable": False,
            "message": "Google Spreadsheet ID is not configured."
        }

    target_sheet = sheet_name or cfg["sheet_name"] or "Attendance"
    append_range = f"'{target_sheet}'!A:J"

    try:
        if service is None:
            service = get_sheets_service()

        body = {
            "majorDimension": "ROWS",
            "values": rows
        }

        resp = service.spreadsheets().values().append(
            spreadsheetId=target_id,
            range=append_range,
            valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()

        updated_rows = resp.get("updates", {}).get("updatedRows", len(rows))
        return {
            "success": True,
            "appended_count": updated_rows,
            "updated_range": resp.get("updates", {}).get("updatedRange", ""),
            "message": f"Successfully appended {updated_rows} rows to '{target_sheet}'."
        }

    except HttpError as e:
        domain_err = map_http_error(e, spreadsheet_id=target_id)
        return domain_err.to_dict()
    except (socket.timeout, TimeoutError) as e:
        err = GoogleSheetsNetworkError(f"Network timeout appending attendance rows: {e}")
        return err.to_dict()
    except Exception as e:
        err = GoogleSheetsError(f"Failed to append attendance rows: {e}")
        return err.to_dict()


def get_existing_attendance_ids(
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = "Attendance",
    service: Optional[Resource] = None
) -> set[int]:
    """
    Retrieves all existing Attendance IDs from Column A (rows 2+) of the worksheet.
    Core mechanism for duplicate prevention and idempotent synchronization.
    
    :param spreadsheet_id: Target spreadsheet ID.
    :param sheet_name: Target tab name (default: 'Attendance').
    :param service: Optional Google API discovery Resource.
    :return: Set of integer Attendance IDs present in the spreadsheet.
    """
    cfg = get_google_config()
    target_id = spreadsheet_id if spreadsheet_id is not None else cfg["spreadsheet_id"]
    if not target_id or not target_id.strip():
        raise GoogleSheetsConfigError("Google Spreadsheet ID is not configured.")

    target_sheet = sheet_name or cfg["sheet_name"] or "Attendance"
    read_range = f"'{target_sheet}'!A2:A"

    if service is None:
        service = get_sheets_service()

    try:
        resp = service.spreadsheets().values().get(
            spreadsheetId=target_id.strip(),
            range=read_range
        ).execute()

        values = resp.get("values", [])
        existing_ids = set()

        for row in values:
            if row and len(row) > 0 and row[0] is not None:
                cell_str = str(row[0]).strip().lstrip("'")
                if cell_str:
                    try:
                        existing_ids.add(int(cell_str))
                    except (ValueError, TypeError):
                        pass

        return existing_ids

    except HttpError as e:
        domain_err = map_http_error(e, spreadsheet_id=target_id)
        raise domain_err
    except (socket.timeout, TimeoutError) as e:
        raise GoogleSheetsNetworkError(f"Network timeout reading existing attendance IDs: {e}")
    except Exception as e:
        if isinstance(e, GoogleSheetsError):
            raise
        raise GoogleSheetsError(f"Failed to read existing attendance IDs: {e}")
