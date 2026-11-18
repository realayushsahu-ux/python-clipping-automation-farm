"""AI voice commentary: our own voice on every clip (original value for YouTube's reused-content rule).

  intro  : spoken over the first seconds (clip sound ducked) - frames WHAT to watch, never spoils it
  take   : our short opinion at the end, ending on a question (comments); a short freeze if it needs time

Free and local: Kokoro (kokoro-onnx, models/kokoro/, Apache-2.0 model), no key, no internet.
The lines come from the same AI call that writes titles (no extra cost). A line that is generic,
too long, makes a claim or repeats the hook is dropped: no commentary beats bad commentary.
Settings: voice_commentary (yes/no), voice_name, voice_speed, voice_duck.
"""

import re
from functools import lru_cache

import numpy as np

from core.logger import get_logger
from core.media import SR
from core.paths import ROOT

log = get_logger("voice")
MODEL_DIR = ROOT / "models" / "kokoro"
MAX_INTRO_S = 4.2
MAX_TAKE_S = 5.0
MAX_INTRO_WORDS = 12
MAX_TAKE_WORDS = 15

# lines every lazy clip channel says: they add nothing a viewer couldn't guess (= not original commentary)
GENERIC = ("watch till the end", "wait for it", "you won't believe", "you wont believe", "insane", "viral",
           "like and subscribe", "subscribe", "what happens next", "check this out", "look at this",
           "this is crazy", "crazy moment", "must watch", "don't miss", "let's go", "lets go", "comment below",
           "follow for more", "part 2", "unbelievable", "mind blowing", "mind-blowing", "epic moment")
STOP = set("""the a an and or but so to of in on at for with is it this that these those was were be been are
he she they his her their them you your we our i me my just what how why who when then than there here
does did do not no yes very really about into out up down off over again more most some any all can will
would could should has have had get got gets going goes go""".split())


# ------------------------------------------------------------------------------------- checks
def _words(t):
    return re.findall(r"[a-z0-9']+", (t or "").lower())


def check_line(line, kind, context, hook="", used=()) -> tuple[str, str]:
    """→ (clean line, "") or ("", reason). context = transcript + title: the line must name something in it."""
    from ai.quality import has_claim, similar
    line = re.sub(r"\s+", " ", (line or "").replace("#", "").strip().strip('"'))
    if not line:
        return "", "empty"
    w = _words(line)
    limit = MAX_INTRO_WORDS if kind == "intro" else MAX_TAKE_WORDS
    if len(w) < 4 or len(w) > limit:
        return "", f"{len(w)} words (4-{limit})"
    low = line.lower()
    bad = [g for g in GENERIC if g in low]
    if bad:
        return "", f"generic ({bad[0]})"
    if has_claim(line):
        return "", "claim"
    stem = lambda x: x[:-1] if x.endswith("s") and len(x) > 3 else x  # noqa: E731 - cops ~ cop
    ctx = {stem(x) for x in _words(context)} - STOP
    concrete = [x for x in w if x not in STOP and len(x) > 2 and stem(x) in ctx]
    if not concrete:
        return "", "names nothing from the clip"
    if hook and similar(line, hook) >= 0.6:
        return "", "repeats the on-screen hook"
    if any(similar(line, u) >= 0.55 for u in used if u):
        return "", "same as another clip"
    if kind == "take" and not line.endswith("?"):
        line = line.rstrip(".!") + "?"
    return line, ""


def pick_lines(out: dict, context: str, hook: str, used=()) -> dict:
    """From the AI's options, the first intro and take that pass. Returns {"voice_intro", "voice_take", "voice_notes"}."""
    res, notes = {"voice_intro": "", "voice_take": "", "voice_intro_alts": [], "voice_take_alts": []}, []
    for kind in ("intro", "take"):
        opts = out.get(f"voice_{kind}_options") or []
        if out.get(f"voice_{kind}"):
            opts = [out[f"voice_{kind}"], *opts]
        good = []
        for o in opts:
            line, why = check_line(o, kind, context, hook, used)
            if line and line not in good:
                good.append(line)
            elif not line:
                notes.append(f"{kind} dropped ({why}): {o}")
        res[f"voice_{kind}"] = good[0] if good else ""
        res[f"voice_{kind}_alts"] = good[1:]
    res["voice_notes"] = notes
    return res


# ---------------------------------------------------------------------------------- speech
def available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return (MODEL_DIR / "kokoro-v1.0.int8.onnx").exists() and (MODEL_DIR / "voices-v1.0.bin").exists()


@lru_cache(maxsize=1)
def _engine():
    from kokoro_onnx import Kokoro
    return Kokoro(str(MODEL_DIR / "kokoro-v1.0.int8.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))


def speak(text, settings) -> np.ndarray:
    """Stereo float array at SR, silence trimmed."""
    samples, rate = _engine().create(text, voice=settings.get("voice_name", "am_fenrir"),
                                     speed=float(settings.get("voice_speed", 1.12)), lang="en-us")
    x = np.asarray(samples, dtype=np.float64)
    nz = np.flatnonzero(np.abs(x) > 0.01)
    if len(nz):
        x = x[max(0, nz[0] - int(0.03 * rate)): nz[-1] + int(0.08 * rate)]
    if rate != SR:
        t_new = np.arange(int(len(x) * SR / rate)) / SR
        x = np.interp(t_new, np.arange(len(x)) / rate, x)
    return np.stack([x, x], axis=1)


def word_times(text, t0, dur, seg):
    """Caption words spread over the spoken line (by length), in the renderer's word format."""
    ws = text.split()
    weights = np.array([len(w) + 2 for w in ws], dtype=float)
    edges = t0 + np.concatenate([[0], np.cumsum(weights)]) / weights.sum() * dur
    return [{"w": w, "t0": float(edges[i]), "t1": float(edges[i + 1]), "pend": i == len(ws) - 1, "seg": seg}
            for i, w in enumerate(ws)]


def prepare(pkg, settings) -> dict | None:
    """{"intro": {"text", "audio", "dur"}, "take": {...}} for render_clip, or None (off / no lines / no engine)."""
    if not settings.get("voice_commentary", True):
        return None
    if not (pkg.get("voice_intro") or pkg.get("voice_take")):
        return None
    if not available():
        log.warning("voice commentary skipped: Kokoro not installed (pip install kokoro-onnx + models/kokoro)")
        return None
    out = {}
    base = float(settings.get("voice_speed", 1.12))
    kinds = [("intro", MAX_INTRO_S)]
    mode = settings.get("voice_take_mode", "text")
    if mode == "voice":
        kinds.append(("take", MAX_TAKE_S))
    elif mode == "text" and pkg.get("voice_take"):
        out["take_text"] = pkg["voice_take"]          # shown on screen at the end, the clip's own voice plays on
    for kind, cap in kinds:
        lines = [x for x in [pkg.get(f"voice_{kind}"), *(pkg.get(f"voice_{kind}_alts") or [])] if x]
        tries = [(t, base) for t in lines] + [(t, base + 0.12) for t in lines[:2]]   # then a little faster
        for text, speed in tries:
            try:
                audio = speak(text, {**settings, "voice_speed": speed})
            except Exception as exc:  # noqa: BLE001 - a clip without voice is still a clip
                log.warning(f"voice {kind} failed: {exc}")
                break
            dur = len(audio) / SR
            if dur <= cap:
                out[kind] = {"text": text, "audio": audio, "dur": dur}
                break
            log.info(f"voice {kind} {dur:.1f}s > {cap}s at speed {speed:.2f}: {text}")
    return out or None


def text_parts(text, clip_dur, per_word=0.32, max_words=6):
    """The closing comment as 1-3 short on-screen lines over the last seconds: [(t0, t1, words)]."""
    ws = text.split()
    parts = [ws[i:i + max_words] for i in range(0, len(ws), max_words)]
    if len(parts) > 1 and len(parts[-1]) <= 2:              # no lonely last word
        parts[-2] += parts.pop()
    total = min(max(3.0, per_word * len(ws) + 0.6), 6.0, clip_dur * 0.4)
    t, out = clip_dur - total, []
    for p in parts:
        d = total * len(p) / len(ws)
        out.append((round(t, 2), round(t + d, 2), " ".join(p)))
        t += d
    return out


def plan(voice, dur, gap_after_intro=2.0, take_overlap=1.2):
    """Where the lines go on a clip of `dur` seconds. → (intro_end, take_start, extra_seconds)."""
    intro, take = (voice or {}).get("intro"), (voice or {}).get("take")
    intro_end = intro["dur"] + 0.15 if intro else 0.0
    if not take:
        return intro_end, None, 0.0
    take_start = max(intro_end + gap_after_intro, dur - take_overlap)
    extra = max(0.0, take_start + take["dur"] + 0.35 - dur)
    return intro_end, take_start, extra


def level_match(voice_audio, dialogue, boost=1.4):
    """Scale the voice to sit a bit above the (unducked) clip speech."""
    m = dialogue.mean(1) if dialogue.ndim == 2 else dialogue
    hop = SR // 20
    frames = m[: len(m) // hop * hop].reshape(-1, hop)
    rms = np.sqrt((frames ** 2).mean(1)) if len(frames) else np.array([0.05])
    active = rms[rms > 10 ** (-40 / 20)]
    target = (np.median(active) if len(active) else 0.05) * boost
    v = voice_audio.mean(1)
    vr = np.sqrt((v ** 2).mean()) or 1.0
    return voice_audio * min(target / vr, 8.0)


def duck_env(n, windows, low=0.25, ramp=0.08):
    """1.0 everywhere, `low` inside the (start, end) windows, smooth ramps of `ramp` seconds."""
    mask = np.zeros(n)
    for a, b in windows:
        mask[max(0, int(a * SR)): min(n, int(b * SR))] = 1.0
    k = max(1, int(ramp * SR))
    if n > k:
        mask = np.minimum(1.0, np.convolve(mask, np.ones(k) / k * 2, "same"))
    return 1 - (1 - low) * mask
