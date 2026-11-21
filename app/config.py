"""
Configuration settings for the Smart Attendance Management System.
Manages database paths and environment settings using Python's pathlib.
"""

from pathlib import Path
from datetime import timedelta
import json
import os
import re

# Root directory of the project (c:\Users\niraj\Downloads\Smart-Attendance-System)
BASE_DIR = Path(__file__).resolve().parent.parent

# Database storage folder
DATABASE_DIR = BASE_DIR / "database"
BACKUP_DIR = BASE_DIR / "backups"
CREDENTIALS_DIR = BASE_DIR / "credentials"


def validate_device_api_keys(keys_dict: dict) -> dict[str, str]:
    """
    Strictly validates a dictionary of device credentials:
    - Must be a dictionary of {device_id: api_key}
    - device_id: non-empty string, 3 to 50 chars, alphanumeric/dashes/underscores
    - api_key: non-empty string, minimum 32 characters, non-whitespace
    - Rejects empty, malformed, or partially invalid entries
    """
    if not isinstance(keys_dict, dict):
        raise ValueError("DEVICE_API_KEYS must be a dictionary mapping device_id to api_key.")

    validated: dict[str, str] = {}
    for dev_id, key in keys_dict.items():
        if not isinstance(dev_id, str):
            raise ValueError(f"Invalid device_id '{dev_id}': must be a string.")
        dev_id_clean = dev_id.strip()
        if not dev_id_clean:
            raise ValueError("DEVICE_API_KEYS contains an empty or whitespace-only device_id.")
        if len(dev_id_clean) < 3 or len(dev_id_clean) > 50:
            raise ValueError(f"device_id '{dev_id_clean}' must be between 3 and 50 characters.")
        if not re.match(r"^[A-Za-z0-9_-]+$", dev_id_clean):
            raise ValueError(f"device_id '{dev_id_clean}' contains invalid characters (allowed: alphanumeric, dashes, underscores).")

        if not isinstance(key, str):
            raise ValueError(f"API key for device '{dev_id_clean}' must be a string.")
        key_clean = key.strip()
        if not key_clean:
            raise ValueError(f"API key for device '{dev_id_clean}' cannot be empty or whitespace-only.")
        if len(key_clean) < 32:
            raise ValueError(f"API key for device '{dev_id_clean}' is too short (minimum 32 characters required).")

        validated[dev_id_clean] = key_clean

    return validated


def load_device_api_keys() -> dict[str, str]:
    """
    Loads and strictly validates DEVICE_API_KEYS from environment variable.
    - If DEVICE_API_KEYS is missing or empty: returns {} (fail closed).
    - If set: parses as JSON object with duplicate-key detection and strict validation.
    - Rejects malformed JSON, duplicate keys, empty keys, or invalid types.
    """
    raw_env = os.environ.get("DEVICE_API_KEYS", "").strip()
    if not raw_env:
        return {}

    def _check_duplicates(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError(f"Duplicate device_id '{k}' detected in DEVICE_API_KEYS configuration.")
            d[k] = v
        return d

    try:
        parsed = json.loads(raw_env, object_pairs_hook=_check_duplicates)
    except json.JSONDecodeError as err:
        raise ValueError(f"Malformed JSON in DEVICE_API_KEYS environment variable: {err}") from err

    return validate_device_api_keys(parsed)

class Config:
    """Base configuration settings."""
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-smart-attendance-2026")
    DATABASE_PATH = DATABASE_DIR / "attendance.db"
    BACKUP_DIR = BACKUP_DIR
    CREDENTIALS_DIR = CREDENTIALS_DIR
    DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Asia/Kolkata")
    
    # Session & Cookie Security settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    
    # Authentication & CSRF defaults
    LOGIN_DISABLED = False
    CSRF_ENABLED = True
    WTF_CSRF_ENABLED = True

    # Hardware Device Authentication (Milestone 1)
    DEVICE_API_KEYS = load_device_api_keys()

    # Google Sheets Integration (Phase 3)
    GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        str(CREDENTIALS_DIR / "service_account.json")
    )
    GOOGLE_SHEETS_SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    GOOGLE_SHEETS_SHEET_NAME = os.environ.get("GOOGLE_SHEETS_SHEET_NAME", "Attendance").strip() or "Attendance"
    GOOGLE_SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

    # Google Sheets Background Worker Configuration (Phase 3 Milestone 4)
    GOOGLE_SHEETS_SYNC_ENABLED = os.environ.get("GOOGLE_SHEETS_SYNC_ENABLED", "true").lower() == "true"
    GOOGLE_SHEETS_SYNC_POLL_INTERVAL = int(os.environ.get("GOOGLE_SHEETS_SYNC_POLL_INTERVAL", 10))
    GOOGLE_SHEETS_SYNC_BATCH_SIZE = int(os.environ.get("GOOGLE_SHEETS_SYNC_BATCH_SIZE", 20))
    GOOGLE_SHEETS_SYNC_LEASE_SECONDS = int(os.environ.get("GOOGLE_SHEETS_SYNC_LEASE_SECONDS", 60))

class DevelopmentConfig(Config):
    """Development environment configuration."""
    DEBUG = True
    DATABASE_PATH = DATABASE_DIR / "attendance.db"
    SESSION_COOKIE_SECURE = False

class ProductionConfig(Config):
    """Production environment configuration with strict security requirements."""
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SECRET_KEY = None

    def __init__(self):
        super().__init__()
        self.validate_production_secret()

    @classmethod
    def validate_production_secret(cls) -> str:
        """
        Validates that a non-empty, cryptographically random SECRET_KEY is set in the environment.
        Raises RuntimeError if missing, empty, or using the development fallback.
        """
        secret = os.environ.get("SECRET_KEY", "").strip()
        if not secret:
            raise RuntimeError(
                "FATAL: Production configuration error: SECRET_KEY environment variable is missing or empty! "
                "In production, SECRET_KEY must be set to a secure, random string."
            )
        if secret == "dev-secret-key-smart-attendance-2026":
            raise RuntimeError(
                "FATAL: Production configuration error: The development fallback SECRET_KEY is strictly forbidden in production! "
                "Please configure a unique, cryptographically random SECRET_KEY."
            )
        cls.SECRET_KEY = secret
        return secret

class TestingConfig(Config):
    """Testing environment configuration using an isolated test database."""
    TESTING = True
    DEBUG = True
    DATABASE_PATH = DATABASE_DIR / "test_attendance.db"
    # By standard Flask convention, disable login and CSRF by default for isolated regression tests;
    # tests in test_auth.py explicitly enable them to test security enforcement.
    LOGIN_DISABLED = True
    CSRF_ENABLED = False
    WTF_CSRF_ENABLED = False

# Mapping to look up configuration classes by environment name
CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}

def get_config(env_name=None):
    """
    Returns the appropriate configuration class based on the environment name
    or the FLASK_ENV / APP_ENV environment variable.
    """
    if env_name is None:
        env_name = (os.environ.get("FLASK_ENV") or os.environ.get("APP_ENV") or "development").lower()
    
    if isinstance(env_name, str) and env_name.lower() == "production":
        ProductionConfig.validate_production_secret()
        return ProductionConfig
        
    return CONFIG_MAP.get(env_name, DevelopmentConfig)
