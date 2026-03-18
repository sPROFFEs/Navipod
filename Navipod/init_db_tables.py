import sys
import os

# Ensure we can import from the directory
sys.path.append(os.getcwd())

from concierge import database

def init_tables():
    print("Initialising database tables...")
    try:
        database.Base.metadata.create_all(bind=database.engine)
        print("Tables created successfully.")
    except Exception as e:
        print(f"Error creating tables: {e}")

if __name__ == "__main__":
    init_tables()
