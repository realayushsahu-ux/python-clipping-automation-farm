"""Local speech-to-text with faster-whisper (free, no API).

- language(): 3 short samples, majority vote (Hindi streams then use the bigger model)
- scan(): whole video with the fast model, in 20-minute blocks, each block cached
  (a stopped run resumes where it stopped)
- words_for(): accurate word timings for one clip (caption model)
"""

import json
import threading
import time

import numpy as np

import config.settings  # noqa: F401  (sets HF_HOME before faster_whisper is imported)
from config.settings import env
from core.logger import get_logger
from core.media import pcm
from core.safe_io import read_json, write_json

log = get_logger("transcript")
_MODELS, _LOCK = {}, threading.Lock()
BLOCK = 20 * 60


def _model(name: str):
    with _LOCK:
        if name not in _MODELS:
            from faster_whisper import WhisperModel
            device = env("WHISPER_DEVICE", "cpu") or "cpu"
            log.info(f"loading Whisper '{name}' on {device}...")
            try:
                _MODELS[name] = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
            except Exception as exc:  # noqa: BLE001 - no CUDA → CPU
                if device == "cpu":
                    raise
                log.warning(f"GPU Whisper failed ({str(exc)[:80]}); using CPU")
                _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _MODELS[name]


def _words(model: str, audio: np.ndarray, language, offset=0.0, beam=None, tick=None, save=None):
    np.seterr(divide="ignore", over="ignore", invalid="ignore")
    segs, info = _model(model).transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                                          vad_parameters={"min_silence_duration_ms": 500},
                                          beam_size=beam or (1 if model == "tiny" else 5), condition_on_previous_text=False)
    out = []
    for s in segs:
        if tick:
            tick(s.end)                       # seconds of this block heard so far (live progress)
        for w in s.words or []:
            t = w.word.strip()
            if t:
                out.append({"s": round(w.start + offset, 2), "e": round(w.end + offset, 2), "w": t})
        if save:
            save(out, s.end)                  # checkpoint: a power cut loses at most about a minute
    return out, info.language


def language(audio_path, lo: float, hi: float) -> str | None:
    votes = {}
    for frac in (0.15, 0.45, 0.75):
        t = lo + (hi - lo) * frac
        if t + 30 > hi:
            continue
        try:
            # Whisper decides the language from the first 30 s before writing a word, and the words come
            # lazily: reading info.language without the words took seconds instead of 17 min (2026-11-21).
            _, info = _model("tiny").transcribe(pcm(audio_path, 16000, t, min(30, hi - t)), language=None,
                                                beam_size=1, vad_filter=True)
            lang = info.language
        except Exception:  # noqa: BLE001
            continue
        if lang:
            votes[lang] = votes.get(lang, 0) + 1
    return max(votes, key=votes.get) if votes else None


def scan(audio_path, lo: float, hi: float, workdir, settings, lang=None, windows=None, progress=None) -> dict:
    """Whole range in 20-min blocks, or (long streams) only `windows` [(start, end)] picked from chat +
    loudness spikes. Each block/window is cached, so a stopped run continues where it was.
    progress(done_s, total_s, eta_s): called while listening (eta_s None until it can be estimated)."""
    cache = workdir / "transcript.json"
    data = read_json(cache)
    if data is not None:
        log.info(f"reusing transcript ({len(data['words'])} words)")
        return data
    if not lang or lang == "auto":
        lang = language(audio_path, lo, hi) or "en"
        log.info(f"spoken language: {lang}")
    model = settings["whisper_hindi_model"] if lang.startswith("hi") else settings["whisper_scan_model"]
    if windows:
        blocks = [(float(a), float(b)) for a, b in windows]
        beam, tag = int(settings.get("focus_scan_beam", 1)), "w"
        model = settings.get("focus_scan_model") or model      # this PC: small runs ~4x slower than real time
        log.info(f"listening to {sum(b - a for a, b in blocks) / 60:.0f} of {(hi - lo) / 60:.0f} min: "
                 f"{len(blocks)} stretch(es) where chat/voice spiked (model {model})...")
    else:
        blocks = [(float(t), float(min(t + BLOCK, hi))) for t in np.arange(lo, hi, BLOCK)]
        # beam 1 (greedy): measured 2026-11-21 on this PC (i5-4590T), Hindi, model small: 2x faster than beam 5
        # with as many words. Only for finding moments; the captions of each clip keep the careful setting.
        beam, tag = int(settings.get("whisper_scan_beam", 1)), ""
        log.info(f"listening to {(hi - lo) / 60:.0f} min in {len(blocks)} block(s) (model {model}, beam {beam})...")
    words, done = [], 0.0
    total = sum(b - a for a, b in blocks)
    t0, fresh0 = time.time(), None          # speed is measured only on what is heard in this run

    def tell(at):
        nonlocal fresh0
        if not progress:
            return
        if fresh0 is None:
            fresh0 = at
        speed = (at - fresh0) / max(time.time() - t0, 1)
        eta = (total - at) / speed if speed > 0 and at - fresh0 > 30 else None
        try:
            progress(min(at, total), total, eta)
        except Exception:  # noqa: BLE001 - progress is only for display
            pass

    for i, (a, b) in enumerate(blocks):
        part = workdir / (f"transcript_w{model}_{int(a)}.json" if tag else f"transcript_{i:03d}.json")
        got = read_json(part)
        if got is None:
            # resume inside the block from the last checkpoint (saved about every minute while listening)
            half = workdir / (part.stem + "_partial.json")
            prev = read_json(half) or {}
            upto = float(prev.get("upto") or 0) if prev.get("model") == model else 0.0
            before = prev.get("words") or [] if upto else []
            if upto:
                log.info(f"  carrying on from {(a + upto) / 60:.1f} min (saved before the farm stopped)")
            last_save = [time.time()]

            def save(out, at, start=a + upto, before=before, half=half):
                if time.time() - last_save[0] >= 60:
                    last_save[0] = time.time()
                    write_json(half, {"model": model, "upto": start - a + at, "words": before + out})

            tell(done + upto)
            got, _ = _words(model, pcm(audio_path, 16000, a + upto, b - a - upto), lang, a + upto, beam,
                            tick=lambda t, d=done + upto: tell(d + t), save=save)
            got = before + got
            write_json(part, got)
            half.unlink(missing_ok=True)
        words += got
        done += b - a
        log.info(f"  {done / 60:.0f}/{total / 60:.0f} min heard")
    if not words:
        raise RuntimeError("no speech found in this video")
    data = {"language": lang, "lo": lo, "hi": hi, "words": words, "windows": [list(w) for w in windows or []]}
    write_json(cache, data)
    for p in workdir.glob("transcript_*.json"):
        p.unlink()
    return data


def words_for(media_path, start: float, length: float, lang: str, settings) -> list:
    """Accurate words for one clip; times relative to `start` of `media_path`."""
    model = settings["whisper_hindi_model"] if (lang or "").startswith("hi") else settings["whisper_caption_model"]
    words, _ = _words(model, pcm(media_path, 16000, start, length), lang)
    return words
