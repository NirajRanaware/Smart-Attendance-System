"""
Database Inspection and Verification Script.
Checks SQLite tables, columns, indexes, foreign keys, and constraints.
"""

import sys
from pathlib import Path

# Add project root to sys.path so we can import from app
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import get_config
from app.database import get_db_connection, close_db_connection


def inspect_database():
    config = get_config("development")
    db_path = config.DATABASE_PATH
    
    print("=" * 60)
    print("SMART ATTENDANCE SYSTEM - DATABASE INSPECTION")
    print("=" * 60)
    print(f"Database Location: {db_path}")
    print(f"File Exists:       {db_path.exists()}")
    if db_path.exists():
        print(f"File Size:         {db_path.stat().st_size} bytes")
    print("-" * 60)
    
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        
        # 1. Verify Foreign Keys
        cursor.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        print(f"Foreign Key Enforcement: {'ENABLED (1)' if fk_status == 1 else 'DISABLED (0)'}")
        assert fk_status == 1, "Foreign keys must be enabled!"
        
        # 2. Check Tables
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%' 
            ORDER BY name;
        """)
        tables = [row["name"] for row in cursor.fetchall()]
        print(f"\nDiscovered Tables ({len(tables)}): {', '.join(tables)}")
        
        expected_tables = ["attendance", "class_sessions", "students"]
        missing_tables = [t for t in expected_tables if t not in tables]
        if missing_tables:
            print(f"ERROR: Missing expected tables: {missing_tables}")
            return False
        
        # 3. Inspect Schema of Each Table
        for table_name in expected_tables:
            print(f"\n--- Table Structure: '{table_name}' ---")
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            print(f"{'CID':<5} {'Column Name':<20} {'Data Type':<12} {'Not Null':<10} {'Default':<10} {'PK':<5}")
            print("-" * 65)
            for col in columns:
                print(f"{col['cid']:<5} {col['name']:<20} {col['type']:<12} {str(bool(col['notnull'])):<10} {str(col['dflt_value']):<10} {str(bool(col['pk'])):<5}")
        
        # 4. Check Foreign Keys on attendance table
        print("\n--- Foreign Keys for 'attendance' ---")
        cursor.execute("PRAGMA foreign_key_list(attendance);")
        fks = cursor.fetchall()
        for fk in fks:
            print(f"Foreign Key: attendance.{fk['from']} -> {fk['table']}.{fk['to']} (ON DELETE {fk['on_delete']})")
        
        # 5. Check UNIQUE constraint and indexes on attendance
        print("\n--- Indexes & Constraints for 'attendance' ---")
        cursor.execute("PRAGMA index_list(attendance);")
        indexes = cursor.fetchall()
        has_unique_attendance = False
        for idx in indexes:
            unique_flag = "UNIQUE" if idx["unique"] else "NON-UNIQUE"
            print(f"Index: {idx['name']} [{unique_flag}]")
            cursor.execute(f"PRAGMA index_info({idx['name']});")
            idx_cols = [col["name"] for col in cursor.fetchall()]
            print(f"       Columns: {', '.join(idx_cols)}")
            if idx["unique"] and set(idx_cols) == {"student_id", "session_id"}:
                has_unique_attendance = True
        
        print("-" * 60)
        if has_unique_attendance:
            print("SUCCESS: UNIQUE(student_id, session_id) constraint verified!")
        else:
            print("WARNING: UNIQUE(student_id, session_id) constraint not found!")
            return False
            
        print("=" * 60)
        print("ALL DATABASE CHECKS PASSED SUCCESSFULLY!")
        print("=" * 60)
        return True
        
    finally:
        close_db_connection(conn)


if __name__ == "__main__":
    success = inspect_database()
    if not success:
        sys.exit(1)
