"""Look at the clip before titling it (2026-11-20). The moment and the on-screen title are chosen from the
transcript, and a fast Hindi transcript can be wrong: a test clip was titled "Horse collides with wall"
while the frames showed a cutscene with Lucia and a boat. Here 4 frames of the clip go to the AI, which
keeps the title only if it is TRUE on screen and otherwise writes one from what it sees.
Cost: ~1,500 input tokens per clip (gpt-5-mini, ~Rs 0.3). No key / no OpenAI → title unchanged."""

import base64
import subprocess

from config.settings import env
from core.logger import get_logger

log = get_logger("vision")

SYSTEM = """You check short gaming clips before they are posted. You see 4 frames of the clip in order \
and what is said. Trending GTA 6 clips carry ONE plain title the whole time that a stranger understands \
at once and that is TRUE on screen ("Airport reveal in GTA 6 🤯", "IShowSpeed almost eaten by a GTA 6 \
alligator 🐊😂", "The NPCs are crazy in GTA 6", "Bro put her in the trunk and jumped the bridge 💀")."""

USER = """Proposed title: "{banner}"
Game: {game} (KNOWN from the source video: never call it another game, even if it looks like an older one).
Streamer/source: {creator}. What the clip is about (from the source): {premise}
What is said (may contain transcription errors): {text}

1. Describe in one short sentence what the frames really show (place, characters, action).
2. Is the proposed title TRUE for these frames? Judge what you can see AND what is clearly said: a \
feature described in the speech (e.g. "no minimap") can be true even if you cannot see it. Only if it is \
clearly FALSE, write a new one: 3-9 words, normal sentence case, names {game}, ONE emoji at the end, no \
hashtags. If unsure, keep it (title_true = true).
3. Does the FIRST frame already show the thing the title promises (yes/no)?"""

SCHEMA = {"type": "object", "properties": {
    "seen": {"type": "string"}, "title_true": {"type": "boolean"}, "title": {"type": "string"},
    "first_frame_shows_it": {"type": "boolean"}}}


def frames(video, start, length, n=4, width=480):
    """n JPEG frames (base64) spread over [start, start+length] of the video."""
    out = []
    for i in range(n):
        t = start + (0.4 if i == 0 else length * i / n)
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"],
                           capture_output=True, timeout=60)
        if r.stdout:
            out.append(base64.b64encode(r.stdout).decode())
    return out


def check_title(settings, video, start, length, pkg, text, client=None) -> dict:
    """{'title', 'seen', 'changed', 'first_frame_shows_it'} or {} when it cannot check."""
    if not settings.get("vision_check", True) or not env("OPENAI_API_KEY"):
        return {}
    banner = pkg.get("banner") or ""
    try:
        imgs = frames(video, start, length)
        if not imgs:
            return {}
        content = [{"type": "input_text", "text": USER.format(banner=banner, game=pkg.get("game") or "GTA 6",
                                                              creator=pkg.get("creator") or "the streamer",
                                                              premise=pkg.get("youtube_title") or "",
                                                              text=(text or "")[:900])}]
        content += [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}", "detail": "low"}
                    for b in imgs]
        from ai.openai_client import OpenAIProvider
        got = OpenAIProvider().json(SYSTEM, content, SCHEMA, max_tokens=1500)
    except Exception as exc:  # noqa: BLE001 - the clip still renders with its own title
        log.warning(f"vision check skipped: {str(exc)[:150]}")
        return {}
    title = (got.get("title") or "").strip() if not got.get("title_true") else banner
    game = (pkg.get("game") or "").strip()
    if title and game and game.lower().replace(" ", "") not in title.lower().replace(" ", ""):
        title = banner                       # a rewrite that drops or changes the game name is not trusted
    return {"title": title or banner, "seen": got.get("seen", ""), "changed": bool(title and title != banner),
            "first_frame_shows_it": got.get("first_frame_shows_it")}
