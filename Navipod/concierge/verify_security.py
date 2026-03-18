import sys
import os

# Add local directory to path
sys.path.append("/media/sdksdk/HardDisk/Github/Navipod/Navipod/concierge")

from database import SessionLocal, TokenBlacklist, engine, Base
from auth import blacklist_token, is_token_blacklisted
import uuid

def test_blacklist_logic():
    print("[TEST] Verifying TokenBlacklist model...")
    
    # Ensure table exists (it should have been created by app startup, but we force it here just in case)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        token = f"test-token-{uuid.uuid4()}"
        
        print(f"[TEST] Blacklisting token: {token}")
        blacklist_token(db, token)
        
        print("[TEST] Checking if token is blacklisted...")
        if is_token_blacklisted(db, token):
            print("[PASS] Token successfully blacklisted.")
        else:
            print("[FAIL] Token NOT found in blacklist.")
            return False
            
        # Test non-existent token
        if not is_token_blacklisted(db, "non-existent-token"):
             print("[PASS] Non-existent token correctly identified.")
        else:
             print("[FAIL] Non-existent token reported as blacklisted.")
             return False
             
        # Cleanup
        db.query(TokenBlacklist).filter(TokenBlacklist.token == token).delete()
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
