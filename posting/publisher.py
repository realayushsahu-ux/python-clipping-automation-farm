"""Phase 6: publish due posts through the official APIs, each clip × platform at most once.

  posting_enabled = no (default)  → nothing is ever published (dry safety switch in Settings)
  a post is claimed atomically (scheduled/failed → publishing) before any upload starts
  Retryable error  → failed, tried again after a wait (max posting_max_attempts, then needs you)
  Permanent error  → needs_manual (fix the cause, then type retry in Ready to Post → Approve)
  Ambiguous error  → needs_manual (may be live: check the channel, type posted or retry)
  stuck "publishing" (farm crashed mid-upload) → needs_manual after posting_stuck_minutes
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from core.db import get_db, now
from core.logger import get_logger
from posting import queue
from posting.errors import Ambiguous, Permanent, Retryable

log = get_logger("publisher")


def _platforms():
    from posting import instagram, youtube_shorts
    return {"youtube": youtube_shorts.publish, "instagram": instagram.publish}


PUBLISHERS = None          # tests put fakes here: {"youtube": fn(job, settings) -> dict}


def job_for(clip) -> dict:
    """Everything a platform needs, from the clip's post.json + its video file."""
    folder = Path(clip.get("folder") or "")
    try:
        p = json.loads((folder / "post.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Permanent(f"post.json missing in {folder}") from exc
    video = folder / f"{clip['clip_id']}.mp4"
    return {"clip_id": clip["clip_id"], "file": str(video), "youtube_title": p.get("youtube_title") or clip.get("title"),
            "youtube_description": p.get("youtube_description") or clip.get("description") or "",
            "instagram_caption": p.get("instagram_caption") or clip.get("caption") or "",
            "hashtags": p.get("hashtags") or [], "search_queries": p.get("search_queries") or [],
            "pin_comment": p.get("pin_comment") or "", "hook": clip.get("hook")}


def recover_stuck(settings) -> int:
    db = get_db()
    limit = (datetime.now() - timedelta(minutes=int(settings.get("posting_stuck_minutes", 120)))).isoformat()
    n = 0
    for p in db.posts("publishing"):
        if (p.get("claimed_at") or "") < limit:
            db.update_post(p["id"], status="needs_manual",
                           last_error="the farm stopped during the upload: check the channel, then type "
                                      "posted (it is there) or retry (it is not) in Ready to Post → Approve")
            db.event("error", p["clip_id"], "publish", f"{p['platform']}: stopped mid-upload, needs a check")
            n += 1
    return n


def publish_one(post_id, settings) -> str:
    """Publish one post now (if it may be published). Returns its new status."""
    db = get_db()
    p = db.get_post(post_id)
    c = db.get_clip(p["clip_id"]) if p else None
    if not p or not c:
        return "unknown"
    if not settings.get("posting_enabled", False):
        return "disabled"
    if c["status"] not in ("approved", "posting"):
        return f"clip is {c['status']}"
    if not db.claim_post(post_id):                    # someone else has it / already posted
        return db.get_post(post_id)["status"]
    db.update_clip(c["clip_id"], status="posting")    # no remake / no while uploading
    fns = PUBLISHERS or _platforms()
    attempts = db.get_post(post_id)["attempts"]
    try:
        fn = fns.get(p["platform"])
        if not fn:
            raise Permanent(f"no publisher for {p['platform']}")
        try:
            res = fn({**job_for(c), "attempt": attempts}, settings)
        except (Retryable, Ambiguous, Permanent):
            raise
        except Exception as exc:  # noqa: BLE001
            # ONLY a dropped connection while SIGNING IN (nothing sent yet) may be retried. A dropped connection
            # anywhere else may come after the video arrived: retrying made a duplicate upload on 2026-11-19
            # (CLIP0016 twice on YouTube) → "maybe posted", a human checks the channel first.
            if _is_network(exc) and _before_upload(exc):
                raise Retryable(f"network error while signing in: {str(exc)[:300]}", 5) from exc
            if _is_network(exc):
                raise Ambiguous(f"connection dropped during the upload: {str(exc)[:300]}") from exc
            raise
    except Retryable as exc:
        if not online():
            # PC just woke up / Wi-Fi down: this try does not count, look again in 10 minutes
            nxt = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
            db.update_post(post_id, status="failed", attempts=max(0, attempts - 1), last_error=str(exc)[:900],
                           next_try=nxt)
            db.event("warning", c["clip_id"], "publish", f"{p['platform']}: no internet, next try {nxt[11:16]}")
            _clip_back(c["clip_id"])
            return "failed"
        if attempts >= int(settings.get("posting_max_attempts", 3)):
            return _manual(p, f"gave up after {attempts} tries: {exc}")
        nxt = (datetime.now() + timedelta(minutes=exc.wait_minutes * attempts)).isoformat(timespec="seconds")
        db.update_post(post_id, status="failed", last_error=str(exc)[:900], next_try=nxt)
        db.event("warning", c["clip_id"], "publish", f"{p['platform']}: {exc} (next try {nxt[11:16]})")
        _clip_back(c["clip_id"])
        return "failed"
    except Ambiguous as exc:
        return _manual(p, f"MAYBE POSTED - check the channel, then type posted or retry: {exc}")
    except Exception as exc:  # noqa: BLE001 - Permanent and anything unexpected
        return _manual(p, str(exc))
    db.update_post(post_id, note=(res.get("note") or "")[:500])
    queue.record_posted(post_id, res.get("external_id", ""), res.get("url", ""), how="api")
    return "posted"


NETWORK_WORDS = ("connectionpool", "connection aborted", "connection reset", "max retries exceeded",
                 "name resolution", "getaddrinfo", "timed out", "temporarily unavailable", "network is unreachable",
                 "remotedisconnected", "connecterror", "transporterror")


def _is_network(exc) -> bool:
    import socket
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
    except ImportError:
        pass
    if isinstance(exc, (socket.timeout, socket.gaierror, ConnectionError, TimeoutError)):
        return True
    txt = f"{type(exc).__name__} {exc}".lower()
    return any(w in txt for w in NETWORK_WORDS)


def _before_upload(exc) -> bool:
    """The error came from the sign-in step (token refresh), before any video byte was sent."""
    txt = str(exc).lower()
    return "oauth2.googleapis.com" in txt or "refresherror" in type(exc).__name__.lower()


def online(host="www.google.com", port=443, timeout=4) -> bool:
    import socket
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _manual(p, msg):
    db = get_db()
    db.update_post(p["id"], status="needs_manual", last_error=msg[:900])
    db.event("error", p["clip_id"], "publish", f"{p['platform']}: {msg}")
    _clip_back(p["clip_id"])
    return "needs_manual"


def _clip_back(cid):
    """Nothing published yet → the clip is plain "approved" again (can still be remade / rejected)."""
    db = get_db()
    c = db.get_clip(cid)
    # only a clip that is still "posting" goes back: a clip you rejected / sent to remake while an upload was
    # running must stay that way (2026-11-20: a rejected clip was revived like this and posted again)
    if c and c["status"] != "posting":
        return
    if not any(x["status"] in ("posted", "publishing") for x in db.posts() if x["clip_id"] == cid):
        db.update_clip(cid, status="approved")


def due_posts(settings, at=None):
    """Posts whose time has come: scheduled with scheduled_at <= now, or failed with next_try <= now."""
    from posting.slots import FMT, _tz
    nw = at or datetime.now(_tz(settings))
    t = nw.strftime(FMT)
    # a post more than missed_post_grace_minutes late is NOT published now: reschedule_missed gives it
    # the next free posting time (PC was off at 18:00 and on again at 18:20 → it goes out at the next time)
    oldest = (nw - timedelta(minutes=int(settings.get("missed_post_grace_minutes", 15)))).strftime(FMT)
    iso = datetime.now().isoformat(timespec="seconds")
    out = [p for p in get_db().posts("scheduled") if p.get("scheduled_at") and oldest <= p["scheduled_at"] <= t]
    out += [p for p in get_db().posts("failed") if (p.get("next_try") or "") <= iso]
    return out


def publish_due(settings) -> dict:
    """One round: recover stuck uploads, then publish everything that is due (oldest first)."""
    if not settings.get("posting_enabled", False):
        return {"disabled": True}
    rep = {"stuck": recover_stuck(settings)}
    if queue.reschedule_missed(settings):         # late clips → next free time, before anything is posted
        queue.plan_posts(settings)
    for p in sorted(due_posts(settings), key=lambda x: x.get("scheduled_at") or ""):
        st = publish_one(p["id"], settings)
        rep[st] = rep.get(st, 0) + 1
    return rep


def retry_clip(cid) -> int:
    """You typed retry: needs_manual / failed posts of this clip are tried again right away."""
    db = get_db()
    n = 0
    for p in db.posts("needs_manual", "failed"):
        if p["clip_id"] == cid:
            db.update_post(p["id"], status="failed", next_try=now(), last_error=None, attempts=0)
            n += 1
    if n:
        db.event("info", cid, "publish", f"retry requested for {n} post(s)")
    return n
