"""Settings tab → new posting_times: every waiting clip moves onto the new times, same order (2026-11-28).
Temp database; nothing real is touched.

Run:  .venv\\Scripts\\python.exe -m tests.test_retime
"""

import tempfile
from pathlib import Path

import core.db as dbmod
import core.paths as paths
from config.settings import load_settings
from core.db import DB
from posting import queue
from posting.slots import assign_post_times


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    paths.DATA_DIR = tmp
    db = dbmod.get_db()
    db.add_video("vid00000001", "https://youtu.be/vid00000001")
    st = load_settings()
    st.update(manual_approval=False, posting_times=["10:00", "18:00", "20:00"], posts_per_day=3)
    ids = []
    for i in range(5):
        cid = db.add_clip("vid00000001", i, i * 60, i * 60 + 40, score=90 - i)
        db.update_clip(cid, status="approved")
        ids.append(cid)
    assign_post_times(st)
    assert queue.retime_on_new_slots(st) == {}, "first run only remembers the times"
    assert queue.retime_on_new_slots(st) == {}, "same times: nothing moves"
    st.update(posting_times=["09:00", "21:00"], posts_per_day=5)
    queue.retime_on_new_slots(st)
    got = [db.get_clip(x)["post_time"] for x in ids]
    assert got == sorted(got), "same order"
    assert {t[11:] for t in got} <= {"09:00", "21:00"}, got
    db.update_clip(ids[0], post_time=got[0][:11] + "21:30")              # you typed your own time for one clip
    assert queue.retime_on_new_slots(st) == {}, "a typed time is not undone"
    print("test_retime: all checks passed")


if __name__ == "__main__":
    main()
