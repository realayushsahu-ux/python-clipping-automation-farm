"""Suggested posting times for ready clips.

Uses config/settings.json: posting_times (["12:30", "18:00", "21:00"]), posts_per_day, timezone.
Every ready clip gets the next free slot (best score first), never two clips in one slot.
Phase 7's scheduler publishes at these times; until then they tell you when to post.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.db import get_db

FMT = "%Y-%m-%d %H:%M"


def _tz(settings):
    try:
        return ZoneInfo(settings.get("timezone", "Asia/Kolkata"))
    except Exception:  # noqa: BLE001 - tzdata missing: fall back to India time
        from datetime import timezone
        return timezone(timedelta(hours=5, minutes=30))


def _slot_times(settings):
    times = sorted(settings.get("posting_times") or ["18:00"])
    per_day = int(settings.get("posts_per_day") or len(times))
    return times[:max(1, per_day)]


def next_free_slots(settings, n, taken, now=None):
    """n slot strings 'YYYY-MM-DD HH:MM' (local time) after now+30 min that are not in `taken`."""
    tz = _tz(settings)
    now = now or datetime.now(tz)
    earliest = now + timedelta(minutes=int(settings.get("min_minutes_before_post", 30)))
    out, day = [], earliest.date()
    times = _slot_times(settings)
    while len(out) < n:
        for t in times:
            hh, mm = map(int, t.split(":"))
            dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            s = dt.strftime(FMT)
            if dt >= earliest and s not in taken:
                out.append(s)
                taken.add(s)
                if len(out) == n:
                    break
        day += timedelta(days=1)
    return out


def assign_post_times(settings, only_clip_ids=None):
    """Give every ready clip without a time (or the given clips) the next free slot.
    Returns {clip_id: post_time}."""
    db = get_db()
    ready = db.clips(None, "ready", "approved")
    taken = {c["post_time"] for c in db.clips() if c.get("post_time")
             and c["status"] in ("ready", "approved", "scheduled", "posting")}
    todo = [c for c in ready if not c.get("post_time") or (only_clip_ids and c["clip_id"] in only_clip_ids)]
    if only_clip_ids:
        todo = [c for c in todo if c["clip_id"] in only_clip_ids]
        taken -= {c["post_time"] for c in todo if c.get("post_time")}
    todo.sort(key=lambda c: -(c.get("score") or 0))            # best clip gets the earliest slot
    slots = next_free_slots(settings, len(todo), taken)
    result = {}
    for c, s in zip(todo, slots):
        db.update_clip(c["clip_id"], post_time=s)
        result[c["clip_id"]] = s
    return result
