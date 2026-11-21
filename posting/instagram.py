"""Instagram Reels with the official Instagram Graph API, uploading the file straight from this PC.

Flow (Meta docs, "Content Publishing" → resumable upload, Facebook Login for Business):
  1  POST graph.facebook.com/<ver>/<IG_USER_ID>/media   media_type=REELS, upload_type=resumable, caption
  2  POST rupload.facebook.com/ig-api-upload/<ver>/<container>   the video bytes
  3  GET  /<container>?fields=status_code   until FINISHED (Instagram processes the video)
  4  POST /<IG_USER_ID>/media_publish creation_id=<container>   → the Reel is live
  5  GET  /<media_id>?fields=permalink
Only step 4 publishes. A failure before it is safe to retry; a lost answer at step 4 is "ambiguous".

Needs (.env): INSTAGRAM_USER_ID (the IG Business/Creator account id), INSTAGRAM_ACCESS_TOKEN
(a long-lived Page token with instagram_basic, instagram_content_publish, pages_read_engagement;
instagram_manage_comments for the first comment). Setup: docs/POSTING_SETUP.md.
Meta allows 100 API posts per 24 h per account.
"""

import time
from pathlib import Path

from config.settings import env
from core.logger import get_logger
from posting.errors import Ambiguous, Permanent, Retryable

log = get_logger("instagram")
RATE_CODES = {4, 9, 17, 32, 613, 80002}           # Meta "slow down" error codes
NEEDS_YOU_CODES = {10, 102, 190}                  # permission / session problems: only a person can fix these


def _cfg():
    host = env("INSTAGRAM_API_HOST", "graph.facebook.com").replace("https://", "").strip("/")
    return {"host": host, "ver": env("INSTAGRAM_GRAPH_VERSION", "v23.0"),
            "ig": env("INSTAGRAM_USER_ID"), "token": env("INSTAGRAM_ACCESS_TOKEN")}


def build_caption(job, settings) -> str:
    tags = [h if h.startswith("#") else f"#{h}" for h in (job.get("hashtags") or [])][:30]   # IG max 30
    cap = (job.get("instagram_caption") or "").strip()
    if tags:
        cap = f"{cap}\n.\n{' '.join(tags)}".strip()
    return cap[:2200]


def _check(r, step, published_step=False):
    """Turn an HTTP answer into data or the right error."""
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code < 400 and "error" not in data:
        return data
    err = data.get("error") or {}
    code = err.get("code")
    msg = f"Instagram {step}: {r.status_code} {err.get('message') or r.text[:200]}"
    if code == 190 or r.status_code == 401:
        raise Permanent(msg + " → the access token expired or was revoked; make a new one (POSTING_SETUP.md)")
    if code in RATE_CODES or r.status_code == 429:
        raise Retryable(msg + " (rate limit)", 60)
    if r.status_code >= 500 or err.get("is_transient"):
        if published_step:
            raise Ambiguous(msg)
        raise Retryable(msg, 10)
    if code in NEEDS_YOU_CODES or (code and 200 <= int(code) <= 299) or "permission" in msg.lower():
        raise Permanent(msg + " → the Instagram app/token is missing a permission (POSTING_SETUP.md)")
    # Any other refusal (ProcessingFailedError, media not ready, bad request...) came with a clear answer
    # that NOTHING was published, so trying again can never make a duplicate. The farm heals itself:
    # the next try uses smaller upload pieces and a lighter Instagram copy (see publish).
    raise Retryable(msg, 15)


IG_MAX_MB = 35       # keeps uploads quick. The ProcessingFailedError of 2026-11 was the single long upload
                     # request, not the size: see upload_chunks


def ig_video_kbps(duration_s: float, max_mb: float = IG_MAX_MB) -> int:
    """Video bitrate (kbps) so the copy stays under max_mb: 8000 for short clips, less for long ones
    (Instagram re-encodes Reels to well under 5 Mbps anyway, so nothing visible is lost)."""
    if not duration_s or duration_s <= 0:
        return 8000
    budget = max_mb * 8_000 / duration_s - 128 - 150          # minus audio + container overhead
    return int(max(1500, min(8000, budget)))


def _duration(video: Path) -> float:
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(video)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def ig_copy(video: Path, max_mb: float = None) -> Path:
    """An Instagram-spec copy of the clip (Reels refuse our 15 Mbps / 192 kbps master with
    "ProcessingFailedError"): H.264 High ≤8 Mbps and at most IG_MAX_MB, AAC 128 kbps 48 kHz, faststart.
    Kept in storage/_system/instagram and reused while it is newer than the clip and small enough."""
    import subprocess
    from core.paths import SYSTEM
    cap = float(max_mb or IG_MAX_MB)
    out = SYSTEM / "instagram" / (f"{video.stem}_ig.mp4" if cap >= IG_MAX_MB else f"{video.stem}_ig_{int(cap)}mb.mp4")
    if (out.is_file() and out.stat().st_mtime >= video.stat().st_mtime
            and 0 < out.stat().st_size <= cap * 1_000_000):
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    kb = ig_video_kbps(_duration(video), cap)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video),
           "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
           "-r", "30", "-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.25)}k", "-bufsize", f"{kb * 2}k", "-g", "60",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=900)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise Retryable(f"could not make the Instagram copy: {str(getattr(exc, 'stderr', '') or exc)[:200]}", 10) from exc
    tmp.replace(out)
    log.info(f"{video.stem}: Instagram copy {out.stat().st_size // 1_000_000} MB")
    return out


CHUNK_BYTES = 4 * 1024 * 1024


def upload_chunks(s, uri, video: Path, token: str, chunk=None):
    """Send the video in 4 MB pieces (rupload 'offset' protocol).

    Found 2026-11-21: one long upload request (> ~30 s) comes back 400 ProcessingFailedError for every
    clip over ~45 s, although the video is fine. In pieces each request takes 1-3 s; only the last one
    waits while Instagram checks the whole video, and that last answer can still be this same 400 even
    though the container then turns FINISHED. So after the last piece a ProcessingFailedError (or a
    timeout) is not trusted: the caller asks the container's status_code, which is the real answer."""
    import requests
    chunk = chunk or CHUNK_BYTES
    size = video.stat().st_size
    off = 0
    with open(video, "rb") as fh:
        while off < size:
            fh.seek(off)
            data = fh.read(chunk)
            last = off + len(data) >= size
            for attempt in range(3):
                try:
                    r = s.post(uri, data=data, timeout=(30, 300), headers={
                        "Authorization": f"OAuth {token}", "offset": str(off), "file_size": str(size)})
                except (requests.ConnectionError, requests.Timeout):
                    if last:
                        log.info(f"{video.stem}: no answer to the last upload piece, checking the status")
                        return
                    if attempt == 2:
                        raise
                    time.sleep(5)
                    continue
                txt = r.text or ""
                if r.status_code < 400 or "PartialRequestError" in txt:
                    break                                  # 200 done, 206 = piece stored, send the next
                if last and "ProcessingFailedError" in txt:
                    log.info(f"{video.stem}: Instagram answered ProcessingFailedError to the last piece; "
                             "checking the real status")
                    return
                if r.status_code >= 500 and attempt < 2:
                    time.sleep(5)
                    continue
                _check(r, "upload")                        # raises the right error kind
                break
            off += len(data)


def account_name(session=None) -> str:
    import requests
    s, c = session or requests.Session(), _cfg()
    if not c["ig"] or not c["token"]:
        raise Permanent("INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN are empty in .env")
    r = s.get(f"https://{c['host']}/{c['ver']}/{c['ig']}", params={"fields": "username",
                                                                    "access_token": c["token"]}, timeout=30)
    return "@" + _check(r, "account")["username"]


def publish(job, settings, session=None, sleep=time.sleep, prepare=ig_copy) -> dict:
    import requests
    s, c = session or requests.Session(), _cfg()
    if not c["ig"] or not c["token"]:
        raise Permanent("INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN are empty in .env (POSTING_SETUP.md)")
    if c["host"] == "graph.instagram.com":
        raise Permanent("uploading from this PC needs Facebook Login (INSTAGRAM_API_HOST=graph.facebook.com); "
                        "Instagram Login only accepts videos at a public web address")
    video = Path(job["file"])
    if not video.is_file():
        raise Permanent(f"video file missing: {video}")
    retry = int(job.get("attempt") or 1) >= 2
    mb = float(settings.get("instagram_retry_max_mb" if retry else "instagram_max_mb", 20 if retry else IG_MAX_MB))
    video = prepare(video, mb) if prepare is ig_copy else prepare(video)   # before the container
    chunk = int(float(settings.get("instagram_upload_chunk_mb", 4)) * 1024 * 1024)
    if retry:
        chunk = max(256 * 1024, chunk // 4)       # self-heal: a retry sends 1 MB pieces
        log.info(f"{job.get('clip_id')}: Instagram try {job.get('attempt')}: lighter copy ({mb:.0f} MB cap), "
                 f"{chunk // 1024} KB pieces")
    base = f"https://{c['host']}/{c['ver']}"
    net = (requests.ConnectionError, requests.Timeout)
    try:
        # 1. container
        r = s.post(f"{base}/{c['ig']}/media", timeout=60, data={
            "media_type": "REELS", "upload_type": "resumable", "caption": build_caption(job, settings),
            "share_to_feed": "true", "access_token": c["token"]})
        cont = _check(r, "create")
        cid = cont["id"]
        uri = cont.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{c['ver']}/{cid}"
        # 2. upload the bytes
        upload_chunks(s, uri, video, c["token"], chunk)
        # 3. wait for Instagram to process it
        waited, status = 0, ""
        limit = int(settings.get("instagram_processing_timeout_s", 600))
        while waited <= limit:
            r = s.get(f"{base}/{cid}", params={"fields": "status_code,status", "access_token": c["token"]},
                      timeout=30)
            st = _check(r, "status")
            status = st.get("status_code", "")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise Retryable(f"Instagram could not process the video: {st.get('status') or status}", 30)
            if status == "PUBLISHED":
                raise Ambiguous("container already published")
            sleep(10)
            waited += 10
        if status != "FINISHED":
            raise Retryable(f"Instagram still processing after {limit}s", 15)
    except net as exc:
        raise Retryable(f"Instagram network error before publishing: {exc}", 10) from exc
    # 4. publish: from here on a lost answer means "maybe posted"
    try:
        r = s.post(f"{base}/{c['ig']}/media_publish", timeout=120,
                   data={"creation_id": cid, "access_token": c["token"]})
    except net as exc:
        raise Ambiguous(f"connection lost while publishing: {exc}") from exc
    media_id = _check(r, "publish", published_step=True)["id"]
    out = {"external_id": media_id, "url": "", "note": ""}
    try:   # 5. link + first comment never fail the post
        r = s.get(f"{base}/{media_id}", params={"fields": "permalink", "access_token": c["token"]}, timeout=30)
        out["url"] = r.json().get("permalink", "")
        if settings.get("post_first_comment", True) and job.get("pin_comment"):
            r = s.post(f"{base}/{media_id}/comments", timeout=30,
                       data={"message": job["pin_comment"][:2200], "access_token": c["token"]})
            out["note"] = "comment posted, pin it in the app" if r.status_code < 400 else "first comment failed"
    except Exception as exc:  # noqa: BLE001
        log.warning(f"{job['clip_id']}: after-publish step failed: {str(exc)[:200]}")
    log.info(f"{job['clip_id']}: published on Instagram {out['url'] or media_id}")
    return out
