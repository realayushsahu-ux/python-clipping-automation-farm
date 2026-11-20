"""Sound design: SFX, background music, ducking, noise reduction.

Sources, in order:
- SFX:   assets/sfx/<type>/*.wav|mp3|ogg  (type = whoosh, boom, riser, glitch, shutter, slice, click, ...)
         → else generated (whoosh, boom, riser, glitch) or silent
- Music: assets/music/**/*.mp3|wav|m4a|ogg (your licensed tracks; least-used track first,
         never the same track twice in a row)                                         → else generated beat
Only put audio you are allowed to use on Instagram/YouTube into assets/ (see assets/LICENSES.md).
"""

import json
import random
import subprocess
from pathlib import Path

import numpy as np

from core.media import SR, pcm
from core.paths import ASSETS_DIR, DATA_DIR

AUDIO_EXT = (".wav", ".mp3", ".m4a", ".ogg", ".flac")
_rng = np.random.default_rng(7)


# ----------------------------------------------------------------------------- generated SFX
def _bandnoise(n, lo, hi):
    spec = np.fft.rfft(_rng.standard_normal(n))
    fr = np.fft.rfftfreq(n, 1 / SR)
    spec[(fr < lo) | (fr > hi)] = 0
    x = np.fft.irfft(spec, n)
    return x / (np.abs(x).max() + 1e-9)


def gen_whoosh(dur=0.5):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    out = np.zeros(n)
    for k, (lo, hi) in enumerate([(200, 600), (500, 1400), (1200, 3500), (3000, 8000)]):
        out += _bandnoise(n, lo, hi) * np.exp(-((t - (0.25 + 0.15 * k)) / 0.18) ** 2)
    out *= np.sin(np.pi * t) ** 0.7
    pan = np.linspace(0.2, 0.8, n)
    return np.stack([out * (1 - pan), out * pan], 1) * 0.9


def gen_boom(dur=0.9, f0=70, f1=32):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t * 6)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
    s += _bandnoise(n, 800, 6000) * np.exp(-t * 60) * 0.5
    s = np.tanh(s * 1.8) * 0.8
    return np.stack([s, s], 1)


def gen_riser(dur=1.2):
    n = int(dur * SR)
    t = np.linspace(0, 1, n)
    s = _bandnoise(n, 2000, 12000) * t ** 2.2 * 0.6
    s += np.sin(2 * np.pi * np.cumsum(200 + 1000 * t ** 2) / SR) * t ** 2 * 0.25
    return np.stack([s, s], 1)


def gen_glitch(dur=0.18):
    n = int(dur * SR)
    s = np.round(_bandnoise(n, 300, 9000) * 6) / 6
    s = s * (np.floor(np.arange(n) / (SR * 0.02)) % 2 == 0) * 0.35
    return np.stack([s, s * 0.8], 1)


GENERATORS = {"whoosh": gen_whoosh, "boom": gen_boom, "riser": gen_riser, "glitch": gen_glitch}


class SFX:
    """Library SFX if present (random pick per use), generated otherwise."""

    def __init__(self, seed=0):
        self.rand = random.Random(seed)
        self.cache = {}

    def get(self, kind, dur=None):
        folder = ASSETS_DIR / "sfx" / kind
        files = sorted(p for p in folder.glob("*") if p.suffix.lower() in AUDIO_EXT) if folder.exists() else []
        if files:
            f = self.rand.choice(files)
            if f not in self.cache:
                sig = pcm(f, SR, channels=2).astype(np.float64)
                peak = np.abs(sig).max()
                self.cache[f] = sig * (0.9 / peak) if peak > 1e-6 else sig  # same level as the generated SFX
            sig = self.cache[f]
            if dur and len(sig) > int(dur * SR):
                sig = sig[: int(dur * SR)].copy()
                fl = min(int(0.06 * SR), len(sig))  # short fade so a cut-off file never clicks
                sig[len(sig) - fl:] *= np.linspace(1, 0, fl)[:, None]
            return sig
        gen = GENERATORS.get(kind)
        if gen is None:  # no file and nothing to generate (e.g. shutter, slice): silent
            return np.zeros((int((dur or 0.1) * SR), 2))
        return gen(dur) if dur else gen()


# ----------------------------------------------------------------------------- music
PROGS = [
    ("dark trap Am", 142, [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]),
    ("drill Cm", 144, [(60, 63, 67), (56, 60, 63), (58, 62, 65), (55, 58, 62)]),
    ("phonk Fm", 130, [(53, 56, 60), (49, 53, 56), (51, 55, 58), (48, 52, 55)]),
    ("hype Em", 150, [(52, 55, 59), (48, 52, 55), (55, 59, 62), (50, 54, 57)]),
    ("chill trap Dm", 136, [(50, 53, 57), (46, 50, 53), (48, 52, 55), (45, 49, 52)]),
    ("menace Gm", 140, [(55, 58, 62), (51, 55, 58), (50, 53, 57), (53, 57, 60)]),
    ("bounce Bbm", 128, [(58, 61, 65), (54, 58, 61), (56, 60, 63), (53, 56, 60)]),
    ("rage F#m", 154, [(54, 57, 61), (50, 54, 57), (52, 56, 59), (49, 52, 56)]),
    ("cinematic Bm", 132, [(59, 62, 66), (55, 59, 62), (57, 61, 64), (54, 57, 61)]),
]


def generated_beat(dur, variant=0):
    """Royalty-free beat made in code; a different style/tempo/key per variant."""
    name, bpm, chords = PROGS[variant % len(PROGS)]
    r = np.random.default_rng(100 + variant)
    n = int(dur * SR) + SR
    out = np.zeros((n, 2))
    step = 60 / bpm / 4
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)  # noqa: E731

    def put(sig, t, gain=1.0):
        i = int(t * SR)
        if i >= n:
            return
        sig = sig[: n - i]
        if sig.ndim == 1:
            sig = np.stack([sig, sig], 1)
        out[i: i + len(sig)] += sig * gain

    tk = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-tk * 35)) / SR) * np.exp(-tk * 9)
    tc = np.arange(int(0.25 * SR)) / SR
    clap = _bandnoise(len(tc), 900, 5000) * np.exp(-tc * 22)
    th = np.arange(int(0.05 * SR)) / SR
    hat = _bandnoise(len(th), 7000, 16000) * np.exp(-th * 90)
    tb = np.arange(int(0.12 * SR)) / SR
    cow = (np.sign(np.sin(2 * np.pi * 560 * tb)) + np.sign(np.sin(2 * np.pi * 845 * tb))) * np.exp(-tb * 25) * 0.15
    kicks = [(0, 10), (0, 7, 10), (0, 3, 10), (0, 6, 11)][variant % 4]
    phonk = "phonk" in name or "menace" in name
    for b in range(int(dur / (16 * step)) + 2):
        t0 = b * 16 * step
        ch = chords[b % 4]
        for s in (kicks if b % 2 == 0 else (0, 7, 10)):
            put(kick, t0 + s * step, 0.9)
        put(clap, t0 + 8 * step, 0.45)
        for s in range(0, 16, 2 if bpm < 145 else 4):
            put(hat, t0 + s * step, 0.18 if s % 4 else 0.24)
        if b % 2 == 1:
            for k in range(8):
                put(hat, t0 + (12 + k * 0.5) * step, 0.14)
        if phonk:
            for s in (0, 3, 6, 10, 12):
                put(cow, t0 + s * step, 0.7 if s else 1.0)
        t8 = np.arange(int(8 * step * SR)) / SR
        b808 = np.tanh(np.sin(2 * np.pi * hz(ch[0] - 24) * t8) * np.exp(-t8 * 1.4) * 1.6) * 0.55
        put(b808, t0, 1.0)
        put(b808 * np.exp(-t8 * 2), t0 + 10 * step, 0.8)
        tp = np.arange(int(16 * step * SR)) / SR
        pad = np.zeros(len(tp))
        for m in ch:
            for det in (-0.08, 0.08):
                for h_ in range(1, 6):
                    pad += np.sin(2 * np.pi * hz(m + det) * h_ * tp + r.uniform(0, 6)) / (h_ ** 1.6)
        pad *= np.minimum(1, tp / 0.3) * np.minimum(1, (tp[-1] - tp) / 0.3) * 0.022
        put(pad, t0, 1.0)
        if b % 2 == 0:
            tl = np.arange(int(2 * step * SR)) / SR
            for s in (0, 3, 6, 8):
                put(np.sin(2 * np.pi * hz(ch[r.integers(0, 3)] + 12) * tl) * np.exp(-tl * 8) * 0.06, t0 + s * step)
    return out[: int(dur * SR)], f"generated: {name} {bpm} bpm"


def _usage_file():
    return DATA_DIR / "music_usage.json"


def library_tracks():
    root = ASSETS_DIR / "music"
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXT) if root.exists() else []


def library_track(dur, mood=None):
    """Least-used licensed track (optionally from assets/music/<mood>/), never the last one used."""
    tracks = library_tracks()
    if mood:
        tracks = [t for t in tracks if mood.lower() in t.parent.name.lower()] or tracks
    if not tracks:
        return None, None
    uf = _usage_file()
    usage = json.loads(uf.read_text(encoding="utf-8")) if uf.exists() else {"counts": {}, "last": None}
    choices = [t for t in tracks if str(t) != usage.get("last")] or tracks
    pick = min(choices, key=lambda t: (usage["counts"].get(str(t), 0), random.random()))
    usage["counts"][str(pick)] = usage["counts"].get(str(pick), 0) + 1
    usage["last"] = str(pick)
    uf.parent.mkdir(parents=True, exist_ok=True)
    uf.write_text(json.dumps(usage, indent=1), encoding="utf-8")
    sig = pcm(pick, SR, channels=2)
    start = int(len(sig) * 0.12) if len(sig) > dur * SR * 1.3 else 0     # skip slow intros
    sig = sig[start:]
    if len(sig) < dur * SR:                                               # loop short tracks
        sig = np.tile(sig, (int(np.ceil(dur * SR / max(len(sig), 1))), 1))
    return sig[: int(dur * SR)].astype(np.float64), f"library: {pick.name}"


def music_for(dur, mode, variant, mood=None):
    if mode == "none":
        return None, "none"
    if mode in ("library", "library_only"):
        sig, name = library_track(dur, mood)
        if sig is not None:
            return sig, name
        if mode == "library_only":       # no licensed track: no music at all (generated beats sound cheap)
            return None, "none (no track in assets/music)"
    return generated_beat(dur, variant)


# ----------------------------------------------------------------------------- processing
def denoise(stereo: np.ndarray, workdir: Path) -> np.ndarray:
    """Gentle FFmpeg noise reduction on the dialogue track (afftdn)."""
    from core.media import write_wav
    a, b = workdir / "_dn_in.wav", workdir / "_dn_out.wav"
    write_wav(a, stereo)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a), "-af", "afftdn=nr=10:nf=-42:tn=1,highpass=f=70",
                    str(b)], check=True)
    out = pcm(b, SR, channels=2).astype(np.float64)
    a.unlink(missing_ok=True)
    b.unlink(missing_ok=True)
    n = len(stereo)
    return out[:n] if len(out) >= n else np.pad(out, ((0, n - len(out)), (0, 0)))


def duck_curve(dialogue: np.ndarray, low=0.22) -> np.ndarray:
    """1.0 in gaps, `low` while someone talks (smoothed)."""
    n = len(dialogue)
    hop = SR // 50
    d_db = 20 * np.log10(np.sqrt((dialogue.mean(1)[: n // hop * hop].reshape(-1, hop) ** 2).mean(1)) + 1e-6)
    duck = 1 - (1 - low) * np.clip((d_db + 40) / 12, 0, 1)
    duck = np.convolve(duck, np.ones(12) / 12, "same")
    duck = np.repeat(duck, hop)
    return np.pad(duck, (0, max(0, n - len(duck))), mode="edge")[:n]


def loudnorm_2pass(src: Path, dst: Path, lufs=-14.0, tp=-1.5, lra=11.0) -> dict:
    """Two-pass EBU R128 normalisation (measure, then correct with linear gain when possible).
    One-pass loudnorm can land 3+ LU too quiet on clips with big jumps (quiet talk → yelling)."""
    import json as _json
    import re as _re
    flt = f"loudnorm=I={lufs}:TP={tp}:LRA={lra}"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(src), "-af", flt + ":print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    m = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, _re.S)
    if not m:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", flt, "-ar", str(SR), str(dst)], check=True)
        return {"mode": "one-pass"}
    j = _json.loads(m.group(0))
    measured = (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
                f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", flt + measured, "-ar", str(SR), str(dst)],
                   check=True)
    return {"mode": "two-pass", "input_i": j["input_i"]}


def soft_limit(x: np.ndarray) -> np.ndarray:
    return np.tanh(x * 0.9) / np.tanh(0.9)
