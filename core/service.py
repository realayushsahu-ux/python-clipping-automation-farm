"""Phase 7: keep the farm running by itself on Windows.

  main.py service        supervisor: runs `main.py watch` and starts it again if it stops/crashes
  main.py start | stop   start the farm in the background / stop it (also stops the supervisor)
  main.py autostart on|off|status
                         Windows Task Scheduler task "ClippingFarm": starts the supervisor when you
                         log on, and WAKES the PC a few minutes before every posting time

Pieces used by `watch`:
  * single instance: data/watch.lock (a second watch exits straight away)
  * heartbeat: data/heartbeat.json every 30 s (main.py status / doctor show it)
  * keep awake: Windows is told not to sleep from `keep_awake_minutes` before a post until it is done
  * stop flag: data/stop.flag (main.py stop) → watch and supervisor end cleanly
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import get_logger
from core.paths import DATA_DIR, LOGS_DIR, ROOT

log = get_logger("service")
TASK = "ClippingFarm"
LOCK = DATA_DIR / "watch.lock"
SUP_LOCK = DATA_DIR / "service.lock"
HEARTBEAT = DATA_DIR / "heartbeat.json"
STOP = DATA_DIR / "stop.flag"
PAUSED = DATA_DIR / "paused.flag"   # main.py stop: stays off (no auto start) until main.py start / the shortcut
GUARD_TASK = "ClippingFarmGuard"
GUARD_MINUTES = 10                 # Windows checks this often that the farm runs (window closed → back by itself)
INSTALLED = DATA_DIR / "autostart.json"
RELOAD = 4                     # exit code of `watch` when the farm's code changed: supervisor starts it again at once
CODE_SKIP = {".venv", "venv", ".git", "__pycache__", "data", "logs", "storage", "models", "tests", "secrets"}


def code_version() -> float:
    """Newest change time of the farm's own .py files. When it changes, `watch` finishes what it is doing
    and restarts itself so the new code is used (no one has to restart the farm by hand)."""
    newest = 0.0
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in CODE_SKIP and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(folder, f)))
                except OSError:
                    pass
    return newest


# ------------------------------------------------------------------------------ single instance
class Lock:
    """Exclusive lock on a file while the process lives (released by Windows if it crashes)."""

    def __init__(self, path: Path):
        self.path, self.fh = Path(path), None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self):
        if self.fh:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.fh.close()
            self.fh = None


def is_locked(path: Path) -> bool:
    lk = Lock(path)
    if lk.acquire():
        lk.release()
        return False
    return True


# ------------------------------------------------------------------------------ heartbeat
def beat(**info):
    data = {"pid": os.getpid(), "time": datetime.now().isoformat(timespec="seconds"), **info}
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(HEARTBEAT)


def farm_state() -> dict:
    """{"running": bool, "last": iso time or None, "age_s": seconds, ...heartbeat fields}"""
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hb = {}
    age = None
    if hb.get("time"):
        age = (datetime.now() - datetime.fromisoformat(hb["time"])).total_seconds()
    return {**hb, "running": is_locked(LOCK), "age_s": age,
            "supervisor": is_locked(SUP_LOCK), "last": hb.get("time")}


def describe() -> str:
    s = farm_state()
    if not s["running"]:
        return "farm is NOT running (main.py start)"
    txt = f"farm running (pid {s.get('pid')}, last check {int(s['age_s'] or 0)} s ago"
    txt += ", auto-restart on)" if s["supervisor"] else ", no auto-restart: started by hand)"
    if s.get("next_post"):
        txt += f"; next post {s['next_post']}"
    if s.get("awake"):
        txt += "; keeping the PC awake"
    return txt


# ------------------------------------------------------------------------------ keep awake
def next_post_time(settings, posts):
    """Earliest time a post will go out: scheduled_at, or next_try of a failed one ('YYYY-MM-DD HH:MM')."""
    times = [p["scheduled_at"] for p in posts if p["status"] == "scheduled" and p.get("scheduled_at")]
    times += [p["next_try"][:16].replace("T", " ") for p in posts if p["status"] == "failed" and p.get("next_try")]
    return min(times) if times else None


def should_stay_awake(settings, posts, now: datetime) -> bool:
    """True from keep_awake_minutes before the next post until nothing is due or uploading."""
    mode = str(settings.get("keep_awake", "posting")).lower()
    if mode in ("always", "yes"):
        return True
    if mode in ("off", "no") or not settings.get("posting_enabled"):
        return False
    if any(p["status"] == "publishing" for p in posts):
        return True
    nxt = next_post_time(settings, posts)
    if not nxt:
        return False
    before = timedelta(minutes=int(settings.get("keep_awake_minutes", 15)))
    return now.strftime("%Y-%m-%d %H:%M") >= (datetime.strptime(nxt, "%Y-%m-%d %H:%M") - before).strftime(
        "%Y-%m-%d %H:%M")


_awake = False


def set_awake(on: bool):
    """Tell Windows not to sleep (call from the same thread each time; ES_CONTINUOUS is per thread)."""
    global _awake
    if os.name != "nt" or on == _awake:
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE = 0x80000000, 0x00000001, 0x00000040
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_AWAYMODE if on else 0)
    if not ctypes.windll.kernel32.SetThreadExecutionState(flags) and on:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    _awake = on
    log.info("keeping the PC awake for posting" if on else "PC may sleep again")


# ------------------------------------------------------------------------------ supervisor
def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def python_console() -> str:
    exe = Path(sys.executable)
    c = exe.with_name("python.exe")
    return str(c if c.exists() else exe)


def farm_exe(settings) -> str:
    """farm_window = yes: the farm runs in its own console window (like any script you start by hand)."""
    return python_console() if settings.get("farm_window", True) else pythonw()


def _has_console() -> bool:
    try:
        return sys.stdout is not None and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _watch(out_path: Path, visible: bool) -> int:
    """Run main.py watch once. In a window its lines show there AND go to watch_console.log."""
    cmd = [sys.executable, str(ROOT / "main.py"), "watch"]
    with open(out_path, "a", encoding="utf-8") as out:
        if not visible:
            return subprocess.call(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        p = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             text=True, encoding="utf-8", errors="replace", bufsize=1)
        from core.narrator import SAY_MARK
        for line in p.stdout:
            if line.startswith(SAY_MARK):          # only the plain-English status lines go to the window
                try:
                    sys.stdout.write(line[len(SAY_MARK):])
                    sys.stdout.flush()
                except (OSError, ValueError):
                    pass
            out.write(line)
            out.flush()
        return p.wait()


def supervise():
    """Run `main.py watch` forever; restart it 30 s after it stops (unless main.py stop was used)."""
    if PAUSED.exists():                    # you stopped it on purpose: the 10-minute check leaves it off
        print("The farm is stopped (main.py stop). Start it with the 'Start Clipping Farm' desktop shortcut.")
        return 0
    lock = Lock(SUP_LOCK)
    if not lock.acquire():
        print("the farm supervisor is already running")
        return 0
    STOP.unlink(missing_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fails, started = 0, 0.0
    visible = _has_console()
    if visible:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("Clipping Farm (do not close: closing stops posting)")
        print("Clipping Farm is running by itself (no Claude needed). It starts with Windows, wakes the PC\n"
              "for every post and restarts if it crashes. Close this window or run  main.py stop  to stop it.\n",
              flush=True)
    log.info("supervisor started")
    try:
        while not STOP.exists():
            started = time.time()
            from core.safety import check_db, rotate
            rotate(LOGS_DIR / "watch_console.log")          # the window's log never grows past 10 MB
            try:
                check_db()                                  # damaged by a power cut → good backup put back
            except Exception as exc:  # noqa: BLE001
                log.error(f"database check: {exc}")
            rc = _watch(LOGS_DIR / "watch_console.log", visible)
            if STOP.exists():
                break
            if rc == RELOAD:                   # the farm's code was updated: start the new version straight away
                log.info("watch restarted to load updated code")
                if visible:
                    print(f"{datetime.now().strftime('%I:%M %p')}  The farm was updated. Restarting it with the "
                          "new version...", flush=True)
                fails = 0
                continue
            if rc == 3:                        # another watch holds the lock (started by hand)
                time.sleep(60)
                continue
            fails = fails + 1 if time.time() - started < 300 else 1
            wait = min(30 * fails, 600)
            log.error(f"watch stopped (exit {rc}); starting it again in {wait} s")
            if visible:
                print(f"{datetime.now().strftime('%I:%M %p')}  The farm hit a problem and stopped. "
                      f"Starting it again in {wait} seconds (details: logs/watch_console.log).", flush=True)
            for _ in range(wait):
                if STOP.exists():
                    break
                time.sleep(1)
    finally:
        lock.release()
        log.info("supervisor stopped")
    return 0


LOW_DISK = DATA_DIR / "lowdisk.flag"


def disk_check(min_d_gb=5.0, min_c_gb=0.5) -> str:
    """'' when there is room, else what is too full. New videos wait while a drive is too full (posting
    goes on), because a full disk in the middle of an edit breaks files. data/lowdisk.flag tells the window."""
    import shutil
    from core.paths import STORAGE
    low = []
    for path, need in ((STORAGE, min_d_gb), (Path(os.environ.get("SystemDrive", "C:") + "\\"), min_c_gb)):
        try:
            free = shutil.disk_usage(path).free / 1e9
        except OSError:
            continue
        if free < need:
            low.append(f"{str(path)[:2]} has {free:.1f} GB free (needs {need:g} GB)")
    msg = "; ".join(low)
    try:
        if msg:
            LOW_DISK.write_text(msg, encoding="utf-8")
        else:
            LOW_DISK.unlink(missing_ok=True)
    except OSError:
        pass
    return msg


def clean_tmp(days=2) -> int:
    """Delete the farm's own temporary files older than `days` (storage/_system/tmp only)."""
    from core.paths import SYSTEM
    n, limit = 0, time.time() - days * 86400
    for p in (SYSTEM / "tmp").glob("**/*"):
        try:
            if p.is_file() and p.stat().st_mtime < limit:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


SETTINGS_FILES = ["config/settings.json", "config/editing_presets.json", "config/approved_format.json",
                  "config/voice.md", "config/winners.md", "config/trends.md", ".env"]


def backup_settings(keep=14) -> Path:
    """Once a day: settings, editing presets, voice, .env and secrets/ (the logins) zipped into
    data/backups/settings_<day>.zip, last `keep` days. data/ is git-ignored: the keys never reach GitHub,
    and everything stays on D:. To restore: unzip it into the farm folder."""
    import zipfile
    folder = DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"settings_{datetime.now():%Y-%m-%d}.zip"
    if not out.exists():
        tmp = out.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in SETTINGS_FILES:
                if (ROOT / rel).is_file():
                    z.write(ROOT / rel, rel)
            for p in (ROOT / "secrets").rglob("*"):
                if p.is_file():
                    z.write(p, str(p.relative_to(ROOT)))
        os.replace(tmp, out)
        log.info(f"settings and logins backed up to {out.name}")
    for old in sorted(folder.glob("settings_*.zip"))[:-keep]:
        old.unlink(missing_ok=True)
    return out


def backup_db(keep=7) -> Path:
    """A copy of the farm's database per day in data/backups (the last `keep` days). SQLite's own backup,
    so the copy is complete even while the farm is writing."""
    import sqlite3
    from core.paths import DB_FILE
    folder = DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"state_{datetime.now():%Y-%m-%d}.db"
    if not out.exists() and DB_FILE.exists():
        tmp = out.with_suffix(".tmp")
        src, dst = sqlite3.connect(DB_FILE), sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp, out)
        log.info(f"database backed up to {out.name}")
    for old in sorted(folder.glob("state_*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return out


def start_background(settings=None) -> str:
    if farm_state()["running"] or is_locked(SUP_LOCK):
        PAUSED.unlink(missing_ok=True)
        return "already running: " + describe()
    STOP.unlink(missing_ok=True)
    PAUSED.unlink(missing_ok=True)
    if autostart_installed():
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, text=True)
        if r.returncode == 0:
            return "started (Task Scheduler task ClippingFarm)"
    window = bool((settings or {}).get("farm_window", True))
    flags = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if window else getattr(subprocess, "DETACHED_PROCESS", 0))         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([python_console() if window else pythonw(), str(ROOT / "main.py"), "service"], cwd=str(ROOT),
                     creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)
    return "started in the background"


def stop_farm(timeout=120) -> str:
    PAUSED.write_text(datetime.now().isoformat(), encoding="utf-8")    # stays off until you start it
    if not farm_state()["running"] and not is_locked(SUP_LOCK):
        STOP.unlink(missing_ok=True)
        return "the farm was not running"
    STOP.write_text(datetime.now().isoformat(), encoding="utf-8")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not farm_state()["running"] and not is_locked(SUP_LOCK):
            return "stopped"
        time.sleep(2)
    return f"asked to stop; still busy after {timeout} s (it stops after the current video/upload)"


# ------------------------------------------------------------------------------ Task Scheduler
def wake_times(settings) -> list:
    """HH:MM a few minutes before every posting time (the PC is woken then)."""
    m = int(settings.get("wake_minutes_before", 10))
    out = []
    for t in sorted(settings.get("posting_times") or []):
        hh, mm = map(int, t.split(":"))
        x = (datetime(2000, 1, 2, hh, mm) - timedelta(minutes=m)).strftime("%H:%M")
        if x not in out:
            out.append(x)
    return out


def _xml_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def task_xml(settings, user: str, guard: bool = False) -> str:
    """guard=False: task ClippingFarm (logon + wake-ups before posts). guard=True: task ClippingFarmGuard,
    every GUARD_MINUTES all day, never wakes the PC: the window was closed / it crashed → back by itself.
    (A repetition on the logon trigger only starts after the next logon: found 2026-11-21.)"""
    wake = bool(settings.get("wake_pc_for_posting", True)) and not guard
    cal = "".join(
        f"""
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{t}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>""" for t in wake_times(settings))
    if guard:
        cal = f"""
    <CalendarTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition><Interval>PT{GUARD_MINUTES}M</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>"""
    logon = "" if guard else f"""
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{_xml_escape(user)}</UserId>
      <Delay>PT1M</Delay>
    </LogonTrigger>"""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Python Clipping Automation Farm: runs main.py service (watch + auto posting). Made by main.py autostart on.</Description>
  </RegistrationInfo>
  <Triggers>{logon}{cal}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{_xml_escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>{'true' if wake else 'false'}</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_escape(pythonw() if guard else farm_exe(settings))}</Command>
      <Arguments>"{_xml_escape(ROOT / 'main.py')}" {'guard' if guard else 'service'}</Arguments>
      <WorkingDirectory>{_xml_escape(ROOT)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def guard() -> str:
    """Run by task ClippingFarmGuard every few minutes, silently (pythonw): if the farm is not running and
    you did not stop it on purpose (main.py stop / Stop shortcut), start it again."""
    if PAUSED.exists():
        return "stopped on purpose: left off"
    if is_locked(SUP_LOCK) or farm_state()["running"]:
        return "running"
    log.warning("guard: the farm was not running (window closed or crashed); starting it again")
    return start_background(None)


def _user() -> str:
    dom, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{name}" if dom else name


def install_autostart(settings) -> str:
    if os.name != "nt":
        raise RuntimeError("autostart is for Windows")
    xml = DATA_DIR / "ClippingFarm_task.xml"
    xml.write_text(task_xml(settings, _user()), encoding="utf-16")
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/F"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    gxml = DATA_DIR / "ClippingFarmGuard_task.xml"
    gxml.write_text(task_xml(settings, _user(), guard=True), encoding="utf-16")
    r = subprocess.run(["schtasks", "/Create", "/TN", GUARD_TASK, "/XML", str(gxml), "/F"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    INSTALLED.write_text(json.dumps({"wake_times": wake_times(settings),
                                     "wake": bool(settings.get("wake_pc_for_posting", True)),
                                     "window": bool(settings.get("farm_window", True)),
                                     "guard": GUARD_MINUTES,
                                     "installed": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    try:
        make_shortcuts()
    except Exception as exc:  # noqa: BLE001 - shortcuts are a convenience
        log.warning(f"desktop shortcuts not made: {exc}")
    return (f"task {TASK} installed: starts at logon, checks every {GUARD_MINUTES} min that it runs, "
            f"wakes the PC at {', '.join(wake_times(settings))}")


def make_shortcuts() -> list:
    """Shortcuts 'Start Clipping Farm' / 'Stop Clipping Farm' (double-click instead of typing), on the
    Desktop and in the farm's own folder (D:\\python-clipping-automation-farm)."""
    if os.name != "nt":
        return []
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    py = py if py.exists() else Path(sys.executable)
    made = []
    places = ("[Environment]::GetFolderPath('Desktop')", "'" + str(ROOT).replace("'", "''") + "'")
    jobs = (("Start Clipping Farm", "start"), ("Stop Clipping Farm", "stop"), ("Reconnect YouTube", "auth reconnect"))
    for name, cmd, where in [(n, c, w) for n, c in jobs for w in places]:
        wait = "pause" if cmd.startswith("auth") else "timeout /t 8"      # read what it says before it closes
        args = f'/c ""{py}" "{ROOT / "main.py"}" {cmd} & {wait}"'
        ps = (f"$d={where};"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '" + name + ".lnk'));"
              "$s.TargetPath=\"$env:ComSpec\";"
              "$s.Arguments='" + args.replace("'", "''") + "';"
              "$s.WorkingDirectory='" + str(ROOT).replace("'", "''") + "';"
              "$s.IconLocation=\"$env:SystemRoot\\System32\\shell32.dll," + {"start": "137", "stop": "131"}.get(cmd, "238") + "\";"
              "$s.Save()")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])
        made.append(name)
    return made


def remove_autostart() -> str:
    subprocess.run(["schtasks", "/Delete", "/TN", GUARD_TASK, "/F"], capture_output=True, text=True)
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, text=True)
    INSTALLED.unlink(missing_ok=True)
    return "task removed" if r.returncode == 0 else "task was not installed"


def autostart_installed() -> bool:
    if os.name != "nt":
        return False
    return subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True).returncode == 0


def refresh_autostart(settings):
    """Posting times changed in the Sheet → move the wake-up times too (only if autostart is on)."""
    try:
        old = json.loads(INSTALLED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    want = {"wake_times": wake_times(settings), "wake": bool(settings.get("wake_pc_for_posting", True)),
            "window": bool(settings.get("farm_window", True)), "guard": GUARD_MINUTES}
    if all(old.get(k, True if k == "window" else None) == v for k, v in want.items()):
        return None
    try:
        msg = install_autostart(settings)
        log.info(f"posting times changed: {msg}")
        return msg
    except Exception as exc:  # noqa: BLE001
        log.error(f"could not update the autostart task: {exc}")
        return None
