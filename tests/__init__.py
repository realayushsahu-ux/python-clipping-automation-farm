"""Tests log to logs/tests.log, never into the real farm.log."""
import os

os.environ["FARM_LOG_FILE"] = "tests.log"
