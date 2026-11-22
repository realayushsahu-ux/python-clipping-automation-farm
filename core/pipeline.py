"""The processing pipeline for one video (see docs/DESIGN.md §5-6).

new → downloading → transcribing → analyzing → editing → done   (or failed)

Every stage caches its result in storage/processing/<VIDEO_ID>/ and its status in SQLite,
so re-running after a crash continues where it stopped and never makes duplicate clips.
"""

import json
import re
import time
import traceback
from pathlib import Path

from ai.caption_generator import package, romanize_lines, wants_hinglish
from analyzer import signals, transcript
from analyzer.hook_detector import find_moments
from config.settings import clip_max_for, load_presets
from core import progress
from core.outputs import archive_old, store_clip, write_post_info
from posting.slots import assign_post_times
from core.db import get_db
from core.logger import get_logger
from core.media import fmt_time, parse_section
from core.paths import video_sourcedir, video_workdir
from core.retry import FarmError, PermanentError, retry
from downloader import video_downloader as dl
from editor import qc
from editor.renderer import render_clip

log = get_logger("pipeline")
MARGIN = 2.0
DONE_CLIP = ("ready", "approved", "scheduled", "posting", "posted", "manual_post", "rejected")


def _romanize_words(words, settings):
    """Devanagari words → Roman Hinglish, keeping each word's timing."""
    if not any(re.search(r"[ऀ-ॿ]", w["w"]) for w in words):
        return words
    lines, groups, cur = [], [], []
    for w in words:
        cur.append(w)
        if w["w"].endswith((".", "!", "?", "।")) or len(cur) >= 8:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    lines = [" ".join(w["w"] for w in g) for g in groups]
    roman = romanize_lines(lines, settings)
    out = []
    for g, line in zip(groups, roman):
        toks = line.split()
        if len(toks) == len(g):
            out += [dict(w, w=t) for w, t in zip(g, toks)]
        else:                                   # different word count: spread over the same time
            t0, t1 = g[0]["s"], g[-1]["e"]
            step = (t1 - t0) / max(len(toks), 1)
            out += [{"w": t, "s": round(t0 + i * step, 2), "e": round(t0 + (i + 1) * step, 2)} for i, t in enumerate(toks)]
    return out


def add_video(url, **opts):
    vid = dl.video_id_from_url(url)
    if not vid:
        raise PermanentError(f"not a supported video link: {url}")
    db = get_db()
    created = db.add_video(vid, url, **{k: v for k, v in opts.items() if v not in (None, "")})
    if not created:
        log.info(f"{vid} is already in the farm (status: {db.get_video(vid)['status']})")
    return vid


def redo_video(video_id, **opts):
    """Process a finished video again (new settings, new AI, another section) WITHOUT touching its
    old clips: they keep their IDs, files and post history. The new run gets new clip IDs.
    Cached audio + transcript are reused; only the moment list is redone (old one archived)."""
    db = get_db()
    v = db.get_video(video_id)
    if not v:
        raise ValueError(f"unknown video {video_id}")
    work = video_workdir(video_id)
    mf = work / "moments.json"
    if mf.exists():
        mf.rename(work / f"moments_{time.strftime('%Y%m%d_%H%M%S')}.json")
    old = db.clips(video_id)
    base = (max(c["idx"] for c in old) + 1) if old else 0
    (work / "run.json").write_text(json.dumps({"idx_base": base}), encoding="utf-8")
    fields = {k: val for k, val in opts.items() if val not in (None, "")}
    db.update_video(video_id, status="retry", stage_detail="redo", **fields)
    db.event("info", video_id, "redo", f"redo requested, new clips start at idx {base}")
    log.info(f"{video_id}: redo — old clips kept ({len(old)}), new clips from idx {base}")


REMAKE_OK = ("ready", "qc_failed", "failed", "rendered", "planned", "rejected", "approved", "remake")


def _ai_works(settings) -> bool:
    """One tiny question to the AI (a few hundred tokens): is it answering again (spend limit raised...)?"""
    from ai.base import ask_json, providers
    if all(p.is_offline for p in providers(settings)):
        return False
    try:
        out = ask_json(settings, "Reply with JSON.", "Say ok.",
                       {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"],
                        "additionalProperties": False}, max_tokens=200, offline_fn=lambda: {"_offline": True},
                       voice=False)
        return bool(out) and not out.get("_offline")
    except Exception:  # noqa: BLE001
        return False


def heal_clips(settings) -> list:
    """Clips that came out wrong for a passing reason get made again by themselves (found 2026-11-21):
      * made while the AI was not answering (OpenAI spend limit): generic title, no on-screen hook.
        Remade with the AI once it answers again, if the clip is not due to post within 90 minutes.
      * failed while editing (e.g. ffmpeg 'Broken pipe'): one more try after its video is finished.
    Each clip is healed at most twice. Returns the clip IDs sent to remake."""
    db = get_db()
    counts = {}
    for e in db.events("info", 3000):
        if e.get("stage") == "heal":
            counts[e["item"]] = counts.get(e["item"], 0) + 1
    soon = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 90 * 60))
    offline, broken = [], []
    for c in db.clips(None, "ready", "approved", "failed"):
        if counts.get(c["clip_id"], 0) >= 2:
            continue
        v = db.get_video(c["video_id"])
        if not v:
            continue
        if c["status"] == "failed":
            if v["status"] == "done":
                broken.append(c)
            continue
        f = video_workdir(c["video_id"]) / f"{c['clip_id']}_package.json"
        try:
            pkg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            pkg = {}
        if pkg.get("_offline") and (not c.get("post_time") or c["post_time"] > soon):
            offline.append(c)
    if offline and not _ai_works(settings):
        offline = []
    out = []
    for c, why in [(c, "made without the AI; the AI answers again") for c in offline] + \
                  [(c, "failed while editing; one more try") for c in broken]:
        db.update_clip(c["clip_id"], status="remake")
        db.event("info", c["clip_id"], "heal", why)
        log.info(f"{c['clip_id']}: {why} → making it again")
        out.append(c["clip_id"])
    return out


def remake_clip(clip_id, settings, repackage=True):
    """Re-package (new hook/title/caption) and re-render ONE clip in place, same clip ID.
    Refused for clips that are scheduled, being posted or posted (never change what went out).
    The old folder is kept in storage/processing/<video>/old/ - nothing is deleted."""
    db = get_db()
    c = db.get_clip(clip_id)
    if not c:
        raise ValueError(f"unknown clip {clip_id}")
    if c["status"] not in REMAKE_OK:
        raise PermanentError(f"{clip_id} is '{c['status']}' - only unposted clips can be remade")
    v = db.get_video(c["video_id"])
    work, src_dir = video_workdir(v["video_id"]), video_sourcedir(v["video_id"])
    info = dl.video_info(v["url"], work)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        archive_old(clip_id, work / "old", stamp)
    except PermissionError as exc:
        raise PermanentError(f"a file of {clip_id} is open in another program (Explorer, a video player, a "
                             f"terminal). Close it and try again. Backups are in {work / 'old'}") from exc
    pkg_file = work / f"{clip_id}_package.json"
    if repackage and pkg_file.exists():
        pkg_file.rename(work / f"{clip_id}_package_{stamp}.json")
    m = {"start": c["start"], "end": c["end"], "kind": c.get("kind"), "score": c.get("score"),
         "hook_text": c.get("hook") or "", "reason": ""}
    _make_clip(clip_id, m, info, v.get("language") or "en", v["url"], work, src_dir, settings, load_presets(), v)
    db.event("info", clip_id, "remake", f"remade (repackage={repackage})")
    return db.get_clip(clip_id)


def needs_voice_retry(pkg: dict, voice) -> bool:
    """True when the clip would go out with no spoken intro although the AI could write one:
    once per clip (pkg["_voice_retry"]), never for offline/fake packages."""
    if pkg.get("_offline") or pkg.get("_voice_retry"):
        return False
    return not (voice and "intro" in voice)


def _used_voices(work, cid) -> list:
    """Narrator lines of the other clips of this video (so every clip says something new)."""
    out = []
    for f in work.glob("CLIP*_package.json"):
        if f.name.startswith(cid + "_"):
            continue
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out += [p.get("voice_intro"), p.get("voice_take")]
    return [x for x in out if x]


def retitle_clip(clip_id, settings) -> dict:
    """New YouTube title, hashtags and description with the current rules; the video file and its
    on-screen hook stay as they are (no re-render). Refused once a clip is being posted or posted."""
    from ai.caption_generator import finish_package
    db = get_db()
    c = db.get_clip(clip_id)
    if not c:
        raise ValueError(f"unknown clip {clip_id}")
    busy = [p for p in db.posts("publishing", "posted") if p["clip_id"] == clip_id]
    if c["status"] in ("posting", "posted") or busy or not c.get("folder"):
        raise PermanentError(f"{clip_id} is '{c['status']}' - only clips not yet posted can be retitled")
    v = db.get_video(c["video_id"])
    work = video_workdir(v["video_id"])
    info = dl.video_info(v["url"], work)
    pkg_file = work / f"{clip_id}_package.json"
    pj = Path(c["folder"]) / "post.json"
    old = json.loads(pkg_file.read_text(encoding="utf-8")) if pkg_file.exists() else         json.loads(pj.read_text(encoding="utf-8"))
    titles = [t["title"] if isinstance(t, dict) else t for t in old.get("title_options") or []] or [old.get("youtube_title", "")]
    out = {"creator": old.get("creator"), "hooks": [old.get("hook") or ""], "youtube_titles": titles,
           "hashtags": old.get("hashtags"), "youtube_description": old.get("youtube_description", ""),
           "instagram_caption": old.get("instagram_caption", ""), "pin_comment": old.get("pin_comment", ""),
           "search_queries": old.get("search_queries", []), "loop_line": old.get("loop_line", ""),
           "hook_formula": old.get("hook_formula", ""), "best_time_note": old.get("best_time_note", "")}
    others = [x for x in db.clips(v["video_id"]) if x["clip_id"] != clip_id and x.get("title")]
    text = f"{old.get('hook', '')}. {old.get('youtube_description', '')}"
    wf = work / f"{clip_id}_words.json"                       # what is said in the clip, for a concrete title
    if wf.exists():
        try:
            words = json.loads(wf.read_text(encoding="utf-8"))
            last = max(w["s"] for w in words) if words else 0     # times are relative to the downloaded part
            text = " ".join(w["w"] for w in words if w["s"] >= last - (c["end"] - c["start"]))[:1500] or text
        except (OSError, ValueError, KeyError):
            pass
    new = finish_package(out, {"kind": c.get("kind")}, info, text, "English", settings,
                         {"titles": [x["title"] for x in others]})
    new["hook"] = old.get("hook", "")                         # burned into the video: never changes here
    pkg = {**old, **{k: new[k] for k in ("youtube_title", "hashtags", "title_options", "on_topic", "quality")}}
    pkg["quality"]["hook_score"] = (old.get("quality") or {}).get("hook_score", 0)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if pkg_file.exists():
        pkg_file.rename(work / f"{clip_id}_package_{stamp}.json")
    pkg_file.write_text(json.dumps(pkg, indent=1, ensure_ascii=False), encoding="utf-8")
    if pj.exists():
        meta = json.loads(pj.read_text(encoding="utf-8"))
        meta.update({k: pkg[k] for k in ("youtube_title", "hashtags", "title_options", "on_topic")})
        pj.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    db.update_clip(clip_id, title=pkg["youtube_title"], hashtags=pkg["hashtags"])
    write_post_info(clip_id)
    db.event("info", clip_id, "retitle", f"{old.get('youtube_title')} -> {pkg['youtube_title']}")
    return {"clip_id": clip_id, "old": old.get("youtube_title"), "new": pkg["youtube_title"], "hashtags": pkg["hashtags"]}


def process_video(video_id, settings):
    db = get_db()
    v = db.get_video(video_id)
    if not v:
        raise ValueError(f"unknown video {video_id}")
    if v["status"] == "done":
        log.info(f"{video_id} already done")
        return
    db.update_video(video_id, attempts=v["attempts"] + 1, last_error=None)
    try:
        _run(v, settings)
        db.update_video(video_id, status="done", stage_detail="finished")
        db.event("info", video_id, "done", "video processed")
    except PermanentError as exc:
        _fail(video_id, "permanent", exc)
    except Exception as exc:  # noqa: BLE001
        _fail(video_id, v["status"], exc)


def _fail(video_id, stage, exc):
    db = get_db()
    msg = f"{type(exc).__name__}: {exc}"
    db.update_video(video_id, status="failed", last_error=msg[:1000])
    db.event("error", video_id, stage, msg + "\n" + traceback.format_exc()[-1500:])
    log.error(f"{video_id} failed at {stage}: {msg[:300]}")


def _check_topic(info, settings):
    """Lesson from the first posts: clips that are not about the channel's game (GTA 5 RP on a GTA 6
    channel) got 2-5 views. With only_topic_videos = yes such videos stop here, before any work."""
    from ai.caption_generator import is_topic
    name = settings.get("topic_name")
    if name and settings.get("only_topic_videos") and not is_topic(settings, info):
        raise PermanentError(f"not about {name} (title/description never mention it). This channel's viewers "
                             f"come for {name}; to clip it anyway set Settings → only_topic_videos = no, "
                             "then type retry")


def _run(v, settings):
    db = get_db()
    vid, url = v["video_id"], v["url"]
    work, src_dir = video_workdir(vid), video_sourcedir(vid)
    waits = settings["retry_waits_seconds"]
    tries = settings["retry_attempts"]

    # ---------------------------------------------------------------- download
    db.update_video(vid, status="downloading", stage_detail="video info")
    info = retry(dl.video_info, url, work, attempts=tries, waits=waits, what="video info")
    db.update_video(vid, title=info.get("title"), channel=info.get("channel"), duration=info.get("duration"))
    log.info(f"{vid}: {info.get('channel')} · {info.get('title')} · {fmt_time(info.get('duration') or 0)}")
    _check_topic(info, settings)
    db.update_video(vid, stage_detail="audio")
    audio = retry(dl.download_audio, url, src_dir, attempts=tries, waits=waits, what="audio download")

    # ---------------------------------------------------------------- listen
    db.update_video(vid, status="transcribing", stage_detail="language")
    total = float(info.get("duration") or 0) or None
    sec = parse_section(v.get("section") or "")
    lo, hi = (sec if sec else (0.0, total or 1e9))
    if total:
        hi = min(hi, total)
    lang = v.get("language") or settings["language"]
    loud = signals.relative(signals.loudness(audio, work))
    chat = dl.chat_rate(url, info, work)
    windows = None
    if (hi - lo) / 60 > float(settings.get("focus_scan_over_minutes", 150)):
        # a 10-hour stream cannot be fully transcribed on a CPU in time: listen where chat/voice spiked
        windows = signals.focus_windows(loud, chat, lo, hi, budget_s=float(settings.get("focus_scan_minutes", 90)) * 60)
    last = [0.0]

    def heard(done, total, eta):
        if time.time() - last[0] < 20 and done < total:
            return                                   # the database gets at most one update every 20 s
        last[0] = time.time()
        db.update_video(vid, stage_detail=progress.listening_detail(done, total, eta))

    tr = transcript.scan(audio, lo, hi, work, settings, None if lang == "auto" else lang, windows, progress=heard)
    db.update_video(vid, language=tr["language"])

    # ---------------------------------------------------------------- pick
    db.update_video(vid, status="analyzing", stage_detail="moments")
    moments = find_moments(tr, loud, chat, info, settings, work, v.get("clips_wanted"))

    # ---------------------------------------------------------------- edit
    db.update_video(vid, status="editing")
    presets = load_presets()
    clip_ids = []
    run_file = work / "run.json"
    base = json.loads(run_file.read_text(encoding="utf-8")).get("idx_base", 0) if run_file.exists() else 0
    for i, m in enumerate(moments):
        cid = db.add_clip(vid, base + i, m["start"], m["end"], score=m.get("score"), kind=m.get("kind"),
                          hook=m.get("hook_text"), title=m.get("title"))
        clip_ids.append(cid)
    for i, (cid, m) in enumerate(zip(clip_ids, moments)):
        c = db.get_clip(cid)
        if c["status"] in DONE_CLIP:
            continue
        db.update_video(vid, stage_detail=f"clip {i + 1}/{len(moments)} ({cid})")
        try:
            try:
                _make_clip(cid, m, info, tr["language"], url, work, src_dir, settings, presets, v)
            except PermanentError:
                raise
            except Exception:  # noqa: BLE001
                from core import net
                if net.online():
                    raise
                net.wait_online()                    # the internet dropped mid-clip: wait, then make it again
                _make_clip(cid, m, info, tr["language"], url, work, src_dir, settings, presets, v)
        except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the others
            db.update_clip(cid, status="failed", qc={"error": str(exc)[:500]})
            db.event("error", cid, "editing", f"{exc}\n{traceback.format_exc()[-1500:]}")
            log.error(f"{cid} failed: {str(exc)[:200]}")
    made = sum(1 for c in db.clips(vid, "ready", "approved", "scheduled", "posting", "posted", "manual_post", "qc_failed")
               if c["clip_id"] in clip_ids)          # auto-approve may have moved finished clips on already
    db.update_video(vid, stage_detail=f"{made} clip(s) made")
    if made == 0:
        raise FarmError("no clip could be made")


def _video_settings(settings, v):
    """The video's own choices from the Sheet (Video Input → Voiceover) over the Settings tab."""
    vo = (v.get("voiceover") or "").strip().lower()
    if vo not in ("voiceover", "normal", "yes", "no"):
        return settings
    import copy
    st = copy.copy(settings)
    st["voice_commentary"] = vo in ("voiceover", "yes")
    return st


def _video_layout(settings, v) -> str:
    """Video Input → Layout, else Settings → default_layout (split = streamer on top), else auto."""
    return (v.get("layout") or settings.get("default_layout") or "auto").strip().lower()


def _make_clip(cid, m, info, lang, url, work, src_dir, settings, presets, v):
    db = get_db()
    settings = _video_settings(settings, v)
    waits, tries = settings["retry_waits_seconds"], settings["retry_attempts"]
    length = m["end"] - m["start"]
    log.info(f"{cid}: {m.get('hook_text')} ({length:.0f}s @ {fmt_time(m['start'])}, score {m.get('score')})")
    db.update_clip(cid, status="rendering")
    h = int(settings.get("source_max_height", 1440))
    src = src_dir / (f"{cid}.mp4" if h == 1080 else f"{cid}_{h}p.mp4")   # sharper source = its own file
    a = max(0.0, m["start"] - MARGIN)
    retry(dl.download_section, url, a, m["end"] + MARGIN, src, h, attempts=tries, waits=waits,
          what=f"{cid} video")
    cs = m["start"] - a

    words_file = work / f"{cid}_words.json"
    if words_file.exists():
        words = json.loads(words_file.read_text(encoding="utf-8"))
    else:
        words = transcript.words_for(src, 0, cs + length + 0.5, lang, settings)
        if wants_hinglish(settings, lang):
            words = _romanize_words(words, settings)
        words_file.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    text = " ".join(w["w"] for w in words if cs - 0.2 <= w["s"] <= cs + length)

    pkg_file = work / f"{cid}_package.json"
    if pkg_file.exists():
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    else:
        others = [c for c in db.clips(v["video_id"]) if c["clip_id"] != cid and c["status"] in DONE_CLIP + ("qc_failed",)]
        used = {"titles": [c["title"] for c in others if c.get("title")],
                "hooks": [c["hook"] for c in others if c.get("hook")],
                "voices": _used_voices(work, cid)}
        pkg = package(m, info, text, lang, settings, used)
        pkg_file.write_text(json.dumps(pkg, indent=1, ensure_ascii=False), encoding="utf-8")

    voice = None
    if settings.get("voice_commentary", True) and pkg.get("hook") is not None:
        from editor import voiceover
        if "voice_intro" not in pkg and not pkg.get("_offline"):       # packaged before voice existed
            from ai.caption_generator import voice_lines
            pkg.update(voice_lines(pkg, info, text, settings, _used_voices(work, cid)))
            pkg_file.write_text(json.dumps(pkg, indent=1, ensure_ascii=False), encoding="utf-8")
        for n in pkg.get("voice_notes") or []:
            log.info(f"{cid}: {n}")
        for n in voiceover.recheck_saved(pkg):          # older packages: today's intro rules apply too
            log.info(f"{cid}: {n}")
        voice = voiceover.prepare(pkg, settings)
        if settings.get("voice_commentary") and needs_voice_retry(pkg, voice) and voiceover.available():
            # no spoken intro survived (too long / sounded like a repost): ONE small AI call for new lines,
            # because a clip without our narrator is just someone else's footage (~Rs 0.2, once per clip)
            from ai.caption_generator import voice_lines
            pkg["_voice_retry"] = True
            try:
                again = voice_lines(pkg, info, text, settings, _used_voices(work, cid))
            except Exception as exc:  # noqa: BLE001 - the clip still renders without a narrator
                again = {}
                log.warning(f"{cid}: new narrator lines failed: {str(exc)[:150]}")
            if again.get("voice_intro"):
                pkg["voice_intro"], pkg["voice_intro_alts"] = again["voice_intro"], again.get("voice_intro_alts", [])
                if not pkg.get("voice_take") and again.get("voice_take"):
                    pkg["voice_take"], pkg["voice_take_alts"] = again["voice_take"], again.get("voice_take_alts", [])
                voice = voiceover.prepare(pkg, settings)
            for n in again.get("voice_notes") or []:
                log.info(f"{cid}: retry {n}")
            log.info(f"{cid}: narrator retry → {'intro ok' if voice and 'intro' in voice else 'still no intro'}")
            pkg_file.write_text(json.dumps(pkg, indent=1, ensure_ascii=False), encoding="utf-8")

    variant = int(cid[4:]) if cid[4:].isdigit() else 0
    max_len = clip_max_for(settings, m.get("kind"))
    on_screen = pkg.get("hook") or m.get("hook_text", "")
    if presets["presets"].get(settings["editing_preset"], {}).get("title_cfg") and pkg.get("banner"):
        from ai import vision
        seen = vision.check_title(settings, src, cs, length, pkg, text)
        if seen:
            log.info(f"{cid}: frames show: {seen['seen'][:120]} | title "
                     f"{'REWRITTEN → ' + seen['title'] if seen['changed'] else 'ok: ' + seen['title']}")
            pkg["banner"], pkg["seen"] = seen["title"], seen["seen"]
            pkg_file.write_text(json.dumps(pkg, indent=1, ensure_ascii=False), encoding="utf-8")
            if seen.get("other_content") and settings.get("skip_other_content", True):
                # the moment runs into a different trailer/game/end card: a "GTA 6" title would mislead
                msg = f"skipped: part of the clip is not {pkg.get('game') or 'the game'} ({seen.get('other_what', '')[:120]})"
                db.update_clip(cid, status="qc_failed", qc={"error": msg})
                db.event("warning", cid, "vision", msg)
                log.warning(f"{cid}: {msg}")
                return
    if presets["presets"].get(settings["editing_preset"], {}).get("title_cfg"):
        on_screen = pkg.get("banner") or re.sub(r"\s#\S+", "", pkg.get("youtube_title") or "") or on_screen
    res = render_clip(src, words, cs, cs + length, on_screen, presets, settings,
                      work / "render", cid, work / "render", variant=variant,
                      preset_name=settings["editing_preset"], layout=_video_layout(settings, v), max_seconds=max_len,
                      voice=voice)
    check = qc.check(res["file"], max_dur=max_len + 10)
    ok = check["ok"]
    meta = {"clip_id": cid, "video_id": v["video_id"], "source_url": url,
            "source_time": f"{fmt_time(m['start'])}-{fmt_time(m['end'])}", "channel": info.get("channel"),
            "video_title": info.get("title"), "score": m.get("score"), "kind": m.get("kind"),
            "reason": m.get("reason"), "picked_by": m.get("picked_by"),
            "platforms": settings.get("default_platforms"), "timezone": settings.get("timezone"),
            "render": res, "qc": check, **pkg}
    out = store_clip(cid, res["file"], res.get("nomusic_file"), ok, meta)
    db.update_clip(cid, status="ready" if ok else "qc_failed", file=out["file"], nomusic_file=out["nomusic_file"],
                   folder=out["folder"], creator=pkg.get("creator") or info.get("channel"),
                   hook=pkg.get("hook"), title=pkg.get("youtube_title"), caption=pkg.get("instagram_caption"),
                   hashtags=pkg.get("hashtags", []), description=pkg.get("youtube_description"),
                   music=res.get("music"), layout=res.get("layout"), qc=check)
    if ok:
        assign_post_times(settings)
        write_post_info(cid)
        from posting import queue
        queue.step(settings)            # manual_approval = no → approved + scheduled right away
    log.info(f"{cid}: {'READY' if ok else 'QC FAILED ' + '; '.join(check['issues'])} → {out['folder']}")
