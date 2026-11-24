"""Ready to Post → Action (skip / post next / post last / move up / move down) and the Skipped tab.
Temp database + temp folders + in-memory sheet; your real clips and Sheet are not touched.

Run:  .venv\\Scripts\\python.exe -m tests.test_skip
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import core.db as dbmod
import core.outputs as outs
import google_sheets.sync as S
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets.client import MemorySheet


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    outs.READY = S.READY_DIR = tmp / "ready"
    outs.POSTED = tmp / "posted"
    S.PROC_DIR = tmp / "proc"
    db = dbmod.get_db()
    db.add_video("vid00000001", "https://youtu.be/vid00000001")
    st = load_settings()
    st.update(manual_approval=False, posting_times=["10:00", "18:00"], posts_per_day=2, posting_enabled=False)
    ids = []
    for i in range(5):
        cid = db.add_clip("vid00000001", i, i * 60, i * 60 + 40, score=90 - i, kind="funny")
        folder = outs.READY / cid
        folder.mkdir(parents=True)
        (folder / f"{cid}.mp4").write_bytes(b"\x00" * 100)
        (folder / "post.json").write_text(json.dumps({"platforms": ["instagram", "youtube"],
                                                      "instagram_caption": f"caption {cid}",
                                                      "hashtags": ["#gta6", "#speed"]}), encoding="utf-8")
        db.update_clip(cid, status="ready", folder=str(folder), title=f"title {cid}")
        ids.append(cid)
    io = MemorySheet()
    S.sync(io, st)
    a, b, c, d, e = ids
    times = {x: db.get_clip(x)["post_time"] for x in ids}
    slots = sorted(times.values())
    order = lambda: [x["clip_id"] for x in sorted(db.clips(None, "approved"), key=lambda x: x["post_time"])]  # noqa: E731
    assert order() == ids, order()                                     # best score first

    def act(cid, what):
        rows = io.read(T.READY)
        h = {x: i for i, x in enumerate(rows[0])}
        assert rows[0][4] == "Action", rows[0]
        next(r for r in rows[1:] if r[h["Clip ID"]] == cid)[h["Action"]] = what
        io.write_all(T.READY, rows)
        S.sync(io, st)

    # 1. skip b: it leaves the queue, everyone after b moves one time EARLIER, no gap; posts cancelled
    act(b, "skip")
    assert db.get_clip(b)["status"] == "skipped"
    assert order() == [a, c, d, e] and [db.get_clip(x)["post_time"] for x in order()] == slots[:4], order()
    assert {p["status"] for p in db.posts() if p["clip_id"] == b} == {"cancelled"}
    assert {p["scheduled_at"] for p in db.posts() if p["clip_id"] == c and p["status"] == "scheduled"} == {slots[1]}
    sk = io.read(T.SKIPPED)
    hs = {x: i for i, x in enumerate(sk[0])}
    assert [r[hs["Clip ID"]] for r in sk[1:]] == [b] and sk[1][hs["Instagram Caption"]] == f"caption {b}"
    assert sk[1][hs["Hashtags"]] == "#gta6 #speed"
    assert b not in [r[0] for r in io.read(T.READY)[1:]], "skipped clip leaves Ready to Post"
    assert all(r[4] == "" for r in io.read(T.READY)[1:]), "the farm clears Action after acting"

    # 2. post next / post last / move down: same four times, other order
    act(e, "post next")
    assert order() == [e, a, c, d]
    act(e, "post last")
    assert order() == [a, c, d, e]
    act(a, "move down")
    assert order() == [c, a, d, e] and [db.get_clip(x)["post_time"] for x in order()] == slots[:4]

    # 3. back from Skipped: last row, the first free time AFTER the last one
    sk = io.read(T.SKIPPED)
    sk[1][hs["Ready to Post"]] = "yes"
    io.write_all(T.SKIPPED, sk)
    S.sync(io, st)
    assert db.get_clip(b)["status"] == "approved" and order()[-1] == b
    assert db.get_clip(b)["post_time"] > slots[3], (db.get_clip(b)["post_time"], slots)
    assert {p["status"] for p in db.posts() if p["clip_id"] == b} == {"scheduled"}
    assert len(io.read(T.SKIPPED)) == 1, "the Skipped tab is empty again"
    assert [r[0] for r in io.read(T.READY)[1:]][-1] == b, "back at the last row of Ready to Post"

    # 4. a clip about to post (within 15 min) can't be moved: note, nothing changes
    soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    db.update_clip(c, post_time=soon)
    act(c, "skip")
    assert db.get_clip(c)["status"] == "approved"
    row = next(r for r in io.read(T.READY)[1:] if r[0] == c)
    assert "can't be moved" in row[7], row
    print("test_skip: all checks passed")


if __name__ == "__main__":
    main()
