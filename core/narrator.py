"""Plain-English status lines for the farm window, e.g.

    11:05 PM  Clipping Farm started. Posting is ON.
    11:05 PM  Next post: CLIP0039 at Today 11:30 PM (YouTube + Instagram). 9 clips waiting.
    11:30 PM  CLIP0039 posted to YouTube: https://youtube.com/shorts/...
    02:10 PM  CLIP0052 is ready to post at Sat 22 Nov 8:00 PM (YouTube + Instagram)

It only reads the database (every 15 s) and says what changed, so it can never change what the farm does.
Every line starts with SAY_MARK; the supervisor shows only those lines in the window (the full technical
log stays in logs/farm.log and logs/watch_console.log)."""

import json
import time
from datetime import datetime

from core.db import get_db
from core.logger import get_logger

SAY_MARK = "» "
log = get_logger("narrator")
PLATFORM = {"youtube": "YouTube", "instagram": "Instagram"}
VIDEO_STAGE = {"downloading": "downloading the video",
               "transcribing": "listening to the video",
               "analyzing": "finding the best moments",
               "editing": "editing the clips"}


def say(text: str):
    line = f"{datetime.now().strftime('%I:%M %p')}  {text}"
    print(SAY_MARK + line, flush=True)
    log.debug(f"say: {text}")


def when(ts) -> str:
    """'2026-11-20 20:00' -> 'Today 8:00 PM' / 'Tomorrow 10:00 AM' / 'Sat 22 Nov 6:00 PM'."""
    try:
        t = datetime.strptime(str(ts)[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(ts or "")
    d = (t.date() - datetime.now().date()).days
    day = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(d, t.strftime("%a %d %b"))
    return f"{day} {t.strftime('%I:%M %p').lstrip('0')}"


def where(platforms) -> str:
    return " + ".join(PLATFORM.get(p, p.title()) for p in sorted(platforms, key=lambda p: p != "youtube"))


def short(text, n=110) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


def _qc_error(c) -> str:
    try:
        return (json.loads(c.get("qc") or "{}") or {}).get("error") or ""
    except (TypeError, ValueError):
        return ""


class Narrator:
    def __init__(self, settings):
        self.settings = settings
        self.videos, self.clips, self.posts = {}, {}, {}
        self.posting = None
        self.last_next = None

    # ------------------------------------------------------------------ reading
    def _read(self):
        db = get_db()
        videos = {v["video_id"]: v for v in db.videos()}
        clips = {c["clip_id"]: c for c in db.clips()}
        posts = {p["id"]: p for p in db.posts()}
        return videos, clips, posts

    def _next(self, posts, clips):
        due = sorted((p for p in posts.values() if p["status"] in ("scheduled", "failed") and p.get("scheduled_at")),
                     key=lambda p: p["scheduled_at"])
        waiting = {p["clip_id"] for p in due}
        if not due:
            return None, 0
        first = due[0]
        plats = [p["platform"] for p in due if p["clip_id"] == first["clip_id"]]
        return (first["clip_id"], first["scheduled_at"], tuple(sorted(plats))), len(waiting)

    def _say_next(self, posts, clips, force=False):
        nxt, waiting = self._next(posts, clips)
        if nxt == self.last_next and not force:
            return
        self.last_next = nxt
        if not nxt:
            say("No clips waiting to post. Paste a new video link in the Sheet's Video Input tab.")
            return
        cid, ts, plats = nxt
        more = f" {waiting} clip{'s' if waiting != 1 else ''} waiting." if waiting else ""
        say(f"Next post: {cid} at {when(ts)} ({where(plats)}).{more}")

    # ------------------------------------------------------------------ start
    def start(self):
        self.videos, self.clips, self.posts = self._read()
        self.posting = bool(self.settings.get("posting_enabled"))
        say("Clipping Farm started. " + self._posting_text())
        busy = [v for v in self.videos.values() if v["status"] in ("new", "retry", *VIDEO_STAGE)]
        for v in busy:
            say(f"Video waiting to be edited: {short(v.get('title') or v['url'], 80)}")
        self._say_next(self.posts, self.clips, force=True)

    def _posting_text(self):
        return ("Posting is ON." if self.settings.get("posting_enabled")
                else "Posting is OFF (turn on posting_enabled in the Sheet's Settings tab).")

    # ------------------------------------------------------------------ changes
    def step(self):
        videos, clips, posts = self._read()
        changed = False

        posting = bool(self.settings.get("posting_enabled"))
        if posting != self.posting:
            self.posting = posting
            say(self._posting_text())

        for vid, v in videos.items():
            old = self.videos.get(vid)
            if old and old["status"] == v["status"]:
                continue
            name = short(v.get("title") or v["url"], 80)
            st = v["status"]
            if st in ("new", "retry") and not old:
                say(f"New video link added: {name}")
            elif st in VIDEO_STAGE:
                say(f"{name}: {VIDEO_STAGE[st]}...")
            elif st == "done":
                made = [c for c in clips.values() if c["video_id"] == vid
                        and c["status"] not in ("planned", "rejected", "qc_failed")]
                say(f"Finished editing {name}: {len(made)} clip{'s' if len(made) != 1 else ''} made.")
            elif st == "failed":
                say(f"Could not finish {name}: {short(v.get('last_error') or 'see logs/farm.log', 100)}")

        new_sched = {}
        for cid, c in clips.items():
            old = self.clips.get(cid)
            if old and old["status"] == c["status"]:
                continue
            st = c["status"]
            if st == "qc_failed":
                say(f"{cid} skipped: {short(_qc_error(c) or 'did not pass the quality check', 100)}")
            elif st == "rejected" and old:
                say(f"{cid} rejected, it will not be posted.")
            elif st == "remake":
                say(f"{cid}: making it again...")
            elif st in ("ready",) and (not old or old["status"] in ("planned", "remake")):
                say(f"{cid} is made: \"{short(c.get('title') or c.get('hook'), 70)}\" (check it in the Ready to Post tab)")

        for pid, p in posts.items():
            old = self.posts.get(pid)
            cid, plat = p["clip_id"], PLATFORM.get(p["platform"], p["platform"])
            if not old or (old["scheduled_at"] != p["scheduled_at"] and p["status"] == "scheduled"):
                if p["status"] == "scheduled":
                    new_sched.setdefault((cid, p["scheduled_at"]), []).append(p["platform"])
                continue
            if old["status"] == p["status"]:
                continue
            changed = True
            st = p["status"]
            if st == "publishing":
                say(f"Posting {cid} to {plat}...")
            elif st == "posted":
                say(f"{cid} posted to {plat}: {p.get('url') or 'done'}")
            elif st == "failed":
                say(f"{cid} could not post to {plat}: {short(p.get('last_error'), 90)}. It will try again.")
            elif st == "needs_manual":
                say(f"{cid} needs you: {plat} post stopped ({short(p.get('last_error'), 90)}). See the Sheet.")
            elif st == "cancelled":
                say(f"{cid}: {plat} post cancelled.")
        for (cid, ts), plats in sorted(new_sched.items(), key=lambda x: x[0][1] or ""):
            changed = True
            say(f"{cid} is ready to post at {when(ts)} ({where(plats)})")

        self.videos, self.clips, self.posts = videos, clips, posts
        self._say_next(posts, clips, force=False)
        return changed

    def run(self, stop, every=15):
        try:
            self.start()
        except Exception as exc:  # noqa: BLE001
            log.error(f"narrator: {exc}")
        while not stop.wait(every):
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - only words on a screen; never stop the farm
                log.error(f"narrator: {str(exc)[:200]}")
            time.sleep(0)
