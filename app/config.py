"""
Configuration settings for the Smart Attendance Management System.
Manages database paths and environment settings using Python's pathlib.
"""

from pathlib import Path
from datetime import timedelta
import os

# Root directory of the project (c:\Users\niraj\Downloads\Smart-Attendance-System)
BASE_DIR = Path(__file__).resolve().parent.parent

# Database storage folder
DATABASE_DIR = BASE_DIR / "database"
BACKUP_DIR = BASE_DIR / "backups"
CREDENTIALS_DIR = BASE_DIR / "credentials"

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
