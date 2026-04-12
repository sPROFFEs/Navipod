#!/bin/bash

# Navipod Setup Script
# --------------------

echo "Starting Navipod setup..."

# --- CONFIGURATION ---
DATA_ROOT="/opt/saas-data"
IMPORT_STAGE="$DATA_ROOT/import_stage"
CONFIG_ROOT="$DATA_ROOT/config"
CONFIG_ENV="$CONFIG_ROOT/navipod.env"

# 0. Check Dependencies
echo "Checking dependencies..."

if ! command -v docker &> /dev/null; then
    echo "Docker is not installed."
    echo "   Attempting to install Docker (Ubuntu/Debian)..."
    if [ -x "$(command -v apt-get)" ]; then
        sudo apt-get update
        sudo apt-get install -y docker.io docker-compose-plugin
        sudo usermod -aG docker "$USER"
        echo "Docker installed. You may need to relogin for group changes to take effect."
    else
        echo "Automatic installation failed. Please install Docker manually."
        exit 1
    fi
fi

if ! docker compose version &> /dev/null; then
    echo "'docker compose' (V2) is not available."
    echo "   Attempting to install docker-compose-plugin..."
    if [ -x "$(command -v apt-get)" ]; then
        sudo apt-get update
        sudo apt-get install -y docker-compose-plugin
    else
        echo "Please install 'docker-compose-plugin' manually."
        exit 1
    fi
fi

# 1. Create Data Directories
echo "Creating data directories in $DATA_ROOT..."
sudo mkdir -p "$DATA_ROOT/pool"
sudo mkdir -p "$DATA_ROOT/cache"
sudo mkdir -p "$DATA_ROOT/users"
sudo mkdir -p "$DATA_ROOT/backups"
sudo mkdir -p "$CONFIG_ROOT"
sudo mkdir -p "$IMPORT_STAGE"
sudo chown -R "$USER:$USER" "$DATA_ROOT"
sudo chmod -R 777 "$DATA_ROOT"
sudo chmod -R 777 "$IMPORT_STAGE"

# 2. Setup Environment Variables
if [ ! -f "$CONFIG_ENV" ]; then
    echo "Creating runtime config file at $CONFIG_ENV from .env.example..."
    sudo cp .env.example "$CONFIG_ENV"
    sudo chown "$USER:$USER" "$CONFIG_ENV"

    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        RANDOM_KEY=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | fold -w 32 | head -n 1)
        sed -i "s/generate_a_very_long_random_string_here/$RANDOM_KEY/g" "$CONFIG_ENV"
    fi

    echo "Action required: open $CONFIG_ENV and set your DOMAIN/TUNNEL_TOKEN if needed."
    echo "   (Press Enter to continue setup once you're ready, or Ctrl+C to stop)"
    read
else
    echo "Runtime config already exists at $CONFIG_ENV."
fi

if [ ! -e .env ]; then
    ln -s "$CONFIG_ENV" .env
fi

# 3. Pull & Start Containers
echo "Pulling and starting Docker containers..."
docker compose --env-file "$CONFIG_ENV" up -d --build

echo "Waiting 10s for database to initialize..."
sleep 10

# 4. Interactive: Create Admin
echo ""
echo "------------------------------------------------"
echo "ADMIN USER CREATION"
read -p "Do you want to create an Admin user now? (y/n): " CREATE_ADMIN
if [[ "$CREATE_ADMIN" == "y" || "$CREATE_ADMIN" == "Y" ]]; then
    read -p "Username: " ADMIN_USER
    read -s -p "Password: " ADMIN_PASS
    echo ""

    echo "Creating admin user in database..."

    docker compose --env-file "$CONFIG_ENV" exec -T concierge python -c "
import database, auth
try:
    db = database.SessionLocal()
    user = '$ADMIN_USER'
    pw = '$ADMIN_PASS'
    existing = db.query(database.User).filter(database.User.username == user).first()
    if existing:
        print(f'User {user} already exists.')
    else:
        hashed = auth.get_password_hash(pw)
        new_user = database.User(username=user, hashed_password=hashed, is_admin=True, is_active=True)
        db.add(new_user)
        db.flush()
        settings = database.DownloadSettings(user_id=new_user.id, audio_quality='320')
        db.add(settings)

        if not db.query(database.SystemSettings).first():
            db.add(database.SystemSettings(pool_limit_gb=100))

        db.commit()
        print(f'Admin {user} created successfully.')
except Exception as e:
    print(f'Error creating admin: {e}')
finally:
    db.close()
"
fi

# 5. Interactive: Import Music
echo ""
echo "------------------------------------------------"
echo "MUSIC LIBRARY IMPORT"
echo "Navipod can import your existing music library."
echo "Ideally, point to a folder structured like /Artist/Album/Song.mp3"
read -p "Do you want to import music now? (y/n): " IMPORT_MUSIC

if [[ "$IMPORT_MUSIC" == "y" || "$IMPORT_MUSIC" == "Y" ]]; then
    read -p "Enter full path to music folder on HOST: " SRC_PATH

    if [ -d "$SRC_PATH" ]; then
        echo "WARNING: This will MOVE files from the source to the internal pool to save space."
        echo "   The source folder will be emptied."
        read -p "Are you sure? (y/n): " CONFIRM_MOVE

        if [[ "$CONFIRM_MOVE" == "y" || "$CONFIRM_MOVE" == "Y" ]]; then
            echo "Moving music to staging area ($IMPORT_STAGE)..."
            mv "$SRC_PATH"/* "$IMPORT_STAGE/" 2>/dev/null || mv "$SRC_PATH"/.[!.]* "$IMPORT_STAGE/" 2>/dev/null

            echo "Running Importer Engine..."
            docker compose --env-file "$CONFIG_ENV" exec -T concierge python importer.py /saas-data/import_stage

            echo "Import process finished. Check logs for details."
        else
            echo "Import cancelled."
        fi
    else
        echo "Directory not found: $SRC_PATH"
    fi
fi

echo ""
echo "------------------------------------------------"
echo "Setup complete."
echo "Access Navipod at http://localhost (or your configured domain)."
echo "------------------------------------------------"
