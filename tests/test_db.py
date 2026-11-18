"""Run:  .venv\\Scripts\\python.exe -m tests.test_db
Checks the safety rules: no duplicate videos/clips, unique clip IDs, never two posts."""

import tempfile
from pathlib import Path

from core.db import DB


def main():
    db = DB(Path(tempfile.mkdtemp()) / "t.db")
    assert db.add_video("abc", "u") is True
    assert db.add_video("abc", "u") is False, "same video twice must be refused"
    c1 = db.add_clip("abc", 0, 1.0, 30.0)
    c2 = db.add_clip("abc", 1, 40.0, 70.0)
    assert db.add_clip("abc", 0, 1.0, 30.0) == c1, "same clip twice must return the same ID"
    assert c1 != c2 and c1.startswith("CLIP")
    assert db.schedule_post(c1, "instagram", "2026-11-18T18:00") is True
    assert db.schedule_post(c1, "instagram", "2026-11-19T18:00") is False, "second post must be refused"
    pid = db.posts()[0]["id"]
    assert db.claim_post(pid) is True
    assert db.claim_post(pid) is False, "a post being published cannot be claimed again"
    db.update_post(pid, status="posted")
    assert db.claim_post(pid) is False, "a posted clip can never be published again"
    print("all safety tests passed")


if __name__ == "__main__":
    main()
