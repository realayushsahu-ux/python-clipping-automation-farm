"""Where finished clips go, in plain folders:

  storage/clips_generated/          EVERY edited clip, one flat folder
      CLIP0004.mp4  CLIP0004_NOMUSIC.mp4  CLIP0007_QC_FAILED.mp4 ...
  storage/ready_to_post/CLIP0004/   one folder per clip that passed the quality check
      CLIP0004.mp4  CLIP0004_NOMUSIC.mp4   (same files as in clips_generated, no extra disk space)
      post_info.txt                        post time, titles, captions, hashtags, pin comment
      post.json                            the same for the program (Sheets / posting)
  storage/posted_clips/CLIP0004/    the folder is moved here after it is published (Phase 6/7)
"""

import json
import os
import shutil
from pathlib import Path

from core.db import get_db
from core.logger import get_logger
from core.paths import FAILED, GENERATED, POSTED, READY

log = get_logger("outputs")


def _link_or_copy(src: Path, dst: Path):
    """Hard link (same file, no extra space on the same drive), copy if linking is not possible."""
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def store_clip(cid: str, video_file: str, nomusic_file: str | None, ok: bool, meta: dict) -> dict:
    """Move a fresh render into clips_generated and, if it passed QC, into ready_to_post/<cid>/."""
    GENERATED.mkdir(parents=True, exist_ok=True)
    tag = "" if ok else "_QC_FAILED"
    gen_main = GENERATED / f"{cid}{tag}.mp4"
    shutil.move(video_file, gen_main)
    gen_nom = None
    if nomusic_file:
        gen_nom = GENERATED / f"{cid}_NOMUSIC{tag}.mp4"
        shutil.move(nomusic_file, gen_nom)

    folder = (READY if ok else FAILED) / cid
    folder.mkdir(parents=True, exist_ok=True)
    main, nom = gen_main, gen_nom
    if ok:
        main = folder / f"{cid}.mp4"
        _link_or_copy(gen_main, main)
        if gen_nom:
            nom = folder / f"{cid}_NOMUSIC.mp4"
            _link_or_copy(gen_nom, nom)
    (folder / "post.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"file": str(main), "nomusic_file": str(nom or ""), "folder": str(folder), "generated": str(gen_main)}


def archive_old(cid: str, old_dir: Path, stamp: str):
    """Before a remake: move the clip's current files out of the way (kept, never deleted)."""
    old_dir.mkdir(parents=True, exist_ok=True)
    for f in GENERATED.glob(f"{cid}*.mp4"):
        shutil.move(str(f), str(old_dir / f"{stamp}_{f.name}"))
    for folder in (READY / cid, FAILED / cid):
        if folder.exists():
            shutil.move(str(folder), str(old_dir / f"{cid}_{stamp}"))


def write_post_info(cid: str):
    """(Re)write ready_to_post/<cid>/post_info.txt from post.json + the planned post time."""
    db = get_db()
    c = db.get_clip(cid)
    folder = READY / cid
    if c and c.get("folder") and (Path(c["folder"]) / "post.json").exists():
        folder = Path(c["folder"])                     # ready_to_post/<cid> or posted_clips/<cid>
    pj = folder / "post.json"
    if not c or not pj.exists():
        return
    p = json.loads(pj.read_text(encoding="utf-8"))
    p["post_time"] = c.get("post_time")
    pj.write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    tags = " ".join(p.get("hashtags", []))
    q = p.get("qc") or {}
    r = p.get("render") or {}
    lines = [
        f"CLIP {cid}   ({p.get('creator') or p.get('channel') or ''})",
        "=" * 60,
        f"POST TIME:     {c.get('post_time') or 'not planned yet'}  ({p.get('timezone', 'Asia/Kolkata')})",
        f"PLATFORMS:     {', '.join(p.get('platforms') or ['instagram', 'youtube'])}",
        f"VIDEO:         {cid}.mp4  (with music)   |   {cid}_NOMUSIC.mp4  (add a trending sound in the app)",
        f"LENGTH:        {q.get('duration', r.get('duration', '?'))} s   loudness {q.get('lufs', '?')} LUFS   "
        f"score {p.get('score', '?')}   kind {p.get('kind', '?')}",
        f"ON-SCREEN HOOK: {p.get('hook') or '(none)'}",
        f"SOURCE:        {p.get('source_url', '')}  @ {p.get('source_time', '')}",
        "",
        "YOUTUBE SHORTS",
        "-" * 60,
        f"Title:\n{p.get('youtube_title', '')}",
        "",
        f"Description:\n{p.get('youtube_description', '')}\n{tags}",
        "",
        "INSTAGRAM REELS",
        "-" * 60,
        f"Caption:\n{p.get('instagram_caption', '')}\n.\n{tags}",
        "",
        "PIN THIS COMMENT (both platforms)",
        "-" * 60,
        p.get("pin_comment", ""),
    ]
    done = [x for x in db.posts("posted") if x["clip_id"] == cid]
    if done:
        lines += ["", "POSTED", "-" * 60]
        lines += [f"{x['platform']:<10} {(x.get('posted_at') or '')[:16].replace('T', ' ')}  {x.get('url') or '(by hand)'}"
                  for x in done]
    if p.get("search_queries"):
        lines += ["", "SEARCHES THIS SHORT SHOULD WIN", "-" * 60, *p["search_queries"]]
    if p.get("hook_options") or p.get("title_options"):
        lines += ["", "OTHER OPTIONS (score)", "-" * 60]
        lines += [f"hook  {h['score']:3d}  {h['hook']}" for h in (p.get("hook_options") or [])[1:]]
        lines += [f"title {t['score']:3d}  {t['title']}" for t in (p.get("title_options") or [])[1:]]
    (folder / "post_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    old = folder / "caption.txt"
    old.unlink(missing_ok=True)  # replaced by post_info.txt


def move_to_posted(cid: str) -> Path:
    """After publishing: ready_to_post/<cid>/ → posted_clips/<cid>/ (used by Phase 6/7)."""
    src, dst = READY / cid, POSTED / cid
    POSTED.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dst))
    get_db().update_clip(cid, folder=str(dst))
    return dst
