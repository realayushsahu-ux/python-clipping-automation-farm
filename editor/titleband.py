"""The on-screen title of the trending format: one plain line (or two) with a colour emoji, drawn with
PIL (libass cannot draw colour emoji). Returns a BGRA image to paste on the frame."""

import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = Path("C:/Windows/Fonts")
EMOJI = re.compile("([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+)")


@lru_cache(8)
def _font(name, size):
    for n in (name, "arialbd.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / n), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _runs(text):
    return [(p, bool(EMOJI.fullmatch(p))) for p in EMOJI.split(text) if p]


def _width(text, font, efont):
    return sum((efont if e else font).getlength(p) for p, e in _runs(text))


def _wrap(text, font, efont, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if _width(t, font, efont) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # never leave the emoji alone on its own line (looked like a stray box): it joins the line above
    if len(lines) > 1 and EMOJI.fullmatch(lines[-1].replace(" ", "")):
        last = lines.pop()
        lines[-1] += " " + last
    return lines[:3]


def render(text, W, size=58, color=(255, 255, 255), stroke=0, stroke_color=(0, 0, 0), box=None,
           font="arialbd.ttf", uppercase=False, max_w_ratio=0.86, pad=(26, 14)):
    """BGRA image W wide with the title centred; box=(r,g,b) draws a rounded box behind each line."""
    text = (text or "").replace("Fe0f", "").strip()
    if uppercase:
        text = text.upper()
    f, ef = _font(font, size), _font("seguiemj.ttf", int(size * 0.92))
    lines = _wrap(text, f, ef, W * max_w_ratio)
    lh = int(size * 1.22) + (2 * pad[1] if box else 0)
    img = Image.new("RGBA", (W, lh * len(lines) + 2 * stroke + 6), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        w = _width(ln, f, ef)
        x = (W - w) / 2
        y = i * lh + (pad[1] if box else 0) + stroke
        if box:
            d.rounded_rectangle([x - pad[0], y - pad[1] + 2, x + w + pad[0], y + size * 1.22 + pad[1] - 4],
                                radius=14, fill=tuple(box) + (255,))
        for part, is_emoji in _runs(ln):
            if is_emoji:
                d.text((x, y + size * 0.06), part, font=ef, embedded_color=True)
                x += ef.getlength(part)
            else:
                d.text((x, y), part, font=f, fill=tuple(color) + (255,), stroke_width=stroke,
                       stroke_fill=tuple(stroke_color) + (255,))
                x += f.getlength(part)
    a = np.array(img)
    return a[:, :, [2, 1, 0, 3]].copy()          # RGBA → BGRA


def paste(frame, band, y):
    """Alpha-blend a BGRA band onto a BGR frame with its top at y."""
    if band is None or not band.size:
        return frame
    h = min(band.shape[0], frame.shape[0] - y)
    if h <= 0:
        return frame
    a = band[:h, :, 3:4].astype(np.float32) / 255.0
    roi = frame[y:y + h]
    frame[y:y + h] = (band[:h, :, :3] * a + roi * (1 - a)).astype(np.uint8)
    return frame
