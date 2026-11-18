"""Phase 8 test: stats saved/updated, winners by threshold, Performance tab, winners.md → AI prompt.
Temp database, fake numbers, in-memory sheet, offline AI: no account and no internet used.

Run:  .venv\Scripts\python.exe -m tests.test_growth
"""

import tempfile
from pathlib import Path

import ai.voice as voice
import core.db as dbmod
import growth.analytics as an
import growth.learn as learn
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets.client import MemorySheet
from google_sheets.sync import ensure_tabs, write_performance, write_posted


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    learn.WINNERS_MD = voice.WINNERS_FILE = tmp / "winners.md"
    learn.STATE = tmp / "state.json"
    db = dbmod.get_db()
    st = load_settings()
    st["ai_provider"], st["ai_fallback_provider"] = "offline", "offline"
    assert st["winner_views_youtube"] == 1000 and st["winner_views_instagram"] == 100

    rows = [("youtube", "yt1", 1290, "GTA-6 BODYGUARD"), ("youtube", "yt2", 5, "speed naked"),
            ("instagram", "ig1", 140, "speed angry"), ("instagram", "ig2", 99, "gameplay"),
            ("instagram", "ig3", None, "no permission yet")]
    for pl, ext, views, title in rows:
        db.save_stat({"platform": pl, "external_id": ext, "title": title, "views": views, "likes": 1,
                      "url": f"https://x/{ext}", "checked_at": "2030-01-01T10:00:00"})

    # 1. thresholds: YouTube 1000+, Instagram 100+; unknown views never a winner
    assert {w["external_id"] for w in an.winners(st)} == {"yt1", "ig1"}
    st["winner_views_instagram"] = 150
    assert {w["external_id"] for w in an.winners(st)} == {"yt1"}
    st["winner_views_instagram"] = 100

    # 2. a later read without views keeps the old number (COALESCE), history grows
    db.save_stat({"platform": "instagram", "external_id": "ig1", "views": None, "likes": 9, "checked_at": "2030-01-02"})
    ig1 = next(r for r in db.stats() if r["external_id"] == "ig1")
    assert ig1["views"] == 140 and ig1["likes"] == 9 and ig1["title"] == "speed angry"
    assert db.conn.execute("SELECT COUNT(*) FROM stats_history").fetchone()[0] == 6

    # 3. Performance tab: most views first, ⭐ on winners, link, "(no permission)" for unknown views
    io = MemorySheet()
    ensure_tabs(io, st)
    write_performance(io, st)
    write_posted(io, st)
    perf = io.read(T.PERFORMANCE)
    h = {x: i for i, x in enumerate(perf[0])}
    assert [r[h["Views"]] for r in perf[1:]] == ["1290", "140", "99", "5", "(no permission)"], perf
    assert [r[h["Winner"]] for r in perf[1:3]] == ["⭐", "⭐"] and perf[3][h["Winner"]] == ""
    assert perf[1][h["Link"]] == "▶ open"

    # 4. learn (offline AI → plain winners list) → winners.md → added to every AI prompt; runs once
    res = learn.learn(st)
    assert res == {"winners": 2, "updated": True, "rules": []}, res
    text = learn.WINNERS_MD.read_text(encoding="utf-8")
    assert "1290 views" in text and "140 views" in text and "gameplay" not in text
    assert "LEARNED FROM THIS CHANNEL" in voice.with_voice("SYSTEM")
    assert learn.learn(st)["updated"] is False, "same winners: no new AI call"
    st["learn_from_winners"] = False
    assert learn.learn(st).get("off")
    print("all growth tests passed")


if __name__ == "__main__":
    main()
