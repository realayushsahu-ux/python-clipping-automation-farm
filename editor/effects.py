"""Visual side of the edit: face/scene analysis, layout (split / full), smart 9:16 crop,
zoom/shake transform, color grade, flash + glitch transitions."""

import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from core.paths import YUNET_MODEL


# ----------------------------------------------------------------------------- analysis
def analyse(src):
    """Per source frame: scene-cut flag and faces [(cx, cy, w, h, score)] in 0-1 units."""
    det = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (640, 360), 0.6, 0.3, 5000)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-vf", "scale=640:360",
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    cuts, faces, prev, i = [], {}, None, 0
    size = 640 * 360 * 3
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(360, 640, 3)
        tiny = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (32, 18)).astype(np.float32)
        cuts.append(prev is not None and np.abs(tiny - prev).mean() > 26)
        prev = tiny
        if i % 2 == 0:
            _, f = det.detect(fr)
            if f is not None and len(f):
                faces[i] = [((x + w / 2) / 640, (y + h / 2) / 360, w / 640, h / 360, s)
                            for x, y, w, h, s in f[:, [0, 1, 2, 3, 14]] if s > 0.6]
        i += 1
    p.wait()
    return np.array(cuts + [False]), faces, i


def smooth_track(n, pts, sigma=9.0, bounds=None):
    xs, ys = np.full(n, np.nan), np.full(n, np.nan)
    bounds = bounds or [0, n]
    for s, e in zip(bounds, bounds[1:]):
        ks = sorted(k for k in pts if s <= k < e)
        if not ks:
            continue
        idx = np.arange(s, e)
        seg = [np.interp(idx, ks, [pts[k][j] for k in ks]) for j in range(2)]
        if e - s > 5:
            r = int(sigma * 2.7)
            ker = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
            ker /= ker.sum()
            seg = [np.convolve(np.pad(v, r, mode="edge"), ker, "valid") for v in seg]
        xs[s:e], ys[s:e] = seg
    return xs, ys


def plan_layout(want, cuts, faces, n):
    """'split' when a facecam sits in one place for much of the clip, else 'full'."""
    big = {k: max(v, key=lambda f: f[2] * f[3] * f[4]) for k, v in faces.items() if v}
    cam = None
    if big:
        arr = np.array([(f[0], f[1], f[3]) for f in big.values()])
        med = np.median(arr, axis=0)
        near = [k for k, f in big.items() if abs(f[0] - med[0]) < 0.08 and abs(f[1] - med[1]) < 0.1]
        if len(near) >= 0.35 * (n / 2) and med[2] < 0.5:
            cam = med
    layout = want if want in ("split", "full") else ("split" if cam is not None else "full")
    if layout == "split" and cam is None:
        layout = "full"
    bounds = [0] + [j for j in range(n) if cuts[j]] + [n]
    if layout == "split":
        pts = {}
        for k, v in faces.items():
            c = [f for f in v if abs(f[0] - cam[0]) < 0.12 and abs(f[1] - cam[1]) < 0.15]
            if c:
                f = max(c, key=lambda f: f[3])
                pts[k] = (f[0], f[1], f[3])
        fx, fy = smooth_track(n, pts, sigma=12.0)
        fx = np.clip(np.where(np.isnan(fx), cam[0], fx), cam[0] - 0.08, cam[0] + 0.08)
        fy = np.clip(np.where(np.isnan(fy), cam[1], fy), cam[1] - 0.1, cam[1] + 0.1)
        # crop size locked to the typical face size: a turned/covered face must not zoom out
        size = float(np.median([p[2] for p in pts.values()])) if pts else float(cam[2])
        return {"layout": "split", "cam": cam, "fx": fx, "fy": fy, "fs": np.full(n, size)}
    pts = {}
    for k, v in faces.items():
        if not v:
            continue
        xs = [f[0] for f in v]
        f = max(v, key=lambda f: f[2] * f[3] * f[4])
        cx = (min(xs) + max(xs)) / 2 if max(xs) - min(xs) < 0.26 else f[0]
        pts[k] = (cx, f[1])
    fx, fy = smooth_track(n, pts, sigma=9.0, bounds=bounds)
    return {"layout": "full", "fx": np.where(np.isnan(fx), 0.5, fx), "fy": np.where(np.isnan(fy), 0.45, fy)}


# ----------------------------------------------------------------------------- framing
def _affine(cx, cy, scale, ocx, ocy, rot=0.0):
    r = math.radians(rot)
    A = scale * np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
    t = np.array([ocx, ocy]) - A @ np.array([cx, cy])
    return np.hstack([A, t[:, None]]).astype(np.float32)


def render_frame(frame, lay, sf, z, dx, dy, rot, W, H, top_ratio=0.55):
    """Source BGR frame (any 16:9 size) → W x H vertical frame."""
    sh, sw = frame.shape[:2]
    if lay["layout"] == "full":
        ch = sh / z
        cw = ch * W / H
        cx = np.clip(lay["fx"][sf] * sw, cw / 2, sw - cw / 2)
        cy = np.clip(sh / 2 + (lay["fy"][sf] * sh - sh / 2) * (1 - 1 / z), ch / 2, sh - ch / 2)
        M = _affine(cx, cy, H / ch, W / 2 + dx, H / 2 + dy, rot)
        return cv2.warpAffine(frame, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    top_h = int(H * top_ratio)
    out = np.empty((H, W, 3), np.uint8)
    cam = lay["cam"]
    th = sh * 0.87 / (1 + (z - 1) * 0.35)
    tw = th * W / top_h
    cam_l, cam_r = (cam[0] - cam[2] * 1.2) * sw, (cam[0] + cam[2] * 1.2) * sw
    if cam[0] < 0.5:
        tx = max(cam_r - 0.1 * tw, sw / 2 - tw / 2) + tw / 2
    else:
        tx = min(cam_l + 0.1 * tw, sw / 2 + tw / 2) - tw / 2
    tx = float(np.clip(tx, tw / 2, sw - tw / 2))
    M = _affine(tx, sh / 2, top_h / th, W / 2 + dx * 0.5, top_h / 2 + dy * 0.5, rot * 0.5)
    out[:top_h] = cv2.warpAffine(frame, M, (W, top_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    bh = H - top_h
    fh = lay["fs"][sf] * sh
    ch = np.clip(fh * 3.3, sh * 0.28, sh * 0.52) / z
    cw = ch * W / bh
    cx = np.clip(lay["fx"][sf] * sw, cw / 2, sw - cw / 2)
    cy = np.clip(lay["fy"][sf] * sh + 0.1 * ch, ch / 2, sh - ch / 2)
    M = _affine(cx, cy, bh / ch, W / 2 + dx, bh / 2 + dy, rot)
    out[top_h:] = cv2.warpAffine(frame, M, (W, bh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    return out


# ----------------------------------------------------------------------------- look
class Grade:
    def __init__(self, cfg, W, H):
        lut = np.clip(255 * (0.5 + (np.arange(256) / 255 - 0.5) * cfg["contrast"]), 0, 255)
        self.lut = (255 * (lut / 255) ** cfg["gamma"]).astype(np.uint8)
        self.sat = cfg["saturation"]
        self.sharp = cfg["sharpen"]
        yy, xx = np.mgrid[0:H, 0:W]
        vig = np.clip(1 - cfg["vignette"] * (((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2), 0, 1)
        self.vig3 = cv2.merge([vig.astype(np.float32)] * 3)

    def __call__(self, img):
        img = cv2.LUT(img, self.lut)
        gray = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        img = cv2.addWeighted(img, self.sat, gray, 1 - self.sat, 0)
        if self.sharp > 0:
            blur = cv2.GaussianBlur(img, (0, 0), 2.2)
            img = cv2.addWeighted(img, 1 + self.sharp, blur, -self.sharp, 0)
        return cv2.multiply(img, self.vig3, dtype=cv2.CV_8U)


def transition(img, d, flash=True, glitch=True):
    """d = seconds since the drop (cold open → clip)."""
    if glitch and 0 <= d < 0.14:
        s = int(18 * (1 - d / 0.14)) + 4
        b, g, r = cv2.split(img)
        img = cv2.merge([np.roll(b, s, 1), g, np.roll(r, -s, 1)])
    if flash and 0 <= d < 0.2:
        a = 0.75 * (1 - d / 0.2)
        img = cv2.addWeighted(img, 1 - a, np.full_like(img, 255), a, 0)
    return img


# ----------------------------------------------------------------------------- library overlays
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def pick_overlay(folders, rand):
    """Random overlay clip from assets/overlays/<folder>/ (light leaks, flares, film burns).
    Returns a Path or None when the folders are empty."""
    from core.paths import ASSETS_DIR
    files = []
    for f in folders or []:
        d = ASSETS_DIR / "overlays" / f
        if d.exists():
            files += sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXT)
    return rand.choice(files) if files else None


class Overlay:
    """Frames of an overlay clip (black background), scaled to cover W×H, screen-blended on top.
    `max_seconds` keeps memory small (≈6 MB per 1080×1920 frame)."""

    def __init__(self, path, W, H, fps, max_seconds=1.2, strength=1.0):
        vf = (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=bgr24")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-t", f"{max_seconds:.2f}",
                              "-an", "-vf", vf, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        n = len(raw) // (W * H * 3)
        self.frames = np.frombuffer(raw[: n * W * H * 3], np.uint8).reshape(n, H, W, 3)
        self.fps, self.strength, self.name = fps, strength, Path(path).name

    @property
    def seconds(self):
        return len(self.frames) / self.fps

    def apply(self, img, d):
        """d = seconds since the overlay started. Screen blend: 255 - (255-a)(255-b)/255."""
        k = int(d * self.fps)
        if d < 0 or k >= len(self.frames):
            return img
        ov = self.frames[k]
        if self.strength < 1:
            ov = cv2.convertScaleAbs(ov, alpha=self.strength)
        return 255 - cv2.multiply(255 - img, 255 - ov, scale=1 / 255)


def lut_filter(grade_cfg, workdir):
    """FFmpeg filter text for the LUT look (assets/luts/<file>.cube) mixed at lut_strength,
    or '' when no LUT is set. The .cube is copied next to the job so the path needs no escaping."""
    import shutil
    from core.paths import ASSETS_DIR
    name = (grade_cfg or {}).get("lut")
    if not name:
        return ""
    src = ASSETS_DIR / "luts" / name
    if not src.exists():
        return ""
    shutil.copyfile(src, Path(workdir) / "look.cube")
    s = float(grade_cfg.get("lut_strength", 0.5))
    if s >= 0.99:
        return "lut3d=look.cube,"
    return f"split[o][g];[g]lut3d=look.cube[l];[o][l]blend=all_mode=normal:all_opacity={s:.2f},"
