"""Non-speech signals that show where the good moments are: loudness per second
(screams, hype) and how far each second sits above its surroundings."""

import json
import subprocess

import numpy as np


def loudness(audio_path, workdir) -> list:
    """dB per second, streamed through ffmpeg (never loads a whole 6 h file)."""
    cache = workdir / "loudness.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    rate = 8000
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(audio_path), "-vn", "-ac", "1",
                             "-ar", str(rate), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out = []
    while True:
        buf = proc.stdout.read(rate * 2 * 60)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(x) // rate
        if n:
            sec = x[: n * rate].reshape(n, rate)
            out += (20 * np.log10(np.sqrt((sec ** 2).mean(axis=1)) + 1e-6)).round(1).tolist()
    proc.wait()
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def relative(values, window=120) -> np.ndarray:
    """How many dB each second is above the median of the 2 minutes around it."""
    v = np.asarray(values, dtype=np.float32)
    if len(v) < 5:
        return np.zeros_like(v)
    half = window // 2
    padded = np.pad(v, (half, half), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, window + 1)[: len(v)]
    return v - np.median(win[:, ::4], axis=1)


def transcript_lines(words, loud_rel, chat, line_seconds=8, loud_db=8.0):
    """~8 s transcript lines with [LOUD +NdB] / [CHAT xN] markers, for the AI picker."""
    lines, cur, start = [], [], None
    chat_base = float(np.median([c for c in chat if c > 0])) if chat and any(chat) else 0.0

    def flush(t0, t1, text):
        tags = []
        if len(loud_rel):
            a, b = int(t0), max(int(t0) + 1, int(np.ceil(t1)))
            if a < len(loud_rel):
                peak = float(np.max(loud_rel[a:b]))
                if peak >= loud_db:
                    tags.append(f"[LOUD +{peak:.0f}dB]")
        if chat_base:
            seg = chat[int(t0 // 5): int(t1 // 5) + 2]
            if seg and max(seg) / chat_base >= 2.5:
                tags.append(f"[CHAT x{max(seg) / chat_base:.0f}]")
        lines.append((t0, t1, (" ".join(tags) + " " if tags else "") + text))

    for w in words:
        if start is None:
            start = w["s"]
        cur.append(w["w"])
        if w["e"] - start >= line_seconds:
            flush(start, w["e"], " ".join(cur))
            cur, start = [], None
    if cur:
        flush(start, words[-1]["e"], " ".join(cur))
    return lines
