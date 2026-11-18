"""Every folder the farm uses, in one place. Everything lives inside the project (on D:)
unless FARM_STORAGE_DIR / HF_HOME in .env point somewhere else."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
MODELS_DIR = ROOT / "models"
ASSETS_DIR = ROOT / "assets"
SECRETS_DIR = ROOT / "secrets"


def storage_root() -> Path:
    custom = os.environ.get("FARM_STORAGE_DIR", "").strip()
    return Path(custom) if custom else ROOT / "storage"


STORAGE = storage_root()
# --- the 3 folders you use ---
GENERATED = STORAGE / "clips_generated"      # EVERY edited clip, flat: CLIP0004.mp4, CLIP0004_NOMUSIC.mp4
READY = STORAGE / "ready_to_post"            # <CLIP_ID>/ clip + post_info.txt (post time, titles, caption, #)
POSTED = STORAGE / "posted_clips"            # <CLIP_ID>/ moved here after it is published
# --- farm's own working files (you never need to open these) ---
SYSTEM = STORAGE / "_system"
INPUT = SYSTEM / "input"
PROCESSING = SYSTEM / "processing"           # per-video working files (cached stages)
SOURCES = SYSTEM / "sources"                 # source audio + clip sections (never auto-deleted)
FAILED = SYSTEM / "qc_failed"                # post info of clips that failed the quality check
DB_FILE = DATA_DIR / "state.db"
YUNET_MODEL = MODELS_DIR / "yunet.onnx"


def ensure_dirs():
    for d in (DATA_DIR, LOGS_DIR, MODELS_DIR, SECRETS_DIR, GENERATED, INPUT, PROCESSING, SOURCES, READY, POSTED,
              FAILED, ASSETS_DIR / "music", ASSETS_DIR / "sfx", ASSETS_DIR / "fonts"):
        d.mkdir(parents=True, exist_ok=True)


def video_workdir(video_id: str) -> Path:
    d = PROCESSING / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def video_sourcedir(video_id: str) -> Path:
    d = SOURCES / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d
