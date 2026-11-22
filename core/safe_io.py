"""Files that survive a power cut.

write_json()  writes to a temporary file first and swaps it in, so a file is either the old one or the
              new one, never half written (Windows can cut power in the middle of a write).
read_json()   a damaged file (half written before this existed) is moved aside and treated as missing,
              so the farm simply makes it again instead of stopping."""

import json
import os
from pathlib import Path

from core.logger import get_logger

log = get_logger("safe_io")


def write_json(path, obj, **kw):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, **kw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        bad = path.with_name(path.name + ".damaged")
        try:
            os.replace(path, bad)
        except OSError:
            pass
        log.warning(f"{path.name} was damaged ({str(exc)[:80]}); it will be made again")
        return default


def heal_folder(folder) -> int:
    """Move aside every damaged .json in a work folder (run once when the farm starts)."""
    n = 0
    for p in Path(folder).rglob("*.json"):
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            read_json(p)
            n += 1
    for p in Path(folder).rglob("*.json.tmp"):      # a write that never finished
        p.unlink(missing_ok=True)
    return n
