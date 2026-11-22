"""Finds the moments worth clipping.

1. The transcript is cut into ~12-minute parts with [LOUD]/[CHAT] markers.
2. Each part goes to the AI provider (parallel). No key â†’ offline_moments() scores windows
   from loudness, chat, speech density and reaction words.
3. Times are snapped onto real word edges (never cut mid-word), length limits enforced.
4. Overlaps removed, weak ones (below min_score) dropped, 3-9 kept (settings).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ai.base import ask_json, providers
from ai.prompts import ENGLISH_NOTE, HINGLISH_NOTE, MOMENTS_SCHEMA, MOMENTS_SYSTEM, MOMENTS_USER
from analyzer.signals import transcript_lines
from config.settings import clip_max_for
from core.logger import get_logger

log = get_logger("moments")

REACTION_WORDS = re.compile(r"\b(what|bro|no way|oh my god|omg|wait|yo|nah|why|run|help|huh|wtf|"
                            r"let'?s go|stop|bhai|kya|arre|abe|oh no|holy)\b", re.I)


def _snap(m, words, min_d, max_d):
    start, end = float(m["start"]), float(m["end"])
    near = [w for w in words if w["s"] >= start - 1.5]
    if not near:
        return None
    first = min(near, key=lambda w: abs(w["s"] - start))
    inside = [w for w in words if first["s"] <= w["s"] and w["e"] <= end + 1.5]
    if not inside:
        return None
    last = min(inside, key=lambda w: abs(w["e"] - end))
    if last["e"] - first["s"] > max_d:
        fit = [w for w in inside if w["e"] - first["s"] <= max_d]
        if not fit:
            return None
        ends = [w for w in fit if w["w"].rstrip().endswith((".", "!", "?"))]
        last = ends[-1] if ends else fit[-1]
    if last["e"] - first["s"] < min_d:
        after = [w for w in words if w["e"] > last["e"] and w["e"] - first["s"] <= max_d]
        reach = [w for w in after if w["e"] - first["s"] >= min_d]
        last = reach[0] if reach else (after[-1] if after else last)
        if last["e"] - first["s"] < min_d:
            return None
    m = dict(m)
    m["start"] = round(max(0.0, first["s"] - 0.6), 2)
    m["end"] = round(min(last["e"] + 0.6, m["start"] + max_d), 2)
    return m


def _dedupe(cands):
    kept = []
    for m in sorted(cands, key=lambda m: -float(m.get("score", 0))):
        span = m["end"] - m["start"]
        if all(min(m["end"], k["end"]) - max(m["start"], k["start"]) <= 0.3 * min(span, k["end"] - k["start"])
               for k in kept):
            kept.append(m)
    return kept


def offline_moments(words, loud_rel, chat, lo, hi, min_d, max_d, want):
    """Rule-based picker: windows that start on a phrase, ranked by hype signals."""
    lines = transcript_lines(words, loud_rel, chat or [], line_seconds=4)
    if not lines:
        return []
    loud = np.asarray(loud_rel, dtype=np.float32)
    chat_arr = np.asarray(chat or [], dtype=np.float32)
    chat_base = float(np.median(chat_arr[chat_arr > 0])) if chat_arr.size and (chat_arr > 0).any() else 0.0
    cands = []
    for t0, _, _ in lines:
        if t0 < lo or t0 >= hi:
            continue
        for length in (min_d + 5, (min_d + max_d) / 2, max_d - 2):
            t1 = min(t0 + length, hi)
            if t1 - t0 < min_d:
                continue
            seg = loud[int(t0): int(t1)] if loud.size else np.zeros(1)
            hype = float(np.clip(seg, 0, None).mean()) if seg.size else 0.0
            peak = float(seg.max()) if seg.size else 0.0
            txt = " ".join(w["w"] for w in words if t0 <= w["s"] < t1)
            n_words = len(txt.split())
            density = min(n_words / max(t1 - t0, 1) / 2.5, 1.0)
            react = len(REACTION_WORDS.findall(txt)) + txt.count("!") + txt.count("?")
            chat_boost = 0.0
            if chat_base:
                c = chat_arr[int(t0 // 5): int(t1 // 5) + 2]
                chat_boost = float(c.max() / chat_base) if c.size else 0.0
            # late peak = payoff inside the clip
            late = float(seg[len(seg) // 2:].max()) if seg.size > 2 else 0.0
            raw = 2.2 * hype + 0.6 * peak + 0.5 * late + 8 * density + 1.4 * min(react, 8) + 3 * min(chat_boost, 5)
            cands.append({"start": t0, "end": t1, "raw": raw, "text": txt})
    if not cands:
        return []
    raws = np.array([c["raw"] for c in cands])
    lo_r, hi_r = np.percentile(raws, 50), raws.max() + 1e-6
    out = []
    for c in cands:
        score = int(np.clip(55 + 40 * (c["raw"] - lo_r) / (hi_r - lo_r), 1, 95))
        words_txt = c["text"].split()
        hook = " ".join(words_txt[:6]).strip(" ,.") if words_txt else "Wait for it"
        out.append({"start": c["start"], "end": c["end"], "kind": "reaction", "title": hook[:40],
                    "hook_text": hook, "score": score, "reason": "offline score: loudness, chat and reaction words"})
    return _dedupe(out)[: max(want * 3, want)]


def find_moments(transcript, loud_rel, chat, info, settings, workdir, clips_wanted=None):
    cache = workdir / "moments.json"
    if cache.exists():
        found = json.loads(cache.read_text(encoding="utf-8"))
        log.info(f"reusing {len(found)} picked moment(s)")
        return found
    words = transcript["words"]
    min_d, max_d = settings["clip_min_seconds"], settings["clip_max_seconds"]
    want_total = int(clips_wanted or settings["clips_per_video"])
    lang = transcript.get("language") or "en"
    note = HINGLISH_NOTE if (settings["caption_language"] == "hinglish" or
                             (settings["caption_language"] == "auto" and lang.startswith("hi"))) else ENGLISH_NOTE
    lines = transcript_lines(words, loud_rel, chat or [])
    first, total = words[0]["s"], words[-1]["e"]
    window = settings["ai_chunk_minutes"] * 60
    parts = []
    for i in range(int(np.ceil((total - first) / window)) or 1):
        lo, hi = first + i * window, min(first + (i + 1) * window, total)
        body = [f"[{a:.1f}] {text}" for a, _, text in lines if lo <= a < hi]
        if sum(len(b.split()) for b in body) >= 30:
            parts.append((i + 1, lo, hi, "\n".join(body)))
    if not parts:
        raise RuntimeError("not enough talking in this video to clip")
    want = max(2, min(5, round(want_total / len(parts)) + 2))
    names = [p.name for p in providers(settings)]
    log.info(f"picking moments in {len(parts)} part(s) with: {' â†’ '.join(names)}")

    def work(part):
        idx, lo, hi, body = part
        user = MOMENTS_USER.format(channel=info.get("channel"), title=info.get("title"), part=idx, lo=lo, hi=hi,
                                   want=want, min_d=min_d, max_d=max_d, transcript=body, language_note=note,
                                   long_max=int(settings.get("long_clip_max_seconds", max_d)),
                                   long_kinds="/".join(settings.get("long_clip_kinds") or ["funny"]))
        got = ask_json(settings, MOMENTS_SYSTEM, user, MOMENTS_SCHEMA, max_tokens=12000,
                       effort=settings.get("ai_effort_moments", "low"),
                       offline_fn=lambda: {"clips": offline_moments(words, loud_rel, chat, lo, hi, min_d, max_d, want),
                                           "_offline": True})
        clips = (got or {}).get("clips", [])
        ok = []
        for m in clips:
            try:
                if lo - 5 <= float(m["start"]) < hi + 30:
                    s = _snap(m, words, min_d, clip_max_for(settings, m.get("kind")))
                    if s:
                        s["picked_by"] = "offline" if (got or {}).get("_offline") else names[0]
                        ok.append(s)
            except (KeyError, TypeError, ValueError):
                continue
        return ok

    cands = []
    with ThreadPoolExecutor(max_workers=max(1, int(settings["ai_parallel"]))) as pool:
        for got in pool.map(work, parts):
            cands += got
    if not cands:
        raise RuntimeError("no strong moments found in this video")
    ranked = _dedupe(cands)
    strong = [m for m in ranked if float(m.get("score", 0)) >= settings["min_score"]]
    keep_min = int(settings["min_clips_per_video"])
    chosen = strong[:want_total] if len(strong) >= keep_min else ranked[:min(keep_min, want_total)]
    chosen.sort(key=lambda m: -float(m.get("score", 0)))
    log.info(f"picked {len(chosen)} of {len(cands)} candidate moments "
             f"({len(strong)} scored â‰¥ {settings['min_score']})")
    from core.safe_io import write_json
    write_json(cache, chosen, indent=1, ensure_ascii=False)
    (workdir / "candidates.json").write_text(json.dumps(cands, indent=1, ensure_ascii=False), encoding="utf-8")
    return chosen
