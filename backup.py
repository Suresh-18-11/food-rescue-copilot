"""Create an SQLite-consistent backup. Run on the host that owns the disk."""
import argparse
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

parser = argparse.ArgumentParser()
parser.add_argument("destination", help="New backup file path (must not already exist)")
args = parser.parse_args()
source = Path(os.getenv("DATA_DIR", "./data")) / "food_rescue_v1.db"
destination = Path(args.destination)
if not source.exists() or destination.exists() or source.resolve() == destination.resolve():
    raise SystemExit("Source missing, destination exists, or source/destination are identical.")
destination.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
    src.backup(dst)
print("Backup created:", destination)
