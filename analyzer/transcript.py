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


def _words(model: str, audio: np.ndarray, language, offset=0.0, beam=None):
    np.seterr(divide="ignore", over="ignore", invalid="ignore")
    segs, info = _model(model).transcribe(audio, language=language, word_timestamps=True, vad_filter=True,
                                          vad_parameters={"min_silence_duration_ms": 500},
                                          beam_size=beam or (1 if model == "tiny" else 5), condition_on_previous_text=False)
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


def scan(audio_path, lo: float, hi: float, workdir, settings, lang=None, windows=None) -> dict:
    """Whole range in 20-min blocks, or (long streams) only `windows` [(start, end)] picked from chat +
    loudness spikes. Each block/window is cached, so a stopped run continues where it was."""
    cache = workdir / "transcript.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
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
        beam, tag = None, ""
        log.info(f"listening to {(hi - lo) / 60:.0f} min in {len(blocks)} block(s) (model {model})...")
    words, done = [], 0.0
    total = sum(b - a for a, b in blocks)
    for i, (a, b) in enumerate(blocks):
        part = workdir / (f"transcript_w{model}_{int(a)}.json" if tag else f"transcript_{i:03d}.json")
        if part.exists():
            got = json.loads(part.read_text(encoding="utf-8"))
        else:
            got, _ = _words(model, pcm(audio_path, 16000, a, b - a), lang, a, beam)
            part.write_text(json.dumps(got), encoding="utf-8")
        words += got
        done += b - a
        log.info(f"  {done / 60:.0f}/{total / 60:.0f} min heard")
    if not words:
        raise RuntimeError("no speech found in this video")
    data = {"language": lang, "lo": lo, "hi": hi, "words": words, "windows": [list(w) for w in windows or []]}
    cache.write_text(json.dumps(data), encoding="utf-8")
    for p in workdir.glob("transcript_*.json"):
        p.unlink()
    return data


def words_for(media_path, start: float, length: float, lang: str, settings) -> list:
    """Accurate words for one clip; times relative to `start` of `media_path`."""
    model = settings["whisper_hindi_model"] if (lang or "").startswith("hi") else settings["whisper_caption_model"]
    words, _ = _words(model, pcm(media_path, 16000, start, length), lang)
    return words
