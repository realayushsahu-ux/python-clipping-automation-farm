"""Keeps SQLite (the truth) and Google Sheets (your control panel) in step. One call = one sync:

  read  Settings tab        → overrides config/settings.json for this run
  read  Video Input         → new links are queued; "retry" / "redo" in Status are honoured
  read  Ready to Post       → Action (skip / post next / post last / move up / move down), Approve
                              (yes / no / remake / posted) and Post Time edits are applied
  read  Skipped             → Ready to Post = yes: the clip goes back to the end of the queue
  read  Posted Reels        → Post URL pasted for clips you posted by hand
  run   posting queue       → auto-approve, missed slots, one post per platform (posting/queue.py)
  write every farm column   → Video Input status, Processing, Ready to Post, Posting Schedule,
                              Posted Reels, Failed Tasks
Typing in a farm-owned column does nothing (it is overwritten on the next sync).
"""

import json
import re
import shutil
import time
from datetime import datetime

from core.db import get_db
from core.logger import get_logger
from core.media import fmt_time
from core.outputs import write_post_info
from core import progress
from core.paths import PROCESSING as PROC_DIR
from core.paths import READY as READY_DIR
from google_sheets import tabs as T
from google_sheets.client import Link
from posting import queue

log = get_logger("sheets")
TIME_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})\s*$")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _short(ts):
    return (ts or "")[:16].replace("T", " ")


def _header_map(rows):
    return {h.strip().lower(): i for i, h in enumerate(rows[0])} if rows else {}


def _new_columns(io, tab, rows):
    """An update added a column (e.g. Voiceover, 2026-11-22): rebuild the tab in the new column order, moving
    what you typed by column NAME, so nothing is lost or lands in the wrong column. Unknown columns you
    added yourself are kept at the end."""
    want = T.TABS[tab]["headers"]
    h = _header_map(rows)
    if all(x.lower() in h for x in want):
        return rows
    extra = [i for x, i in h.items() if x not in {w.lower() for w in want}]
    out = [want + [rows[0][i] for i in extra]]
    for r in rows[1:]:
        out.append([_cell(r, h, x) for x in want] + [r[i] if i < len(r) else "" for i in extra])
    io.write_all(tab, out)
    spec = T.TABS[tab]
    io.format_tab(tab, spec.get("widths"), spec.get("dropdowns"), spec.get("typed"))
    log.info(f"{tab}: new column(s) {[x for x in want if x.lower() not in h]} added")
    return out


def _cell(row, hmap, name):
    i = hmap.get(name.lower())
    return row[i].strip() if i is not None and i < len(row) else ""


def _post_json(cid):
    f = READY_DIR / cid / "post.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------------------------ setup
def folder_link(settings, cid):
    from core.clip_server import clip_link
    return Link(f"▶ open {cid}", clip_link(settings, cid))


def refresh_layout(io):
    """Re-apply widths/dropdowns to every tab (after columns moved in an update)."""
    have = set(io.tabs())
    for name, spec in T.TABS.items():
        if name in have:
            io.format_tab(name, spec.get("widths"), spec.get("dropdowns"), spec.get("typed"))
    io.arrange(T.ORDER, T.TAB_COLORS)


def ensure_tabs(io, settings):
    """Create missing tabs (headers, widths, dropdowns). Existing tabs are never wiped."""
    have = set(io.tabs())
    made = []
    for name, spec in T.TABS.items():
        if name not in have:
            io.add_tab(name, spec["headers"], spec.get("widths"), spec.get("dropdowns"), spec.get("typed"))
            made.append(name)
            if name == T.SETTINGS:
                rows = [spec["headers"]]
                for key, meaning in T.SETTINGS_ROWS:
                    v = settings.get(key)
                    v = ",".join(map(str, v)) if isinstance(v, list) else ("yes" if v is True else "no" if v is False else v)
                    rows.append([key, "" if v is None else str(v), meaning])
                io.write_all(name, rows)
    _add_new_settings(io, settings, have)
    if made:
        io.arrange(T.ORDER, T.TAB_COLORS)
    return made


def _add_new_settings(io, settings, have):
    """Settings added by an update are appended to an existing Settings tab (your values stay)."""
    if T.SETTINGS not in have:
        return
    rows = io.read(T.SETTINGS)
    known = {r[0].strip() for r in rows if r}
    extra = []
    for key, meaning in T.SETTINGS_ROWS:
        if key not in known:
            v = settings.get(key)
            v = ",".join(map(str, v)) if isinstance(v, list) else ("yes" if v is True else "no" if v is False else v)
            extra.append([key, "" if v is None else str(v), meaning])
    if extra:
        io.update_cells(T.SETTINGS, [(len(rows) + 1 + i, j + 1, val)
                                     for i, row in enumerate(extra) for j, val in enumerate(row)])


def read_settings(io, settings):
    rows = io.read(T.SETTINGS)
    over = {r[0].strip(): r[1].strip() for r in rows[1:] if len(r) >= 2 and r[0].strip()}
    settings.apply_overrides(over)
    return over


# ------------------------------------------------------------------------------- Video Input
def sync_video_input(io, settings):
    from core.pipeline import add_video, redo_video
    from downloader.video_downloader import video_id_from_url
    db = get_db()
    rows = io.read(T.VIDEO_INPUT)
    if not rows:
        return {"new": 0}
    rows = _new_columns(io, T.VIDEO_INPUT, rows)
    h = _header_map(rows)
    col = {name: h.get(name.lower()) for name in T.TABS[T.VIDEO_INPUT]["headers"]}
    cells, seen, new = [], {}, 0
    for r, row in enumerate(rows[1:], start=2):
        url = _cell(row, h, "URL")
        if not url:
            continue
        vid = video_id_from_url(url)
        want = {}
        if not vid:
            want = {"Status": "failed", "Message": "not a supported video link"}
        elif vid in seen:
            want = {"Video ID": vid, "Status": "duplicate", "Message": f"same video as row {seen[vid]}"}
        else:
            seen[vid] = r
            typed = _cell(row, h, "Status").lower()
            opts = {"clips_wanted": _int(_cell(row, h, "Clips Wanted")), "language": _cell(row, h, "Language") or None,
                    "section": _cell(row, h, "Section") or None, "layout": _cell(row, h, "Layout") or None,
                    "voiceover": _cell(row, h, "Voiceover").lower() or None,
                    "platforms": _cell(row, h, "Platforms") or None}
            v = db.get_video(vid)
            if v is None:
                add_video(url, **opts)
                db.update_video(vid, sheet_row=r)
                db.event("info", vid, "sheets", f"queued from Video Input row {r}")
                new += 1
            elif typed == "retry" and v["status"] == "failed":
                db.update_video(vid, status="retry", last_error=None)
            elif typed == "redo" and v["status"] in ("done", "failed"):
                redo_video(vid, **{k: x for k, x in opts.items() if x})
            if v is not None and (opts["voiceover"] or None) != (v.get("voiceover") or None):
                db.update_video(vid, voiceover=opts["voiceover"])      # changed later: used for clips made from now on
            v = db.get_video(vid)
            made = len(db.clips(vid, "ready", "approved", "scheduled", "posting", "posted"))
            want = {"Video ID": vid, "Status": v["status"], "Title": v.get("title") or "",
                    "Clips Made": str(made) if v["status"] == "done" or made else "",
                    "Added At": _short(v["created_at"]), "Updated At": _short(v["updated_at"]),
                    "Message": _video_message(db, v)}
        for name, val in want.items():
            c = col.get(name)
            if c is not None and (row[c] if c < len(row) else "") != str(val):
                cells.append((r, c + 1, val))
    # videos added outside the sheet (main.py process, or by Claude in chat) get a row too, so Video Input
    # is the complete list of every video the farm ever worked on
    n = len(rows)
    for v in sorted(db.videos(), key=lambda v: v.get("created_at") or ""):
        if v["video_id"] in seen:
            continue
        n += 1
        made = len(db.clips(v["video_id"], "ready", "approved", "scheduled", "posting", "posted"))
        want = {"URL": v["url"], "Video ID": v["video_id"], "Status": v["status"], "Title": v.get("title") or "",
                "Clips Made": str(made) if v["status"] == "done" or made else "",
                "Added At": _short(v["created_at"]), "Updated At": _short(v["updated_at"]),
                "Message": "added outside the sheet"}
        for name, val in want.items():
            c = col.get(name)
            if c is not None:
                cells.append((n, c + 1, val))
        db.update_video(v["video_id"], sheet_row=n)
    io.update_cells(T.VIDEO_INPUT, cells)
    return {"new": new, "cells": len(cells)}


def _int(s):
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------------------ Ready to Post
ACTIONS = ("skip", "post next", "post last", "move up", "move down")


def _apply_actions(rows, settings, notes, done):
    """Action column (Ready to Post or Posting Schedule): skip / post next / post last / move up / move down."""
    h = _header_map(rows)
    for row in rows[1:]:
        cid = _cell(row, h, "Clip ID").upper()
        act = _cell(row, h, "Action").lower()
        if not cid or act not in ACTIONS:
            continue
        try:
            queue.reorder(settings, cid, act)
            done["skipped" if act == "skip" else "moved"] = done.get("skipped" if act == "skip" else "moved", 0) + 1
            log.info(f"{cid}: {act} (from the Sheet)")
        except ValueError as exc:
            notes[cid] = f"{act}: {exc}"


def apply_schedule_edits(io, settings, notes, done):
    """Posting Schedule → Action (the same choices as Ready to Post)."""
    rows = io.read(T.SCHEDULE)
    if rows and "action" in [x.strip().lower() for x in rows[0]]:
        _apply_actions(rows, settings, notes, done)


def _write_tab(io, tab, rows):
    """Write a farm tab; when an update changed its columns, its dropdowns / colours are set again."""
    old = io.read(tab)[:1]
    io.write_all(tab, rows)
    if old and old[0][:len(rows[0])] != rows[0]:
        spec = T.TABS[tab]
        io.format_tab(tab, spec.get("widths"), spec.get("dropdowns"), spec.get("typed"))
        io.arrange(T.ORDER, T.TAB_COLORS)


def apply_ready_edits(io, settings):
    """Approve yes/no/remake and Post Time edits typed in the Ready to Post tab."""
    db = get_db()
    rows = io.read(T.READY)
    if not rows:
        return {}
    h = _header_map(rows)
    done = {"approved": 0, "rejected": 0, "remake": 0, "time": 0, "posted": 0, "skipped": 0, "moved": 0}
    notes = {}
    _apply_actions(rows, settings, notes, done)      # Action first, in row order (the farm clears it after)
    for row in rows[1:]:
        cid = _cell(row, h, "Clip ID").upper()
        c = db.get_clip(cid) if cid else None
        if not c or c["status"] == "skipped":
            continue
        notes[cid] = notes.get(cid) or _cell(row, h, "Notes")
        ans = _cell(row, h, "Approve").lower()
        # act only when YOU changed the cell since the farm last wrote it (a finished remake must
        # not start again just because the old word is still in the cell)
        if ans == (c.get("sheet_approve") or ""):
            ans = None
        if ans is None:
            pass
        elif ans in ("yes", "y", "approve", "approved", "ok") and c["status"] == "ready":
            db.update_clip(cid, status="approved")
            db.event("info", cid, "approve", "approved in Google Sheets")
            done["approved"] += 1
        elif ans in ("posted", "posted by hand", "done") and c["status"] in ("ready", "approved", "posting"):
            try:
                queue.mark_posted_by_hand(cid, settings)
                done["posted"] += 1
            except ValueError as exc:
                notes[cid] = str(exc)
        elif ans == "retry":
            from posting.publisher import retry_clip
            if not retry_clip(cid):
                notes[cid] = "nothing to retry (no failed post)"
        elif ans in ("no", "n", "reject", "rejected") and c["status"] in ("ready", "approved"):
            src = READY_DIR / cid
            try:
                if src.exists():
                    (PROC_DIR / "rejected").mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(PROC_DIR / "rejected" / f"{cid}_{time.strftime('%Y%m%d_%H%M%S')}"))
            except PermissionError:
                notes[cid] = "close the clip's folder/video first, then type no again"
                continue
            db.update_clip(cid, status="rejected", folder="")
            db.event("info", cid, "approve", "rejected in Google Sheets (video stays in clips_generated)")
            done["rejected"] += 1
        elif ans == "" and c["status"] == "approved" and settings.get("manual_approval", True):
            # you cleared "yes" → back to waiting (with manual_approval = no it stays approved)
            db.update_clip(cid, status="ready")
            db.event("info", cid, "approve", "approval removed in Google Sheets")
        elif ans == "remake" and c["status"] in ("ready", "approved"):
            db.update_clip(cid, status="remake")
            db.event("info", cid, "approve", "remake requested in Google Sheets")
            done["remake"] += 1
        typed = _cell(row, h, "Post Time")
        # a time is YOUR edit only if it differs from what the farm last wrote in the cell
        # (the farm may have moved the time since: missed slot / new clip)
        if typed and typed != (c.get("sheet_time") or "") and typed != (c.get("post_time") or "")                 and c["status"] in ("ready", "approved"):
            m = TIME_RE.match(typed)
            if m:
                y, mo, d, hh, mm = map(int, m.groups())
                new = f"{y:04d}-{mo:02d}-{d:02d} {hh:02d}:{mm:02d}"
                db.update_clip(cid, post_time=new)
                write_post_info(cid)
                db.event("info", cid, "post_time", f"post time set to {new} in Google Sheets")
                done["time"] += 1
            else:
                notes[cid] = f"Post Time must look like 2026-11-18 18:00 (got '{typed}')"
    return {"done": done, "notes": notes}


def write_ready(io, settings, notes):
    db = get_db()
    clips = db.clips(None, "ready", "approved", "remake", "posting")
    posts = {}
    for x in db.posts():
        posts.setdefault(x["clip_id"], []).append(x)
    clips.sort(key=lambda c: (c.get("post_time") or "9999", c["clip_id"]))
    rows = [T.TABS[T.READY]["headers"]]
    for c in clips:
        p = _post_json(c["clip_id"])
        approve = {"approved": "yes", "posting": "yes", "remake": "remake"}.get(c["status"], "")
        if (c.get("sheet_approve") or "") != approve or (c.get("sheet_time") or "") != (c.get("post_time") or ""):
            db.update_clip(c["clip_id"], sheet_approve=approve, sheet_time=c.get("post_time") or "")
        mine = [x for x in posts.get(c["clip_id"], []) if x["status"] != "cancelled"]
        n_done = sum(x["status"] == "posted" for x in mine)
        stuck = [x for x in mine if x["status"] == "needs_manual"]
        if stuck and c["clip_id"] not in notes:
            notes[c["clip_id"]] = " | ".join(f"{x['platform']}: {(x.get('last_error') or '')[:150]}" for x in stuck)
        status = {"ready": "waiting approval" if settings.get("manual_approval", True) else "ready",
                  "approved": "scheduled" if mine else "approved", "remake": "remaking…",
                  "posting": f"posting ({n_done}/{len(mine)} done)"}[c["status"]]
        if stuck:
            status = "needs you (see Notes)"
        elif c["status"] == "approved" and mine and not settings.get("posting_enabled", False):
            status = "scheduled (posting off)"
        rows.append([c["clip_id"], folder_link(settings, c["clip_id"]), c.get("title") or "",
                     c.get("post_time") or "", "", approve, status, notes.get(c["clip_id"], ""),
                     (p.get("qc") or {}).get("duration", ""), p.get("instagram_caption", c.get("caption") or ""),
                     " ".join(p.get("hashtags") or []), p.get("youtube_description", c.get("description") or ""),
                     p.get("pin_comment", ""), c.get("hook") or "",
                     ",".join(p.get("platforms") or settings.get("default_platforms") or []), _num(c.get("score")),
                     c.get("kind") or "", c.get("creator") or p.get("creator") or "", c["video_id"]])
    _write_tab(io, T.READY, rows)
    return len(rows) - 1


# ------------------------------------------------------------------------------- Skipped
def apply_skipped_edits(io, settings) -> int:
    """Skipped tab → Ready to Post = yes: the clip goes back to the end of the queue."""
    if T.SKIPPED not in io.tabs():
        return 0
    rows = io.read(T.SKIPPED)
    h = _header_map(rows)
    n = 0
    for row in rows[1:]:
        cid = _cell(row, h, "Clip ID").upper()
        if cid and _cell(row, h, "Ready to Post").lower() in ("yes", "y", "ready"):
            try:
                queue.unskip(settings, cid)
                n += 1
            except (ValueError, StopIteration) as exc:
                log.warning(f"{cid}: could not bring back: {exc}")
    return n


def write_skipped(io, settings):
    db = get_db()
    rows = [T.TABS[T.SKIPPED]["headers"]]
    for c in sorted(db.clips(None, "skipped"), key=lambda c: c.get("skipped_at") or "", reverse=True):
        p = _post_json(c["clip_id"])
        rows.append([c["clip_id"], folder_link(settings, c["clip_id"]), c.get("title") or "", "",
                     _short(c.get("skipped_at") or ""), p.get("instagram_caption", c.get("caption") or ""),
                     " ".join(p.get("hashtags") or []), p.get("youtube_description", c.get("description") or ""),
                     p.get("pin_comment", ""), c.get("hook") or "", (p.get("qc") or {}).get("duration", ""),
                     _num(c.get("score")), c.get("creator") or p.get("creator") or "", c["video_id"]])
    io.write_all(T.SKIPPED, rows)
    return len(rows) - 1


def _num(x):
    return "" if x is None else (int(x) if float(x).is_integer() else x)


def _video_message(db, v) -> str:
    """Video Input → Message: a progress bar while the farm works on it, else the error / result."""
    if v["status"] in progress.WORKING or v["status"] in ("new", "retry"):
        return progress.line(v, db.clips(v["video_id"]))
    return (v.get("last_error") or v.get("stage_detail") or "")[:300]


# --------------------------------------------------------------------- the other (view) tabs
def write_processing(io):
    db = get_db()
    rows = [T.TABS[T.PROCESSING]["headers"]]
    for v in db.videos("new", "retry", "downloading", "transcribing", "analyzing", "editing"):
        rows.append([v["video_id"], v.get("title") or v["url"], v["status"],
                     progress.line(v, db.clips(v["video_id"])), v["attempts"], _short(v["updated_at"]), (v.get("last_error") or "")[:300]])
    for c in db.clips(None, "rendering", "remake"):
        rows.append([c["clip_id"], c.get("title") or c.get("hook") or "", c["status"],
                     f"{fmt_time(c['start'])}-{fmt_time(c['end'])} of {c['video_id']}", "", _short(c["updated_at"]), ""])
    io.write_all(T.PROCESSING, rows)


def _login_fixed_since(posts) -> bool:
    """True when the YouTube login was saved again after these posts last failed on it."""
    try:
        from posting.youtube_shorts import _path
        saved = datetime.fromtimestamp(_path("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json").stat().st_mtime)
        tried = max(datetime.fromisoformat(p["claimed_at"]) for p in posts if p.get("claimed_at"))
        return saved > tried
    except (OSError, ValueError, TypeError, ImportError):
        return False


def _day(ts):
    """'2026-11-20 20:00' -> 'Today 8:00 PM' / 'Tomorrow 10:00 AM' / 'Sat 22 Nov 6:00 PM'."""
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""
    d = (t.date() - datetime.now().date()).days
    day = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(d, t.strftime("%a %d %b"))
    return f"{day} {t.strftime('%I:%M %p').lstrip('0')}"


def _due(p):
    """When a post goes out: its retry time if it failed, else its scheduled time ('YYYY-MM-DD HH:MM')."""
    if p["status"] == "failed" and p.get("next_try"):
        return p["next_try"][:16].replace("T", " ")
    return p.get("scheduled_at") or "9999"


def schedule_items(settings):
    """One item per clip that is still to be posted: (time, clip, platforms, status, problem)."""
    db = get_db()
    rank = {"needs_manual": 0, "failed": 1, "publishing": 2, "scheduled": 3}
    names = {"scheduled": "✅ scheduled" if settings.get("posting_enabled", False) else "⏸ scheduled (posting off)",
             "publishing": "⏳ posting now…", "failed": "⚠ failed, will retry",
             "needs_manual": "❗ needs you (see Problem)"}
    by = {}
    for p in db.posts("scheduled", "publishing", "failed", "needs_manual"):
        by.setdefault(p["clip_id"], []).append(p)
    items = []
    for cid, ps in by.items():
        c = db.get_clip(cid) or {"clip_id": cid}
        worst = min(ps, key=lambda p: rank[p["status"]])
        problem = " | ".join(f"{p['platform']}: {(p.get('last_error') or '')[:150]}"
                             for p in ps if p["status"] in ("failed", "needs_manual") and p.get("last_error"))
        status = names[worst["status"]]
        login_waits = [p for p in ps if p["status"] == "failed"
                       and "youtube login" in (p.get("last_error") or "").lower()]
        if login_waits and _login_fixed_since(login_waits):
            status = "⏳ late, posting soon (late clips go 20 min apart)"
            problem = ""
        elif login_waits:
            status = "🔑 waiting for YouTube login (Desktop: Reconnect YouTube)"
        elif worst["status"] == "failed" and worst.get("next_try"):
            status = f"🔁 trying again at {_day(worst['next_try'][:16].replace('T', ' '))}"
        items.append((min(_due(p) for p in ps), c, sorted(p["platform"] for p in ps), status, problem))
    for c in db.clips(None, "ready"):
        items.append((c.get("post_time") or "9999", c, sorted(queue.platforms_of(c, settings)),
                      "✋ waiting for your yes (Ready to Post)", ""))
    return sorted(items, key=lambda x: (x[0], x[1].get("clip_id", "")))


def write_schedule(io, settings):
    """Every clip still to be posted, one row each, next post first."""
    rows = [T.TABS[T.SCHEDULE]["headers"]]
    pretty = {"youtube": "YouTube", "instagram": "Instagram"}
    for when, c, pls, status, problem in schedule_items(settings):
        when = "" if when == "9999" else when
        rows.append([when, _day(when), c.get("clip_id", ""), "", c.get("title") or "",
                     " + ".join(pretty.get(x, x) for x in pls), status, folder_link(settings, c.get("clip_id", "")),
                     problem])
    _write_tab(io, T.SCHEDULE, rows)


def apply_posted_edits(io):
    """Post URL pasted in Posted Reels (for clips you posted by hand) is saved."""
    db = get_db()
    rows = io.read(T.POSTED)
    if not rows:
        return 0
    h = _header_map(rows)
    known = {(p["clip_id"], p["platform"]): p for p in db.posts("posted")}
    n = 0
    for row in rows[1:]:
        p = known.get((_cell(row, h, "Clip ID").upper(), _cell(row, h, "Platform").lower()))
        url = _cell(row, h, "Post URL")
        if p and url.startswith("http") and not p.get("url"):
            db.update_post(p["id"], url=url)
            write_post_info(p["clip_id"])
            n += 1
    return n


def write_posted(io, settings):
    db = get_db()
    stats = {(s["platform"], s["external_id"]): s for s in db.stats()}
    stats_url = {s["url"]: s for s in stats.values() if s.get("url")}
    rows = [T.TABS[T.POSTED]["headers"]]
    for p in sorted(db.posts("posted"), key=lambda p: p.get("posted_at") or "", reverse=True):
        c = db.get_clip(p["clip_id"]) or {}
        s = stats.get((p["platform"], p.get("external_id") or "")) or stats_url.get(p.get("url") or "-") or {}
        rows.append([_short(p.get("posted_at")), p["clip_id"], c.get("title") or "", p["platform"],
                     p.get("url") or "", _n(s.get("views")), _n(s.get("likes")), _n(s.get("comments")),
                     _feed(p, s), "" if s.get("watched_pct") is None else f"{s['watched_pct']:.0f}%",
                     folder_link(settings, p["clip_id"])])
    io.write_all(T.POSTED, rows)


def _feed(p, s):
    """YouTube only: views from the Shorts feed. 0 after 3+ days = YouTube did not show it in the feed."""
    if p["platform"] != "youtube" or s.get("feed_views") is None:
        return ""
    try:
        age = (datetime.now() - datetime.fromisoformat(str(p.get("posted_at"))[:19])).days
    except ValueError:
        age = 0
    if s["feed_views"] == 0:
        return "0 (not shown in feed)" if age >= 3 else "0 (YouTube data is 2-3 days late)"
    return s["feed_views"]


def _n(x):
    return "" if x is None else x


def write_performance(io, settings):
    from growth.analytics import is_winner
    rows = [T.TABS[T.PERFORMANCE]["headers"]]
    for s in get_db().stats():
        rows.append([s["platform"], "⭐" if is_winner(settings, s) else "",
                     s["views"] if s.get("views") is not None else "(no permission)",
                     _n(s.get("likes")), _n(s.get("comments")), _n(s.get("shares")), _n(s.get("saves")),
                     int(s["duration_s"]) if s.get("duration_s") else "",
                     (s.get("published_at") or "").replace("T", " "), s.get("title") or "", s.get("clip_id") or "",
                     Link("▶ open", s["url"]) if s.get("url") else "", _short(s.get("checked_at"))])
    io.write_all(T.PERFORMANCE, rows)


_trends_written = None


def write_trends(io, force=False):
    """Trends tab from data/trends.json (rewritten only when a new trends run finished)."""
    global _trends_written
    from growth import trends
    st = trends.load()
    if not st or (st.get("at") == _trends_written and not force):
        return 0
    rows = [T.TABS[T.TRENDS]["headers"]]
    for v in st.get("sources", []):
        rows.append(["🎥 source", "", v["views"], v["per_hour"], v["hours"], int(v["duration_s"]), v["channel"],
                     v["title"], "", Link("▶ open", v["url"]),
                     "already in Video Input" if v.get("added") else ("LIVE now" if v.get("live") == "live"
                                                                     else "copy the link into Video Input to clip it")])
    for v in st.get("ideas", []):
        rows.append(["💡 idea", f"{v['multiple']}x", v["views"], v["per_hour"], v["hours"], int(v["duration_s"]),
                     v["channel"], v["title"], v["formula"], Link("▶ open", v["url"]),
                     f"its channel usually gets {v['median']} views"])
    rows.append(["", "", "", "", "", "", "", f"updated {_short(st['at'])} · searches today {st.get('searches', 0)} · "
                 f"hashtags: {' '.join(t for t, _ in st.get('hashtags', [])[:8])}", "", "", ""])
    io.write_all(T.TRENDS, rows)
    _trends_written = st.get("at")
    return len(rows) - 2


_audit_written = None


def write_audit(io, force=False):
    """Audit tab from the newest data/audits/audit_*.json (rewritten only after a new audit)."""
    global _audit_written
    from growth import channel_audit as ca
    a = ca.last()
    if not a or (a.get("at") == _audit_written and not force):
        return 0
    r, f = a["report"], a["facts"]
    rows = [T.TABS[T.AUDIT]["headers"],
            [f"AUDIT {f['date']}", "", f"made by {'AI' if r.get('by') == 'ai' else 'fixed rules (no AI)'}; "
                                        "nothing was changed or published"],
            ["🎯 THE ONE FIX", r.get("fix", ""), r.get("why", "")]]
    rows += [["   this week", s, ""] for s in r.get("this_week") or []]
    rows += [["✅ already working", s, "keep doing it"] for s in r.get("working") or []]
    rows += [["⏸ not yet", s, ""] for s in r.get("not_yet") or []]
    q = f["queue"]
    rows += [["", "", ""], ["FACTS", "", ""],
             ["queue", f"{q['clips_scheduled']} post time(s) booked, {q['empty_slots_next_48h']} of "
                       f"{q['slots_next_48h']} slots empty in the next 48 h",
              f"first empty: {q['first_empty_slot'] or '-'}; videos being processed: {q['videos_being_processed']}"]]
    for pl in ("youtube", "instagram"):
        rh = f.get(f"rhythm_{pl}") or {}
        x = f["results"].get(pl) or {}
        rows.append([f"{pl} rhythm", f"{rh.get('posts_last_14_days', 0)} posts on {rh.get('days_with_posts', 0)} "
                                     f"of the last {rh.get('days_counted', 14)} days", f"longest gap {rh.get('longest_gap_days', 0)} days"])
        if x.get("posts"):
            rows.append([f"{pl} views", f"median of the last 10: {_n(x.get('median_views_last_10'))}; "
                                        f"10 before: {_n(x.get('median_views_10_before'))}",
                         x.get("note") or f"winners ({x.get('winner_threshold')}+ views): {x.get('winners')}"])
            for key, label in (("by_length", "length"), ("by_kind", "kind"), ("by_hour", "post hour")):
                if x.get(key):
                    rows.append([f"{pl} by {label}", "; ".join(f"{k}: {v['median_views']} ({v['posts']} posts)"
                                                              for k, v in sorted(x[key].items())), "median views"])
    t = f.get("titles_youtube_last_10") or {}
    if t.get("count"):
        rows.append(["titles (last 10)", f"lint score median {t['median_lint_score']}/100",
                     "issues: " + ", ".join(f"{k} x{n}" for k, n in t["common_issues"]) +
                     "; shapes: " + ", ".join(f"{k} x{n}" for k, n in t["same_shape"])])
    hk = f.get("hooks_last_10_clips") or {}
    if hk.get("count"):
        rows.append(["hooks (last 10 clips)", f"fit median {hk['median_hook_fit']}/100",
                     "weakest: " + "; ".join(f"{c} '{h}' {s}" for c, h, s in hk["weakest"])])
    ret = f.get("retention") or {}
    rows.append(["retention", f"median {ret['median_avg_view_pct']}% of each Short watched ({ret['days']} days)"
                 if ret.get("available") else "not connected", ret.get("note") or ret.get("error") or
                 "optional: main.py auth youtube-analytics (read-only) shows how much of each Short people watch"])
    h = f["health"]
    rows.append(["health", f"{h['posts_needing_you']} post(s) need you, {h['posts_failed']} failed",
                 f"{h['errors_last_7_days']} error(s) in 7 days"])
    past = ca.history()[1:]
    if past:
        rows += [["", "", ""], ["EARLIER FIXES", "", ""]] + [[d, fx, ""] for d, fx in past]
    io.write_all(T.AUDIT, rows)
    _audit_written = a.get("at")
    return len(rows)


def write_failed(io):
    db = get_db()
    rows = [T.TABS[T.FAILED]["headers"]]
    for v in db.videos("failed"):
        rows.append([_short(v["updated_at"]), v["video_id"], "video", (v.get("last_error") or "")[:450],
                     "fix the link/problem, then type retry in Video Input → Status"])
    for c in db.clips(None, "failed", "qc_failed"):
        try:
            qc = json.loads(c.get("qc") or "{}")
        except ValueError:
            qc = {}
        err = qc.get("error") or "; ".join(qc.get("issues") or []) or c["status"]
        rows.append([_short(c["updated_at"]), c["clip_id"], c["status"], err[:450],
                     "video is in clips_generated (…_QC_FAILED.mp4); remake: main.py remake " + c["clip_id"]])
    for e in db.events("error", 30):
        if e["item"] and any(r[1] == e["item"] for r in rows[1:]):
            continue
        rows.append([_short(e["at"]), e["item"] or "", e["stage"] or "", (e["message"] or "").splitlines()[0][:450],
                     "see logs/farm.log"])
    io.write_all(T.FAILED, rows)


# ------------------------------------------------------------------------------ START HERE
GUIDE_HOW = [
    ["HOW TO USE (3 steps)", ""],
    ["1. Add a video", "Video Input tab: paste a YouTube/Twitch link in the URL column. That's all. "
                       "The farm downloads it, finds the best moments and edits the clips by itself."],
    ["2. Check the clips", "Ready to Post tab: click the Watch link to play a clip. "
                           "Action column: skip = not now (it waits in the Skipped tab, the next clips move up), "
                           "post next / post last / move up / move down = change the order (the times stay). "
                           "Approve column: no = never post it, remake = edit it again. "
                           "Post Time = when it goes out (write it like 2026-11-21 18:00)."],
    ["   Skipped clips", "Skipped tab: every skipped clip with its caption and hashtags. Ready to Post = yes → "
                        "it goes back to the end of the queue with the next free time."],
    ["3. Relax", "Clips post by themselves at their time to YouTube Shorts + Instagram Reels. "
                 "Posting Schedule = what goes out next. Posted Reels = links + views of what went out."],
    ["", ""],
    ["COLOURS", ""],
    ["Green tab / green column title", "You may type here."],
    ["Blue tab", "Just look: the farm fills it every few minutes (typing there is overwritten)."],
    ["Grey tab", "Advanced (work in progress, errors, trends, weekly audit). You can ignore these."],
    ["", ""],
    ["GOOD TO KNOW", ""],
    ["Stop all posting", "Settings tab: posting_enabled = no  (yes = posting again)."],
    ["Change the daily post times", "Settings tab: posting_times, e.g. 10:00,18:00,20:00,22:00,23:30 (India time)."],
    ["Something is wrong", "Posting Schedule: the Status shows ❗ and the Problem column says why."],
    ["Watch links", "Work only on the farm PC while the farm is running."],
]


def write_guide(io, settings):
    """START HERE: what is happening now (top) + how to use the sheet."""
    db = get_db()
    items = schedule_items(settings)
    nxt = next((x for x in items if x[0] != "9999"), None)
    today = datetime.now().strftime("%Y-%m-%d")
    posted_today = {p["clip_id"] for p in db.posts("posted") if (p.get("posted_at") or "").startswith(today)}
    working = db.videos("new", "retry", "downloading", "transcribing", "analyzing", "editing")
    stuck = [x for x in items if x[3].startswith("❗")]
    on = settings.get("posting_enabled", False)
    rows = [["CLIPPING FARM", f"updated {_now()}"],
            ["RIGHT NOW", ""],
            ["Posting", "✅ ON: clips post by themselves" if on
             else "⏸ OFF: nothing is posted (Settings: posting_enabled)"],
            ["Next post", f"{_day(nxt[0])}: {nxt[1].get('title') or nxt[1].get('clip_id')}" if nxt
             else "nothing scheduled"],
            ["Clips waiting to post", str(len(items))],
            ["Clips posted today", str(len(posted_today))],
            ["Videos being edited", ", ".join((v.get("title") or v["url"])[:60] for v in working) or "none"],
            ["Needs your attention", f"❗ {len(stuck)} clip(s): see Posting Schedule" if stuck else "nothing 👍"],
            ["", ""]] + GUIDE_HOW
    io.write_all(T.GUIDE, rows)


# -------------------------------------------------------------------------------- one sync
def sync(io, settings):
    """One full sync. Returns a small report."""
    t0 = time.time()
    made = ensure_tabs(io, settings)
    read_settings(io, settings)
    vi = sync_video_input(io, settings)
    edits = apply_ready_edits(io, settings)
    apply_schedule_edits(io, settings, edits.setdefault("notes", {}), edits.setdefault("done", {}))
    edits["done"]["back"] = apply_skipped_edits(io, settings)
    urls = apply_posted_edits(io)
    q = queue.step(settings)
    n_ready = write_ready(io, settings, edits.get("notes", {}))
    write_skipped(io, settings)
    write_processing(io)
    write_schedule(io, settings)
    write_guide(io, settings)
    write_posted(io, settings)
    write_failed(io)
    write_performance(io, settings)
    write_trends(io)
    write_audit(io)
    rep = {"tabs_created": made, "new_videos": vi.get("new", 0), "ready_rows": n_ready,
           "approvals": edits.get("done", {}), "queue": q, "urls_saved": urls, "seconds": round(time.time() - t0, 1)}
    log.info(f"sheets sync: {rep}")
    return rep
