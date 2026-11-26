"""YouTube login expired (found 2026-11-24/26): posts WAIT instead of "needs you", old ones heal, they go out
by themselves (spaced apart) once the login is back, and a tried clip keeps its post time.
Temp database, fake platforms; nothing real is posted.

Run:  .venv\\Scripts\\python.exe -m tests.test_login_wait
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import core.db as dbmod
import core.outputs as outs
from config.settings import load_settings
from core.db import DB
from posting import publisher, queue
from posting.errors import LoginNeeded


class Fake:
    def __init__(self):
        self.login_ok, self.calls = False, 0

    def __call__(self, job, settings):
        self.calls += 1
        if not self.login_ok:
            raise LoginNeeded("YouTube login expired: double-click 'Reconnect YouTube' on the Desktop")
        return {"external_id": f"yt{self.calls}", "url": f"https://youtube.com/shorts/yt{self.calls}"}


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    outs.READY, outs.POSTED = tmp / "ready", tmp / "posted"
    db = dbmod.get_db()
    db.add_video("vid00000001", "https://youtu.be/vid00000001")
    st = load_settings()
    st.update(posting_enabled=True, manual_approval=False)
    yt = Fake()
    publisher.PUBLISHERS = {"youtube": yt}
    import core.net as net
    net.online = lambda timeout=3: True
    ids = []
    for i, ago in enumerate((300, 200, 5)):                  # two clips hours late, one due now
        cid = db.add_clip("vid00000001", i, i * 60, i * 60 + 40, score=80)
        folder = outs.READY / cid
        folder.mkdir(parents=True)
        (folder / f"{cid}.mp4").write_bytes(b"\x00" * 100)
        (folder / "post.json").write_text(json.dumps({"platforms": ["youtube"]}), encoding="utf-8")
        t = (datetime.now() - timedelta(minutes=ago)).strftime("%Y-%m-%d %H:%M")
        db.update_clip(cid, status="approved", folder=str(folder), post_time=t, title=cid)
        ids.append(cid)
    queue.plan_posts(st)
    a, b, c = ids
    times = {x: db.get_clip(x)["post_time"] for x in ids}
    # the two late ones were already tried once before (an older farm put them in "needs you")
    for x in (a, b):
        p = next(p for p in db.posts() if p["clip_id"] == x)
        db.update_post(p["id"], status="needs_manual", attempts=1, claimed_at=datetime.now().isoformat(),
                       last_error="YouTube is not connected (or access was removed): run  main.py auth youtube")

    # 1. login still dead: nothing is "needs you", everything waits, nothing moves to another day
    publisher.publish_due(st)
    ps = {p["clip_id"]: p for p in db.posts()}
    assert {p["status"] for p in ps.values()} == {"failed"}, {k: v["status"] for k, v in ps.items()}
    assert all("login" in (p["last_error"] or "").lower() for p in ps.values())
    assert {x: db.get_clip(x)["post_time"] for x in ids} == times, "a tried clip keeps its post time"
    assert ps[c]["attempts"] == 0, "waiting for the login does not use up tries"

    # 2. you sign in again → they go out by themselves; the late ones one at a time, 20 min apart
    yt.login_ok = True
    for p in db.posts():
        db.update_post(p["id"], next_try=datetime.now().isoformat(timespec="seconds"))
    publisher.publish_due(st)
    done = sorted(p["clip_id"] for p in db.posts("posted"))
    assert done == [a, c], done            # the one due now + ONE hours-late catch-up; the other late one waits
    publisher.publish_due(st)
    assert sorted(p["clip_id"] for p in db.posts("posted")) == [a, c], "next catch-up waits 20 minutes"
    for p0 in db.posts("posted"):
        db.update_post(p0["id"], posted_at=(datetime.now() - timedelta(minutes=25)).isoformat(timespec="seconds"))
    publisher.publish_due(st)
    assert sorted(p["clip_id"] for p in db.posts("posted")) == [a, b, c]
    print("test_login_wait: all checks passed")


if __name__ == "__main__":
    main()
