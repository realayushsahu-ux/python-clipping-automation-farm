"""Phase 5 test: ready → approved → scheduled → posted, folders, Sheet links, never twice.
Temp database + temp folders + in-memory sheet; your real clips, folders and Sheet are not touched.

Run:  .venv\\Scripts\\python.exe -m tests.test_queue
"""

import json
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import core.db as dbmod
import core.outputs as outs
import google_sheets.sync as S
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets.client import Link, MemorySheet
from posting import queue


def make_clip(db, tmp, idx, score, post_time):
    cid = db.add_clip("vid00000001", idx, idx * 60, idx * 60 + 40, score=score, kind="funny")
    folder = outs.READY / cid
    folder.mkdir(parents=True)
    (folder / f"{cid}.mp4").write_bytes(b"\x00" * 5000)
    (folder / "post.json").write_text(json.dumps({"platforms": ["instagram", "youtube"], "hashtags": []}),
                                      encoding="utf-8")
    db.update_clip(cid, status="ready", post_time=post_time, folder=str(folder), title=f"title {cid}")
    return cid


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    outs.READY = S.READY_DIR = tmp / "ready"
    outs.POSTED = tmp / "posted"
    S.PROC_DIR = tmp / "proc"
    import core.clip_server as cs
    cs.READY, cs.POSTED, cs.GENERATED, cs.FAILED = outs.READY, outs.POSTED, tmp / "gen", tmp / "failed"
    db = dbmod.get_db()
    db.add_video("vid00000001", "https://youtu.be/vid00000001")
    st = load_settings()
    st["links_port"] = 18765
    assert st["manual_approval"] is False, "default: every clip approved automatically"
    io = MemorySheet()

    future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d") + " 18:00"
    past = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    a = make_clip(db, tmp, 0, 90, future)
    b = make_clip(db, tmp, 1, 80, past)          # farm was off: its slot passed
    c = make_clip(db, tmp, 2, 70, None)

    # 1. auto approval: all ready clips approved, missed slot moved, one post per platform
    S.sync(io, st)
    for cid in (a, b, c):
        assert db.get_clip(cid)["status"] == "approved", cid
        assert db.get_clip(cid)["post_time"], cid
    assert db.get_clip(b)["post_time"] > past, "missed time must move to a future slot"
    posts = db.posts()
    assert len(posts) == 6 and {p["status"] for p in posts} == {"scheduled"}
    assert all(p["scheduled_at"] == db.get_clip(p["clip_id"])["post_time"] for p in posts)
    rt = io.read(T.READY)
    h = {x: i for i, x in enumerate(rt[0])}
    assert rt[0][:7] == ["Clip ID", "Watch", "YouTube Title", "Post Time", "Action", "Approve", "Status"], rt[0]
    assert all(r[h["Approve"]] == "yes" and r[h["Status"]].startswith("scheduled") for r in rt[1:]), rt
    assert rt[1][h["Watch"]].startswith("▶ open CLIP")
    assert len(io.read(T.SCHEDULE)) == 4  # one row per clip

    # 2. running again changes nothing (no duplicate posts)
    S.sync(io, st)
    queue.step(st)
    assert len(db.posts()) == 6

    # 3. you move a's time → its posts move; you type no on c → its posts are cancelled
    rt = io.read(T.READY)
    for r in rt[1:]:
        if r[h["Clip ID"]] == a:
            r[h["Post Time"]] = "2030-01-02 12:30"
        if r[h["Clip ID"]] == c:
            r[h["Approve"]] = "no"
    io.write_all(T.READY, rt)
    S.sync(io, st)
    assert {p["scheduled_at"] for p in db.posts() if p["clip_id"] == a} == {"2030-01-02 12:30"}
    assert db.get_clip(c)["status"] == "rejected"
    assert {p["status"] for p in db.posts() if p["clip_id"] == c} == {"cancelled"}
    assert not (outs.READY / c).exists(), "rejected folder leaves ready_to_post"

    # 4. Phase 6 publishes a on YouTube only → "posting (1/2)"; then Instagram → posted, folder moves
    pa = {p["platform"]: p for p in db.posts() if p["clip_id"] == a}
    queue.record_posted(pa["youtube"]["id"], "yt123", "https://youtube.com/shorts/yt123")
    assert db.get_clip(a)["status"] == "posting" and (outs.READY / a).exists()
    S.sync(io, st)
    row = next(r for r in io.read(T.READY)[1:] if r[h["Clip ID"]] == a)
    assert row[h["Status"]] == "posting (1/2 done)", row
    queue.record_posted(pa["instagram"]["id"], "ig456", "https://instagram.com/reel/ig456")
    queue.record_posted(pa["instagram"]["id"], "ig456", "https://instagram.com/reel/ig456")   # twice: no harm
    ca = db.get_clip(a)
    assert ca["status"] == "posted" and not (outs.READY / a).exists() and (outs.POSTED / a / f"{a}.mp4").exists()
    assert "youtube.com/shorts/yt123" in (outs.POSTED / a / "post_info.txt").read_text(encoding="utf-8")
    assert not db.claim_post(pa["youtube"]["id"]), "a posted post can never be claimed again"

    # 5. you posted b yourself: type "posted" → all platforms posted, folder moved, paste the URL later
    rt = io.read(T.READY)
    next(r for r in rt[1:] if r[h["Clip ID"]] == b)[h["Approve"]] = "posted"
    io.write_all(T.READY, rt)
    S.sync(io, st)
    assert db.get_clip(b)["status"] == "posted" and (outs.POSTED / b).exists()
    pr = io.read(T.POSTED)
    hp = {x: i for i, x in enumerate(pr[0])}
    assert {(r[hp["Clip ID"]], r[hp["Platform"]]) for r in pr[1:]} == {(a, "youtube"), (a, "instagram"),
                                                                        (b, "youtube"), (b, "instagram")}
    for r in pr[1:]:
        if r[hp["Clip ID"]] == b and r[hp["Platform"]] == "youtube":
            r[hp["Post URL"]] = "https://youtube.com/shorts/byhand"
    io.write_all(T.POSTED, pr)
    S.sync(io, st)
    assert any(p["url"] == "https://youtube.com/shorts/byhand" for p in db.posts("posted"))
    assert [r[h["Clip ID"]] for r in io.read(T.READY)[1:]] == [], "posted clips leave Ready to Post"
    try:
        queue.mark_posted_by_hand(c, st)
        raise AssertionError("a rejected clip can't be marked posted")
    except ValueError:
        pass

    # 6. manual approval on: new clips wait for your yes, no posts until then
    srows = io.read(T.SETTINGS)
    next(r for r in srows if r[0] == "manual_approval")[1] = "yes"     # you switch it on in the Settings tab
    io.write_all(T.SETTINGS, srows)
    S.sync(io, st)
    assert st["manual_approval"] is True
    d = make_clip(db, tmp, 3, 85, future)
    S.sync(io, st)
    assert db.get_clip(d)["status"] == "ready" and not [p for p in db.posts() if p["clip_id"] == d]

    # 7. the Folder link: serves the page + video (Range), opens nothing on non-Windows
    lk = S.folder_link(st, d)
    assert isinstance(lk, Link) and lk.url == f"http://localhost:18765/clip/{d}"
    assert lk.formula().startswith('=HYPERLINK("http://localhost:18765/clip/')
    cs._last_open[d] = 9e18                    # don't pop up Explorer during the test
    srv = cs.start(st)
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:18765/clip/{d}").read().decode()
        assert f'src="/video/{d}"' in page
        req = urllib.request.Request(f"http://127.0.0.1:18765/video/{d}", headers={"Range": "bytes=100-199"})
        r = urllib.request.urlopen(req)
        assert r.status == 206 and len(r.read()) == 100
        try:
            urllib.request.urlopen("http://127.0.0.1:18765/clip/..%2F..%2Fsecrets")
            raise AssertionError("bad clip id must be refused")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
    print(f"all posting-queue tests passed ({len(db.posts())} posts, {io.writes} sheet writes)")


if __name__ == "__main__":
    threading.current_thread().name = "test"
    main()
