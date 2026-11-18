"""Editing decisions for one clip (no rendering here):
- best window ≤ final_max_seconds (long moments are trimmed to their strongest part)
- silence removal: quiet gaps between lines are cut (jump cuts)
- cold open: the most intense ~1.6 s plays first
- punch-in and shake points from loudness onsets
Times are seconds in the clip's source file.
"""

import numpy as np


def pick_window(phrases, env, cs, ce, max_len=40.0, min_len=22.0):
    if ce - cs <= max_len:
        return cs, ce
    base = np.median(env[int(cs * 10): int(ce * 10)])
    starts = sorted({cs} | {p[0]["t0"] - 0.15 for p in phrases if cs <= p[0]["t0"] < ce})
    ends = sorted({ce} | {p[-1]["t1"] + 0.35 for p in phrases if cs < p[-1]["t1"] <= ce})
    best, best_s = (cs, cs + max_len), -1e9
    for a in starts:
        for b in ends:
            if min_len <= b - a <= max_len:
                seg = env[int(a * 10): int(b * 10)]
                s = np.clip(seg - base, 0, None).sum() / 10 + 0.25 * (b - a) + (3 if b == ce else 0)
                if s > best_s:
                    best, best_s = (a, b), s
    return best


def tighten(a, b, words, env, gap_min=0.8, keep=0.3, enabled=True):
    if not enabled:
        return [(a, b)]
    ws = [w for w in words if w["t0"] >= a - 0.05 and w["t1"] <= b + 0.05]
    med = np.median(env[int(a * 10): int(b * 10)])
    cuts = []
    for w1, w2 in zip(ws, ws[1:]):
        g0, g1 = w1["t1"], w2["t0"]
        if g1 - g0 > gap_min:
            seg = env[int(g0 * 10): int(g1 * 10) + 1]
            if len(seg) and seg.max() < med + 3:      # never cut loud action
                cuts.append((g0 + keep / 2, g1 - keep / 2))
    segs, cur = [], a
    for c0, c1 in cuts:
        if c0 - cur > 0.3:
            segs.append((cur, c0))
        cur = c1
    segs.append((cur, b))
    return segs


def pick_cold(a, b, words, env, length=1.6):
    best, best_s = None, -1e9
    t = a + 3.0
    while t + length <= b:
        seg = env[int(t * 10): int((t + length) * 10)]
        talk = sum(1 for w in words if t <= w["t0"] < t + length)
        s = seg.mean() + 0.6 * seg.max() + min(talk, 4) * 1.5
        if s > best_s:
            best, best_s = t, s
        t += 0.1
    if best is None:
        return None
    for w in words:
        if best <= w["t0"] < best + 0.4:
            best = w["t0"] - 0.08
            break
    return (best, best + length)


def punch_points(env_out, fps, dur, main_start, has_cold, per_seconds=4):
    med = np.median(env_out)
    punches = []
    total = len(env_out)
    for k in range(int(0.5 * fps), total):
        t = k / fps
        if has_cold and abs(t - main_start) < 0.8:
            continue
        onset = env_out[k] - env_out[max(0, k - 15): k].min()
        if env_out[k] > med + 5 and onset > 7 and env_out[k] >= env_out[max(0, k - 2): k + 3].max():
            if not punches or t - punches[-1][0] > 2.4:
                punches.append((t, float(onset)))
    cap = max(1, int(dur / per_seconds))
    if len(punches) > cap:
        punches = sorted(sorted(punches, key=lambda p: -p[1])[:cap])
    shakes = [p[0] for p in sorted(punches, key=lambda p: -p[1])[:4] if p[1] > 12]
    return punches, shakes
