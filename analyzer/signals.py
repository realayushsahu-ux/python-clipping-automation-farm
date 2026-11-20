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


def focus_windows(loud_rel, chat, lo, hi, budget_s=5400, win_s=180, pre_s=120, per_hour_share=0.3):
    """For very long streams: the stretches worth transcribing, [(start, end)] sorted.
    Score per second = chat spike (messages vs. the stream's median, from the 5 s chat buckets) + how far
    the streamer's voice is above its 2-minute median. The best seconds become windows that start
    `pre_s` before the peak (the build-up) and last `win_s`; no hour takes more than `per_hour_share`
    of the budget, so the whole stream is covered, not one loud hour."""
    n = int(hi - lo)
    if n <= 0:
        return []
    score = np.zeros(n, dtype=np.float32)
    lr = np.asarray(loud_rel, dtype=np.float32)
    if len(lr):
        seg = lr[int(lo): int(lo) + n]
        score[: len(seg)] += np.clip(seg, 0, 20) / 4.0
    if chat:
        c = np.asarray(chat, dtype=np.float32)
        base = float(np.median(c[c > 0])) if (c > 0).any() else 1.0
        per_s = np.repeat(c / max(base, 1.0), 5)[int(lo): int(lo) + n]
        score[: len(per_s)] += np.clip(per_s - 1.0, 0, 8)
    # smooth over 10 s so one shout does not beat a real moment
    k = np.ones(10, dtype=np.float32) / 10
    score = np.convolve(score, k, mode="same")
    order = np.argsort(-score)
    taken, used, per_hour = [], 0, {}
    cap = budget_s * per_hour_share
    for i in order:
        if used >= budget_s or score[i] <= 0:
            break
        a = max(0, int(i) - pre_s)
        b = min(n, a + win_s)
        if any(a < y and b > x for x, y in taken):
            continue
        h = (lo + a) // 3600
        if per_hour.get(h, 0) + (b - a) > cap:
            continue
        taken.append((a, b))
        used += b - a
        per_hour[h] = per_hour.get(h, 0) + (b - a)
    taken.sort()
    merged = []
    for a, b in taken:                       # touching windows become one
        if merged and a <= merged[-1][1] + 5:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return [(lo + a, lo + b) for a, b in merged]


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
