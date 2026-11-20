"""Phase 4 test: the whole Google Sheets sync against an in-memory sheet and a temporary database.
No Google account, no internet, your real database and folders are not touched.

Run:  .venv\\Scripts\\python.exe -m tests.test_sheets
"""

import tempfile
from pathlib import Path

import core.db as dbmod
import google_sheets.sync as S
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets.client import MemorySheet


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")          # temp database for this thread
    S.READY_DIR = tmp / "ready"                   # temp folders
    S.PROC_DIR = tmp / "proc"
    import core.outputs as outs
    outs.READY = S.READY_DIR
    outs.POSTED = tmp / "posted"
    db = dbmod.get_db()
    st = load_settings()
    io = MemorySheet()

    # 1. first sync creates the 7 tabs, Settings filled from settings.json
    rep = S.sync(io, st)
    assert set(io.tabs()) == set(T.TABS), io.tabs()
    keys = [r[0] for r in io.read(T.SETTINGS)[1:]]
    assert "manual_approval" in keys and "posting_times" in keys

    # 2. you paste links in Video Input (one good, one duplicate, one bad)
    vi = io.read(T.VIDEO_INPUT)
    vi += [["https://www.youtube.com/watch?v=lcetk9qIzrA", "5", "", "0:20:00-0:32:00"],
           ["https://youtu.be/lcetk9qIzrA"],
           ["https://example.com/not-a-video"]]
    io.write_all(T.VIDEO_INPUT, vi)
    # and change a setting in the Settings tab
    srows = io.read(T.SETTINGS)
    for r in srows:
        if r[0] == "min_score":
            r[1] = "75"
        if r[0] == "manual_approval":       # this test checks the manual flow (Phase 5 test: automatic)
            r[1] = "yes"
    io.write_all(T.SETTINGS, srows)
    rep = S.sync(io, st)
    assert rep["new_videos"] == 1, rep
    assert st["min_score"] == 75, "Settings tab must override settings.json"
    v = db.get_video("lcetk9qIzrA")
    assert v and v["clips_wanted"] == 5 and v["section"] == "0:20:00-0:32:00" and v["status"] == "new"
    vi = io.read(T.VIDEO_INPUT)
    h = {x: i for i, x in enumerate(vi[0])}
    assert vi[1][h["Video ID"]] == "lcetk9qIzrA" and vi[1][h["Status"]] == "new"
    assert vi[2][h["Status"]] == "duplicate"
    assert vi[3][h["Status"]] == "failed"
    assert db.videos().__len__() == 1, "duplicates / bad links must not be queued"
    assert any(r[0] == "lcetk9qIzrA" for r in io.read(T.PROCESSING)[1:]), "queued video shows in Processing"

    # 3. the farm made two clips → they appear in Ready to Post
    c1 = db.add_clip("lcetk9qIzrA", 0, 10, 50, score=90, kind="funny")
    c2 = db.add_clip("lcetk9qIzrA", 1, 60, 95, score=80, kind="reaction")
    db.update_video("lcetk9qIzrA", status="done")
    for c, t in ((c1, "2030-11-18 12:30"), (c2, "2030-11-18 18:00")):
        db.update_clip(c, status="ready", post_time=t, title=f"title {c}", hook=f"hook {c}")
    S.sync(io, st)
    rt = io.read(T.READY)
    h = {x: i for i, x in enumerate(rt[0])}
    assert [r[h["Clip ID"]] for r in rt[1:]] == [c1, c2]
    assert rt[1][h["Status"]] == "waiting approval"

    # 4. you approve clip 1, reject clip 2... no: remake clip 2, and move clip 1 to another time
    rt[1][h["Approve"]] = "yes"
    rt[1][h["Post Time"]] = "2030-11-19 21:00"
    rt[2][h["Approve"]] = "remake"
    io.write_all(T.READY, rt)
    rep = S.sync(io, st)
    assert db.get_clip(c1)["status"] == "approved" and db.get_clip(c1)["post_time"] == "2030-11-19 21:00"
    assert db.get_clip(c2)["status"] == "remake"
    sched = io.read(T.SCHEDULE)
    assert [(r[2], r[4]) for r in sched[1:]] == [(c1, "Instagram + YouTube")], sched
    rt = io.read(T.READY)
    assert rt[2][h["Status"]] == "remaking…" or rt[1][h["Status"]] == "remaking…"

    # 5. a bad time is refused with a note; "no" rejects
    db.update_clip(c2, status="ready")
    S.sync(io, st)
    rt = io.read(T.READY)
    row2 = next(r for r in rt[1:] if r[h["Clip ID"]] == c2)
    row2[h["Post Time"]] = "tomorrow evening"
    io.write_all(T.READY, rt)
    S.sync(io, st)
    row2 = next(r for r in io.read(T.READY)[1:] if r[h["Clip ID"]] == c2)
    assert "must look like" in row2[h["Notes"]], row2
    assert db.get_clip(c2)["post_time"] == "2030-11-18 18:00"
    rt = io.read(T.READY)
    next(r for r in rt[1:] if r[h["Clip ID"]] == c2)[h["Approve"]] = "no"
    io.write_all(T.READY, rt)
    S.sync(io, st)
    assert db.get_clip(c2)["status"] == "rejected"
    assert [r[h["Clip ID"]] for r in io.read(T.READY)[1:]] == [c1], "rejected clips leave Ready to Post"

    # 6. a failed video shows in Failed Tasks; typing retry re-queues it
    db.update_video("lcetk9qIzrA", status="failed", last_error="HTTP 403")
    S.sync(io, st)
    assert any("HTTP 403" in r[3] for r in io.read(T.FAILED)[1:])
    vi = io.read(T.VIDEO_INPUT)
    hv = {x: i for i, x in enumerate(vi[0])}
    vi[1][hv["Status"]] = "retry"
    io.write_all(T.VIDEO_INPUT, vi)
    S.sync(io, st)
    assert db.get_video("lcetk9qIzrA")["status"] == "retry"

    print(f"all sheets tests passed ({io.writes} writes for 7 syncs)")


if __name__ == "__main__":
    main()
