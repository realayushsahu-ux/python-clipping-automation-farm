"""Is the internet there? Used so a broken connection makes the farm WAIT instead of failing.

online()       quick check (a TCP connection to Google / Cloudflare DNS, 3 s)
wait_online()  blocks until the internet is back (checks every 30 s); data/offline.flag exists meanwhile,
               so the farm window can say "No internet" and "Internet is back"."""

import socket
import time

from core.logger import get_logger
from core.paths import DATA_DIR

log = get_logger("net")
OFFLINE = DATA_DIR / "offline.flag"
HOSTS = (("8.8.8.8", 53), ("1.1.1.1", 53), ("www.google.com", 443))


def online(timeout=3) -> bool:
    for host, port in HOSTS:
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def wait_online(max_hours=48, every=30) -> bool:
    """True once online (straight away if it already is). Nothing is counted as a failed try meanwhile."""
    if online():
        return True
    log.warning("no internet: waiting for it to come back")
    try:
        OFFLINE.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
    except OSError:
        pass
    t0 = time.time()
    try:
        while time.time() - t0 < max_hours * 3600:
            time.sleep(every)
            if online():
                log.info(f"internet is back after {(time.time() - t0) / 60:.0f} min")
                return True
        return False
    finally:
        OFFLINE.unlink(missing_ok=True)
