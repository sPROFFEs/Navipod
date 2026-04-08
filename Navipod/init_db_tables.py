import sys
import os

# Ensure we can import from the directory
sys.path.append(os.getcwd())

from concierge import operations_service

def init_tables():
    print("Initialising database schema...")
    try:
        applied = operations_service.apply_schema_migrations()
        if applied:
            print(f"Applied migrations: {', '.join(applied)}")
        else:
            print("Schema already up to date.")
    except Exception as e:
        print(f"Error initialising schema: {e}")

if __name__ == "__main__":
    init_tables()
