"""Packaging for one finished clip: hook, platform titles, caption, hashtags, description,
pinned comment. Uses the AI provider chain; offline_package() is the no-key fallback."""

import re

from ai import quality
from ai.base import ask_json
from ai.prompts import (ENGLISH_NOTE, HINGLISH_NOTE, PACKAGE_SCHEMA, PACKAGE_SYSTEM, PACKAGE_USER,
                        ROMANIZE_SCHEMA, ROMANIZE_SYSTEM, ROMANIZE_USER, TITLE_FIX_SCHEMA, TITLE_FIX_SYSTEM,
                        TITLE_FIX_USER)
from core.logger import get_logger

log = get_logger("package")

STOP = set("the a an and or but so to of in on at for with is it i you me my we he she they this that".split())


def _tag(s: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", s.lower())


def wants_hinglish(settings, lang: str) -> bool:
    mode = settings.get("caption_language", "auto")
    return mode == "hinglish" or (mode == "auto" and (lang or "").startswith("hi"))


OFFLINE_TITLES = {
    "funny": "{c} did NOT see this coming 😭",
    "reaction": "{c}'s reaction to this 💀",
    "fail": "{c} really thought this would work 💀",
    "clutch": "{c} pulled this off 😳",
}


def offline_package(moment, info, text):
    """No AI available: clean template text, and NO on-screen hook (a bad hook is worse than none)."""
    channel = info.get("channel") or "streamer"
    words = [w for w in re.findall(r"[A-Za-z']+", text) if w.lower() not in STOP and len(w) > 3]
    topic = " ".join(words[:5])
    base = ["#gaming", "#shorts", "#reels", "#gamer", "#streamer", "#funnymoments", "#gamingclips"]
    tags = [_tag(channel), *base]
    title = (info.get("title") or "").split("|")[0][:40]
    headline = OFFLINE_TITLES.get(moment.get("kind"), OFFLINE_TITLES["reaction"]).format(c=channel)
    return {
        "hook": "",
        "youtube_title": headline[:70],
        "instagram_caption": f"{headline}\nWatch till the end 👀",
        "youtube_description": f"{channel}: {topic}. Clip from \"{title}\".",
        "hashtags": list(dict.fromkeys(t for t in tags if len(t) > 2))[:10],
        "pin_comment": "What would you have done? 👇",
        "best_time_note": "evening slot (default)",
        "_offline": True,
    }


def ig_caption(cap: str, title: str) -> str:
    """Instagram shows only the first line before "more": it must stop the scroll. A weak first line is
    replaced by the (checked) title."""
    lines = [x for x in (cap or "").splitlines() if x.strip()]
    if not lines or not quality.opener(lines[0])[0] or len(lines[0]) > 90:
        lines = [title.strip()] + lines[1:] if lines and len(lines[0]) <= 90 and not quality.opener(lines[0])[0] \
            else [title.strip()] + lines
    return "\n".join(lines[:4])


def is_topic(settings, info, text="") -> bool:
    return quality.on_topic(settings, info.get("title"), (info.get("description") or "")[:800],
                            " ".join(info.get("tags") or [])[:400], text[:2000])


def topic_note(settings, info, text) -> str:
    name = settings.get("topic_name", "")
    if not name:
        return ""
    if is_topic(settings, info, text):
        return (f"This clip is from {name}: the YouTube title MUST name \"{name}\" within its first 40 characters (what "
                f"people search), opened by a scroll-stopping first word, then the specific moment/character/creator. "
                f"First hashtag: {settings.get('topic_hashtag', '')}.")
    return (f"This clip is NOT from {name}: never write {name} or its hashtag anywhere (misleading tags "
            "can get a video removed). Put the REAL game in `game`; the YouTube title MUST name that game within "
            "its first 40 characters (what people search), opened by a scroll-stopping first word; the first "
            "hashtag is that game (e.g. #minecraft, #valorant, #bgmi). Same quality rules as always.")


def voice_lines(pkg, info, text, settings, used_voices=()) -> dict:
    """Narrator lines for a clip that was packaged before voice commentary existed (one small AI call)."""
    from ai.prompts import VOICE_SCHEMA, VOICE_SYSTEM, VOICE_USER
    from editor import voiceover
    used = ("Lines already used on other clips, write different ones:\n" + "\n".join(f"- {u}" for u in used_voices)
            if used_voices else "")
    out = ask_json(settings, VOICE_SYSTEM, VOICE_USER.format(
        title=info.get("title"), creator=pkg.get("creator") or info.get("channel"), hook=pkg.get("hook", ""),
        yt_title=pkg.get("youtube_title", ""), text=text[:2500], used=used), VOICE_SCHEMA, max_tokens=3000,
        offline_fn=lambda: {}, effort="low") or {}
    return voiceover.pick_lines(out, f"{info.get('title', '')} {pkg.get('creator', '')} {pkg.get('youtube_title', '')} "
                                     f"{pkg.get('hook', '')} {text}", pkg.get("hook", ""),
                                used_voices)


def package(moment, info, text, lang, settings, used=None) -> dict:
    """used = {"titles": [...], "hooks": [...]} already given to other clips of the same video."""
    used = used or {}
    if used.get("titles") or used.get("hooks"):
        text_note = ("\nALREADY USED for other clips of this video - write something clearly DIFFERENT:\n"
                     + "\n".join(f"- title: {t}" for t in used.get("titles", []))
                     + "\n" + "\n".join(f"- hook: {h}" for h in used.get("hooks", [])))
    else:
        text_note = ""
    return _package(moment, info, text, lang, settings, used, text_note)


def _package(moment, info, text, lang, settings, used, text_note) -> dict:
    """AI packaging + the YouTube-skills quality gate (ai/quality.py). Output keys stay the same as
    before (hook, youtube_title, ...) plus hook_options / title_options / quality for the Sheet."""
    note = HINGLISH_NOTE if wants_hinglish(settings, lang) else ENGLISH_NOTE
    language = "Hinglish" if note is HINGLISH_NOTE else "English"
    user = PACKAGE_USER.format(channel=info.get("channel"), title=info.get("title"), kind=moment.get("kind"),
                               text=text[:4000], reason=moment.get("reason", ""), hook=moment.get("hook_text", ""),
                               language=language, language_note=note + "\n" + topic_note(settings, info, text),
                               formulas=quality.hook_formulas_text())
    user += text_note
    out = ask_json(settings, PACKAGE_SYSTEM, user, PACKAGE_SCHEMA, max_tokens=6000,
                   offline_fn=lambda: offline_package(moment, info, text),
                   effort=settings.get("ai_effort_packaging", "medium"))
    if not out or out.get("_offline"):
        return out
    return finish_package(out, moment, info, text, language, settings, used)


def finish_package(out, moment, info, text, language, settings, used=None) -> dict:
    """Pick the best hook and title, fix weak titles once, strip fake claims."""
    creator = (out.get("creator") or info.get("channel") or "").strip()
    spoken_first = " ".join(text.split()[:8])

    hooks = list(out.get("hooks") or [])
    if moment.get("hook_text"):
        hooks.append(moment["hook_text"])  # the moment picker's idea competes too
    used = used or {}
    ranked = quality.rank_hooks(hooks, spoken_first, used.get("hooks"))
    min_fit = settings.get("hook_min_fit", 60)
    hook = ranked[0]["hook"] if ranked and ranked[0]["fit"] >= min_fit else ""

    name = settings.get("topic_name", "")
    game = re.sub(r"\s+", " ", str(out.get("game") or "")).strip()
    if game.lower() in ("none", "unknown", "n/a", "-"):
        game = ""
    topic = is_topic(settings, info, text) or (bool(game) and quality.on_topic(settings, game))
    if topic:
        keyword, tag = name, settings.get("topic_hashtag") or ""
    else:                                   # any other game: the same rules, built around THAT game
        keyword = game if 2 <= len(game) <= 30 else ""
        tag = ("#" + re.sub(r"[^a-z0-9]", "", game.lower())) if keyword else ""
    wrong = () if topic or not name else tuple(w.lower() for w in settings.get("topic_words") or [])
    titles = quality.best_title(out.get("youtube_titles") or [], hook, creator, used.get("titles"), keyword, wrong)
    need = settings.get("title_min_score", 75)
    if (not titles or titles[0]["score"] < need) and settings.get("title_fix_rounds", 1) > 0:
        issues = "; ".join(m for _, m in titles[0]["issues"]) if titles else "no title"
        fix = ask_json(settings, TITLE_FIX_SYSTEM,
                       TITLE_FIX_USER.format(title=titles[0]["title"] if titles else "", hook=hook, issues=issues,
                                             text=text[:1200], language=language),
                       TITLE_FIX_SCHEMA, max_tokens=1500, offline_fn=lambda: {"titles": []})
        titles = quality.best_title((fix or {}).get("titles", []) + [t["title"] for t in titles], hook, creator,
                                    used.get("titles"), keyword, wrong)
    title = titles[0]["title"] if titles else OFFLINE_TITLES["reaction"].format(c=creator or "streamer")
    hashtags = quality.clean_hashtags(out.get("hashtags"), settings.get("max_hashtags", 5),
                                      first=tag or None,
                                      drop=() if topic else {"#" + w.replace(" ", "") for w in wrong})
    from editor import voiceover
    lines = voiceover.pick_lines(out, f"{info.get('title', '')} {creator} {title} {hook} {text}", hook,
                                 (used or {}).get("voices", ()))
    yt_title = title
    if tag and len(tag) > 2 and settings.get("youtube_title_hashtag", True) and tag not in title.lower() \
            and len(title) + len(tag) + 1 <= 70:
        yt_title = f"{title} {tag}"            # the game tag in the title: shown + searchable on Shorts

    res = {
        "creator": creator,
        "hook": hook,
        "youtube_title": yt_title[:100],
        "instagram_caption": ig_caption(quality.clean_text(out.get("instagram_caption", "")), title),
        "youtube_description": quality.clean_text(out.get("youtube_description", "")),
        "hashtags": hashtags,
        "on_topic": topic,
        "game": name if topic else game,
        **lines,
        "pin_comment": out.get("pin_comment", ""),
        "banner": quality.clean_text(out.get("banner", ""))[:80],
        "best_time_note": out.get("best_time_note", ""),
        "search_queries": out.get("search_queries", [])[:3],
        "loop_line": out.get("loop_line", ""),
        "hook_formula": out.get("hook_formula", ""),
        "hook_options": ranked[:5],
        "title_options": titles[:3],
        "quality": {"hook_score": ranked[0]["score"] if ranked else 0,
                    "hook_fit": ranked[0]["fit"] if ranked else 0,
                    "title_score": titles[0]["score"] if titles else 0},
    }
    log.info(f"package: hook '{hook}' ({res['quality']['hook_score']}), title '{title}' "
             f"({res['quality']['title_score']})")
    return res


# ------------------------------------------------------------------ Devanagari → Roman
_V = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri"}
_M = {"ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri",
      "ं": "n", "ँ": "n", "ः": "h", "्": ""}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
      "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
      "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
      "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "rh", "फ़": "f"}


def translit(word: str) -> str:
    """Basic Devanagari → Roman (offline fallback; the AI romanizer is better)."""
    out, i = [], 0
    while i < len(word):
        ch = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if ch in _C:
            out.append(_C[ch])
            if nxt in _M:
                out.append(_M[nxt])
                i += 1
            elif nxt and (nxt in _C) and i + 2 < len(word):
                out.append("a")
        elif ch in _V:
            out.append(_V[ch])
        elif ch in _M:
            out.append(_M[ch])
        elif ch == "़":
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def romanize_lines(lines, settings):
    """Devanagari caption lines → Roman Hinglish, same count/order."""
    if not any(re.search(r"[ऀ-ॿ]", ln) for ln in lines):
        return lines

    def offline():
        return {"lines": [" ".join(translit(w) for w in ln.split()) for ln in lines]}

    out = ask_json(settings, ROMANIZE_SYSTEM, ROMANIZE_USER.format(lines="\n".join(lines)), ROMANIZE_SCHEMA,
                   max_tokens=3000, offline_fn=offline, voice=False)
    got = (out or {}).get("lines", [])
    return got if len(got) == len(lines) else offline()["lines"]
