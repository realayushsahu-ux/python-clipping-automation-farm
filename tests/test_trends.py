"""Phase 8 test: trends finder with a fake YouTube client (no account, no internet, offline AI).

Run:  .venv\\Scripts\\python.exe -m tests.test_trends
"""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ai.voice as voice
import core.db as dbmod
import growth.trends as tr
from config.settings import load_settings
from core.db import DB
from google_sheets import tabs as T
from google_sheets import sync as sy
from google_sheets.client import MemorySheet

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)


def _iso(days):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# channel A: usual ~1000 views, one Short at 9000 (9x); channel B: 3 videos only (too few for a median)
VIDS = {
    "a1": ("A", "GTA 6 cops chase", 1000, "PT40S", 1), "a2": ("A", "x", 900, "PT50S", 20),
    "a3": ("A", "y", 1100, "PT30S", 25), "a4": ("A", "z", 1000, "PT35S", 30),
    "a5": ("A", "Why Speed QUIT GTA 6 #gta6", 9000, "PT45S", 2),
    "b1": ("B", "huge", 50000, "PT30S", 1), "b2": ("B", "b", 100, "PT30S", 3), "b3": ("B", "c", 100, "PT30S", 4),
    "me1": ("ME", "my own short", 99999, "PT30S", 1),
    "s1": ("C", "GTA 6 FULL STREAM day 1", 60000, "PT3H", 1), "s2": ("D", "GTA 6 live", 20000, "PT2H", 2),
    "s3": ("C", "GTA 6 trailer react", 5000, "PT5M", 1),
    "s4": ("E", "GTA 5 LIVE #gta5", 900000, "PT4H", 1),
}


class Req:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self.data


class Res:
    def __init__(self, fake, kind):
        self.fake, self.kind = fake, kind

    def list(self, **p):
        f = self.fake
        f.calls.append((self.kind, p))
        if self.kind == "search":
            ids = ["a1", "a5", "b1", "me1"] if p["videoDuration"] == "short" else ["s1", "s2", "s3", "s4"]
            return Req({"items": [{"id": {"videoId": i}} for i in ids]})
        if self.kind == "channels":
            if p.get("mine"):
                return Req({"items": [{"id": "ME"}]})
            if p.get("forHandle"):
                return Req({"items": [{"id": "B"}]})
            return Req({"items": [{"id": c, "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c}}}
                                  for c in p["id"].split(",")]})
        if self.kind == "playlistItems":
            ch = p["playlistId"][2:]
            return Req({"items": [{"contentDetails": {"videoId": k}} for k, v in VIDS.items() if v[0] == ch]})
        if self.kind == "videos":
            return Req({"items": [{"id": k, "snippet": {"channelId": VIDS[k][0], "channelTitle": "ch" + VIDS[k][0],
                                                       "title": VIDS[k][1], "publishedAt": _iso(VIDS[k][4]),
                                                       "description": "", "liveBroadcastContent": "none"},
                                   "statistics": {"viewCount": str(VIDS[k][2])},
                                   "contentDetails": {"duration": VIDS[k][3]}} for k in p["id"].split(",")]})
        raise AssertionError(self.kind)


class FakeYT:
    def __init__(self):
        self.calls = []

    def __getattr__(self, kind):
        return lambda: Res(self, kind)


def main():
    tmp = Path(tempfile.mkdtemp())
    dbmod._local.db = DB(tmp / "t.db")
    tr.STATE, tr.TRENDS_MD = tmp / "trends.json", tmp / "trends.md"
    voice.TRENDS_FILE = tr.TRENDS_MD
    voice.WINNERS_FILE = tmp / "none.md"
    st = load_settings()
    st["ai_provider"], st["ai_fallback_provider"] = "offline", "offline"
    st["trend_queries"] = ["GTA 6"]
    st["trend_channels"] = ["@someone"]
    dbmod.get_db().conn.execute("INSERT INTO videos (video_id, url, status, created_at, updated_at) VALUES ('v1', "
                                "'https://www.youtube.com/watch?v=s2', 'done', 'x', 'x')")

    # 1. outliers: only channels with ≥4 Shorts, ranked by multiple of the channel median; own channel ignored
    yt = FakeYT()
    rep = tr.refresh(st, yt=yt, now=NOW)
    s = tr.load()
    ids = [v["id"] for v in s["ideas"]]
    assert ids == ["a5"], s["ideas"]
    assert s["ideas"][0]["multiple"] == 9.0 and s["ideas"][0]["median"] == 1000
    assert s["ideas"][0]["url"] == "https://youtube.com/shorts/a5" and "_text" not in s["ideas"][0]
    assert tr.on_topic(st, {"title": "GTA-VI leak"}) and not tr.on_topic(st, {"title": "GTA 5 live"})
    assert ("#gta6", 1) in [tuple(x) for x in s["hashtags"]]

    # 2. sources: on-topic (GTA 6, not GTA 5) long videos only (≥10 min), most views per hour first, already-added ones marked
    assert [v["id"] for v in s["sources"]] == ["s1", "s2"], s["sources"]
    assert s["sources"][1]["added"] and not s["sources"][0]["added"]
    assert s["sources"][0]["url"] == "https://www.youtube.com/watch?v=s1"

    # 3. quota: 2 searches per query, capped per day; the cap carries over within the same day
    assert rep["searches"] == 2 and s["searches"] == 2
    st["trend_max_searches_per_day"] = 3
    rep = tr.refresh(st, yt=FakeYT(), now=NOW)
    assert rep["searches"] == 3 and rep["notes"], rep
    fy = FakeYT()
    rep = tr.refresh(st, yt=fy, now=NOW)                                  # limit used up: last result kept
    assert rep.get("kept") and rep["ideas"] == 1 and not [c for c in fy.calls if c[0] == "search"] and tr.load()["ideas"], rep
    rep = tr.refresh(st, yt=FakeYT(), now=NOW + timedelta(days=1))      # new day, new budget
    assert rep["searches"] == 2
    st["trend_max_searches_per_day"] = 4

    # 4. due(): once per trend_refresh_hours; off switch
    assert not tr.due(st)
    assert tr.due(st, now=datetime.now() + timedelta(hours=25))
    st["trends_enabled"] = False
    assert not tr.due(st, now=datetime.now() + timedelta(hours=25))
    assert tr.refresh(st, yt=FakeYT(), now=NOW) == {"off": True}
    st["trends_enabled"] = True

    # 5. trends.md → AI prompt; ignored when stale; removed when switched off
    assert tr.TRENDS_MD.exists()
    p = voice.with_voice("SYS")
    assert "TRENDING IN THE NICHE" in p and "Why Speed QUIT" in p and "#gta6" in p
    old = time.time() - 4 * 24 * 3600
    os.utime(tr.TRENDS_MD, (old, old))
    assert "TRENDING" not in voice.with_voice("SYS")
    st["use_trends_in_prompts"] = False
    tr.write_md(st, tr.load())
    assert not tr.TRENDS_MD.exists()

    # 6. Trends tab: sources first, then ideas, footer; not rewritten until a new run
    io = MemorySheet()
    sy.ensure_tabs(io, st)
    assert sy.write_trends(io) == 3
    rows = io.read(T.TRENDS)
    h = {x: i for i, x in enumerate(rows[0])}
    assert [r[h["Type"]] for r in rows[1:4]] == ["🎥 source", "🎥 source", "💡 idea"]
    assert rows[2][h["Note"]] == "already in Video Input"
    assert rows[3][h["Beat Its Channel"]] == "9.0x" and rows[3][h["Link"]] == "▶ open"
    assert "searches today" in rows[4][h["Title"]]
    assert sy.write_trends(io) == 0
    keys = {r[0] for r in io.read(T.SETTINGS)}
    assert {"trends_enabled", "trend_queries", "use_trends_in_prompts"} <= keys

    # 7. title formula classifier never crashes on odd titles
    assert isinstance(tr.classify(""), str) and isinstance(tr.classify("Why Speed QUIT"), str)
    print("test_trends: all checks passed")
    json.dumps(s)


if __name__ == "__main__":
    main()
