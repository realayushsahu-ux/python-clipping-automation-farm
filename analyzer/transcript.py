"""Local speech-to-text with faster-whisper (free, no API).

- language(): 3 short samples, majority vote (Hindi streams then use the bigger model)
- scan(): whole video with the fast model, in 20-minute blocks, each block cached
  (a stopped run resumes where it stopped)
- words_for(): accurate word timings for one clip (caption model)
"""

import json
import threading

import numpy as np

import config.settings  # noqa: F401  (sets HF_HOME before faster_whisper is imported)
from config.settings import env
from core.logger import get_logger
from core.media import pcm

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


def _words(model: str, audio: np.ndarray, language, offset=0.0):
    np.seterr(divide="ignore", over="ignore", invalid="ignore")
    segs, info = _model(model).transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                                          vad_parameters={"min_silence_duration_ms": 500},
                                          beam_size=1 if model == "tiny" else 5, condition_on_previous_text=False)
    out = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                out.append({"s": round(w.start + offset, 2), "e": round(w.end + offset, 2), "w": t})
    return out, info.language


def language(audio_path, lo: float, hi: float) -> str | None:
    votes = {}
    for frac in (0.15, 0.45, 0.75):
        t = lo + (hi - lo) * frac
        if t + 30 > hi:
            continue
        try:
            _, lang = _words("tiny", pcm(audio_path, 16000, t, min(90, hi - t)), None)
        except Exception:  # noqa: BLE001
            continue
        if lang:
            votes[lang] = votes.get(lang, 0) + 1
    return max(votes, key=votes.get) if votes else None


def scan(audio_path, lo: float, hi: float, workdir, settings, lang=None) -> dict:
    cache = workdir / "transcript.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        log.info(f"reusing transcript ({len(data['words'])} words)")
        return data
    if not lang or lang == "auto":
        lang = language(audio_path, lo, hi) or "en"
        log.info(f"spoken language: {lang}")
    model = settings["whisper_hindi_model"] if lang.startswith("hi") else settings["whisper_scan_model"]
    starts = list(np.arange(lo, hi, BLOCK))
    log.info(f"listening to {(hi - lo) / 60:.0f} min in {len(starts)} block(s) (model {model})...")
    words = []
    for i, t in enumerate(starts):
        part = workdir / f"transcript_{i:03d}.json"
        if part.exists():
            got = json.loads(part.read_text(encoding="utf-8"))
        else:
            got, _ = _words(model, pcm(audio_path, 16000, float(t), float(min(BLOCK, hi - t))), lang, float(t))
            part.write_text(json.dumps(got), encoding="utf-8")
        words += got
        log.info(f"  {(min(t + BLOCK, hi) - lo) / 60:.0f}/{(hi - lo) / 60:.0f} min heard")
    if not words:
        raise RuntimeError("no speech found in this video")
    data = {"language": lang, "lo": lo, "hi": hi, "words": words}
    cache.write_text(json.dumps(data), encoding="utf-8")
    for p in workdir.glob("transcript_*.json"):
        p.unlink()
    return data


def words_for(media_path, start: float, length: float, lang: str, settings) -> list:
    """Accurate words for one clip; times relative to `start` of `media_path`."""
    model = settings["whisper_hindi_model"] if (lang or "").startswith("hi") else settings["whisper_caption_model"]
    words, _ = _words(model, pcm(media_path, 16000, start, length), lang)
    return words
