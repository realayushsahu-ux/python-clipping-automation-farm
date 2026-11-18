"""One-time move to the simple folder layout (safe to run again; moves, never deletes media).

  storage/input, processing, sources   → storage/_system/...
  storage/failed                       → storage/_system/qc_failed
  storage/posted_reels                 → storage/posted_clips
  ready_to_post/<CLIP>/*.mp4           → clips_generated/ (+ same file linked back into ready_to_post/<CLIP>/)
  caption.txt                          → post_info.txt (with post time)

Run:  .venv\\Scripts\\python.exe -m scripts.migrate_storage [--reject CLIP0001,CLIP0002]
"""

import os
import shutil
import sys

from config.settings import settings
from core.db import get_db
from core.outputs import _link_or_copy, write_post_info
from core.paths import FAILED, GENERATED, INPUT, POSTED, PROCESSING, READY, SOURCES, STORAGE, ensure_dirs
from posting.slots import assign_post_times


def _move_dir(old, new):
    if old.exists() and old != new:
        new.parent.mkdir(parents=True, exist_ok=True)
        if not new.exists():
            shutil.move(str(old), str(new))
            print(f"moved {old.name} → {new.relative_to(STORAGE)}")
        else:
            for child in old.iterdir():
                if not (new / child.name).exists():
                    shutil.move(str(child), str(new / child.name))
            if not any(old.iterdir()):
                old.rmdir()


def main():
    for old, new in ((STORAGE / "input", INPUT), (STORAGE / "processing", PROCESSING),
                     (STORAGE / "sources", SOURCES), (STORAGE / "failed", FAILED),
                     (STORAGE / "posted_reels", POSTED)):
        _move_dir(old, new)
    ensure_dirs()
    db = get_db()

    reject = []
    if "--reject" in sys.argv:
        reject = [x.strip().upper() for x in sys.argv[sys.argv.index("--reject") + 1].split(",") if x.strip()]

    for folder in sorted(READY.glob("CLIP*")):
        cid = folder.name
        for f in folder.glob("*.mp4"):
            gen = GENERATED / f.name
            if not gen.exists():
                shutil.move(str(f), str(gen))
            if cid in reject:
                f.unlink(missing_ok=True)
            else:
                _link_or_copy(gen, folder / f.name)
        c = db.get_clip(cid)
        if cid in reject:
            if c and c["status"] in ("ready", "approved"):
                db.update_clip(cid, status="rejected", folder="", file=str(GENERATED / f"{cid}.mp4"))
                db.event("info", cid, "migrate", "rejected: offline test version, replaced by the AI clips")
            (PROCESSING / "rejected").mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(PROCESSING / "rejected" / cid))
            print(f"{cid}: rejected (video kept in clips_generated)")
            continue
        if c:
            nm = folder / f"{cid}_NOMUSIC.mp4"
            db.update_clip(cid, file=str(folder / f"{cid}.mp4"), nomusic_file=str(nm) if nm.exists() else "",
                           folder=str(folder))
    times = assign_post_times(settings)
    for folder in sorted(READY.glob("CLIP*")):
        write_post_info(folder.name)
    for cid, t in sorted(times.items(), key=lambda x: x[1]):
        print(f"{cid}: post at {t}")
    print("done. Folders:", ", ".join(p.name for p in sorted(STORAGE.iterdir()) if p.is_dir()))


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
