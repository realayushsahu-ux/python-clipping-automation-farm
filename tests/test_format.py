"""The APPROVED clip format is locked (user, 2026-11-20: "that quality is superb, keep it like that").

Approved clips: CLIP0036 (IShowSpeed first time GTA 6) and CLIP0042 (Speed reacts to the GTA VI extended
look). The format is the trending-reel study of the user's ~20 sample reels (docs/YOUTUBE_LESSONS.md):
streamer face on top (or the black card when there is no facecam), one plain title with an emoji in a white
box the whole clip, small white captions with a yellow keyword, real speed at the source fps (60), quick
2-6 % zooms, silences trimmed, the streamer's own voice only (no AI narrator), no music unless a licensed
track is in assets/music.

config/approved_format.json is a frozen copy of every setting that shapes that result. This test fails when
config/editing_presets.json or config/settings.json drift from it, so a change by hand, by an update or by
an AI can not quietly make the clips worse. To change the format on purpose: make a sample, get the user's
OK, then run  python -m tests.test_format --save  to freeze the new version.
Run: python -m tests.test_format"""

import json
import sys

from core.paths import CONFIG_DIR

LOCK = CONFIG_DIR / "approved_format.json"
SETTING_KEYS = ["editing_preset", "voice_commentary", "music_mode", "clip_min_seconds", "clip_max_seconds",
                "final_max_seconds", "long_clip_kinds", "long_clip_max_seconds", "source_max_height",
                "focus_scan_over_minutes", "focus_scan_minutes", "focus_scan_model", "focus_scan_beam",
                "voice_take_mode", "only_topic_videos", "min_score", "clips_per_video",
                "ai_effort_packaging", "ai_effort_moments"]


def current():
    p = json.loads((CONFIG_DIR / "editing_presets.json").read_text(encoding="utf-8"))
    s = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    used = {"viral_auto", "facecam_top", "card"}
    pre = {k: p["presets"][k] for k in sorted(used)}
    caps = {pre[k].get(c) for k in pre for c in ("captions", "split_captions")} - {None}
    return {"presets": pre,
            "caption_styles": {c: p["caption_styles"][c] for c in sorted(caps)},
            "motion": {m: p["motion"][m] for m in sorted({x["motion"] for x in pre.values()})},
            "sound": {m: p["sound"][m] for m in sorted({x["sound"] for x in pre.values()})},
            "color_grades": {m: p["color_grades"][m] for m in sorted({x["color_grade"] for x in pre.values()})},
            "export": p["export"],
            "settings": {k: s.get(k) for k in SETTING_KEYS}}


def main():
    now = current()
    if "--save" in sys.argv:
        LOCK.write_text(json.dumps({"_help": "frozen approved clip format, see tests/test_format.py", **now},
                                   indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"approved format saved to {LOCK}")
        return
    locked = {k: v for k, v in json.loads(LOCK.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    diffs = []
    for part, want in locked.items():
        for key, val in want.items():
            if now.get(part, {}).get(key) != val:
                diffs.append(f"{part}.{key}: approved {json.dumps(val, ensure_ascii=False)[:200]} "
                             f"-> now {json.dumps(now.get(part, {}).get(key), ensure_ascii=False)[:200]}")
    assert not diffs, "the approved clip format changed:\n  " + "\n  ".join(diffs)
    assert now["settings"]["editing_preset"] == "viral_auto" and now["settings"]["voice_commentary"] is False
    print("test_format: the approved clip format is unchanged")


if __name__ == "__main__":
    main()
