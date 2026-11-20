"""Automatic quality check for a finished clip. A clip that fails goes to 'qc_failed'
instead of the posting queue, so a broken edit is never published without a human look."""

import subprocess

import numpy as np

from core.media import probe, run


def check(path, expect_w=1080, expect_h=1920, min_dur=12, max_dur=90, lufs_target=-14) -> dict:
    issues, info = [], {}
    try:
        p = probe(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "issues": [f"unreadable file: {exc}"]}
    dur = float(p["format"]["duration"])
    v = next((s for s in p["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in p["streams"] if s["codec_type"] == "audio"), None)
    info["duration"] = round(dur, 1)
    if not v or (v.get("width"), v.get("height")) != (expect_w, expect_h):
        issues.append(f"wrong video size {v and (v.get('width'), v.get('height'))}")
    if not a:
        issues.append("no audio track")
    if not min_dur <= dur <= max_dur:
        issues.append(f"duration {dur:.1f}s outside {min_dur}-{max_dur}s")

    # loudness
    out = run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
              check=False).stderr
    try:
        lufs = float([ln for ln in out.splitlines() if " I:" in ln][-1].split("I:")[1].split("LUFS")[0])
        info["lufs"] = lufs
        if abs(lufs - lufs_target) > 3:
            issues.append(f"loudness {lufs:.1f} LUFS (target {lufs_target})")
    except (IndexError, ValueError):
        issues.append("could not measure loudness")

    # black / frozen frames: sample 8 frames
    dark, frames = 0, []
    for i in range(8):
        t = dur * (i + 0.5) / 8
        raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=108:192", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        if len(raw) == 108 * 192:
            f = np.frombuffer(raw, np.uint8)
            frames.append(f)
            # judge the middle of the frame: the "card" layout is black above/below the video on purpose
            mid = f.reshape(192, 108)[60:122]
            if mid.mean() < 12:
                dark += 1
    if dark >= 3:
        issues.append(f"{dark}/8 sampled frames are black")
    if len(frames) >= 4 and max(np.abs(frames[i].astype(int) - frames[0]).mean() for i in range(1, len(frames))) < 1.5:
        issues.append("video looks frozen")
    info["sampled_frames"] = len(frames)
    return {"ok": not issues, "issues": issues, **info}
