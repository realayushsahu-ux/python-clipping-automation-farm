"""One progress bar per video, used by the farm window (narrator) and the Sheet's Video Input tab.

    [#########-----------]  45%  Step 2 of 4: listening 30 of 66 min (about 1 h 40 min left)

Steps: 1 download (0-10 %), 2 listen (10-65 %), 3 find the best moments (65-70 %), 4 edit the clips (70-100 %).
Listening is the slow part on a CPU, so it moves live (updated every 20 s while Whisper runs)."""

import re

STEPS = 4
WORKING = ("downloading", "transcribing", "analyzing", "editing")
PENDING_CLIP = ("planned", "rendering")


def _mins(sec) -> str:
    m = max(1, round(sec / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"


def listening_detail(done, total, eta) -> str:
    """Saved in videos.stage_detail while Whisper listens, read back by video_progress()."""
    left = f" (about {_mins(eta)} left)" if eta else ""
    return f"heard {done / 60:.0f} of {total / 60:.0f} min{left}"


def bar(pct, width=20) -> str:
    n = round(width * max(0, min(100, pct)) / 100)
    return "[" + "#" * n + "-" * (width - n) + "]"


def video_progress(v, clips) -> tuple:
    """(percent or None, words) for one video. clips = this video's clips (any status)."""
    st, d = v["status"], v.get("stage_detail") or ""
    if st in ("new", "retry"):
        return 0, "resuming where it stopped" if d == "resuming after a restart" else "waiting to start"
    if st == "downloading":
        return (7 if d == "audio" else 3), f"Step 1 of {STEPS}: downloading the video"
    if st == "transcribing":
        m = re.match(r"heard (\d+) of (\d+) min(.*)", d)
        if m:
            done, total = int(m[1]), max(int(m[2]), 1)
            return 10 + round(55 * done / total), f"Step 2 of {STEPS}: listening {done} of {total} min{m[3]}"
        return 10, f"Step 2 of {STEPS}: listening (checking the language first)"
    if st == "analyzing":
        return 67, f"Step 3 of {STEPS}: finding the best moments"
    if st == "editing":
        mine = [c for c in clips if c["status"] != "rejected"]
        total = len(mine)
        if not total:
            return 70, f"Step 4 of {STEPS}: editing the clips"
        done = sum(c["status"] not in PENDING_CLIP for c in mine)
        return 70 + round(30 * done / total), f"Step 4 of {STEPS}: editing clip {min(done + 1, total)} of {total}"
    if st == "done":
        made = sum(c["status"] not in ("planned", "rendering", "rejected", "qc_failed") for c in clips)
        return 100, f"finished: {made} clip{'s' if made != 1 else ''} made"
    if st == "failed":
        return None, "failed: " + (v.get("last_error") or "see logs/farm.log")[:120]
    return None, st


def line(v, clips) -> str:
    pct, words = video_progress(v, clips)
    return f"{bar(pct)} {pct:3d}%  {words}" if pct is not None else words
