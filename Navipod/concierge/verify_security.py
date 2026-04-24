import sys
import uuid
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

def test_blacklist_logic():
    import auth
    import database

    print("[TEST] Verifying TokenBlacklist model...")
    
    # Ensure table exists (it should have been created by app startup, but we force it here just in case)
    database.Base.metadata.create_all(bind=database.engine)
    
    db = database.SessionLocal()
    try:
        token = f"test-token-{uuid.uuid4()}"
        
        print(f"[TEST] Blacklisting token: {token}")
        auth.blacklist_token(db, token)
        
        print("[TEST] Checking if token is blacklisted...")
        if auth.is_token_blacklisted(db, token):
            print("[PASS] Token successfully blacklisted.")
        else:
            print("[FAIL] Token NOT found in blacklist.")
            return False
            
        # Test non-existent token
        if not auth.is_token_blacklisted(db, "non-existent-token"):
             print("[PASS] Non-existent token correctly identified.")
        else:
             print("[FAIL] Non-existent token reported as blacklisted.")
             return False
             
        # Cleanup
        db.query(database.TokenBlacklist).filter(database.TokenBlacklist.token == token).delete()
        db.commit()
        
    except Exception as e:
        print(f"[ERROR] {e}")
        return False
    finally:
        db.close()
        
    return True

if __name__ == "__main__":
    if test_blacklist_logic():
        print("✅ Security verification passed.")
        sys.exit(0)
    else:
        print("❌ Security verification failed.")
        sys.exit(1)
