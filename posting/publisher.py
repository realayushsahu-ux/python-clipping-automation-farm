"""Phase 6: publish due posts through the official APIs, each clip × platform at most once.

  posting_enabled = no (default)  → nothing is ever published (dry safety switch in Settings)
  a post is claimed atomically (scheduled/failed → publishing) before any upload starts
  Retryable error  → failed, tried again after a wait (max posting_max_attempts, then needs you)
  Permanent error  → needs_manual (fix the cause, then type retry in Ready to Post → Approve)
  Ambiguous error  → needs_manual (may be live: check the channel, type posted or retry)
  stuck "publishing" (farm stopped mid-upload) → the farm checks the channel: live → posted, not there → again;
                    only if it cannot check for posting_stuck_minutes → needs_manual
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from core.db import get_db, now
from core.logger import get_logger
from posting import queue
from posting.errors import Ambiguous, LoginNeeded, Permanent, Retryable

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


LIVE_CHECK = None         # tests put a fake here: fn(post, clip) -> {"external_id", "url"} | None
_first_round = True       # the first round after the farm (re)started: every "publishing" post is left over
_STARTED = datetime.now().isoformat()   # a post claimed before this farm run started was cut off by the restart


def find_live(p, clip):
    """Is a post that was cut off mid-upload (power cut, crash) live after all? Reads the newest uploads of
    the channel with the official APIs (read only) and matches the title / first caption line."""
    if LIVE_CHECK:
        return LIVE_CHECK(p, clip)
    if PUBLISHERS:
        raise RuntimeError("no live check in tests")
    job = job_for(clip)
    if p["platform"] == "youtube":
        from growth.analytics import fetch_youtube
        title = (job.get("youtube_title") or job.get("hook") or clip["clip_id"]).replace("<", "").replace(">", "")
        title = title.strip()[:100]
        for r in fetch_youtube(limit=25):
            if r["title"].strip() == title:
                return {"external_id": r["external_id"], "url": r["url"]}
        return None
    if p["platform"] == "instagram":
        import requests
        from posting.instagram import _cfg, build_caption
        c = _cfg()
        first = build_caption(job, {}).split("\n")[0].strip()[:100]
        r = requests.get(f"https://{c['host']}/{c['ver']}/{c['ig']}/media", timeout=30, params={
            "fields": "id,caption,permalink", "limit": 25, "access_token": c["token"]}).json()
        if "error" in r:
            raise RuntimeError(str(r["error"].get("message"))[:200])
        for m in r.get("data", []):
            if first and (m.get("caption") or "").split("\n")[0].strip()[:100] == first:
                return {"external_id": m["id"], "url": m.get("permalink", "")}
        return None
    raise RuntimeError(f"no live check for {p['platform']}")


def recover_stuck(settings, startup=False) -> int:
    """Posts left in "publishing" because the farm stopped mid-upload (PC off, crash, closed window).
    The farm looks at the channel itself: live → recorded as posted; not there → uploaded again.
    Only when it cannot look (no internet / API error) for posting_stuck_minutes does it ask you."""
    db = get_db()
    limit = (datetime.now() - timedelta(minutes=int(settings.get("posting_stuck_minutes", 120)))).isoformat()
    settle = (datetime.now() - timedelta(minutes=10)).isoformat()   # never judge an upload younger than this
    n = 0
    for p in db.posts("publishing"):
        claimed = p.get("claimed_at") or ""
        # judged at start, once it is older than posting_stuck_minutes, or (found 2026-11-21: a window closed
        # 6 minutes into an upload left it waiting 2 hours) as soon as it settled when an earlier run claimed it
        if not (startup or claimed < limit or claimed < _STARTED) or claimed > settle:
            continue
        clip = db.get_clip(p["clip_id"])
        try:
            live = find_live(p, clip) if clip else None
        except Exception as exc:  # noqa: BLE001 - could not look: try again next round, ask you only later
            log.warning(f"{p['clip_id']} {p['platform']}: cut-off upload, could not check the channel: {str(exc)[:150]}")
            live = "unknown"
        if live == "unknown":
            if claimed >= limit:
                continue
        elif live:
            queue.record_posted(p["id"], live["external_id"], live["url"])
            db.event("info", p["clip_id"], "publish", f"{p['platform']}: upload was cut off but it is live: {live['url']}")
            log.info(f"{p['clip_id']}: {p['platform']} upload was cut off, but it is live ({live['url']})")
            n += 1
            continue
        else:
            db.update_post(p["id"], status="failed", next_try=now(), attempts=max(0, int(p.get("attempts") or 1) - 1),
                           last_error="the farm stopped during the upload; it is not on the channel, uploading again")
            db.event("info", p["clip_id"], "publish", f"{p['platform']}: cut-off upload not live, trying again")
            log.info(f"{p['clip_id']}: {p['platform']} upload was cut off and is not live; uploading it again")
            n += 1
            continue
        if claimed < limit:
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
        most = int(settings.get("posting_max_attempts", 3))
        if "still processing" in str(exc) or "network error" in str(exc):
            most *= 2                   # Instagram's slow side or an internet blink: nothing is wrong with the clip
        if attempts >= most:
            return _manual(p, f"gave up after {attempts} tries: {exc}")
        nxt = (datetime.now() + timedelta(minutes=exc.wait_minutes * attempts)).isoformat(timespec="seconds")
        db.update_post(post_id, status="failed", last_error=str(exc)[:900], next_try=nxt)
        db.event("warning", c["clip_id"], "publish", f"{p['platform']}: {exc} (next try {nxt[11:16]})")
        _clip_back(c["clip_id"])
        return "failed"
    except LoginNeeded as exc:
        # the login, not the clip: wait (this try does not count) and look again in 30 min
        nxt = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
        db.update_post(post_id, status="failed", attempts=max(0, attempts - 1), last_error=str(exc)[:900],
                       next_try=nxt)
        _clip_back(c["clip_id"])
        return "waiting_login"
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
    from core import net
    if not net.online():                          # no internet: post nothing (no try is used up), check again later
        if not net.OFFLINE.exists():
            net.OFFLINE.write_text(now(), encoding="utf-8")
        return {"offline": True}
    net.OFFLINE.unlink(missing_ok=True)
    global _first_round
    rep = {"stuck": recover_stuck(settings, startup=_first_round and not PUBLISHERS)}
    _first_round = False
    if queue.reschedule_missed(settings):         # late clips → next free time, before anything is posted
        queue.plan_posts(settings)
    _heal_login_waits()
    last = _last_posted()
    gap = timedelta(minutes=int(settings.get("catch_up_gap_minutes", 20)))
    late = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    for p in sorted(due_posts(settings), key=lambda x: x.get("scheduled_at") or ""):
        # catch-up (a post 2+ hours late, e.g. after the login came back): one at a time per platform,
        # catch_up_gap_minutes apart, so 8 waiting Shorts don't all land on the channel in the same minute
        if (p.get("scheduled_at") or "") < late and datetime.now() - last.get(p["platform"], datetime.min) < gap:
            continue
        st = publish_one(p["id"], settings)
        rep[st] = rep.get(st, 0) + 1
        if st == "posted":
            last[p["platform"]] = datetime.now()
    return rep


def _last_posted() -> dict:
    out = {}
    for p in get_db().posts("posted"):
        try:
            t = datetime.fromisoformat((p.get("posted_at") or "")[:19])
        except ValueError:
            continue
        out[p["platform"]] = max(out.get(p["platform"], datetime.min), t)
    return out


def _heal_login_waits():
    """Posts that went to "needs you" only because a login had expired (before LoginNeeded existed, or by
    an older message) wait for the login instead: they go out by themselves once you sign in again."""
    db = get_db()
    for p in db.posts("needs_manual"):
        err = (p.get("last_error") or "").lower()
        if "not connected" in err or "login expired" in err or "expired or revoked" in err:
            db.update_post(p["id"], status="failed", next_try=now(), attempts=0,
                           last_error="waiting for the YouTube login (double-click 'Reconnect YouTube' on the Desktop)")


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
