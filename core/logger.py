"""Logging: readable lines in the console, full detail in logs/farm.log (rotated at 5 MB)."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from core.paths import LOGS_DIR

_CONFIGURED = False


def get_logger(name: str = "farm") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger("farm")
        root.setLevel(logging.DEBUG)
        fh = RotatingFileHandler(LOGS_DIR / os.environ.get("FARM_LOG_FILE", "farm.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        root.addHandler(fh)
        root.addHandler(ch)
        _CONFIGURED = True
    return logging.getLogger(name if name.startswith("farm") else f"farm.{name}")
