"""Quality gate for AI packaging, using the YouTube skill tools (growth/yt_skills, MIT):

- hooks:  the AI writes 5 → ranked by on-screen fit + hookscore.py (used as a RANKER only: it is
          tuned for talking-head hooks, so gaming hooks score low by design)
- titles: the AI writes 3 → linted with title.py (length, ALL-CAPS, vague words, title repeating the
          on-screen hook). If the best one still has real problems, the AI gets one rewrite.
- claims: anything saying "viral", "million views", "#1 trending"... is dropped (never invent numbers).
Runs fully offline (plain Python), no Claude.
"""

import importlib.util
import re
from functools import lru_cache

from core.logger import get_logger
from core.paths import ROOT

log = get_logger("quality")
SKILLS = ROOT / "growth" / "yt_skills"

CLAIMS = re.compile(r"\b(viral|went viral|million views|\d+(\.\d+)?\s?[mk]\+?\s+views|#?1 trending|"
                    r"trending #?1|most viewed|record[- ]breaking)\b", re.I)
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")


@lru_cache(maxsize=None)
def _tool(rel: str):
    path = SKILLS / rel
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hook_formulas_text(limit=21) -> str:
    """'The Statistic (a number the viewer did not know...)', ... for the prompt."""
    try:
        fs = _tool("yt-script/hookscore.py").FORMULAS[:limit]
        return "; ".join(f"{f['name']} ({f['shape'].rstrip('.').lower()})" for f in fs)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"hooks.json not readable: {exc}")
        return "The Question; The Reveal; The Impossible; The Warning; The Before-After"


def _words(t):
    return re.findall(r"[a-z0-9']+", t.lower())


def has_claim(text: str) -> bool:
    return bool(CLAIMS.search(text or ""))


# ------------------------------------------------------------------------------------- hooks
# a hook that ends on one of these is cut off ("He did NOT expect", "Wait for the")
DANGLING = set("""expect expected see saw know knew get got do did make made try tried want wanted need needed
believe believed think thought find found take took give gave tell told say said hear heard
the a an to of and or but with for from his her their my your our its is was are were be been
than that which who into onto at in on by about""".split())
def hook_fit(hook: str, spoken_first: str = "") -> tuple[int, list]:
    """0-100: how well a line works as the 2.5 s on-screen hook in our format."""
    issues, s = [], 100
    w = _words(hook)
    n = len(w)
    if n < 3 or n > 7:
        s -= 15 * (3 - n if n < 3 else n - 7)
        issues.append(f"{n} words (3-7 fit the screen)")
    if len(hook) > 40:
        s -= 20
        issues.append(f"{len(hook)} chars (over 2 lines)")
    if EMOJI.search(hook) or "#" in hook:
        s -= 25
        issues.append("emoji/hashtag on screen")
    caps = [x for x in hook.split() if sum(c.isalpha() for c in x) >= 3 and x.isupper()]
    if not caps:
        s -= 8
        issues.append("no CAPITALISED key word")
    elif len(caps) > 2:
        s -= 10 * (len(caps) - 2)
        issues.append(f"{len(caps)} ALL-CAPS words")
    filler = {"the", "a", "an", "i", "you", "is", "it", "to", "this", "that", "bro", "bhai", "and", "of", "he", "she"}
    sp = set(_words(spoken_first)) - filler
    hw = set(w) - filler
    if sp and hw:
        overlap = len(hw & sp) / len(hw)
        if overlap > 0.5:
            s -= 30
            issues.append("repeats the spoken first line (captions already show it)")
    # language sanity (cheap checks for the mistakes small models make)
    if re.search(r"\b(did|does|do|is|was|are|were|can|could|would|will)\s+not\s+(this|that|it|him|her|them)\b",
                 hook, re.I):
        s -= 45
        issues.append("broken grammar (verb missing after NOT)")
    if re.search(r"[a-z][A-Z]", re.sub(r"\b(iShow\w*|YouTube|TikTok|McDonald\w*)\b", "", hook)):
        s -= 35
        issues.append("glued words")
    words_alpha = [x for x in hook.split() if x[:1].isalpha() and len(x) > 2]
    title_cased = [x for x in words_alpha if x[0].isupper() and not x.isupper()]
    if len(words_alpha) >= 4 and len(title_cased) >= len(words_alpha) - 1:
        s -= 15
        issues.append("Title Case On Every Word")
    last = (w[-1] if w else "")
    if last in DANGLING:
        s -= 40
        issues.append(f"unfinished (ends on '{last}')")
    if re.search(r"\b(\w+)\s+\1\b", hook, re.I):
        s -= 30
        issues.append("repeated word")
    strong, why = opener(hook)
    if not strong:
        s -= 30
        issues.append(why)
    if has_claim(hook):
        s = 0
        issues.append("fake claim (viral/views)")
    return max(0, s), issues


def similar(a: str, b: str) -> float:
    """0-1 word overlap (Jaccard) after dropping small words: catches near-duplicate titles/hooks."""
    small = {"the", "a", "an", "in", "on", "of", "to", "s", "and", "his", "her", "this", "that", "5", "v"}
    x = set(_words(a.replace("'", " "))) - small
    y = set(_words(b.replace("'", " "))) - small
    return len(x & y) / len(x | y) if x and y else 0.0


def rank_hooks(hooks, spoken_first="", used=None):
    """[{hook, score, fit, hookscore, issues}] best first. `used` = hooks of other clips (penalised)."""
    out = []
    try:
        hs = _tool("yt-script/hookscore.py")
    except Exception as exc:  # noqa: BLE001
        hs = None
        log.warning(f"hookscore unavailable: {exc}")
    seen = set()
    for i, h in enumerate(hooks or []):
        h = re.sub(r"\s+", " ", (h or "").strip().strip('"'))
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        fit, issues = hook_fit(h, spoken_first)
        if used and max(similar(h, u) for u in used) >= 0.6:
            fit = max(0, fit - 40)
            issues = issues + ["same as another clip's hook"]
        verdict = hs.score(h)[1] if hs else 50
        order = max(0, 100 - 20 * i)  # the AI's own preference counts a little
        total = round(0.5 * fit + 0.3 * verdict + 0.2 * order) if fit > 0 else 0
        out.append({"hook": h, "score": total, "fit": fit, "hookscore": verdict, "issues": issues})
    out.sort(key=lambda r: -r["score"])
    return out


# ------------------------------------------------------------------------------------ titles
def _mentions_creator(title: str, creator: str) -> bool:
    """'Speed' counts for IShowSpeed, 'Carry' for CarryMinati: any 4+ letter word of the title that is
    part of the creator's name."""
    c = re.sub(r"[^a-z0-9]", "", (creator or "").lower())
    if not c:
        return False
    for w in re.findall(r"[a-z0-9]+", title.lower()):
        if len(w) >= 4 and (w in c or c in w):
            return True
    return False


def lint_title(title: str, hook: str = "", creator: str = "", keyword: str = "", wrong: tuple = ()) -> dict:
    """title.py check, adjusted for Shorts: a creator name counts like a number (we never invent
    numbers to satisfy the linter)."""
    r = _tool("yt-package/title.py").check(title, hook or None)
    # our "thumb" is the 3-7 word on-screen hook, not a thumbnail: its own length is checked in hook_fit
    issues = [x for x in r["issues"] if x[0] != "thumb-length"]
    has_creator = _mentions_creator(title, creator)
    if has_creator:
        issues = [x for x in issues if x[0] != "no-number"]
    if any(x[0] == "mobile" for x in issues) and len(title) <= 48:
        issues = [x for x in issues if x[0] != "mobile"]  # a few chars over 40 is fine for Shorts
    # Shorts rules on top of title.py: a named subject, and enough words to promise something
    named = has_creator or any(
        x[:1].isupper() and not x.isupper() for x in title.split()[1:]) or re.search(r"\d", title)
    if not named:
        issues.append(("no-subject", "no creator name, place or number - say WHO this is about"))
    if len(title.strip()) < 15:
        issues.append(("too-short", "under 15 characters - promises nothing specific"))
    low = title.lower()
    if keyword and keyword.lower() not in low and keyword.lower().replace(" ", "") not in low.replace(" ", ""):
        issues.append(("no-keyword", f"put \"{keyword}\" in the first words: it is what people search"))
    elif keyword and re.sub(r"[^a-z0-9]", "", keyword.lower()) not in re.sub(r"[^a-z0-9]", "", low[:42]):
        issues.append(("keyword-late", f"\"{keyword}\" should be in the first 40 characters (what a phone shows)"))
    strong, why = opener(title)
    if not strong:
        issues.append(("weak-opener", why))
    vague = [w for w in VAGUE_TITLE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)]
    if vague:
        issues.append(("vague", f"vague words ({', '.join(vague)}): name the concrete thing that happens"))
    if any(w in low for w in wrong):
        issues.append(("misleading", "names a game this clip is not from"))
    score = max(0, min(100, 100 - 14 * len(issues) + 4 * len(r["good"])))
    if any(x[0] in ("misleading", "no-keyword", "keyword-late", "vague", "weak-opener") for x in issues):
        score = min(score, 60)
    if has_claim(title):
        score, issues = 0, issues + [("claim", "fake claim (viral/views)")]
    if len(title) > 100:
        score = 0
    return {"title": title.strip(), "score": score, "issues": issues}


def best_title(titles, hook="", creator="", used=None, keyword="", wrong=()):
    rows = [lint_title(t, hook, creator, keyword, wrong) for t in titles or [] if (t or "").strip()]
    for r in rows:
        if used and max(similar(r["title"], u) for u in used) >= 0.5:
            r["score"] = max(0, r["score"] - 40)
            r["issues"].append(("repeat", "almost the same title as another clip of this video"))
    rows.sort(key=lambda r: -r["score"])
    return rows


# ---------------------------------------------------------------------------------- cleanup
def clean_text(text: str) -> str:
    """Drop lines that make claims we cannot back up."""
    lines = [ln for ln in (text or "").splitlines() if not has_claim(ln)]
    return "\n".join(lines).strip()


# broad tags every gaming Short uses: they say nothing about THIS clip (max one of them is kept)
FILLER_TAGS = {"#shorts", "#short", "#gaming", "#gamer", "#gamers", "#reels", "#reel", "#fyp", "#foryou",
               "#foryoupage", "#explore", "#explorepage", "#gamingclips", "#gamingvideos", "#gamingshorts",
               "#livestream", "#livestreamclips", "#streamclips", "#clips", "#funny", "#memes", "#youtubeshorts"}


# words from our titles that stayed under 6 views: they describe nothing a viewer can picture
VAGUE_TITLE_WORDS = ("clips", "moments", "goosebumps", "loses it", "trolled", "crazy", "praise", "content",
                     "best parts", "highlights", "compilation")


# How the week's biggest GTA 6 Shorts open (data/trends.json, 10x-800x their channel's usual views):
# "Is 30 FPS...", "Come with me...", "We Bought...", "When GTA 6...", "POV:", "How Many...", "GTA 5 vs GTA 6".
# A title / hook / caption must STOP the scroll with its first 1-2 words.
WEAK_OPENERS = {"the", "a", "an", "so", "here", "here's", "heres", "just", "some", "today", "watch", "check",
                "look", "video", "clip", "clips", "in", "on", "at", "with", "and", "but", "also", "another",
                "tiny", "small", "little", "new", "my", "our", "ign", "review", "reviewing", "highlights", "best"}


SOURCE_OPENERS = {"ign", "gamespot", "polygon", "kotaku", "eurogamer", "gameinformer", "review", "reviews"}


def opener(text: str) -> tuple[bool, str]:
    """(strong?, why). Strong = question, POV/When, I/We action, a name or number, a CAPS word, X vs Y."""
    t = re.sub(r"^[^\w$]+", "", (text or "").strip())
    words = t.split()
    if not words:
        return False, "empty"
    w0 = re.sub(r"[^\w$']", "", words[0]).lower()
    two = " ".join(words[:2])
    if w0 in SOURCE_OPENERS:
        return False, f"starts with '{words[0]}' (who uploaded it) - open with what the viewer will see"
    if w0 in WEAK_OPENERS and not (words[0].isupper() and len(w0) > 2):      # "NEW TV..." shouts: fine
        return False, f"starts with '{words[0]}' - the first 1-2 words must stop the scroll"
    if re.search(r"\bvs\.?\b", " ".join(words[:5]), re.I):
        return True, "versus"
    return True, ("caps" if any(x.isupper() and len(x) > 2 for x in words[:2]) else
                  "number" if re.search(r"[\d$]", two) else "ok")


def on_topic(settings, *texts) -> bool:
    """True when the text names the channel's topic (settings.topic_words, e.g. gta 6 / gta vi)."""
    words = [w.lower() for w in settings.get("topic_words") or []]
    if not words:
        return True
    text = re.sub(r"[\s_-]+", " ", " ".join(t or "" for t in texts).lower())
    flat = text.replace(" ", "")
    return any(w in text or w.replace(" ", "") in flat for w in words)


def clean_hashtags(tags, limit=12, first=None, drop=()):
    """Lowercase, no duplicates, no viral/trending, at most ONE broad filler tag; `first` goes first;
    tags in `drop` are removed (e.g. #gta6 on a clip that is not GTA 6 = misleading)."""
    out, filler = [], 0
    for t in ([first] if first else []) + list(tags or []):
        t = "#" + re.sub(r"[^a-z0-9_]", "", (t or "").lower().lstrip("#"))
        if len(t) <= 2 or t in out or "viral" in t or "trending" in t or t in drop:
            continue
        if t in FILLER_TAGS:
            if filler:
                continue
            filler += 1
        out.append(t)
    return out[:limit]
