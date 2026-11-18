"""YouTube Shorts upload with the official YouTube Data API v3 (videos.insert, resumable upload).

One-time setup: docs/POSTING_SETUP.md → `main.py auth youtube` (opens the browser once, you sign in,
the token is saved in secrets/youtube_token.json and refreshed automatically).

A vertical video of up to 3 minutes is a Short automatically; no "#shorts" needed.
Videos uploaded by an API project Google has not audited are locked to PRIVATE by YouTube.
The farm reports that in the Sheet; request the audit (link in POSTING_SETUP.md) to post publicly.
"""

import json
import socket
from pathlib import Path

from config.settings import env
from core.logger import get_logger
from core.paths import ROOT
from posting.errors import Ambiguous, Permanent, Retryable

log = get_logger("youtube")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]      # force-ssl: the first comment
CHUNK = 8 * 1024 * 1024


def _path(key, default):
    p = Path(env(key, default))
    return p if p.is_absolute() else ROOT / p


def credentials(interactive=False):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token = _path("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json")
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError:
            creds = None
    if creds and creds.valid:
        return creds
    if not interactive:
        raise Permanent("YouTube is not connected (or access was removed): run  main.py auth youtube")
    from google_auth_oauthlib.flow import InstalledAppFlow
    secret = _path("YOUTUBE_CLIENT_SECRET_FILE", "secrets/client_secret.json")
    if not secret.exists():
        raise Permanent(f"missing {secret} (Google Cloud → OAuth client ID → Desktop app → download JSON)")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    extra = {}
    hint = env("YOUTUBE_LOGIN_HINT", "")      # e-mail of the channel's Google account, if the browser
    if hint:                                  # is signed in to a different account by default
        extra["login_hint"] = hint
    creds = flow.run_local_server(port=0, prompt="consent select_account", **extra)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _client(creds=None):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds or credentials(), cache_discovery=False)


def channel_name(interactive=False) -> str:
    yt = _client(credentials(interactive))
    items = yt.channels().list(part="snippet", mine=True).execute().get("items") or []
    return items[0]["snippet"]["title"] if items else "(no channel on this Google account)"


def build_body(job, settings) -> dict:
    """The video's title, description, tags and status (pure, tested offline)."""
    tags_h = [h if h.startswith("#") else f"#{h}" for h in (job.get("hashtags") or [])]
    desc = (job.get("youtube_description") or "").strip()
    if tags_h:
        desc = f"{desc}\n\n{' '.join(tags_h[:15])}".strip()      # >15 hashtags: YouTube ignores them all
    tags, size = [], 0
    for t in [h.lstrip("#") for h in tags_h] + list(job.get("search_queries") or []):
        t = t.replace("<", "").replace(">", "").strip()
        if t and t.lower() not in {x.lower() for x in tags} and size + len(t) + 1 <= 450:
            tags.append(t)
            size += len(t) + 1
    title = (job.get("youtube_title") or job.get("hook") or job["clip_id"]).replace("<", "").replace(">", "")
    return {
        "snippet": {"title": title.strip()[:100], "description": desc.replace("<", "").replace(">", "")[:4900],
                    "tags": tags, "categoryId": str(settings.get("youtube_category_id", "20"))},
        "status": {"privacyStatus": settings.get("youtube_privacy", "public"),
                   "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
    }


def _http_error(exc, sent_all):
    from googleapiclient.errors import HttpError
    if not isinstance(exc, HttpError):
        return None
    code = exc.resp.status
    try:
        reason = json.loads(exc.content.decode())["error"]["errors"][0]["reason"]
    except Exception:  # noqa: BLE001
        reason = ""
    msg = f"YouTube {code} {reason}: {str(exc)[:300]}"
    if code in (500, 502, 503, 504):
        return Ambiguous(msg) if sent_all else Retryable(msg, 5)
    if reason in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"):
        return Retryable(msg + " (daily limit: tries again later)", 180)
    if code == 401:
        return Permanent(msg + " → run main.py auth youtube")
    return Permanent(msg)


def publish(job, settings) -> dict:
    """Upload one Short. Returns {"external_id", "url", "note"}; raises Retryable/Permanent/Ambiguous."""
    from googleapiclient.http import MediaFileUpload
    video = Path(job["file"])
    if not video.is_file():
        raise Permanent(f"video file missing: {video}")
    yt = _client()
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=CHUNK, resumable=True)
    size = video.stat().st_size
    req = yt.videos().insert(part="snippet,status", body=build_body(job, settings), media_body=media,
                             notifySubscribers=True)
    sent, resp, tries = 0, None, 0
    while resp is None:
        last_chunk = size - sent <= CHUNK
        try:
            status, resp = req.next_chunk(num_retries=3)
            if status:
                sent = int(status.resumable_progress)
        except (socket.timeout, ConnectionError, OSError) as exc:
            tries += 1
            if last_chunk:      # the final bytes may have arrived: the video may exist
                raise Ambiguous(f"connection lost on the last part of the upload: {exc}") from exc
            if tries > 5:
                raise Retryable(f"upload keeps failing: {exc}") from exc
            continue            # resumable: picks up where it stopped
        except Exception as exc:  # noqa: BLE001
            err = _http_error(exc, last_chunk)
            raise (err or Permanent(f"YouTube upload: {exc}")) from exc
    vid = resp["id"]
    out = {"external_id": vid, "url": f"https://youtube.com/shorts/{vid}", "note": ""}
    want = settings.get("youtube_privacy", "public")
    got = (resp.get("status") or {}).get("privacyStatus")
    if got and got != want:
        out["note"] = (f"YouTube set it to {got} (wanted {want}): the API project needs Google's audit, "
                       "see docs/POSTING_SETUP.md")
    if settings.get("post_first_comment", True) and job.get("pin_comment"):
        try:
            yt.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": vid, "topLevelComment": {"snippet": {"textOriginal": job["pin_comment"][:9000]}}}}).execute()
            out["note"] = (out["note"] + " · " if out["note"] else "") + "comment posted, pin it in the app"
        except Exception as exc:  # noqa: BLE001 - a missing comment never fails the post
            log.warning(f"{job['clip_id']}: first comment failed: {str(exc)[:200]}")
    log.info(f"{job['clip_id']}: uploaded to YouTube {out['url']} ({got or want})")
    return out
