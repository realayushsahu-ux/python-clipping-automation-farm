# Running the farm by itself (Phase 7)

```powershell
.venv\Scripts\python.exe main.py autostart on      # once
```

That creates the Windows Task Scheduler task **ClippingFarm** (Task Scheduler → Task Scheduler Library):

* starts the farm 1 minute after you log on to Windows
* **wakes the PC 10 minutes before every posting time** (from the Settings tab `posting_times`;
  change the times in the Sheet and the wake-ups follow by themselves within a minute)
* restarts the farm if it crashes (a supervisor runs `main.py watch` and starts it again)

While it runs, the farm syncs the Sheet, makes clips from new videos, and posts at
**10:00 · 18:00 · 20:00 · 22:00 · 23:30** (India time) on YouTube and Instagram at the same time.
A time with no clip is skipped. If the PC was off at a post time, that clip moves to the next free time.

| Command | What it does |
|---|---|
| `main.py status` | first line: running or not, next post |
| `main.py stop` | stops the farm (after the current video / upload) |
| `main.py start` | starts it again in the background |
| `main.py autostart off` | removes the Windows task (farm keeps running until `stop`) |
| `main.py watch` | run it in this window instead (Ctrl+C); refuses if it is already running |

Settings tab:
* `posting_enabled` – no = nothing is published
* `keep_awake` – `posting` (default): the PC is not allowed to sleep from 15 min before a post until it is done; `always`; `off`
* `wake_pc_for_posting` – yes: Windows wakes the PC before each posting time

What it can't do:
* post while the PC is **shut down**, or a **laptop with the lid closed** that ignores wake timers – keep it plugged in and sleeping, not off
* start before you log on after a restart (Windows needs your sign-in; the task runs at logon)
* Windows power setting "Allow wake timers" must stay **Enable** (it is, on this PC)

Logs: `logs\farm.log` (everything), `logs\watch_console.log` (raw output of the background farm).
