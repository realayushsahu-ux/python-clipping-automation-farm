"""Re-render an existing clip with the CURRENT editing presets (to try new looks/assets).

Run:  .venv\\Scripts\\python.exe -m tests.rerender lcetk9qIzrA CLIP0002 [variant]
Output: storage/processing/<video>/rerender/<CLIP>.mp4  (the ready_to_post copy is not touched)
Needs the clip's source section + words file from an earlier `process` run.
"""

import json
import sys

from config.settings import load_presets, load_settings
from core.paths import PROCESSING, SOURCES
from editor import qc
from editor.renderer import render_clip


def main(video_id, cid, variant=0):
    settings, presets = load_settings(), load_presets()
    work = PROCESSING / video_id
    moments = json.loads((work / "moments.json").read_text(encoding="utf-8"))
    moments = moments.get("moments", moments) if isinstance(moments, dict) else moments
    idx = int(cid[-4:]) - 1
    m = moments[idx] if idx < len(moments) else moments[0]
    words = json.loads((work / f"{cid}_words.json").read_text(encoding="utf-8"))
    src = SOURCES / video_id / f"{cid}.mp4"
    margin = 2.0
    cs = min(margin, m["start"])
    out = work / "rerender"
    res = render_clip(src, words, cs, cs + m["end"] - m["start"], "", presets, settings, out, cid, out,
                      variant=variant, preset_name=settings["editing_preset"])
    print(json.dumps(res, indent=1))
    print("QC:", qc.check(res["file"], max_dur=settings["final_max_seconds"] + 10))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
