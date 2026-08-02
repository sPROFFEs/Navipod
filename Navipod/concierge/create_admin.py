"""Non-interactive, injection-safe initial admin creation command."""

import os

import auth
import database


def main() -> None:
    username = os.environ.get("NAVIPOD_ADMIN_USERNAME", "").strip()
    password = os.environ.get("NAVIPOD_ADMIN_PASSWORD", "")
    if not auth.is_valid_username(username):
        raise SystemExit("Invalid admin username")
    if not auth.is_password_strong(password):
        raise SystemExit("Admin password does not meet the password policy")

    with database.SessionLocal() as db:
        if auth.get_user_by_username(db, username):
            raise SystemExit(f"User {username} already exists")
        user = auth.create_user_in_db(db, username, password)
        user.is_admin = True
        if not user.download_settings:
            user.download_settings = database.DownloadSettings(audio_quality="320")
        if not db.query(database.SystemSettings).first():
            db.add(database.SystemSettings(pool_limit_gb=100))
        db.commit()
    print(f"Admin {username} created successfully")


if __name__ == "__main__":
    main()
