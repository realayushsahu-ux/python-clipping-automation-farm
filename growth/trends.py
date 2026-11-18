"""Phase 8: trends finder - what is working in the GTA niche RIGHT NOW (read-only, official YouTube Data API).

  1. Ideas    : Shorts about your search words (trend_queries) from the last trend_days days. Every video is
                ranked by how far it beat ITS OWN channel's median (yt-viral / swipe.py method), so a small
                channel's breakout counts as much as a big channel's normal day. Title formula from hooks.json.
  2. Sources  : long videos / streams about the same words from the last 3 days, ranked by views per hour,
                = fresh streams worth clipping. Only suggestions: copy a link into Video Input yourself.
  3. Trending : hashtags + an AI note of the angles that keep coming back → config/trends.md,
                which ai/voice.py adds to every AI prompt ("use only if THIS clip really shows it").

Quota: a search costs 100 units, so searches are capped (trend_max_searches_per_day, default 4 = 400 units);
channel/video reads cost 1 unit per 50 videos. Results: data/trends.json, Sheet tab "Trends",
`main.py trends`. Never claims anything is viral; numbers are the platform's own counts.
"""

import json
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from core.db import get_db
from core.logger import get_logger
from core.paths import DATA_DIR, ROOT

log = get_logger("trends")
STATE = DATA_DIR / "trends.json"
TRENDS_MD = ROOT / "config" / "trends.md"
HOOKS = ROOT / "growth" / "yt_skills" / "yt-script" / "hooks.json"
SHORT_MAX_S = 180
_formulas = None

SYSTEM = """You look at the Shorts that beat their own channel's usual views the most this week in ONE gaming niche
(GTA / streamer clips). Name the ANGLES that keep coming back (a character, mission, streamer, reaction type,
glitch, comparison...). At most 5 angles, each under 20 words, concrete, usable for picking a moment or writing
a title. Patterns only: never copy a title, never say anything is viral, never invent numbers."""

SCHEMA = {"type": "object", "properties": {
    "angles": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string", "description": "one sentence: what the niche is reacting to this week"}}}


# ------------------------------------------------------------------------------------------ helpers
def _iso_duration(d):
    from growth.analytics import _iso_duration as f
    return f(d)


def _parse_time(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(title) -> str:
    """Title formula (the 21 hooks.json formulas, same as yt-viral/swipe.py). About the words, not the why."""
    global _formulas
    if _formulas is None:
        try:
            _formulas = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
        except (OSError, ValueError, KeyError):
            _formulas = []
    scored = sorted(((sum(1 for p in f["match"] if re.search(p, title or "", re.I)), f["name"])
                     for f in _formulas), reverse=True)
    return scored[0][1] if scored and scored[0][0] else "other"


def outliers(videos, min_multiple=2.0, min_per_channel=4) -> list:
    """Rank by views ÷ the channel's median (needs ≥ min_per_channel videos of that channel)."""
    by = {}
    for v in videos:
        by.setdefault(v["channel_id"], []).append(v)
    out = []
    for vids in by.values():
        if len(vids) < min_per_channel:
            continue
        med = statistics.median(v["views"] for v in vids)
        for v in vids:
            m = v["views"] / med if med else 0
            if m >= min_multiple and v.get("recent"):
                out.append({**v, "median": int(med), "multiple": round(m, 1), "formula": classify(v["title"])})
    return sorted(out, key=lambda r: -r["multiple"])


def load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def due(settings, now=None) -> bool:
    if not settings.get("trends_enabled", True):
        return False
    at = _parse_time(load().get("at"))
    now = now or datetime.now()
    return at is None or now - at.replace(tzinfo=None) >= timedelta(hours=float(settings.get("trend_refresh_hours", 24)))


# -------------------------------------------------------------------------------------- YouTube reads
class Budget:
    """Searches left today (100 units each)."""

    def __init__(self, settings, state, today):
        self.max = int(settings.get("trend_max_searches_per_day", 4))
        self.today = today
        self.used = state.get("searches", 0) if state.get("search_day") == today else 0

    def take(self) -> bool:
        if self.used >= self.max:
            return False
        self.used += 1
        return True


def _search(yt, budget, q, duration, after, region, n):
    if not budget.take():
        return []
    p = {"part": "id", "q": q, "type": "video", "order": "viewCount", "videoDuration": duration,
         "publishedAfter": after.strftime("%Y-%m-%dT%H:%M:%SZ"), "maxResults": n}
    if region:
        p["regionCode"] = region
    return [i["id"]["videoId"] for i in yt.search().list(**p).execute().get("items", []) if i["id"].get("videoId")]


def _videos(yt, ids, now, recent_after) -> list:
    out = []
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).execute()
        for v in r.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            pub = _parse_time(sn.get("publishedAt")) or now
            hours = max(1.0, (now - pub).total_seconds() / 3600)
            views = int(st.get("viewCount", 0))
            out.append({"id": v["id"], "channel_id": sn.get("channelId", ""), "channel": sn.get("channelTitle", ""),
                        "title": sn.get("title", ""), "views": views, "likes": int(st.get("likeCount", 0) or 0),
                        "duration_s": _iso_duration(v["contentDetails"].get("duration")),
                        "published": pub.strftime("%Y-%m-%d %H:%M"), "hours": round(hours, 1),
                        "per_hour": round(views / hours), "live": sn.get("liveBroadcastContent", "none"),
                        "url": (f"https://youtube.com/shorts/{v['id']}" if _iso_duration(v["contentDetails"].get("duration")) <= SHORT_MAX_S
                                else f"https://www.youtube.com/watch?v={v['id']}"),
                        "recent": pub >= recent_after,
                        "_text": f"{sn.get('title', '')} {sn.get('description', '')[:600]} {' '.join(sn.get('tags') or [])}",
                        "tags": re.findall(r"#\w+", f"{sn.get('title', '')} {sn.get('description', '')}")[:15]})
    return out


def on_topic(settings, v) -> bool:
    """Search is loose (GTA 5, other games): keep only videos whose title/description/tags name a must-match word."""
    words = [w.lower() for w in settings.get("trend_must_match") or []]
    text = re.sub(r"[\s_-]+", " ", (v.get("_text") or v.get("title") or "").lower())
    return not words or any(w in text or w.replace(" ", "") in text.replace(" ", "") for w in words)


def _clean(rows):
    return [{k: x for k, x in v.items() if not k.startswith("_")} for v in rows]


def _channel_uploads(yt, channel_ids, per_channel=30) -> list:
    """Recent upload ids of each channel (1 unit per channel + 1 per 50 channels)."""
    ids, chans = [], list(dict.fromkeys(channel_ids))
    for i in range(0, len(chans), 50):
        r = yt.channels().list(part="contentDetails", id=",".join(chans[i:i + 50])).execute()
        for c in r.get("items", []):
            pl = c["contentDetails"]["relatedPlaylists"]["uploads"]
            items = yt.playlistItems().list(part="contentDetails", playlistId=pl, maxResults=per_channel).execute()
            ids += [x["contentDetails"]["videoId"] for x in items.get("items", [])]
    return ids


def _resolve_channels(yt, entries) -> list:
    """trend_channels: UC... ids, @handles or channel URLs."""
    out = []
    for e in entries or []:
        e = e.strip().rstrip("/")
        m = re.search(r"(UC[\w-]{22})", e)
        if m:
            out.append(m.group(1))
            continue
        h = re.search(r"@([\w.-]+)", e)
        if h:
            items = yt.channels().list(part="id", forHandle=h.group(1)).execute().get("items") or []
            out += [items[0]["id"]] if items else []
    return out


# ------------------------------------------------------------------------------------------- refresh
def refresh(settings, yt=None, now=None, force=False) -> dict:
    """Find ideas + sources, save data/trends.json, update config/trends.md. Returns a small report."""
    if not settings.get("trends_enabled", True) and not force:
        return {"off": True}
    if yt is None:
        from posting.youtube_shorts import _client
        yt = _client()
    now = now or datetime.now(timezone.utc)
    old = load()
    budget = Budget(settings, old, now.strftime("%Y-%m-%d"))
    days = int(settings.get("trend_days", 7))
    region = settings.get("trend_region", "IN")
    queries = settings.get("trend_queries") or ["GTA 6"]
    recent = now - timedelta(days=days)
    mine = {c["id"] for c in (yt.channels().list(part="id", mine=True).execute().get("items") or [])}

    if budget.used >= budget.max:                    # nothing can be searched today: keep the last result
        note = f"search limit reached ({budget.max}/day); kept the result from {old.get('at', '-')[:16]}"
        log.info(f"trends: {note}")
        return {"ideas": len(old.get("ideas", [])), "sources": len(old.get("sources", [])), "channels": 0,
                "searches": budget.used, "notes": [note], "kept": True}
    short_ids, long_ids, notes = [], [], []
    for q in queries:
        before = budget.used
        short_ids += _search(yt, budget, q, "short", recent, region, 50)
        long_ids += _search(yt, budget, q, "long", now - timedelta(days=3), region, 25)
        if budget.used - before < 2:
            notes.append(f"search limit reached ({budget.max}/day), skipped part of '{q}'")
    found = [v for v in _videos(yt, short_ids, now, recent) if v["duration_s"] <= SHORT_MAX_S]

    # the channels behind this week's Shorts + your own list → their recent uploads → each channel's median
    top_channels = [c for c, _ in Counter(v["channel_id"] for v in found).most_common(int(settings.get("trend_max_channels", 15)))]
    chans = [c for c in dict.fromkeys(_resolve_channels(yt, settings.get("trend_channels")) + top_channels) if c not in mine]
    pool = {v["id"]: v for v in found}
    for v in _videos(yt, _channel_uploads(yt, chans), now, recent):
        if v["duration_s"] <= SHORT_MAX_S:
            pool.setdefault(v["id"], v)
    ideas = _clean([v for v in outliers([v for v in pool.values() if v["channel_id"] not in mine],
                                        float(settings.get("trend_min_multiple", 2.0))) if on_topic(settings, v)][:40])

    have = " ".join(v["url"] for v in get_db().videos())
    sources = sorted((v for v in _videos(yt, long_ids, now, recent)
                      if v["channel_id"] not in mine and (v["duration_s"] >= 600 or v["live"] == "live")
                      and on_topic(settings, v)),
                     key=lambda v: -v["per_hour"])[:25]
    sources = _clean(sources)
    for v in sources:
        v["added"] = v["id"] in have

    tags = Counter(t.lower() for v in ideas for t in set(v["tags"])).most_common(12)
    state = {"at": datetime.now().isoformat(timespec="seconds"), "search_day": budget.today,
             "searches": budget.used, "queries": queries, "ideas": ideas, "sources": sources,
             "hashtags": tags, "formulas": Counter(v["formula"] for v in ideas).most_common(6), "notes": notes}
    state["angles"], state["summary"] = _angles(settings, ideas)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(settings, state)
    rep = {"ideas": len(ideas), "sources": len(sources), "channels": len(chans), "searches": budget.used,
           "notes": notes}
    log.info(f"trends refreshed: {rep}")
    return rep


def _angles(settings, ideas):
    if not ideas:
        return [], ""
    from ai.base import ask_json
    data = [{"title": v["title"], "channel": v["channel"], "beat_channel_median_x": v["multiple"],
             "formula": v["formula"], "length_s": int(v["duration_s"])} for v in ideas[:25]]
    res = ask_json(settings, SYSTEM, json.dumps(data, ensure_ascii=False), SCHEMA, max_tokens=1200,
                   voice=False, offline_fn=lambda: None)
    if not res:
        return [], ""
    return [a.strip() for a in (res.get("angles") or [])[:5] if a.strip()], (res.get("summary") or "").strip()


def write_md(settings, state):
    """config/trends.md for the AI prompts (or removed when use_trends_in_prompts = no)."""
    if not settings.get("use_trends_in_prompts", True) or not state.get("ideas"):
        TRENDS_MD.unlink(missing_ok=True)
        return
    lines = [f"# Trending in the niche this week (auto, {state['at'][:10]}, from {len(state['ideas'])} Shorts that beat "
             "their channel's median)", "Use an angle or hashtag ONLY if this clip really shows it. Never copy a title.", ""]
    lines += [f"- {a}" for a in state.get("angles") or []]
    if state.get("summary"):
        lines += ["", f"This week: {state['summary']}"]
    if state.get("hashtags"):
        lines += ["", "Hashtags used by those Shorts: " + " ".join(t for t, _ in state["hashtags"][:10])]
    opens = [" ".join(v["title"].split()[:3]) for v in state["ideas"][:12] if not v["title"].startswith("#")][:8]
    if opens:
        lines += ["", "How this week's top titles OPEN (first words decide the swipe): " + " | ".join(opens)]
    lines += ["", "Examples (title formula, not to copy):"]
    lines += [f"- {v['multiple']}x its channel: \"{v['title'][:80]}\" ({v['formula']})" for v in state["ideas"][:5]]
    TRENDS_MD.write_text("\n".join(lines)[:2000] + "\n", encoding="utf-8")
