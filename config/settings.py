"""Loads the farm's configuration.

Order (later wins):  config/settings.json  →  Settings tab in Google Sheets (Phase 4)
Secrets (API keys, tokens) come ONLY from environment variables / the .env file.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from core.paths import CONFIG_DIR, MODELS_DIR, ROOT

load_dotenv(ROOT / ".env")

# Keep model downloads on this drive, not in C:\Users\...\.cache
os.environ.setdefault("HF_HOME", os.environ.get("HF_HOME") or str(MODELS_DIR / "hf"))
if not os.environ["HF_HOME"]:
    os.environ["HF_HOME"] = str(MODELS_DIR / "hf")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("PYTHONUTF8", "1")


def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


class Settings(dict):
    """dict with attribute access: settings.clips_per_video or settings['clips_per_video']."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def apply_overrides(self, overrides: dict):
        """Values from the Google Sheets Settings tab (strings) converted to the right type."""
        for key, raw in overrides.items():
            if key not in self or raw in (None, ""):
                continue
            cur = self[key]
            val = str(raw).strip()
            try:
                if isinstance(cur, bool):
                    self[key] = val.lower() in ("yes", "true", "1", "on")
                elif isinstance(cur, int):
                    self[key] = int(float(val))
                elif isinstance(cur, float):
                    self[key] = float(val)
                elif isinstance(cur, list):
                    self[key] = [x.strip() for x in val.split(",") if x.strip()]
                else:
                    self[key] = val
            except ValueError:
                continue


def clip_max_for(settings, kind) -> float:
    """Max clip length for this kind of moment: funny/story moments may run longer (up to 1:45)."""
    if kind in (settings.get("long_clip_kinds") or []):
        return float(settings.get("long_clip_max_seconds", settings["clip_max_seconds"]))
    return float(settings["clip_max_seconds"])


def load_settings() -> Settings:
    return Settings(_read_json(CONFIG_DIR / "settings.json"))


def load_presets() -> dict:
    return _read_json(CONFIG_DIR / "editing_presets.json")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


settings = load_settings()
