"""A damaged database is put aside and the newest good backup put back; big logs are rotated.
Temp folders only; your real database is not touched.

Run:  .venv\\Scripts\\python.exe -m tests.test_safety
"""

import shutil
import sqlite3
import tempfile
from pathlib import Path

import core.safety as S


def main():
    tmp = Path(tempfile.mkdtemp())
    S.BACKUPS = tmp / "backups"
    S.BACKUPS.mkdir()
    db = tmp / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table t(x)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    assert S.check_db(db) == "ok"
    shutil.copy(db, S.BACKUPS / "state_2026-11-20.db")
    (S.BACKUPS / "state_2026-11-21.db").write_bytes(b"broken backup")        # newest backup is bad too
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 5000)                    # power cut mid-write
    r = S.check_db(db)
    assert r == "restored state_2026-11-20.db", r
    assert sqlite3.connect(db).execute("select x from t").fetchone() == (1,)
    assert list(tmp.glob("state_damaged_*.db")), "the damaged file is kept, never deleted"

    log = tmp / "watch_console.log"
    log.write_bytes(b"x" * 2_000_000)
    S.rotate(log, max_mb=1)
    assert not log.exists() and (tmp / "watch_console.log.1").exists()
    print("test_safety: all checks passed")


if __name__ == "__main__":
    main()
