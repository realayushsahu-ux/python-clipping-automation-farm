"""Animated captions as an .ass file (rendered by FFmpeg/libass).

Style (reference reels): words appear one by one as they are spoken ("typewriter"),
small white caps + ONE big keyword per line (gold; red serif for shock words), soft glow.
Everything visual comes from config/editing_presets.json → caption_styles.
"""

import re
from pathlib import Path

STOP = set("""a an the and or but so to of in on at for with from by as is am are was were be been it its
i im i'm me my we our you your he him his she her they them their this that these those there here
what's it's that's don't do does did just like yeah yo uh um oh okay ok gonna wanna got get go
not no yes if then than too very can will would could should have has had all one up out about
hai ka ki ke ko se ye wo toh bhi aur mein na""".split())
SHOCK = {"what", "no", "bro", "huh", "wtf", "nah", "stop", "wait", "yo", "omg", "why", "kya", "arre", "bhai"}


def ass_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def ass_ts(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


PROFANITY = re.compile(r"\b(f+u+c+k+\w*|s+h+i+t+\w*|bitch\w*|motherf\w*|n+i+g+g+\w*|dick\w*|pussy\w*|"
                       r"a+s+s+h+o+l+e+\w*|chutiya\w*|madarchod\w*|bhenchod\w*|behenchod\w*|bc|mc)\b", re.I)


def censor(word: str) -> str:
    """F**K style: keep first and last letters (and punctuation), stars in between."""
    def repl(m):
        w = m.group(0)
        return w[0] + "*" * max(len(w) - 2, 1) + (w[-1] if len(w) > 2 else "")
    return PROFANITY.sub(repl, word)


def _clean(w):
    return re.sub(r"[^a-z0-9'*?!]", "", w.lower())


def keyword_index(words):
    best, best_s = None, 3.5
    for i, w in enumerate(words):
        c = _clean(w)
        base = c.strip("?!.,")
        if base in STOP and not ("?" in c or "!" in c):
            continue
        s = len(base) + (5 if ("?" in c or "!" in c) else 0) + (4 if base in SHOCK else 0) + (3 if "*" in c else 0)
        s += 8 if sum(ch.isalpha() for ch in w) >= 3 and w.upper() == w else 0
        if s > best_s:
            best, best_s = i, s
    return best


def make_chunks(words_out, max_words=4, max_chars=22):
    """words_out: [{w, t0, t1, pend, seg}] in output time → caption chunks."""
    chunks, cur = [], []
    for w in words_out:
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars
                    or w["t0"] - cur[-1]["t1"] > 0.45 or w.get("seg") != cur[-1].get("seg")):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.get("pend"):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    res = []
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1][0]["t0"] if i + 1 < len(chunks) else 1e9
        res.append({"words": c, "start": c[0]["t0"], "end": min(nxt, c[-1]["t1"] + 0.5)})
    return res


class CaptionWriter:
    def __init__(self, style: dict, W: int, H: int):
        self.s, self.W, self.H = style, W, H
        up = r"\fsp2"
        self.small = (r"{\fn" + style["small_font"] + r"\fs" + str(style["small_size"]) + r"\c"
                      + ass_color(style["small_color"]) + r"\i0" + up + "}")
        self.small_alone = self.small.replace(r"\fs" + str(style["small_size"]), r"\fs" + str(int(style["small_size"] * 1.35)))
        self.big = (r"{\fn" + style["keyword_font"] + r"\fs" + str(style["keyword_size"]) + r"\c"
                    + ass_color(style["keyword_color"]) + (r"\i1" if style.get("keyword_italic") else r"\i0") + r"\fsp0}")
        self.shock = (r"{\fn" + style["shock_font"] + r"\b1\fs" + str(style["shock_size"]) + r"\c"
                      + ass_color(style["shock_color"]) + r"\i0\fsp0}")
        self.lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
                      "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
                      "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                      "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                      "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                      f"Style: Txt,{style['small_font']},{style['small_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,"
                      "&H00000000,-1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1",
                      f"Style: Glow,{style['small_font']},{style['small_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,"
                      f"&H00000000,-1,0,0,0,100,100,0,0,1,{style['glow_size']},0,5,40,40,0,1",
                      "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def _text(self, words, shown, newest, glow):
        up = self.s.get("uppercase", True)
        k = keyword_index([w["w"] for w in words])
        groups = [(0, k), (k, k + 1), (k + 1, len(words))] if k is not None else [(0, len(words))]
        out = []
        for a, b in groups:
            if a >= b:
                continue
            if k is not None and a == k:
                style = self.shock if _clean(words[k]["w"]).strip("?!.,") in SHOCK else self.big
            else:
                style = self.small if k is not None else self.small_alone
            parts = []
            for i in range(a, b):
                txt = words[i]["w"]
                if self.s.get("censor_profanity", True):
                    txt = censor(txt)
                txt = txt.upper() if up else txt
                vis = i < shown
                if glow:
                    tag = r"{\1a&HFF&\3a&H30&\4a&HFF&}" if vis else r"{\alpha&HFF&}"
                else:
                    tag = r"{\alpha&H00&}" if vis else r"{\alpha&HFF&}"
                pop = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}" if i == newest else r"{\fscx100\fscy100}"
                parts.append(tag + pop + txt)
            out.append(style + " ".join(parts))
        return r"\N".join(out)

    def emit(self, s, e, words, shown, newest, y):
        pos = r"{\an5\pos(" + f"{self.W // 2},{int(y)}" + r")}"
        blur = self.s.get("glow_blur", 12)
        self.lines.append(f"Dialogue: 0,{ass_ts(s)},{ass_ts(e)},Glow,,0,0,0,,{pos}" + r"{\blur" + str(blur) + "}"
                          + self._text(words, shown, newest, True))
        self.lines.append(f"Dialogue: 1,{ass_ts(s)},{ass_ts(e)},Txt,,0,0,0,,{pos}" + r"{\blur1.5}"
                          + self._text(words, shown, newest, False))

    def add_hook(self, text, y, seconds, start=0.0):
        hw = [{"w": x} for x in text.split()]
        self.emit(start, start + seconds, hw, len(hw), -1, y)

    def add_chunks(self, chunks, y):
        typewriter = self.s.get("animation", "typewriter_pop").startswith("typewriter")
        for ch in chunks:
            ws = ch["words"]
            if not typewriter:
                self.emit(ch["start"], ch["end"], ws, len(ws), -1, y)
                continue
            for i, w in enumerate(ws):
                s = ch["start"] if i == 0 else w["t0"]
                e = ws[i + 1]["t0"] if i + 1 < len(ws) else ch["end"]
                if e - s >= 0.02:
                    self.emit(s, e, ws, i + 1, i, y)

    def save(self, path: Path):
        Path(path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")
