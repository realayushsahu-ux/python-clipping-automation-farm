"""Downloads with yt-dlp.

- The whole video is fetched as AUDIO only (a 2 h stream ≈ 100 MB). Video is fetched only for
  the few clip sections that get edited (1080p, frame-accurate cuts).
- Browser cookies are used ONLY for age-restricted videos: on normal videos a logged-in
  session makes YouTube answer HTTP 403 (found while building the farm, Nov 2026).
"""

import json
import re
import shutil
import sys
from pathlib import Path

from config.settings import env
from core.logger import get_logger
from core.media import run
from core.retry import PermanentError

log = get_logger("downloader")

PERMANENT = re.compile(r"(Video unavailable|Private video|has been removed|is not a valid URL|Unsupported URL|"
                       r"This video is not available|members-only|Join this channel|Incomplete YouTube ID)", re.I)
AGE = re.compile(r"(age-restricted|Sign in to confirm your age|confirm you.re not a bot)", re.I)


def _ytdlp() -> str:
    local = Path(sys.executable).with_name("yt-dlp.exe")
    if local.exists():
        return str(local)
    found = shutil.which("yt-dlp")
    if not found:
        raise RuntimeError("yt-dlp not found; run scripts\\setup.ps1")
    return found


def _base(url: str, cookies: bool) -> list:
    args = ["--no-warnings", "--no-playlist"]
    if "youtu" in url and not shutil.which("deno"):
        args += ["--js-runtimes", "node"]          # yt-dlp-ejs solves YouTube's JS challenge with Node
    if cookies:
        if env("YTDLP_COOKIES_FILE"):
            args += ["--cookies", env("YTDLP_COOKIES_FILE")]
        elif env("YTDLP_COOKIES_FROM_BROWSER"):
            args += ["--cookies-from-browser", env("YTDLP_COOKIES_FROM_BROWSER")]
        if "youtu" in url:      # signed-in default clients give 403 on video parts; web_safari works (tested)
            args += ["--extractor-args", "youtube:player_client=" + (env("YTDLP_COOKIE_CLIENT") or "web_safari")]
    return args


def _has_cookies() -> bool:
    return bool(env("YTDLP_COOKIES_FILE") or env("YTDLP_COOKIES_FROM_BROWSER"))


def _call(url: str, extra: list, timeout=None):
    """Run yt-dlp; on an age wall retry once with cookies; classify permanent errors."""
    for use_cookies in (False, True):
        if use_cookies and not _has_cookies():
            break
        proc = run([_ytdlp(), *_base(url, use_cookies), *extra, url], timeout=timeout, check=False)
        if proc.returncode == 0:
            return proc
        err = (proc.stderr or proc.stdout or "")
        if AGE.search(err) and not use_cookies:
            log.info("age-restricted video: retrying with browser cookies")
            continue
        if PERMANENT.search(err):
            raise PermanentError(err.strip().splitlines()[-1][:300])
        raise RuntimeError(err.strip().splitlines()[-1][:300] if err.strip() else "yt-dlp failed")
    raise PermanentError("age-restricted: set YTDLP_COOKIES_FROM_BROWSER=firefox (signed in to YouTube) in .env")


def video_id_from_url(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def video_info(url: str, workdir: Path) -> dict:
    cache = workdir / "info.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = _call(url, ["-J"], timeout=180).stdout
    full = json.loads(raw)
    info = {k: full.get(k) for k in ("id", "title", "uploader", "channel", "channel_id", "duration",
                                      "live_status", "extractor_key", "webpage_url", "language",
                                      "view_count", "upload_date")}
    info["channel"] = info.get("channel") or info.get("uploader") or "streamer"
    info["description"] = (full.get("description") or "")[:1000]      # topic check (only_topic_videos)
    info["tags"] = (full.get("tags") or [])[:30]
    cache.write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    return info


def download_audio(url: str, srcdir: Path) -> Path:
    """Whole video, audio only. Cached: a second call returns the existing file."""
    have = [p for p in srcdir.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
    if have:
        return have[0]
    log.info("downloading audio (video comes later, only for the clips)...")
    _call(url, ["-f", "ba/b", "-o", str(srcdir / "audio.%(ext)s"), "--force-overwrites"], timeout=3600)
    have = [p for p in srcdir.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
    if not have:
        raise RuntimeError("audio download produced no file")
    return have[0]


def download_section(url: str, start: float, end: float, out: Path, max_height: int = 1440) -> Path:
    """Video+audio for [start, end] seconds up to max_height (1440p: a 9:16 crop of a 16:9 video stays sharp),
    cut on exact frames."""
    if out.exists() and out.stat().st_size > 100_000:
        return out
    h = int(max_height or 1080)
    _call(url, ["-f", f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b",
                "--merge-output-format", "mp4", "--download-sections", f"*{start:.2f}-{end:.2f}",
                "--force-keyframes-at-cuts", "--force-overwrites", "-o", str(out)], timeout=1800)
    if not out.exists():
        raise RuntimeError("section download produced no file")
    return out


def chat_rate(url: str, info: dict, workdir: Path):
    """Messages per 5 s from a YouTube live replay (how hard chat reacted), or None."""
    cache = workdir / "chat.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if "youtube" not in (info.get("extractor_key") or "").lower() or info.get("live_status") not in ("was_live", "post_live"):
        return None
    log.info("reading the chat replay...")
    try:
        _call(url, ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(workdir / "chat")],
              timeout=900)
    except Exception as exc:  # noqa: BLE001 - chat is optional
        log.warning(f"chat replay not used: {str(exc)[:120]}")
        return None
    files = list(workdir.glob("chat*.live_chat.json*"))
    if not files:
        return None
    buckets = {}
    with open(files[0], encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                item = json.loads(line)["replayChatItemAction"]
                ms = int(item.get("videoOffsetTimeMsec", 0))
            except (ValueError, KeyError, TypeError):
                continue
            buckets[ms // 5000] = buckets.get(ms // 5000, 0) + len(item.get("actions", []))
    for f in files:
        f.unlink()
    if not buckets:
        return None
    rate = [0] * (max(buckets) + 1)
    for k, n in buckets.items():
        rate[k] = n
    cache.write_text(json.dumps(rate), encoding="utf-8")
    log.info(f"chat replay: {sum(rate):,} messages")
    return rate
