"""Phase 8: weekly channel audit (yt-audit method) → ONE fix for this week, 3 things that work, what not to do yet.

Facts are measured in code, never guessed:
  queue      clips left vs your posting times (empty slots coming up = nothing to post)
  rhythm     posts per day over the last 14 days, longest gap (consistency beats volume)
  titles     the last 10 YouTube titles as a set: title lint (yt-package) + same-shape check
  hooks      on-screen hooks of the last 10 farm clips (hook_fit)
  results    median views last 10 vs the 10 before, winners, views by clip length / kind / posting hour
  retention  average % viewed per Short (YouTube Analytics, optional: main.py auth youtube-analytics)
  health     failed posts, errors, missing permissions
Then the AI (or a fixed rule list without AI) picks the ONE fix from those facts. Never invents a number.
Nothing is changed or published: the report lands in the Sheet tab "Audit" and data/audits/.
"""

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from config.settings import env
from core.db import get_db
from core.logger import get_logger
from core.paths import DATA_DIR, ROOT

log = get_logger("audit")
AUDITS = DATA_DIR / "audits"
MIN_COMPARE = 10                    # posts with views needed before comparing hours / lengths / kinds
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]

SYSTEM = """You audit ONE small Shorts/Reels channel that posts GTA streamer clips, using ONLY the measured FACTS given.
An audit that lists twenty problems avoids the one that matters: name the SINGLE biggest fix and exactly what
to do this week. Then three things that already work (specific, from the facts, so they are not broken), and
what NOT to do yet and why. Rules: use only numbers that are in the facts, never invent one; say when a sample
is too small to judge; never call anything viral; nothing is published by you. Short, plain English for a
non-programmer: never write field names (like empty_slots_next_48h or median_lint_score), say it in words.
Empty posting slots are skipped (nothing is posted then), so an empty queue means the channel stops posting;
only the user can add new videos (links in the Sheet's Video Input). Hours in by_hour are India time."""

SCHEMA = {"type": "object", "properties": {
    "fix": {"type": "string", "description": "the ONE biggest fix, max 12 words"},
    "why": {"type": "string", "description": "why, citing the facts, max 40 words"},
    "this_week": {"type": "array", "items": {"type": "string"}, "description": "1-3 concrete steps for this week"},
    "working": {"type": "array", "items": {"type": "string"}, "description": "exactly 3 specific things that work"},
    "not_yet": {"type": "array", "items": {"type": "string"}, "description": "1-2 things not to do yet + why"}}}


# ------------------------------------------------------------------------------------------- helpers
def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "")[:19].replace(" ", "T"))
    except ValueError:
        return None


def _local(s, tz):
    """Platform times are UTC → local (naive) time, so days and hours match your posting times."""
    from datetime import timezone
    d = _dt(s)
    return d.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None) if d and tz else d


def _med(xs):
    xs = [x for x in xs if x is not None]
    return int(statistics.median(xs)) if xs else None


def _bucket(sec):
    if not sec:
        return None
    return "under 35 s" if sec < 35 else "35-50 s" if sec <= 50 else "over 50 s"


# ----------------------------------------------------------------------------------------- the facts
def queue_facts(settings, now):
    from posting.slots import _slot_times
    db = get_db()
    upcoming = sorted({p["scheduled_at"] for p in db.posts("scheduled", "failed")
                       if p.get("scheduled_at") and p["scheduled_at"] >= now.strftime("%Y-%m-%d %H:%M")})
    waiting = [c["clip_id"] for c in db.clips(None, "ready", "approved", "rendered") if not c.get("post_time")]
    slots = []
    for d in range(3):
        day = (now + timedelta(days=d)).strftime("%Y-%m-%d")
        slots += [f"{day} {t}" for t in _slot_times(settings) if f"{day} {t}" > now.strftime("%Y-%m-%d %H:%M")]
    slots = [s for s in slots if s <= (now + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M")]
    empty = [s for s in slots if s not in upcoming]
    in_work = len(db.videos("new", "retry", "downloading", "transcribing", "analyzing", "editing"))
    return {"posts_per_day": len(_slot_times(settings)), "clips_scheduled": len(upcoming),
            "clips_waiting_unscheduled": len(waiting), "videos_being_processed": in_work,
            "slots_next_48h": len(slots), "empty_slots_next_48h": len(empty), "first_empty_slot": empty[0] if empty else None}


def rhythm_facts(rows, now, tz=None):
    days = defaultdict(int)
    first = None
    for r in rows:
        d = _local(r.get("published_at"), tz)
        if d:
            first = min(first or d, d)
        if d and d >= now - timedelta(days=14):
            days[d.date()] += 1
    if not first:
        return {"posts_last_14_days": 0, "days_with_posts": 0, "longest_gap_days": 0, "days_counted": 0}
    span = min(14, (now.date() - first.date()).days + 1)        # a new channel: count from its first post
    seq = [days.get((now - timedelta(days=i)).date(), 0) for i in range(span - 1, -1, -1)]
    gap = best = 0
    for n in seq:
        gap = gap + 1 if n == 0 else 0
        best = max(best, gap)
    return {"posts_last_14_days": sum(seq), "days_with_posts": sum(1 for n in seq if n),
            "longest_gap_days": best, "days_counted": span, "per_day_oldest_first": seq}


def title_facts(rows):
    from ai.quality import lint_title, similar
    from growth.trends import classify
    titles = [r["title"] for r in rows if r.get("title")][:10]
    if not titles:
        return {"count": 0}
    lints = [lint_title(t) for t in titles]
    issues = Counter(i[0] for x in lints for i in x["issues"])
    shapes = Counter(classify(t) for t in titles)
    near = sum(1 for i, a in enumerate(titles) for b in titles[i + 1:] if similar(a, b) >= 0.5)
    return {"count": len(titles), "median_lint_score": _med([x["score"] for x in lints]),
            "common_issues": issues.most_common(4), "same_shape": shapes.most_common(3),
            "near_duplicate_pairs": near, "titles": titles}


def hook_facts():
    from ai.quality import hook_fit
    clips = [c for c in get_db().clips() if c.get("hook") and c["status"] not in ("rejected", "qc_failed")][-10:]
    if not clips:
        return {"count": 0}
    fits = [(c["clip_id"], c["hook"], hook_fit(c["hook"])[0]) for c in clips]
    return {"count": len(fits), "median_hook_fit": _med([f[2] for f in fits]),
            "weakest": sorted(fits, key=lambda f: f[2])[:2], "kinds": Counter(c.get("kind") for c in clips).most_common()}


def result_facts(settings, now, tz=None):
    from growth import analytics
    db = get_db()
    out = {}
    for pl in ("youtube", "instagram"):
        rows = sorted(db.stats(pl), key=lambda r: r.get("published_at") or "", reverse=True)
        known = [r for r in rows if r.get("views") is not None]
        if not rows:
            continue
        f = {"posts": len(rows), "views_known": len(known),
             "median_views_last_10": _med([r["views"] for r in known[:10]]),
             "median_views_10_before": _med([r["views"] for r in known[10:20]]),
             "winners": sum(1 for r in known if analytics.is_winner(settings, r)),
             "winner_threshold": analytics.threshold(settings, pl)}
        by_len, by_kind, by_hour = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in known:
            if _bucket(r.get("duration_s")):
                by_len[_bucket(r["duration_s"])].append(r["views"])
            c = db.get_clip(r["clip_id"]) if r.get("clip_id") else None
            if c and c.get("kind"):
                by_kind[c["kind"]].append(r["views"])
            d = _local(r.get("published_at"), tz)
            if d:
                by_hour[f"{d.hour:02d}h"].append(r["views"])
        if len(known) < MIN_COMPARE:            # one lucky post is not a pattern: no hour/length/kind advice yet
            f["breakdowns"] = f"not shown: only {len(known)} posts with views, need {MIN_COMPARE} to compare"
        else:
            for name, grp in (("by_length", by_len), ("by_kind", by_kind), ("by_hour", by_hour)):
                if grp:
                    f[name] = {k: {"posts": len(v), "median_views": _med(v)} for k, v in grp.items()}
        out[pl] = f
    if analytics.instagram_views_missing() or (out.get("instagram") and not out["instagram"]["views_known"]):
        out.setdefault("instagram", {})["note"] = "views unknown: Page token lacks instagram_manage_insights"
    return out


def analytics_credentials(interactive=False):
    """Separate read-only token (secrets/youtube_analytics_token.json), so the posting login is never touched."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token = ROOT / env("YOUTUBE_ANALYTICS_TOKEN_FILE", "secrets/youtube_analytics_token.json")
    creds = Credentials.from_authorized_user_file(str(token), ANALYTICS_SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            creds = None
    if creds and creds.valid:
        return creds
    if not interactive:
        return None
    from google_auth_oauthlib.flow import InstalledAppFlow
    secret = ROOT / env("YOUTUBE_CLIENT_SECRET_FILE", "secrets/client_secret.json")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), ANALYTICS_SCOPES)
    extra = {"login_hint": env("YOUTUBE_LOGIN_HINT")} if env("YOUTUBE_LOGIN_HINT") else {}
    creds = flow.run_local_server(port=0, prompt="consent select_account", **extra)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def retention_facts(now, creds=None, days=28):
    creds = creds or analytics_credentials()
    if not creds:
        return {"available": False, "how": "main.py auth youtube-analytics (optional, read-only)"}
    from googleapiclient.discovery import build
    ya = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    r = ya.reports().query(ids="channel==MINE", startDate=(now - timedelta(days=days)).strftime("%Y-%m-%d"),
                           endDate=now.strftime("%Y-%m-%d"), dimensions="video", sort="-views", maxResults=25,
                           metrics="views,averageViewDuration,averageViewPercentage").execute()
    titles = {s["external_id"]: s.get("title") for s in get_db().stats("youtube")}
    rows = [{"video": v[0], "title": (titles.get(v[0]) or "")[:60], "views": v[1], "avg_view_s": v[2],
             "avg_view_pct": round(v[3])} for v in r.get("rows", [])]
    return {"available": True, "days": days, "videos": rows,
            "median_avg_view_pct": _med([x["avg_view_pct"] for x in rows]),
            "note": "YouTube Analytics lags about 2 days"}


def health_facts(now):
    db = get_db()
    since = (now - timedelta(days=7)).isoformat()
    errs = [e for e in db.events("error", 200) if (e.get("at") or "") >= since]
    return {"posts_needing_you": len(db.posts("needs_manual")), "posts_failed": len(db.posts("failed")),
            "errors_last_7_days": len(errs),
            "top_errors": Counter((e.get("stage") or "?") for e in errs).most_common(3)}


def facts(settings, now=None, retention=None) -> dict:
    from posting.slots import _tz
    now = now or datetime.now()
    tz = _tz(settings)
    yt = sorted(get_db().stats("youtube"), key=lambda r: r.get("published_at") or "", reverse=True)
    ig = get_db().stats("instagram")
    f = {"date": now.strftime("%Y-%m-%d"), "queue": queue_facts(settings, now),
         "rhythm_youtube": rhythm_facts(yt, now, tz), "rhythm_instagram": rhythm_facts(ig, now, tz),
         "titles_youtube_last_10": title_facts(yt), "hooks_last_10_clips": hook_facts(),
         "results": result_facts(settings, now, tz), "health": health_facts(now)}
    try:
        f["retention"] = retention if retention is not None else retention_facts(now)
    except Exception as exc:  # noqa: BLE001 - optional; the audit works without it
        f["retention"] = {"available": False, "error": str(exc)[:200]}
    return f


# --------------------------------------------------------------------------------- the ONE fix
def rule_based(f) -> dict:
    """Without AI: fixed priority list on the same facts."""
    q, h, yt = f["queue"], f["health"], f["results"].get("youtube", {})
    r = f["rhythm_youtube"]
    if q["empty_slots_next_48h"] > h["posts_needing_you"] and not q["videos_being_processed"]:
        fix = {"fix": "Feed the farm: add new stream links", "why": f"{q['empty_slots_next_48h']} of the next "
               f"{q['slots_next_48h']} posting slots have no clip (first: {q['first_empty_slot']}); empty slots are skipped.",
               "this_week": ["Paste 2-3 fresh GTA 6 stream links into Video Input (see the Trends tab, 🎥 sources)"]}
    elif h["posts_needing_you"] or h["posts_failed"]:
        fix = {"fix": "Clear the posts that need you", "why": f"{h['posts_needing_you']} post(s) wait for you and "
               f"{h['posts_failed']} failed.", "this_week": ["Check Posting Schedule, type retry or posted in Approve"]}
    elif r["longest_gap_days"] >= 3:
        fix = {"fix": "Post every day, without gaps", "why": f"longest gap in 14 days: {r['longest_gap_days']} days.",
               "this_week": ["Keep at least 3 clips scheduled ahead"]}
    else:
        fix = {"fix": "Keep the rhythm, test one hook style", "why": "queue and rhythm are fine.",
               "this_week": ["Compare hooks in the Performance tab after 7 days"]}
    working = []
    if yt.get("winners"):
        working.append(f"{yt['winners']} YouTube Short(s) passed {yt['winner_threshold']} views")
    if r["days_with_posts"]:
        working.append(f"posted on {r['days_with_posts']} of the last 14 days")
    if f["hooks_last_10_clips"].get("median_hook_fit"):
        working.append(f"on-screen hooks score {f['hooks_last_10_clips']['median_hook_fit']}/100 for fit")
    fix.update(working=working[:3], not_yet=["Do not change posting times yet: too few posts to compare hours"])
    return fix


def decide(settings, f) -> dict:
    from ai.base import ask_json
    res = ask_json(settings, SYSTEM, json.dumps(f, ensure_ascii=False, default=str), SCHEMA, max_tokens=6000,
                   voice=False, offline_fn=lambda: None, effort="low")
    if not res or not res.get("fix"):
        res = rule_based(f)
        res["by"] = "rules"
    else:
        res["by"] = "ai"
    return res


# ------------------------------------------------------------------------------------- run + save
def last() -> dict:
    files = sorted(AUDITS.glob("audit_*.json")) if AUDITS.exists() else []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else {}
    except (OSError, ValueError):
        return {}


def history(n=6) -> list:
    out = []
    for p in sorted(AUDITS.glob("audit_*.json"), reverse=True)[:n] if AUDITS.exists() else []:
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
            out.append((a["facts"]["date"], a["report"].get("fix", "")))
        except (OSError, ValueError, KeyError):
            continue
    return out


def due(settings, now=None) -> bool:
    if not settings.get("audit_enabled", True):
        return False
    now = now or datetime.now()
    d = _dt((last().get("facts") or {}).get("date"))
    return d is None or (now - d).days >= int(settings.get("audit_every_days", 7))


def run(settings, now=None, retention=None) -> dict:
    now = now or datetime.now()
    f = facts(settings, now, retention)
    rep = decide(settings, f)
    AUDITS.mkdir(parents=True, exist_ok=True)
    out = {"at": now.isoformat(timespec="seconds"), "facts": f, "report": rep}
    (AUDITS / f"audit_{now:%Y-%m-%d}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                                       encoding="utf-8")
    (AUDITS / f"audit_{now:%Y-%m-%d}.md").write_text(to_text(out), encoding="utf-8")
    log.info(f"audit {now:%Y-%m-%d}: {rep.get('fix')} ({rep.get('by')})")
    return out


def to_text(a) -> str:
    r, f = a["report"], a["facts"]
    lines = [f"CHANNEL AUDIT {f['date']}  (by {r.get('by')})", "", f"THE ONE FIX: {r.get('fix')}", f"  why: {r.get('why', '')}"]
    lines += [f"  this week: {s}" for s in r.get("this_week") or []]
    lines += ["", "ALREADY WORKING (don't break it):"] + [f"  + {s}" for s in r.get("working") or []]
    lines += ["", "NOT YET:"] + [f"  - {s}" for s in r.get("not_yet") or []]
    q = f["queue"]
    lines += ["", f"queue: {q['clips_scheduled']} scheduled, {q['empty_slots_next_48h']}/{q['slots_next_48h']} slots empty "
                  f"in the next 48 h, {q['videos_being_processed']} video(s) being processed"]
    for pl in ("youtube", "instagram"):
        x = f["results"].get(pl)
        if x and x.get("posts"):
            lines.append(f"{pl}: {x['posts']} posts, median views last 10: {x.get('median_views_last_10')}, "
                         f"winners: {x.get('winners')}" + (f"  ({x['note']})" if x.get("note") else ""))
    ret = f.get("retention") or {}
    lines.append(f"retention: median {ret.get('median_avg_view_pct')}% watched" if ret.get("available")
                 else "retention: not connected (main.py auth youtube-analytics)")
    return "\n".join(lines) + "\n"
