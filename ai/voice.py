"""Channel voice (config/voice.md) added to every AI system prompt, so all text sounds like one channel."""

from functools import lru_cache

from core.paths import ROOT

VOICE_FILE = ROOT / "config" / "voice.md"


@lru_cache(maxsize=1)
def _voice_text(mtime: float) -> str:
    try:
        text = VOICE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:4000]


def voice() -> str:
    try:
        mtime = VOICE_FILE.stat().st_mtime
    except OSError:
        return ""
    return _voice_text(mtime)


WINNERS_FILE = ROOT / "config" / "winners.md"      # Phase 8: growth/learn.py writes it from your best posts


def winners_text() -> str:
    try:
        return WINNERS_FILE.read_text(encoding="utf-8").strip()[:2500]
    except OSError:
        return ""


TRENDS_FILE = ROOT / "config" / "trends.md"        # Phase 8: growth/trends.py writes it from the niche's outliers
TRENDS_MAX_AGE_S = 3 * 24 * 3600                   # trends go stale: an old file is ignored


def trends_text() -> str:
    import time
    try:
        if time.time() - TRENDS_FILE.stat().st_mtime > TRENDS_MAX_AGE_S:
            return ""
        return TRENDS_FILE.read_text(encoding="utf-8").strip()[:2000]
    except OSError:
        return ""


def with_voice(system: str) -> str:
    v = voice()
    out = f"{system}\n\nCHANNEL VOICE (follow it):\n{v}" if v else system
    w = winners_text()
    out = f"{out}\n\nLEARNED FROM THIS CHANNEL'S BEST POSTS (patterns, never copy):\n{w}" if w else out
    t = trends_text()
    return f"{out}\n\nTRENDING IN THE NICHE THIS WEEK (only if this clip really shows it, never copy):\n{t}" if t else out
