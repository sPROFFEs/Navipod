import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("NAVIPOD_PUBLIC_ORIGIN", "https://navipod.example.test")
os.environ.setdefault(
    "IMPORTER_FERNET_KEY",
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
)
