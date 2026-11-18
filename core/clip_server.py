"""Tiny local web server behind the "Folder" links in Google Sheets.

Google Sheets can't open D:\\ folders directly (browsers block file:// links from web pages),
so the Sheet links to http://localhost:8765/clip/CLIP0004 . Clicking it:
  * opens the clip's folder in Windows Explorer (video selected), and
  * shows a page in the browser that plays the clip, with the post text under it.

Only this computer can reach it (bound to 127.0.0.1). It only serves clip videos and post text
for valid clip IDs; nothing can be changed through it. Runs inside `main.py watch`,
or alone with `main.py serve`.
"""

import html
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.db import get_db
from core.logger import get_logger
from core.paths import FAILED, GENERATED, POSTED, READY

log = get_logger("links")
CLIP_RE = re.compile(r"^CLIP\d{4,6}$")
_last_open = {}


def base_url(settings) -> str:
    return f"http://localhost:{int(settings.get('links_port', 8765))}"


def clip_link(settings, cid) -> str:
    return f"{base_url(settings)}/clip/{cid}"


def clip_folder(cid) -> Path | None:
    c = get_db().get_clip(cid)
    cands = [Path(c["folder"])] if c and c.get("folder") else []
    cands += [READY / cid, POSTED / cid, FAILED / cid]
    return next((f for f in cands if f.is_dir()), None)


def clip_video(cid, nomusic=False) -> Path | None:
    name = f"{cid}_NOMUSIC.mp4" if nomusic else f"{cid}.mp4"
    folder = clip_folder(cid)
    for f in ([folder / name] if folder else []) + [GENERATED / name,
                                                   GENERATED / name.replace(".mp4", "_QC_FAILED.mp4")]:
        if f.is_file():
            return f
    return None


def open_in_explorer(cid):
    """Explorer with the video selected. Ignores repeat clicks within 5 s (browser reloads)."""
    import time
    if time.time() - _last_open.get(cid, 0) < 5:
        return
    _last_open[cid] = time.time()
    target = clip_video(cid) or clip_folder(cid)
    if not target or sys.platform != "win32":
        return
    if target.is_file():
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        os.startfile(str(target))  # noqa: S606 - our own folder


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{cid}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;background:#111;color:#eee;font:15px system-ui,sans-serif;display:flex;
flex-wrap:wrap;gap:24px;padding:24px}}video{{height:86vh;max-width:100%;border-radius:10px;background:#000}}
pre{{white-space:pre-wrap;flex:1;min-width:300px;max-height:86vh;overflow:auto;background:#1c1c1c;
padding:16px;border-radius:10px;margin:0}}a{{color:#7cf}}</style></head><body>
<div><video src="/video/{cid}" controls autoplay playsinline></video>
<p>{links} · folder: {folder}</p></div><pre>{info}</pre></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if len(parts) != 2 or parts[0] not in ("clip", "video", "nomusic") or not CLIP_RE.match(parts[1].upper()):
            return self._send(404, "not found")
        cid = parts[1].upper()
        if parts[0] == "clip":
            return self._page(cid)
        return self._video(cid, nomusic=parts[0] == "nomusic")

    def _page(self, cid):
        folder = clip_folder(cid)
        if not folder and not clip_video(cid):
            return self._send(404, f"{cid}: no folder or video found")
        open_in_explorer(cid)
        info = ""
        if folder and (folder / "post_info.txt").exists():
            info = (folder / "post_info.txt").read_text(encoding="utf-8")
        links = f'<a href="/video/{cid}">video</a>'
        if clip_video(cid, nomusic=True):
            links += f' · <a href="/nomusic/{cid}">no-music version</a>'
        self._send(200, PAGE.format(cid=cid, info=html.escape(info or "(no post_info.txt)"), links=links,
                                    folder=html.escape(str(folder or GENERATED))))

    def _video(self, cid, nomusic):
        f = clip_video(cid, nomusic)
        if not f:
            return self._send(404, "no video")
        size = f.stat().st_size
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = int(rng.group(2)) if rng.group(2) else end
            else:
                start = max(0, size - int(rng.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass  # browser stopped reading (seek / closed tab)


def start(settings, block=False):
    """Start the link server (background thread unless block=True). Returns the server or None."""
    port = int(settings.get("links_port", 8765))
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{port}/clip/", timeout=2)
        except urllib.error.HTTPError:
            log.info(f"folder links: already running on port {port}")   # our server (404 on /clip/)
            return None
        except OSError:
            pass
        log.warning(f"folder links: port {port} is used by another program ({exc}); "
                    f"change links_port in config/settings.json")
        return None
    srv.daemon_threads = True
    if block:
        srv.serve_forever()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True, name="links").start()
    log.info(f"folder links on {base_url(settings)}/clip/CLIPxxxx")
    return srv

