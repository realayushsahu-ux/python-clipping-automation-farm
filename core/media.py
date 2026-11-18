"""Small FFmpeg / process helpers shared by every module."""

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000


def which_or_fail(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RuntimeError(f"'{tool}' not found. Install it (see docs/SETUP.md) and open a new terminal.")
    return path


def run(cmd, timeout=None, cwd=None, check=True) -> subprocess.CompletedProcess:
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=cwd)
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed ({proc.returncode}): " + " | ".join(tail))
    return proc


def probe(path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
               "-of", "json", str(path)]).stdout
    return json.loads(out)


def duration(path) -> float:
    return float(probe(path)["format"]["duration"])


def pcm(path, rate=16000, start=None, length=None, channels=1) -> np.ndarray:
    """Decode audio to float32 numpy (mono by default)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", str(channels), "-ar", str(rate), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.float32).copy()
    return a.reshape(-1, channels) if channels > 1 else a


def write_wav(path, stereo: np.ndarray, rate=SR):
    pcm16 = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm16.tobytes())


def fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def parse_time(s: str) -> float:
    parts = [float(p) for p in str(s).strip().split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def parse_section(s: str):
    """'0:20:00-0:40:00' -> (1200.0, 2400.0)."""
    if not s:
        return None
    a, b = s.split("-", 1)
    return parse_time(a), parse_time(b)
