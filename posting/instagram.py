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
    raise Permanent(msg)


def ig_copy(video: Path) -> Path:
    """An Instagram-spec copy of the clip (Reels refuse our 15 Mbps / 192 kbps master with
    "ProcessingFailedError"): H.264 High ≤10 Mbps, AAC 128 kbps 48 kHz, faststart.
    Kept in storage/_system/instagram and reused while it is newer than the clip."""
    import subprocess
    from core.paths import SYSTEM
    out = SYSTEM / "instagram" / f"{video.stem}_ig.mp4"
    if out.is_file() and out.stat().st_mtime >= video.stat().st_mtime and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video),
           "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
           "-r", "30", "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M", "-g", "60",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=900)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise Retryable(f"could not make the Instagram copy: {str(getattr(exc, 'stderr', '') or exc)[:200]}", 10) from exc
    tmp.replace(out)
    log.info(f"{video.stem}: Instagram copy {out.stat().st_size // 1_000_000} MB")
    return out


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
    video = prepare(video)        # before the container: a failed encode wastes no container
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
        size = video.stat().st_size
        with open(video, "rb") as fh:
            r = s.post(uri, data=fh, timeout=(30, 900), headers={
                "Authorization": f"OAuth {c['token']}", "offset": "0", "file_size": str(size)})
        _check(r, "upload")
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
