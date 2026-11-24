"""Phase 5: the road from "ready" to "posted". No platform code here (that is Phase 6).

  ready     clip passed QC, folder in storage/ready_to_post/<CLIP>/
  approved  manual_approval = no  → every ready clip is approved automatically (Approve shows yes)
            manual_approval = yes → you type yes in Ready to Post → Approve
            → one row per platform in the posts table (status "scheduled", time = clip's Post Time)
  posting   at least one platform published, others still to go
  posted    every platform published → folder moved to storage/posted_clips/<CLIP>/

Safety:
  * one post per clip per platform, ever (posts.idempotency_key = CLIP:platform, UNIQUE)
  * a clip that is posting/posted can't be rejected, remade or re-approved
  * a missed slot (farm was off) is moved to the next free slot instead of posting at a random hour
"""

import json
from datetime import datetime, timedelta

from core.db import get_db, now
from core.logger import get_logger
from core.outputs import move_to_posted, write_post_info
from posting.slots import FMT, _tz, assign_post_times

log = get_logger("queue")

LIVE_POST = ("scheduled", "failed", "needs_manual")      # not published yet, may still change


def platforms_of(c, settings):
    from pathlib import Path
    folder = Path(c.get("folder") or "")
    try:
        p = json.loads((folder / "post.json").read_text(encoding="utf-8"))
        if p.get("platforms"):
            return list(p["platforms"])
    except (OSError, ValueError):
        pass
    return list(settings.get("default_platforms") or ["instagram", "youtube"])


def auto_approve(settings) -> list[str]:
    """manual_approval off → approve every ready clip. Returns the approved clip IDs."""
    if settings.get("manual_approval", True):
        return []
    db = get_db()
    done = []
    for c in db.clips(None, "ready"):
        db.update_clip(c["clip_id"], status="approved")
        db.event("info", c["clip_id"], "approve", "approved automatically (manual_approval = no)")
        done.append(c["clip_id"])
    return done


def reschedule_missed(settings) -> dict:
    """Clips whose Post Time passed without being published get the next free slot."""
    db = get_db()
    tz = _tz(settings)
    grace = timedelta(minutes=int(settings.get("missed_post_grace_minutes", 15)))
    cutoff = (datetime.now(tz) - grace).strftime(FMT)
    posted = {p["clip_id"] for p in db.posts("posted", "publishing")}
    late = [c["clip_id"] for c in db.clips(None, "ready", "approved")
            if c.get("post_time") and c["post_time"] < cutoff and c["clip_id"] not in posted]
    if not late:
        return {}
    moved = assign_post_times(settings, only_clip_ids=late)
    for cid, t in moved.items():
        write_post_info(cid)
        db.event("info", cid, "post_time", f"missed post time, moved to {t}")
    return moved


def plan_posts(settings) -> dict:
    """Make the posts table match the clips: approved clips get one post per platform at their
    Post Time; clips that are not approved any more lose their unpublished posts."""
    db = get_db()
    made = moved = cancelled = 0
    posts = {}
    for p in db.posts():
        posts.setdefault(p["clip_id"], []).append(p)
    for c in db.clips(None, "approved", "posting"):
        have = {p["platform"]: p for p in posts.get(c["clip_id"], [])}
        for pl in platforms_of(c, settings):
            p = have.get(pl)
            if p is None:
                made += db.schedule_post(c["clip_id"], pl, c.get("post_time"))
            elif p["status"] == "cancelled":
                db.update_post(p["id"], status="scheduled", scheduled_at=c.get("post_time"), last_error=None)
                made += 1
            elif p["status"] in LIVE_POST and p["scheduled_at"] != c.get("post_time"):
                db.update_post(p["id"], scheduled_at=c.get("post_time"))
                moved += 1
    for c in db.clips(None, "ready", "rejected", "remake", "qc_failed", "failed", "skipped"):
        for p in posts.get(c["clip_id"], []):
            if p["status"] in LIVE_POST:
                db.update_post(p["id"], status="cancelled")
                cancelled += 1
    return {"made": made, "moved": moved, "cancelled": cancelled}


def record_posted(post_id, external_id="", url="", how="api") -> dict:
    """A platform published the clip (Phase 6 calls this; also used for "posted by hand").
    When every platform of the clip is done, the folder moves to posted_clips/."""
    db = get_db()
    p = next((x for x in db.posts() if x["id"] == post_id), None)
    if not p:
        raise ValueError(f"unknown post {post_id}")
    if p["status"] != "posted":
        db.update_post(post_id, status="posted", external_id=external_id or p.get("external_id"),
                       url=url or p.get("url"), posted_at=now(), last_error=None)
        db.event("info", p["clip_id"], "posted", f"{p['platform']} posted ({how}) {url}".strip())
    return finish_clip(p["clip_id"])


def finish_clip(cid) -> dict:
    db = get_db()
    mine = [p for p in db.posts() if p["clip_id"] == cid and p["status"] != "cancelled"]
    c = db.get_clip(cid)
    if mine and all(p["status"] == "posted" for p in mine):
        if c["status"] != "posted":
            try:
                move_to_posted(cid)
            except PermissionError:
                db.event("error", cid, "posted", "close the clip's folder/video, the folder moves on the next round")
                db.update_clip(cid, status="posting")
                return db.get_clip(cid)
            db.update_clip(cid, status="posted")
            write_post_info(cid)
            log.info(f"{cid}: posted on {', '.join(p['platform'] for p in mine)} → posted_clips/{cid}")
    elif any(p["status"] == "posted" for p in mine) and c["status"] != "posting":
        db.update_clip(cid, status="posting")
    return db.get_clip(cid)


def mark_posted_by_hand(cid, settings, platforms=None, url="") -> dict:
    """You uploaded the clip yourself: mark it posted so the farm never posts it again."""
    db = get_db()
    c = db.get_clip(cid)
    if not c:
        raise ValueError(f"unknown clip {cid}")
    if c["status"] not in ("ready", "approved", "posting"):
        raise ValueError(f"{cid} is '{c['status']}', can't be marked posted")
    if c["status"] == "ready":
        db.update_clip(cid, status="approved")
    plan_posts(settings)
    for p in db.posts():
        if p["clip_id"] == cid and p["status"] in LIVE_POST and (not platforms or p["platform"] in platforms):
            db.update_post(p["id"], status="posted", url=url or None, posted_at=now(), external_id="manual")
            db.event("info", cid, "posted", f"{p['platform']} marked as posted by hand")
    retry_moves()
    return finish_clip(cid)


def retry_moves():
    """Clips whose folder could not be moved last time (file was open)."""
    for c in get_db().clips(None, "posting"):
        finish_clip(c["clip_id"])


def step(settings) -> dict:
    """One round of the queue: approve, fix missed times, sync posts. Safe to call any time."""
    approved = auto_approve(settings)
    if approved:
        assign_post_times(settings)
    moved = reschedule_missed(settings)
    plan = plan_posts(settings)
    retry_moves()
    return {"auto_approved": len(approved), "rescheduled": len(moved), **plan}


# ------------------------------------------------------------------ order of the queue (Sheet → Action)
def _queue(settings):
    """Approved clips that can still move, in posting order. A clip that is posting / due within
    lock_minutes_before_post (15) keeps its time, so an upload about to start is never touched."""
    db = get_db()
    tz = _tz(settings)
    lock = (datetime.now(tz) + timedelta(minutes=int(settings.get("lock_minutes_before_post", 15)))).strftime(FMT)
    busy = {p["clip_id"] for p in db.posts("posted", "publishing")}
    q = [c for c in db.clips(None, "approved") if c.get("post_time") and c["post_time"] > lock
         and c["clip_id"] not in busy]
    return sorted(q, key=lambda c: (c["post_time"], c["clip_id"]))


def _apply_order(settings, order, slots):
    """Give the clips in `order` the times in `slots` (same length, earliest first)."""
    db = get_db()
    moved = {}
    for c, t in zip(order, slots):
        if c.get("post_time") != t:
            db.update_clip(c["clip_id"], post_time=t)
            write_post_info(c["clip_id"])
            moved[c["clip_id"]] = t
    return moved


def reorder(settings, cid, how) -> dict:
    """how: 'skip' | 'post next' | 'post last' | 'move up' | 'move down'. The SAME posting times are kept;
    only which clip gets which changes. Skip: the clip leaves the queue (Skipped tab) and every later clip
    moves one time earlier, so no slot stays empty. Returns {clip_id: new time}."""
    db = get_db()
    q = _queue(settings)
    ids = [c["clip_id"] for c in q]
    slots = [c["post_time"] for c in q]
    c0 = db.get_clip(cid)
    if how == "skip" and c0 and c0["status"] == "ready":       # not approved yet: nothing to re-time
        db.update_clip(cid, status="skipped", skipped_at=now(), post_time=None)
        db.event("info", cid, "skip", "skipped in Google Sheets (Skipped tab)")
        return {}
    if cid not in ids:
        raise ValueError("this clip can't be moved now (posting, due within 15 minutes, or not approved yet)")
    i = ids.index(cid)
    c = q.pop(i)
    if how == "skip":
        db.update_clip(cid, status="skipped", skipped_at=now(), post_time=None)
        db.event("info", cid, "skip", "skipped in Google Sheets (Skipped tab)")
        return _apply_order(settings, q, slots[:len(q)])
    pos = {"post next": 0, "post last": len(q), "move up": max(0, i - 1), "move down": min(len(q), i + 1)}[how]
    q.insert(pos, c)
    db.event("info", cid, "order", f"{how} in Google Sheets")
    return _apply_order(settings, q, slots)


def unskip(settings, cid) -> str:
    """A skipped clip goes back to the END of the queue: the first free posting time after the last one."""
    from posting.slots import next_free_slots
    db = get_db()
    c = db.get_clip(cid)
    if not c or c["status"] != "skipped":
        raise ValueError("not a skipped clip")
    taken = {x["post_time"] for x in db.clips() if x.get("post_time")
             and x["status"] in ("ready", "approved", "scheduled", "posting")}
    last = max(taken) if taken else ""
    t = next(s for s in next_free_slots(settings, 30 + 4 * len(taken), set(taken)) if s > last)
    db.update_clip(cid, status="approved", post_time=t, skipped_at=None)
    write_post_info(cid)
    db.event("info", cid, "unskip", f"back in the queue from the Skipped tab, posts at {t}")
    return t
