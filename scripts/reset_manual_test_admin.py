"""
Secure Password Reset Utility for Isolated Manual Testing Administrator.
STAGE 9.7B.2A — CONTROLLED TEST ADMINISTRATOR PASSWORD RESET

Strict Safety Invariants:
- Targets ONLY database/manual_test/attendance_manual_test.db
- Rejects any attempt to target or modify database/attendance.db
- Requires an interactive console terminal (TTY); fails closed without falling back to echo
- Uses getpass.getpass() with GetPassWarning treated as a fatal error
- Never falls back to input(), sys.stdin.readline(), or visible text
- Never writes plaintext password to disk, logs, or history
- Enforces application password policy (validate_password_strength)
- Hashes securely using application's official hash_password (scrypt)
- Uses parameterized SQL in an explicit transaction requiring exactly 1 row
- Replaces all assert statements with explicit conditionals active under python -O
- Strictly verifies exact HTTP status codes and exact parsed redirect URL paths via urlsplit
- Executes in-memory Flask test-client smoke tests before reference cleanup
- Never writes result files, JSON dumps, or temporary files to disk
"""

import getpass
import hashlib
import os
import sqlite3
import sys
import warnings
from pathlib import Path
from urllib.parse import urlsplit

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import resolve_development_database_path
from app.auth import validate_password_strength, hash_password, verify_password
from app import create_app


def compute_file_hash(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def get_hidden_password(prompt: str) -> str:
    """
    Reads a password interactively using getpass.getpass().
    - Strictly requires an interactive console (isatty).
    - Traps getpass.GetPassWarning as an error to prevent silent fallback to echo.
    - Never falls back to input(), sys.stdin.readline(), or visible text.
    """
    if not sys.stdin.isatty():
        print("\nFATAL ERROR: Password entry requires an interactive console (TTY).")
        print("Piped input, background processes, and non-interactive environments are disallowed.")
        sys.exit(1)

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            print("\nFATAL ERROR: Terminal cannot disable echo. Password entry aborted.")
            sys.exit(1)
        except Exception as e:
            print(f"\nFATAL ERROR: Failed to read hidden password input: {e}")
            sys.exit(1)


def validate_redirect_path(url: str, expected_path: str) -> tuple[bool, str]:
    """
    Validates a redirect destination URL using urllib.parse.urlsplit:
    - Path must exactly match expected_path.
    - Scheme must be empty or standard http/https on localhost/127.0.0.1.
    - External netloc is strictly rejected.
    - Query parameters are ignored during path comparison.
    """
    if not url:
        return False, "Redirect location header is empty."
    parsed = urlsplit(url)
    if parsed.scheme not in ("", "http", "https"):
        return False, f"Unexpected URL scheme '{parsed.scheme}' in redirect."
    if parsed.netloc not in ("", "localhost", "127.0.0.1", "localhost:5000", "127.0.0.1:5000"):
        return False, f"External network location '{parsed.netloc}' in redirect is rejected."
    if parsed.path != expected_path:
        return False, f"Expected redirect path '{expected_path}', got '{parsed.path}'."
    return True, ""


def main():
    print("=" * 65)
    print("STAGE 9.7B.2A: CONTROLLED TEST ADMINISTRATOR PASSWORD RESET")
    print("=" * 65)

    # 1. Target Database Path Verification (Phase C)
    target_rel = Path("database/manual_test/attendance_manual_test.db")
    target_abs = (PROJECT_ROOT / target_rel).resolve()
    orig_abs = (PROJECT_ROOT / "database" / "attendance.db").resolve()

    resolved_db = resolve_development_database_path()
    print(f"Target DB Path:   {resolved_db}")

    if resolved_db == orig_abs:
        print("\nFATAL ERROR: DATABASE_PATH points to the production/original database!")
        print("Password reset is strictly prohibited on database/attendance.db.")
        sys.exit(1)

    if resolved_db != target_abs:
        print(f"\nFATAL ERROR: Target must be exactly '{target_abs}', got '{resolved_db}'.")
        print("Please configure: $env:DATABASE_PATH='database/manual_test/attendance_manual_test.db'")
        sys.exit(1)

    if not target_abs.exists():
        print(f"\nFATAL ERROR: Target database file does not exist: {target_abs}")
        sys.exit(1)

    # Compute baseline SHA-256 hashes (Phase A)
    orig_hash_before = compute_file_hash(orig_abs)
    test_hash_before = compute_file_hash(target_abs)
    print(f"Original DB Hash: {orig_hash_before}")
    print(f"Test DB Pre-Hash: {test_hash_before}")

    # 2. Pre-Reset Account Verification (Phase A)
    conn = sqlite3.connect(str(target_abs))
    cursor = conn.cursor()

    cursor.execute("SELECT id, username, role, active FROM admin_users;")
    admins = cursor.fetchall()

    if len(admins) != 1:
        print(f"\nFATAL ERROR: Expected exactly 1 administrator in test database, found {len(admins)}.")
        conn.close()
        sys.exit(1)

    admin_id, username, role, active = admins[0]
    if username != "manual_test_admin":
        print(f"\nFATAL ERROR: Expected administrator 'manual_test_admin', found '{username}'.")
        conn.close()
        sys.exit(1)

    if role != "admin" or active != 1:
        print(f"\nFATAL ERROR: Administrator account is invalid (role={role}, active={active}).")
        conn.close()
        sys.exit(1)

    print(f"Account Verified: '{username}' (id={admin_id}, role={role}, active={active})")
    print("-" * 65)

    # 3. Hidden Interactive Password Prompts (Phase D - Strictly TTY getpass)
    try:
        p1 = get_hidden_password("Enter new password for 'manual_test_admin': ")
        p2 = get_hidden_password("Confirm new password for 'manual_test_admin': ")
    except Exception as e:
        print(f"\nError reading password input: {e}")
        conn.close()
        sys.exit(1)

    if not p1 or not p2:
        print("\nError: Password cannot be empty.")
        conn.close()
        sys.exit(1)

    if p1 != p2:
        print("\nError: Passwords do not match. No changes made.")
        conn.close()
        # Best-effort reference cleanup (Note: Python cannot guarantee physical memory zeroization)
        del p1, p2
        sys.exit(1)

    # 4. Validate Password Policy
    is_strong, err_msg = validate_password_strength(p1)
    if not is_strong:
        print(f"\nError: Password does not meet policy requirements: {err_msg}")
        conn.close()
        # Best-effort reference cleanup
        del p1, p2
        sys.exit(1)

    # 5. Hash Password Securely (Scrypt)
    new_hash = hash_password(p1)

    # 6. Execute Atomic Parameterized Update in a Transaction
    try:
        cursor.execute("BEGIN IMMEDIATE;")
        cursor.execute(
            """
            UPDATE admin_users
            SET password_hash = ?
            WHERE username = 'manual_test_admin' AND id = ?;
            """,
            (new_hash, admin_id)
        )

        if cursor.rowcount != 1:
            conn.rollback()
            print(f"\nFATAL ERROR: Expected exactly 1 row updated, got {cursor.rowcount}. Rolled back.")
            conn.close()
            del p1, p2
            sys.exit(1)

        conn.commit()
    except Exception as err:
        conn.rollback()
        print(f"\nFATAL ERROR: Database transaction failed: {err}")
        conn.close()
        del p1, p2
        sys.exit(1)

    # 7. Post-Transaction Invariant Verification (Phase E)
    cursor.execute("SELECT password_hash FROM admin_users WHERE username = 'manual_test_admin';")
    stored_hash = cursor.fetchone()[0]

    if not verify_password(stored_hash, p1):
        print("\nFATAL ERROR: Immediate verification against stored hash failed!")
        conn.close()
        del p1, p2
        sys.exit(1)

    # Check database integrity (Phase G)
    cursor.execute("PRAGMA integrity_check;")
    integrity_result = cursor.fetchone()[0]
    if integrity_result != "ok":
        print(f"\nFATAL ERROR: Integrity check returned '{integrity_result}'.")
        conn.close()
        del p1, p2
        sys.exit(1)

    # Check table row counts
    counts = {}
    for table in ["students", "class_sessions", "attendance", "admin_users", "sheets_sync_queue"]:
        cursor.execute(f"SELECT count(*) FROM {table};")
        counts[table] = cursor.fetchone()[0]

    conn.close()

    # Re-verify original database hash
    orig_hash_after = compute_file_hash(orig_abs)
    test_hash_after = compute_file_hash(target_abs)

    if orig_hash_after != orig_hash_before:
        print("\nCRITICAL SECURITY ALERT: Original database SHA-256 hash changed!")
        del p1, p2
        sys.exit(1)

    print("Phase E Verification: PASS")
    print(f"Original DB Untouched: {orig_hash_after}")
    print(f"Test DB Post-Hash:     {test_hash_after}")

    # 8. Phase F: Authentication Smoke Test via Flask Test Client (Correction 5 & 6)
    print("\n--- Phase F: Authentication Smoke Test ---")
    os.environ["DATABASE_PATH"] = "database/manual_test/attendance_manual_test.db"
    os.environ["GOOGLE_SHEETS_SYNC_ENABLED"] = "false"
    
    app = create_app("development")
    client = app.test_client()

    # F.1 Fetch login page to generate session CSRF token
    login_page_res = client.get("/login")
    if login_page_res.status_code != 200:
        print(f"\nFATAL ERROR: GET /login returned unexpected HTTP status {login_page_res.status_code} (expected 200).")
        del p1, p2
        sys.exit(1)

    with client.session_transaction() as sess:
        csrf_tok = sess.get("csrf_token")
    if not csrf_tok:
        print("\nFATAL ERROR: CSRF token was not generated in session.")
        del p1, p2
        sys.exit(1)

    # F.2 Login with newly set password (exact 302 and parsed URL-path validation)
    login_res = client.post(
        "/login",
        data={"username": "manual_test_admin", "password": p1, "csrf_token": csrf_tok, "next": "/dashboard"},
    )
    if login_res.status_code != 302:
        print(f"\nFATAL ERROR: POST /login returned unexpected HTTP status {login_res.status_code} (expected 302).")
        del p1, p2
        sys.exit(1)

    login_dest = login_res.headers.get("Location", "")
    is_valid_login_dest, login_err = validate_redirect_path(login_dest, "/dashboard")
    if not is_valid_login_dest:
        print(f"\nFATAL ERROR: POST /login redirect validation failed: {login_err} (raw location: '{login_dest}').")
        del p1, p2
        sys.exit(1)

    print(f"1. Login with new password: SUCCESS (HTTP {login_res.status_code} redirecting to {login_dest})")

    # F.3 Access /dashboard as authenticated administrator (exact 200)
    dash_res = client.get("/dashboard")
    if dash_res.status_code != 200:
        print(f"\nFATAL ERROR: Authenticated GET /dashboard returned HTTP status {dash_res.status_code} (expected 200).")
        del p1, p2
        sys.exit(1)

    print(f"2. Authenticated access to /dashboard: SUCCESS (HTTP {dash_res.status_code})")

    # F.4 Logout with CSRF token (exact 302 and parsed URL-path validation)
    with client.session_transaction() as sess:
        logout_csrf = sess.get("csrf_token")
    if not logout_csrf:
        print("\nFATAL ERROR: Logout CSRF token missing from session.")
        del p1, p2
        sys.exit(1)

    logout_res = client.post("/logout", data={"csrf_token": logout_csrf})
    if logout_res.status_code != 302:
        print(f"\nFATAL ERROR: POST /logout returned unexpected HTTP status {logout_res.status_code} (expected 302).")
        del p1, p2
        sys.exit(1)

    logout_dest = logout_res.headers.get("Location", "")
    is_valid_logout_dest, logout_err = validate_redirect_path(logout_dest, "/login")
    if not is_valid_logout_dest:
        print(f"\nFATAL ERROR: POST /logout redirect validation failed: {logout_err} (raw location: '{logout_dest}').")
        del p1, p2
        sys.exit(1)

    print(f"3. CSRF-protected logout: SUCCESS (HTTP {logout_res.status_code} redirecting to {logout_dest})")

    # F.5 Access /dashboard after logout redirects to /login (exact 302 and parsed URL-path validation)
    post_logout_res = client.get("/dashboard")
    if post_logout_res.status_code != 302:
        print(f"\nFATAL ERROR: Post-logout GET /dashboard returned HTTP status {post_logout_res.status_code} (expected 302).")
        del p1, p2
        sys.exit(1)

    post_logout_dest = post_logout_res.headers.get("Location", "")
    is_valid_post_dest, post_err = validate_redirect_path(post_logout_dest, "/login")
    if not is_valid_post_dest:
        print(f"\nFATAL ERROR: Post-logout GET /dashboard redirect validation failed: {post_err} (raw location: '{post_logout_dest}').")
        del p1, p2
        sys.exit(1)

    print(f"4. Post-logout access redirect: SUCCESS (HTTP {post_logout_res.status_code} redirecting to {post_logout_dest})")

    # Best-effort reference cleanup (Note: Python strings are immutable and managed
    # by runtime garbage collection; 'del' removes references but cannot guarantee
    # immediate physical memory zeroization).
    del p1, p2

    print("-" * 65)
    print("SUCCESS: Stage 9.7B.2A password reset and smoke tests completed.")
    print("Integrity Check: " + integrity_result)
    print("Target DB: database/manual_test/attendance_manual_test.db")
    print("Original DB: 100% UNTOUCHED")
    print(f"Table row counts: {counts}")
    print("=" * 65)


if __name__ == "__main__":
    main()
