"""Retries with growing waits. Errors that retrying cannot fix (bad link, private video)
raise PermanentError and fail at once."""

import time

from core.logger import get_logger

log = get_logger("retry")


class PermanentError(Exception):
    """Retrying will not help (invalid link, removed video, bad credentials...)."""


class FarmError(Exception):
    """A stage failed after its retries."""


def retry(fn, *args, attempts=3, waits=(10, 60, 300), what="task", **kwargs):
    last = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except PermanentError:
            raise
        except Exception as exc:  # noqa: BLE001 - every other error is worth another try
            last = exc
            if i + 1 < attempts:
                wait = waits[min(i, len(waits) - 1)]
                log.warning(f"{what} failed ({str(exc)[:160]}); retry {i + 2}/{attempts} in {wait}s")
                time.sleep(wait)
    raise FarmError(f"{what} failed after {attempts} attempts: {last}") from last
