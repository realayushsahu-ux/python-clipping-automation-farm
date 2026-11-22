"""Database repair and log size, so the farm stays safe over weeks of running by itself (added 2026-11-21).

  check_db()    at every start: is the database whole? A damaged one (power cut while writing) is put aside,
                never deleted, and the newest good daily backup from data/backups is put back, so the farm
                starts normally instead of crashing on every start.
  rotate(path)  a log that only grows (logs/watch_console.log, the window's log) is moved to .1 once it
                passes 10 MB, so it can never fill the disk.
(Disk space, temp files and the settings backup are in core/service.py.)
"""

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.paths import DATA_DIR

log = get_logger("safety")
BACKUPS = DATA_DIR / "backups"


def _db_ok(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def check_db(db_file: Path = None) -> str:
    """'ok', 'restored <backup>' or 'damaged, no backup' (the farm then starts a fresh database)."""
    from core.paths import DB_FILE
    db_file = db_file or DB_FILE
    if not db_file.exists() or _db_ok(db_file):
        return "ok"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    aside = db_file.with_name(f"state_damaged_{stamp}.db")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_file) + suffix)
        if p.exists():
            shutil.move(str(p), str(aside) + suffix)
    for b in sorted(BACKUPS.glob("state_*.db"), reverse=True):
        if _db_ok(b):
            shutil.copy2(b, db_file)
            log.error(f"the database was damaged (kept as {aside.name}); put back the backup {b.name}")
            return f"restored {b.name}"
    log.error(f"the database was damaged (kept as {aside.name}) and no good backup was found")
    return "damaged, no backup"


def rotate(path: Path, max_mb=10):
    try:
        if path.exists() and path.stat().st_size > max_mb * 1e6:
            os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass
