"""Phase 8: learn from the winners → short rules every AI prompt follows (picking moments, hooks, titles).

  winners (views ≥ threshold) vs the rest  →  AI writes ≤5 "do more of this" rules  →  config/winners.md
  ai/voice.py adds config/winners.md to every AI prompt, next to config/voice.md.

Runs again only when the set of winners changes. Patterns only: the AI must never copy a title,
never claim a clip is viral, never invent numbers. With no AI key, a plain list of the winners is written.
Delete config/winners.md (or set learn_from_winners = no) to switch it off.
"""

import hashlib
import json
from datetime import datetime

from core.db import get_db
from core.logger import get_logger
from core.paths import DATA_DIR, ROOT
from growth import analytics

log = get_logger("learn")
WINNERS_MD = ROOT / "config" / "winners.md"
STATE = DATA_DIR / "winners_state.json"

SYSTEM = """You analyse which Shorts/Reels of ONE gaming clip channel performed best, to guide the next clips.
You get the WINNERS (most views) and the OTHERS. Find what the winners share that the others lack:
moment type, topic/character, first-line hook style, title style, length, emotion, who is in it.
Write at most 5 short, concrete rules (imperative, under 25 words each) for picking the next moments and
writing hooks/titles. Patterns only: never tell anyone to copy a title word for word, never claim anything
is viral, never invent numbers. If the data is too thin for a rule, say so in `confidence`."""

SCHEMA = {"type": "object", "properties": {
    "rules": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string", "description": "one sentence: what the winners have in common"},
    "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}


def _describe(row) -> dict:
    d = {"platform": row["platform"], "views": row.get("views"), "likes": row.get("likes"),
         "title": row.get("title"), "length_s": row.get("duration_s"), "published": row.get("published_at")}
    c = get_db().get_clip(row["clip_id"]) if row.get("clip_id") else None
    if c:
        d.update(kind=c.get("kind"), on_screen_hook=c.get("hook"), farm_score=c.get("score"),
                 length_s=round((c["end"] or 0) - (c["start"] or 0), 1))
    return d


def _key(win) -> str:
    return hashlib.sha1(json.dumps(sorted((w["platform"], w["external_id"]) for w in win)).encode()).hexdigest()


def learn(settings, force=False) -> dict:
    """Update config/winners.md when the winners changed. Returns {"winners": n, "updated": bool, ...}."""
    if not settings.get("learn_from_winners", True):
        return {"winners": 0, "updated": False, "off": True}
    rows = get_db().stats()
    win = [r for r in rows if analytics.is_winner(settings, r)]
    others = [r for r in rows if r not in win and r.get("views") is not None][:30]
    key = _key(win)
    try:
        old = json.loads(STATE.read_text(encoding="utf-8")).get("key")
    except (OSError, ValueError):
        old = None
    if not win:
        return {"winners": 0, "updated": False}
    if key == old and WINNERS_MD.exists() and not force:
        return {"winners": len(win), "updated": False}

    from ai.base import ask_json
    data = {"winners": [_describe(r) for r in win[:15]], "others": [_describe(r) for r in others]}
    res = ask_json(settings, SYSTEM, json.dumps(data, ensure_ascii=False), SCHEMA, max_tokens=1500,
                   voice=False, offline_fn=lambda: None)
    lines = [f"# What worked on this channel (auto, {datetime.now():%Y-%m-%d}; from {len(win)} winner(s))",
             "Use these as patterns for picking moments and writing hooks/titles. Never copy a title.", ""]
    if res and res.get("rules"):
        lines += [f"- {r.strip()}" for r in res["rules"][:5] if r.strip()]
        conf = res.get("confidence", "low") if len(win) >= 3 else "low"       # 1-2 winners = a hint, not proof
        if res.get("summary"):
            lines += ["", f"Common thread: {res['summary'].strip()} (confidence: {conf}; based on {len(win)} winner(s), "
                          "the clip length limits in settings always apply)"]
    lines += ["", "Winners:"]
    for r in win[:8]:
        d = _describe(r)
        extra = f", kind {d['kind']}, hook \"{d['on_screen_hook']}\"" if d.get("kind") else ""
        lines.append(f"- {r['platform']} {r['views']} views, {int(d.get('length_s') or 0)} s: "
                     f"\"{(r.get('title') or '')[:90]}\"{extra}")
    WINNERS_MD.write_text("\n".join(lines)[:2500] + "\n", encoding="utf-8")
    STATE.write_text(json.dumps({"key": key, "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    log.info(f"winners.md updated from {len(win)} winner(s)")
    return {"winners": len(win), "updated": True, "rules": (res or {}).get("rules", [])}
