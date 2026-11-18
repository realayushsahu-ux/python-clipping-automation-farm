"""Phase 6 test: publishing with FAKE platforms (no account, no internet, nothing is posted).

Checks: the safety switch, once-only claiming, retry with backoff, "maybe posted" never retried
automatically, stuck uploads, retry/posted from the Sheet, the YouTube request body, and the full
Instagram API call sequence against a fake server.

Run:  .venv\\Scripts\\python.exe -m tests.test_publish
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
from posting import instagram, publisher, queue, youtube_shorts
from posting.errors import Ambiguous, Permanent, Retryable

PKG = {"platforms": ["instagram", "youtube"], "youtube_title": "Speed's first GTA RP fail 😂",
       "youtube_description": "IShowSpeed tries GTA RP.", "instagram_caption": "bro really did that 💀",
       "hashtags": ["#gta5", "#ishowspeed", "gtarp"] + [f"#t{i}" for i in range(40)],
       "search_queries": ["ishowspeed gta rp"], "pin_comment": "would you have run?"}


def make_clip(db, idx, post_time):
    cid = db.add_clip("vid00000001", idx, idx * 60, idx * 60 + 40, score=90 - idx, kind="funny")
    folder = outs.READY / cid
    folder.mkdir(parents=True)
    (folder / f"{cid}.mp4").write_bytes(b"\x00" * 3000)
    (folder / "post.json").write_text(json.dumps(PKG), encoding="utf-8")
    db.update_clip(cid, status="ready", post_time=post_time, folder=str(folder), title=PKG["youtube_title"])
    return cid


class Fake:
    """Fake platform: behaves as told, counts calls."""

    def __init__(self, *plan):
        self.plan, self.calls = list(plan), 0

    def __call__(self, job, settings):
        self.calls += 1
        step = self.plan.pop(0) if self.plan else "ok"
        if isinstance(step, Exception):
            raise step
        assert Path(job["file"]).exists() and job["youtube_title"]
        return {"external_id": f"id{self.calls}", "url": f"https://x/{job['clip_id']}/{self.calls}", "note": ""}


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    outs.READY = S.READY_DIR = tmp / "ready"
    outs.POSTED = tmp / "posted"
    S.PROC_DIR = tmp / "proc"
    db = dbmod.get_db()
    db.add_video("vid00000001", "https://youtu.be/vid00000001")
    st = load_settings()
    assert st["posting_enabled"] is False, "posting must be OFF by default"
    io = MemorySheet()
    due = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    later = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d") + " 18:00"

    a = make_clip(db, 0, later)
    b = make_clip(db, 1, later)
    S.sync(io, st)
    assert [r[0] for r in io.read(T.SETTINGS)[1:3]] == ["posting_enabled", "youtube_privacy"]
    pa = {p["platform"]: p for p in db.posts() if p["clip_id"] == a}
    pb = {p["platform"]: p for p in db.posts() if p["clip_id"] == b}
    for cid in (a, b):          # their time is now (5 min ago; missed-slot move only after 15 min)
        db.update_clip(cid, post_time=due)
    queue.plan_posts(st)

    yt, ig = Fake("ok"), Fake(Retryable("network down", 10), "ok")
    publisher.PUBLISHERS = {"youtube": yt, "instagram": ig}

    # 1. switch off → nothing happens
    assert publisher.publish_due(st) == {"disabled": True} and yt.calls == ig.calls == 0
    S.sync(io, st)
    assert "posting off" in io.read(T.READY)[1][3]

    # 2. switch on (Settings tab) → due posts go out, IG fails once (retry later)
    srows = io.read(T.SETTINGS)
    next(r for r in srows if r[0] == "posting_enabled")[1] = "yes"
    io.write_all(T.SETTINGS, srows)
    S.sync(io, st)
    assert st["posting_enabled"] is True
    for p in db.posts():          # sync's missed-slot check would not touch them yet (inside grace)
        assert p["scheduled_at"] == due, p
    publisher.PUBLISHERS["youtube"] = Fake("ok", Permanent("video rejected: too long"))
    rep = publisher.publish_due(st)
    assert db.get_post(pa["youtube"]["id"])["status"] == "posted"
    ia = db.get_post(pa["instagram"]["id"])
    assert ia["status"] == "failed" and ia["next_try"] > datetime.now().isoformat(), ia
    assert db.get_post(pb["youtube"]["id"])["status"] == "needs_manual"
    assert db.get_clip(a)["status"] == "posting", "one platform done"
    assert db.get_clip(b)["status"] == "posting", "b: Instagram posted, YouTube needs you"

    # 3. running again right away: nothing is posted twice, the failed one waits for its next_try
    yt_calls = publisher.PUBLISHERS["youtube"].calls
    publisher.publish_due(st)
    assert publisher.PUBLISHERS["youtube"].calls == yt_calls, "posted / needs_manual are never re-run"
    assert db.get_post(pa["instagram"]["id"])["attempts"] == 1, "failed post waits for next_try"
    assert not db.claim_post(pa["youtube"]["id"]), "posted can never be claimed again"

    # 4. its wait is over → posted → whole clip posted, folder moved
    db.update_post(pa["instagram"]["id"], next_try="2000-01-01T00:00:00")
    publisher.publish_due(st)
    assert db.get_clip(a)["status"] == "posted" and (outs.POSTED / a / f"{a}.mp4").exists()

    # 5. "maybe posted" is never retried by itself; you type retry in the Sheet → it runs again
    c = make_clip(db, 2, later)
    queue.step(st)
    pc = {p["platform"]: p for p in db.posts() if p["clip_id"] == c}
    db.update_clip(c, post_time=due)
    queue.plan_posts(st)
    publisher.PUBLISHERS = {"youtube": Fake(Ambiguous("lost answer"), "ok"), "instagram": Fake("ok")}
    publisher.publish_due(st)
    q = db.get_post(pc["youtube"]["id"])
    assert q["status"] == "needs_manual" and "MAYBE POSTED" in q["last_error"]
    publisher.publish_due(st)
    assert publisher.PUBLISHERS["youtube"].calls == 1, "ambiguous must not be retried automatically"
    S.sync(io, st)
    rt = io.read(T.READY)
    h = {x: i for i, x in enumerate(rt[0])}
    row = next(r for r in rt[1:] if r[h["Clip ID"]] == c)
    assert row[h["Status"]] == "needs you (see Notes)" and "MAYBE POSTED" in row[h["Notes"]], row
    row[h["Approve"]] = "retry"
    io.write_all(T.READY, rt)
    S.sync(io, st)
    publisher.publish_due(st)
    assert db.get_clip(c)["status"] == "posted"

    # 6. b: you check, it was never posted on YouTube → type posted? no: retry; then an upload that
    #    gets stuck (farm killed) becomes needs_manual after posting_stuck_minutes
    publisher.retry_clip(b)
    db.claim_post(pb["youtube"]["id"])        # simulate: claimed, then the farm died
    db.update_post(pb["youtube"]["id"], claimed_at="2000-01-01T00:00:00")
    assert publisher.recover_stuck(st) == 1
    assert db.get_post(pb["youtube"]["id"])["status"] == "needs_manual"

    # 6b. PC was off at the post time and comes back 40 min later → not posted late, moved to the next time
    publisher.PUBLISHERS = {"youtube": Fake(), "instagram": Fake()}
    late = make_clip(db, 3, later)
    queue.step(st)
    db.update_clip(late, post_time=(datetime.now() - timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M"))
    queue.plan_posts(st)
    publisher.publish_due(st)
    assert publisher.PUBLISHERS["youtube"].calls == 0, "a late post must not go out at a random time"
    assert db.get_clip(late)["post_time"] > datetime.now().strftime("%Y-%m-%d %H:%M")
    assert all(p["scheduled_at"] == db.get_clip(late)["post_time"] for p in db.posts() if p["clip_id"] == late)

    # 7. YouTube request body: title ≤100, ≤15 hashtags in the description, tags ≤ 450 chars, Gaming
    body = youtube_shorts.build_body({**PKG, "clip_id": "CLIP9", "youtube_title": "x" * 150}, st)
    assert len(body["snippet"]["title"]) == 100 and body["snippet"]["categoryId"] == "20"
    assert body["snippet"]["description"].count("#") == 15
    assert sum(len(t) + 1 for t in body["snippet"]["tags"]) <= 450
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": False}
    cap = instagram.build_caption(PKG, st)
    assert cap.count("#") == 30 and cap.startswith("bro really")

    # 8. Instagram call sequence against a fake Graph API
    import os
    os.environ.update(INSTAGRAM_USER_ID="1789", INSTAGRAM_ACCESS_TOKEN="test-token",
                      INSTAGRAM_API_HOST="graph.facebook.com")
    video = outs.POSTED / a / f"{a}.mp4"
    sess = FakeGraph()
    out = instagram.publish({**PKG, "clip_id": a, "file": str(video)}, st, session=sess, sleep=lambda s: None,
                            prepare=lambda p: p)
    assert out == {"external_id": "M1", "url": "https://instagram.com/reel/abc", "note": "comment posted, pin it in the app"}
    assert [x[1].split("/")[-1] for x in sess.log] == ["media", "C1", "C1", "C1", "media_publish", "M1", "comments"]
    assert sess.log[1][2]["file_size"] == "3000" and sess.log[1][2]["Authorization"] == "OAuth test-token"
    sess = FakeGraph(publish_fails=True)
    try:
        instagram.publish({**PKG, "clip_id": a, "file": str(video)}, st, session=sess, sleep=lambda s: None,
                            prepare=lambda p: p)
        raise AssertionError("lost publish answer must be Ambiguous")
    except Ambiguous:
        pass
    sess = FakeGraph(token_bad=True)
    try:
        instagram.publish({**PKG, "clip_id": a, "file": str(video)}, st, session=sess, sleep=lambda s: None,
                            prepare=lambda p: p)
        raise AssertionError("expired token must be Permanent")
    except Permanent as exc:
        assert "token" in str(exc)
    print(f"all publish tests passed ({len(db.posts('posted'))} fake posts)")


class _R:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, json.dumps(data)

    def json(self):
        return self._d


class FakeGraph:
    def __init__(self, publish_fails=False, token_bad=False):
        self.log, self.polls = [], 0
        self.publish_fails, self.token_bad = publish_fails, token_bad

    def post(self, url, data=None, headers=None, timeout=None):
        self.log.append(("POST", url, headers or {}))
        if self.token_bad:
            return _R(400, {"error": {"code": 190, "message": "Error validating access token"}})
        if url.endswith("/media"):
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
            return _R(200, {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/C1"})
        if "rupload" in url:
            assert hasattr(data, "read")
            return _R(200, {"success": True, "message": "Upload successful."})
        if url.endswith("/media_publish"):
            if self.publish_fails:
                import requests
                raise requests.ConnectionError("reset")
            return _R(200, {"id": "M1"})
        if url.endswith("/comments"):
            return _R(200, {"id": "K1"})
        raise AssertionError(url)

    def get(self, url, params=None, timeout=None):
        self.log.append(("GET", url, {}))
        if url.endswith("/C1"):
            self.polls += 1
            return _R(200, {"status_code": "FINISHED" if self.polls >= 2 else "IN_PROGRESS"})
        if url.endswith("/M1"):
            return _R(200, {"permalink": "https://instagram.com/reel/abc"})
        raise AssertionError(url)


if __name__ == "__main__":
    main()
