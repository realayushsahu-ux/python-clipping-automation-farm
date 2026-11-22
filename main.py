"""Python Clipping Automation Farm - command line.

  python main.py doctor                     check tools, models, keys
  python main.py process URL [options]      clip one video now
  python main.py run                        process every queued video (Sheets sync: Phase 4)
  python main.py status                     what the farm knows
  python main.py sheets setup|sync|check    create the 7 tabs / sync once / test the connection
  python main.py watch                      run the farm: Sheet in, clips out (Ctrl+C to stop)
  python main.py remake CLIP0006 [...]      new hook/title + re-render one clip (unposted clips only;
                                            --keep-text = only re-render)
  python main.py posted CLIP0004 [--url U]  you posted it yourself: mark posted, folder → posted_clips
  python main.py serve                      only the Folder-link server (watch already includes it)
  python main.py auth youtube|instagram     connect / check the posting accounts (Phase 6)
  python main.py publish CLIP0004 [...]     publish one clip NOW (--platform youtube|instagram)
  python main.py publish CLIP0004 --retry     try a "needs you" post again
  python main.py publish --due              publish every post whose time has come (needs posting_enabled)
  python main.py start | stop               run the farm in the background / stop it (Phase 7)
  python main.py stats [--relearn]          views of every Short/Reel, winners → AI learns (Phase 8)
  python main.py autostart on|off|status    start with Windows + wake the PC before each post time

Options for `process`:
  --clips N           max clips (default from config/settings.json, 3-9 are made)
  --section A-B       only this part, e.g. 0:20:00-0:40:00
  --layout L          auto | split | full
  --language L        auto | en | hi
  --preset NAME       editing preset from config/editing_presets.json (default auto)
  --redo              run a finished video again (new settings/AI/section); old clips stay untouched
  --ai NAME           openai | gemini | offline (overrides settings for this run)
  --music MODE        library | generated | none
"""

import argparse
import shutil
import sys

from config.settings import env, settings
from core.db import get_db
from core.logger import get_logger
from core.paths import ROOT, YUNET_MODEL, ensure_dirs

log = get_logger("main")


def cmd_doctor(_):
    ensure_dirs()
    ok = True
    for tool in ("ffmpeg", "ffprobe", "node"):
        found = shutil.which(tool)
        print(f"[{'ok' if found else 'x '}] {tool}: {found or 'missing'}")
        ok &= bool(found) or tool == "node"
    print(f"[{'ok' if YUNET_MODEL.exists() else 'x '}] face model: {YUNET_MODEL}")
    import os
    hf = os.environ.get("HF_HOME")
    print(f"[ok] model cache: {hf}")
    from ai.base import providers
    names = [p.name for p in providers(settings)]
    print(f"[ok] AI providers in use: {' → '.join(names)}"
          + ("   (no API key set: offline mode)" if names == ["offline"] else ""))
    from posting.youtube_shorts import _path
    yt = _path("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json").exists()
    print(f"[{'ok' if yt else '--'}] YouTube {'connected' if yt else 'not connected (main.py auth youtube)'}")
    print(f"[ok] posting_enabled: {'yes' if settings.get('posting_enabled') else 'no (nothing is published)'}")
    from core import service
    print(f"[{'ok' if service.farm_state()['running'] else '--'}] {service.describe()}")
    print(f"[{'ok' if service.autostart_installed() else '--'}] autostart: "
          f"{'on' if service.autostart_installed() else 'off (main.py autostart on)'}")
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_SHEET_ID", "INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN"):
        print(f"[{'ok' if env(k) else '--'}] {k} {'set' if env(k) else 'not set'}")
    print(f"[ok] project: {ROOT}")
    get_db()
    print("[ok] database ready")
    return 0 if ok else 1


def sheet_settings():
    """Commands typed by hand use the Sheet's Settings too (manual_approval, posting times...), like the farm."""
    if not env("GOOGLE_SHEET_ID"):
        return
    try:
        from google_sheets.client import open_sheet
        from google_sheets.sync import read_settings
        read_settings(open_sheet(), settings)
    except Exception as exc:  # noqa: BLE001
        print(f"(Sheet settings not read, using config/settings.json: {str(exc)[:120]})")


def cmd_process(a):
    ensure_dirs()
    sheet_settings()
    if a.ai:
        settings["ai_provider"] = a.ai
    if a.music:
        settings["music_mode"] = a.music
    if a.preset:
        settings["editing_preset"] = a.preset
    from core.pipeline import add_video, process_video, redo_video
    vid = add_video(a.url, section=a.section, layout=a.layout, language=a.language, clips_wanted=a.clips)
    db = get_db()
    if a.redo:
        redo_video(vid, section=a.section, layout=a.layout, language=a.language, clips_wanted=a.clips)
    v = db.get_video(vid)
    if v["status"] in ("failed", "retry"):
        db.update_video(vid, status="retry")
    process_video(vid, settings)
    return cmd_status(None, only=vid)


def cmd_run(_):
    ensure_dirs()
    from core.pipeline import process_video
    db = get_db()
    queue = db.videos("new", "retry")
    if not queue:
        print("nothing queued")
    for v in queue:
        process_video(v["video_id"], settings)
    return 0


def cmd_status(_, only=None):
    db = get_db()
    if not only:
        from core import service
        print(service.describe())
    vids = [db.get_video(only)] if only else db.videos()
    for v in vids:
        if not v:
            continue
        print(f"\n{v['video_id']}  [{v['status']}]  {v.get('title') or v['url']}")
        if v.get("last_error"):
            print(f"   error: {v['last_error'][:200]}")
        for c in db.clips(v["video_id"]):
            print(f"   {c['clip_id']}  {c['status']:<10} score {c['score'] or '-':>3}  "
                  f"{c.get('post_time') or '':<16}  {c.get('hook') or ''}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Python Clipping Automation Farm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("process")
    p.add_argument("url")
    p.add_argument("--clips", type=int)
    p.add_argument("--section")
    p.add_argument("--layout", choices=["auto", "split", "full"])
    p.add_argument("--language", choices=["auto", "en", "hi"])
    p.add_argument("--preset")
    p.add_argument("--ai", choices=["openai", "gemini", "offline"])
    p.add_argument("--music", choices=["library", "generated", "none"])
    p.add_argument("--redo", action="store_true",
                   help="process a finished video again; old clips are kept, new clips get new IDs")
    sub.add_parser("run")
    sub.add_parser("status")
    s = sub.add_parser("sheets", help="Google Sheets control panel")
    s.add_argument("action", choices=["setup", "sync", "check"])
    sub.add_parser("watch", help="keep running: read the sheet, process new links, remakes, update the sheet")
    rt = sub.add_parser("retitle", help="new title + hashtags with the current rules (video unchanged)")
    rt.add_argument("clip_ids", nargs="+", help="clip IDs, or 'all' = every clip not posted yet")
    r = sub.add_parser("remake")
    r.add_argument("clip_ids", nargs="+")
    r.add_argument("--keep-text", action="store_true", help="keep hook/title/caption, only re-render")
    r.add_argument("--ai", choices=["openai", "gemini", "offline"])
    m = sub.add_parser("posted", help="mark clips you uploaded yourself as posted")
    m.add_argument("clip_ids", nargs="+")
    m.add_argument("--platform", choices=["instagram", "youtube"], help="only this platform")
    m.add_argument("--url", default="", help="link of the post")
    sub.add_parser("serve", help="Folder-link server for the Google Sheet (Ctrl+C to stop)")
    au = sub.add_parser("auth", help="connect / check YouTube or Instagram")
    au.add_argument("platform", choices=["youtube", "instagram", "youtube-analytics"])
    sp = sub.add_parser("stats", help="views of every Short/Reel, winners, learn from them (Phase 8)")
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--relearn", action="store_true", help="write config/winners.md again even if nothing changed")
    sub.add_parser("audit", help="weekly channel audit: the ONE fix for this week (Phase 8)")
    tp = sub.add_parser("trends", help="what works in the niche this week + fresh streams to clip (Phase 8)")
    tp.add_argument("--top", type=int, default=10)
    tp.add_argument("--cached", action="store_true", help="show the last result, no new search")
    sub.add_parser("service", help="supervisor: runs watch and restarts it (used by autostart)")
    sub.add_parser("guard", help="start the farm if it is not running (task ClippingFarmGuard, every 10 min)")
    sub.add_parser("start", help="start the farm in the background")
    sub.add_parser("stop", help="stop the background farm (after the current job)")
    asp = sub.add_parser("autostart", help="start the farm with Windows + wake the PC for posting")
    asp.add_argument("action", choices=["on", "off", "status"])
    pu = sub.add_parser("publish", help="publish clips through the official APIs")
    pu.add_argument("clip_ids", nargs="*")
    pu.add_argument("--platform", choices=["instagram", "youtube"])
    pu.add_argument("--due", action="store_true", help="everything whose post time has come")
    pu.add_argument("--retry", action="store_true", help="also try posts marked 'needs you' again "
                    "(check the platform first if it said MAYBE POSTED)")
    a = ap.parse_args()
    return {"doctor": cmd_doctor, "process": cmd_process, "run": cmd_run, "status": cmd_status,
            "remake": cmd_remake, "sheets": cmd_sheets, "watch": cmd_watch, "posted": cmd_posted,
            "serve": cmd_serve, "auth": cmd_auth, "publish": cmd_publish,
            "service": cmd_service, "stats": cmd_stats, "trends": cmd_trends, "audit": cmd_audit, "retitle": cmd_retitle, "start": cmd_start, "stop": cmd_stop, "guard": cmd_guard, "autostart": cmd_autostart}[a.cmd](a)


def cmd_sheets(a):
    ensure_dirs()
    from google_sheets.client import open_sheet
    from google_sheets.sync import ensure_tabs, refresh_layout, sync
    try:
        io = open_sheet()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "SpreadsheetNotFound" in type(exc).__name__ or "404" in msg or "PERMISSION" in msg.upper():
            msg += "\n→ share the sheet with the service account e-mail (Editor) and check GOOGLE_SHEET_ID"
        print(f"[--] cannot open the Google Sheet: {type(exc).__name__}: {msg}\n    see docs/SHEETS_SETUP.md")
        return 1
    if a.action == "check":
        print(f"[ok] connected to: {io.sh.title}")
        creds = getattr(io.gc, "http_client", None)
        creds = getattr(getattr(creds, "auth", None), "service_account_email", None)
        print(f"[ok] service account: {creds or '(see secrets/service_account.json)'}")
        print(f"[ok] tabs: {', '.join(io.tabs())}")
        return 0
    if a.action == "setup":
        made = ensure_tabs(io, settings)
        print(f"tabs created: {', '.join(made) or 'none (all 7 already there)'}")
        refresh_layout(io)
        print("column widths and dropdowns refreshed")
    rep = sync(io, settings)
    print(rep)
    print(f"open: https://docs.google.com/spreadsheets/d/{env('GOOGLE_SHEET_ID')}")
    return 0


def _power_cut_safety():
    """Runs each time the farm starts, so a power cut / crash / closed window never loses work:
    a copy of the database, damaged half-written files made again, half-done videos carried on."""
    from core import service
    from core.paths import video_workdir
    from core.safe_io import heal_folder
    wlog = get_logger("watch")
    for name in ("backup_db", "backup_settings", "clean_tmp"):
        job = getattr(service, name, None)
        try:
            if job:
                job()
        except Exception as exc:  # noqa: BLE001 - a backup problem never stops the farm
            wlog.warning(f"{name}: {exc}")
    db = get_db()
    for v in db.videos("new", "retry", "downloading", "transcribing", "analyzing", "editing", "failed"):
        try:
            n = heal_folder(video_workdir(v["video_id"]))
        except Exception:  # noqa: BLE001
            n = 0
        if v["status"] in ("downloading", "transcribing", "analyzing", "editing"):
            # stopped in the middle (PC off, crash, update): carry on where it was. Download, transcript
            # (saved every minute), moments and finished clips are kept, so only the last minute is redone.
            db.update_video(v["video_id"], status="retry", stage_detail="resuming after a restart")
            wlog.info(f"{v['video_id']}: was {v['status']} when the farm stopped; resuming")
        elif v["status"] == "failed" and (n or "JSONDecodeError" in (v.get("last_error") or "")):
            db.update_video(v["video_id"], status="retry", last_error=None, stage_detail="resuming after a restart")
            wlog.info(f"{v['video_id']}: failed on a damaged file ({n} repaired); trying again")


def cmd_watch(_):
    """The farm's main loop. A background thread syncs the sheet every sheet_poll_seconds,
    the main thread does the heavy work (videos, remakes) one at a time. Ctrl+C to stop."""
    import threading
    import time
    ensure_dirs()
    from core import service
    lock = service.Lock(service.LOCK)
    if not lock.acquire():
        print("the farm is already running (" + service.describe() + ")")
        return 3
    from core.pipeline import process_video, remake_clip
    _power_cut_safety()
    use_sheet = bool(env("GOOGLE_SHEET_ID"))
    stop = threading.Event()
    synced = threading.Event()                         # first Sheet sync done (Settings tab applied)
    log = get_logger("watch")

    def sheet_loop():
        from google_sheets.client import open_sheet
        from google_sheets.sync import sync
        io = None
        last_stats = 0.0
        while not stop.is_set():
            if time.time() - last_stats > float(settings.get("stats_refresh_hours", 3)) * 3600:
                last_stats = time.time()
                try:                                  # Phase 8: views of every post + learn from winners
                    from growth import analytics, learn
                    analytics.refresh(settings)
                    learn.learn(settings)
                except Exception as exc:  # noqa: BLE001
                    log.error(f"stats: {str(exc)[:300]}")
            try:                                      # Phase 8: weekly channel audit (reads only)
                from growth import channel_audit
                if channel_audit.due(settings):
                    channel_audit.run(settings)
            except Exception as exc:  # noqa: BLE001
                log.error(f"audit: {str(exc)[:300]}")
            try:                                      # Phase 8: trends finder, once a day (quota-capped)
                from growth import trends
                if trends.due(settings):
                    trends.refresh(settings)
            except Exception as exc:  # noqa: BLE001
                log.error(f"trends: {str(exc)[:300]}")
            try:
                io = io or open_sheet()
                sync(io, settings)
            except Exception as exc:  # noqa: BLE001 - keep watching, try again next round
                io = None
                log.error(f"sheet sync failed: {str(exc)[:300]}")
            synced.set()
            stop.wait(max(15, int(settings.get("sheet_poll_seconds", 60))))

    if use_sheet:
        threading.Thread(target=sheet_loop, daemon=True, name="sheets").start()
        print(f"watching Google Sheet every {settings.get('sheet_poll_seconds', 60)} s")
    else:
        print("GOOGLE_SHEET_ID not set: only the local queue is processed (main.py process URL)")
    from core import clip_server
    from posting import publisher, queue

    def publish_loop():
        """Own thread, so a long video job never delays a post time. Checks every 30 s;
        publishes only when posting_enabled = yes. A slot with no clip simply has no post."""
        from datetime import datetime as _dt
        from posting.slots import _tz
        db = get_db()
        while not stop.is_set():
            try:
                rep = publisher.publish_due(settings)
                if rep and not rep.get("disabled") and any(rep.values()):
                    log.info(f"publish: {rep}")
            except Exception as exc:  # noqa: BLE001 - keep going, next round
                log.error(f"publish loop: {str(exc)[:300]}")
            try:
                posts = db.posts("scheduled", "failed", "publishing")
                awake = service.should_stay_awake(settings, posts, _dt.now(_tz(settings)))
                service.set_awake(awake)
                service.beat(posting_enabled=bool(settings.get("posting_enabled")), awake=awake,
                             next_post=service.next_post_time(settings, posts))
                service.refresh_autostart(settings)
                service.backup_db()                      # one copy a day, last 7 kept
                service.backup_settings()                # settings + logins, one zip a day, last 14 kept
            except Exception as exc:  # noqa: BLE001
                log.error(f"heartbeat: {str(exc)[:300]}")
            stop.wait(30)
        service.set_awake(False)

    threading.Thread(target=publish_loop, daemon=True, name="publish").start()

    def narrator_loop():
        from core.narrator import Narrator
        synced.wait(180 if use_sheet else 0)
        Narrator(settings).run(stop)

    threading.Thread(target=narrator_loop, daemon=True, name="narrator").start()
    print(f"auto posting: {'ON' if settings.get('posting_enabled') else 'off'} "
          f"(Settings → posting_enabled), times {', '.join(settings.get('posting_times') or [])}")
    if clip_server.start(settings):
        print(f"Folder links: {clip_server.base_url(settings)}/clip/CLIPxxxx")
    db = get_db()
    last_q = 0
    code_v, last_code, reload, last_low = service.code_version(), time.time(), False, 0.0
    last_heal = 0
    if not service.is_locked(service.SUP_LOCK):      # started by hand: an old stop flag must not stop it
        service.STOP.unlink(missing_ok=True)
    try:
        while not service.STOP.exists():
            if service.is_locked(service.SUP_LOCK) and time.time() - last_code > 60:
                last_code = time.time()                # code updated → restart between videos, never in the middle
                if service.code_version() != code_v:
                    log.info("farm code changed: restarting to load it")
                    reload = True
                    break
            if time.time() - last_heal > 1800:          # clips made without the AI / failed: make them again
                last_heal = time.time()
                try:
                    from core.pipeline import heal_clips
                    heal_clips(settings)
                except Exception as exc:  # noqa: BLE001
                    log.error(f"heal clips: {str(exc)[:200]}")
            if time.time() - last_q > 60:
                last_q = time.time()
                try:
                    queue.step(settings)
                except Exception as exc:  # noqa: BLE001
                    log.error(f"posting queue: {exc}")
            busy = False
            for c in db.clips(None, "remake"):
                busy = True
                try:
                    remake_clip(c["clip_id"], settings)
                except Exception as exc:  # noqa: BLE001
                    db.update_clip(c["clip_id"], status="ready")
                    db.event("error", c["clip_id"], "remake", str(exc))
            low = service.disk_check() if db.videos("new", "retry") else ""
            if low:                                    # a full disk mid-edit breaks files: wait, keep posting
                if time.time() - last_low > 1800:
                    last_low = time.time()
                    log.warning(f"disk too full, new videos wait: {low}")
            for v in ([] if low else db.videos("new", "retry")):
                busy = True
                try:
                    process_video(v["video_id"], settings)
                except Exception as exc:  # noqa: BLE001 - one bad video never stops the farm
                    log.error(f"{v['video_id']}: {str(exc)[:300]}")
                    db.update_video(v["video_id"], status="failed")
                if service.STOP.exists():
                    break
            if not busy:
                time.sleep(10)
        print("restarting to load the updated code" if reload else "stopped (main.py stop)")
    except KeyboardInterrupt:
        print("stopped")
    finally:
        stop.set()
        time.sleep(1)
        lock.release()
    return service.RELOAD if reload else 0


def cmd_stats(a):
    """Read views/likes of every Short and Reel, show the winners, update config/winners.md."""
    ensure_dirs()
    from growth import analytics, learn
    try:
        if env("GOOGLE_SHEET_ID"):
            from google_sheets.client import open_sheet
            from google_sheets.sync import read_settings
            io = open_sheet()
            read_settings(io, settings)
        else:
            io = None
    except Exception as exc:  # noqa: BLE001
        io = None
        print(f"(Sheet not read: {exc})")
    rep = analytics.refresh(settings)
    print(f"read {rep['youtube']} YouTube Shorts, {rep['instagram']} Instagram Reels")
    for e in rep["errors"]:
        print(f"[--] {e}")
    for pl in ("youtube", "instagram"):
        rows = get_db().stats(pl)
        if not rows:
            continue
        print()
        print(f"{pl}  (winner = {analytics.threshold(settings, pl)}+ views)")
        for r in rows[:a.top]:
            v = r["views"] if r.get("views") is not None else "?"
            print(f"  {'⭐' if analytics.is_winner(settings, r) else '  '} {v:>7} views {r.get('likes') or 0:>5} likes  "
                  f"{(r.get('published_at') or '')[:10]}  {r.get('clip_id') or '':<9} {(r.get('title') or '')[:60]}")
    res = learn.learn(settings, force=a.relearn)
    print()
    print(f"winners: {res['winners']}" + ("  → config/winners.md updated, new clips follow it" if res.get("updated") else ""))
    for r in res.get("rules") or []:
        print(f"  - {r}")
    if io is not None:
        from google_sheets.sync import ensure_tabs, write_performance, write_posted
        ensure_tabs(io, settings)
        write_performance(io, settings)
        write_posted(io, settings)
        print("Sheet: Performance + Posted Reels updated")
    return 0


def cmd_retitle(a):
    ensure_dirs()
    sheet_settings()
    from core.pipeline import retitle_clip
    db = get_db()
    ids = a.clip_ids
    if ids == ["all"]:
        ids = [c["clip_id"] for c in db.clips(None, "ready", "approved", "scheduled")]
    bad = 0
    for cid in ids:
        try:
            r = retitle_clip(cid, settings)
            print(f"{cid}: {r['old']}")
            print(f"      → {r['new']}   {' '.join(r['hashtags'])}")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"[--] {cid}: {exc}")
    return 1 if bad else 0


def cmd_audit(_):
    """Measure the channel, pick the ONE fix for this week. Changes nothing, publishes nothing."""
    ensure_dirs()
    from growth import channel_audit
    io = None
    if env("GOOGLE_SHEET_ID"):
        try:
            from google_sheets.client import open_sheet
            from google_sheets.sync import read_settings
            io = open_sheet()
            read_settings(io, settings)
        except Exception as exc:  # noqa: BLE001
            print(f"(Sheet not read: {exc})")
            io = None
    a = channel_audit.run(settings)
    print(channel_audit.to_text(a))
    if io is not None:
        from google_sheets.sync import ensure_tabs, write_audit
        ensure_tabs(io, settings)
        write_audit(io, force=True)
        print("Sheet: Audit tab updated")
    return 0


def cmd_trends(a):
    """Find ideas (Shorts that beat their channel's median) and fresh streams to clip; show them."""
    ensure_dirs()
    from growth import trends
    io = None
    if env("GOOGLE_SHEET_ID"):
        try:
            from google_sheets.client import open_sheet
            from google_sheets.sync import read_settings
            io = open_sheet()
            read_settings(io, settings)
        except Exception as exc:  # noqa: BLE001
            print(f"(Sheet not read: {exc})")
            io = None
    if not a.cached:
        rep = trends.refresh(settings, force=True)
        print(f"searched {', '.join(settings.get('trend_queries') or [])}: {rep['ideas']} ideas from "
              f"{rep['channels']} channels, {rep['sources']} sources (searches today: {rep['searches']})")
        for n in rep["notes"]:
            print(f"[--] {n}")
    st = trends.load()
    if not st:
        print("no trends yet")
        return 1
    print()
    print(f"FRESH STREAMS / LONG VIDEOS (most views per hour, last 3 days)  {st['at'][:16]}")
    for v in st.get("sources", [])[:a.top]:
        mark = "LIVE " if v.get("live") == "live" else ("added" if v.get("added") else "     ")
        print(f"  {mark} {v['per_hour']:>7}/h {v['views']:>9} views  {v['channel'][:18]:<18} {v['title'][:55]}")
        print(f"         {v['url']}")
    print()
    print("IDEAS: Shorts that beat their own channel's usual views")
    for v in st.get("ideas", [])[:a.top]:
        print(f"  {v['multiple']:>5}x {v['views']:>9} views (usual {v['median']})  {v['channel'][:18]:<18} {v['title'][:55]}")
        print(f"         {v['formula']}  {v['url']}")
    if st.get("hashtags"):
        print()
        print("hashtags: " + " ".join(f"{t}({n})" for t, n in st["hashtags"][:10]))
    for x in st.get("angles") or []:
        print(f"  - {x}")
    if trends.TRENDS_MD.exists():
        print("→ config/trends.md updated, new hooks/titles may use these angles (only if the clip shows them)")
    if io is not None:
        from google_sheets.sync import ensure_tabs, write_trends
        ensure_tabs(io, settings)
        write_trends(io, force=True)
        print("Sheet: Trends tab updated")
    return 0


def cmd_guard(_):
    ensure_dirs()
    from core import service
    print(service.guard())
    return 0


def cmd_service(_):
    ensure_dirs()
    from core import service
    return service.supervise()


def cmd_start(_):
    ensure_dirs()
    from core import service
    print(service.start_background(settings))
    return 0


def cmd_stop(_):
    from core import service
    print(service.stop_farm())
    return 0


def cmd_autostart(a):
    ensure_dirs()
    from core import service
    if a.action == "on":
        try:
            if env("GOOGLE_SHEET_ID"):           # use the times from the Sheet's Settings tab
                from google_sheets.client import open_sheet
                from google_sheets.sync import read_settings
                read_settings(open_sheet(), settings)
        except Exception as exc:  # noqa: BLE001
            print(f"(Sheet not read, using config/settings.json: {exc})")
        try:
            print("[ok] " + service.install_autostart(settings))
        except RuntimeError as exc:
            print(f"[x ] could not create the task: {exc}")
            return 1
        print("     " + service.start_background(settings))
    elif a.action == "off":
        print(service.remove_autostart() + " (the farm keeps running until main.py stop)")
    print(f"autostart: {'on' if service.autostart_installed() else 'off'}")
    print(service.describe())
    return 0


def cmd_posted(a):
    ensure_dirs()
    from posting import queue
    rc = 0
    for cid in a.clip_ids:
        try:
            c = queue.mark_posted_by_hand(cid.upper(), settings, [a.platform] if a.platform else None, a.url)
            print(f"{c['clip_id']}  [{c['status']}]  folder: {c.get('folder')}")
        except ValueError as exc:
            print(f"{cid}: {exc}")
            rc = 1
    return rc


def cmd_auth(a):
    ensure_dirs()
    try:
        if a.platform == "youtube-analytics":
            from growth.channel_audit import analytics_credentials
            print("a browser window opens: sign in with the channel's account and allow READ-ONLY YouTube Analytics")
            if not analytics_credentials(interactive=True):
                raise RuntimeError("not connected")
            print("[ok] YouTube Analytics connected (read-only, secrets/youtube_analytics_token.json)")
        elif a.platform == "youtube":
            from posting.youtube_shorts import channel_name
            print("a browser window opens if YouTube is not connected yet: sign in with the channel's account")
            print(f"[ok] YouTube channel: {channel_name(interactive=True)}")
        else:
            from posting.instagram import account_name
            print(f"[ok] Instagram account: {account_name()}")
    except Exception as exc:  # noqa: BLE001
        print(f"[--] {a.platform}: {exc}\n    see docs/POSTING_SETUP.md")
        return 1
    return 0


def cmd_publish(a):
    """Explicit command = your go-ahead for these clips (works even with posting_enabled = no)."""
    ensure_dirs()
    from posting import publisher, queue
    db = get_db()
    queue.step(settings)
    if a.due:
        if not settings.get("posting_enabled", False):
            print("posting_enabled is no (Settings tab / config/settings.json): nothing published")
            return 1
        print(publisher.publish_due(settings))
        return 0
    if not a.clip_ids:
        print("give clip IDs or --due")
        return 1
    settings["posting_enabled"] = True
    rc = 0
    for cid in (x.upper() for x in a.clip_ids):
        c = db.get_clip(cid)
        if not c or c["status"] not in ("approved", "posting"):
            print(f"{cid}: not approved ({c['status'] if c else 'unknown'})")
            rc = 1
            continue
        mine = lambda p: p["clip_id"] == cid and (not a.platform or p["platform"] == a.platform)  # noqa: E731
        if a.retry:
            for p in filter(mine, db.posts("needs_manual")):
                db.update_post(p["id"], status="failed", last_error=None, attempts=0, next_try=None)
                db.event("info", cid, "publish", f"{p['platform']}: retry by command")
        todo = list(filter(mine, db.posts("scheduled", "failed")))
        if not todo:
            left = [f"{p['platform']} {p['status']}" for p in db.posts() if mine(p)]
            print(f"{cid}: nothing to publish ({', '.join(left) or 'no posts'})"
                  + ("  → add --retry to try a 'needs_manual' post again" if any("needs_manual" in x for x in left) else ""))
        for p in todo:
            st = publisher.publish_one(p["id"], settings)
            q = db.get_post(p["id"])
            print(f"{cid} {p['platform']:<9} {st}  {q.get('url') or ''}  {q.get('last_error') or q.get('note') or ''}")
            rc |= st != "posted"
    return rc


def cmd_serve(_):
    from core import clip_server
    print(f"Folder links on {clip_server.base_url(settings)}/clip/CLIPxxxx  (Ctrl+C to stop)")
    try:
        clip_server.start(settings, block=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_remake(a):
    ensure_dirs()
    sheet_settings()
    if a.ai:
        settings["ai_provider"] = a.ai
    from core.pipeline import remake_clip
    rc = 0
    for cid in a.clip_ids:
        try:
            c = remake_clip(cid.upper(), settings, repackage=not a.keep_text)
            print(f"{c['clip_id']}  [{c['status']}]  {c.get('hook') or ''}  |  {c.get('title') or ''}")
        except Exception as exc:  # noqa: BLE001
            print(f"{cid}: not remade - {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)
