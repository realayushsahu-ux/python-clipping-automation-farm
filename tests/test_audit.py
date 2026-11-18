"""Phase 8 test: weekly channel audit on a temp database, offline AI (rule list), in-memory sheet.

Run:  .venv\\Scripts\\python.exe -m tests.test_audit
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import core.db as dbmod
import growth.channel_audit as ca
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets import sync as sy
from google_sheets.client import MemorySheet

NOW = datetime(2030, 1, 10, 12, 0)          # local time (India)


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    ca.AUDITS = tmp / "audits"
    db = dbmod.get_db()
    st = load_settings()
    st["ai_provider"], st["ai_fallback_provider"] = "offline", "offline"
    st["posting_times"] = ["10:00", "18:00", "20:00", "22:00", "23:30"]
    st["posts_per_day"] = 5

    # YouTube: posts on days 1, 2 and 6 ago (UTC times) → 3 days posted, a 3-day gap; one winner
    for i, (days, views, dur) in enumerate([(1, 1290, 41), (2, 5, 30), (6, 3, 58)]):
        t = (NOW - timedelta(days=days, hours=5, minutes=30)).strftime("%Y-%m-%dT%H:%M")
        db.save_stat({"platform": "youtube", "external_id": f"y{i}", "title": f"GTA 6 SPEED moment {i}",
                      "url": f"https://x/y{i}", "published_at": t, "duration_s": dur, "views": views,
                      "checked_at": "x"})
    db.save_stat({"platform": "instagram", "external_id": "i1", "title": "reel", "published_at": "2030-01-09T12:30",
                  "views": None, "likes": 3, "checked_at": "x"})

    # 1. facts: queue empty for the next 48 h (no clips) → 10 empty slots; rhythm in local days
    f = ca.facts(st, NOW, retention={"available": False})
    q = f["queue"]
    assert q["clips_scheduled"] == 0 and q["empty_slots_next_48h"] == q["slots_next_48h"] == 10, q
    assert q["first_empty_slot"] == "2030-01-10 18:00", q
    r = f["rhythm_youtube"]
    assert r["posts_last_14_days"] == 3 and r["days_with_posts"] == 3, r
    assert r["longest_gap_days"] == 3 and r["days_counted"] == 7, r   # counted from the first post (6 days ago)
    y = f["results"]["youtube"]
    assert y["winners"] == 1 and y["median_views_last_10"] == 5, y
    assert "by_length" not in y and "need 10" in y["breakdowns"], y          # 3 posts: too few to compare
    ca.MIN_COMPARE = 2
    y = ca.facts(st, NOW, retention={"available": False})["results"]["youtube"]
    assert y["by_length"]["35-50 s"] == {"posts": 1, "median_views": 1290}, y
    ca.MIN_COMPARE = 10
    assert "instagram_manage_insights" in f["results"]["instagram"]["note"]
    assert f["titles_youtube_last_10"]["count"] == 3

    # 2. no AI → rule list: the empty queue is THE fix, with the real numbers
    a = ca.run(st, NOW, retention={"available": False})
    rep = a["report"]
    assert rep["by"] == "rules" and rep["fix"].startswith("Feed the farm"), rep
    assert "10 of the next 10" in rep["why"]
    assert any("1 YouTube Short" in w for w in rep["working"])
    assert (ca.AUDITS / "audit_2030-01-10.md").exists() and "THE ONE FIX" in ca.to_text(a)

    # 3. due(): weekly
    assert not ca.due(st, NOW + timedelta(days=6)) and ca.due(st, NOW + timedelta(days=7))
    st["audit_enabled"] = False
    assert not ca.due(st, NOW + timedelta(days=30))
    st["audit_enabled"] = True

    # 4. clips scheduled → queue fix disappears; a post needing you becomes the fix
    db.conn.execute("INSERT INTO videos (video_id,url,status,created_at,updated_at) VALUES ('v','u','done','x','x')")
    for k, slot in enumerate(["2030-01-17 18:00", "2030-01-17 20:00", "2030-01-17 22:00", "2030-01-17 23:30",
                              "2030-01-18 10:00", "2030-01-18 18:00", "2030-01-18 20:00", "2030-01-18 22:00",
                              "2030-01-18 23:30", "2030-01-19 10:00"]):
        db.conn.execute("INSERT INTO clips (clip_id,video_id,idx,start,end,status,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (f"C{k}", "v", k, 0, 40, "scheduled", "x", "x"))
        db.conn.execute("INSERT INTO posts (clip_id,platform,scheduled_at,status,idempotency_key) VALUES (?,?,?,?,?)",
                        (f"C{k}", "youtube", slot, "needs_manual" if k == 0 else "scheduled", f"k{k}"))
    later = NOW + timedelta(days=7)
    a2 = ca.run(st, later, retention={"available": True, "days": 28, "median_avg_view_pct": 71, "videos": []})
    assert a2["facts"]["queue"]["empty_slots_next_48h"] == 1, a2["facts"]["queue"]   # the post that needs you
    assert a2["report"]["fix"] == "Clear the posts that need you", a2["report"]

    # 5. Audit tab: the fix on top, facts, retention, earlier fixes; not rewritten until a new audit
    io = MemorySheet()
    sy.ensure_tabs(io, st)
    assert sy.write_audit(io) > 5
    rows = io.read(T.AUDIT)
    assert rows[2][0] == "🎯 THE ONE FIX" and rows[2][1] == "Clear the posts that need you"
    flat = " | ".join(" ".join(r) for r in rows)
    assert "71% of each Short watched" in flat and "EARLIER FIXES" in flat and "Feed the farm" in flat
    assert sy.write_audit(io) == 0
    assert {"audit_enabled", "audit_every_days"} <= {r[0] for r in io.read(T.SETTINGS)}
    print("test_audit: all checks passed")


if __name__ == "__main__":
    main()
