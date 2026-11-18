"""Phase 7 test: single instance, heartbeat, keep-awake decision, wake times, Task Scheduler XML.
Nothing is installed in Windows and your running farm is not touched (temp folders).

Run:  .venv\\Scripts\\python.exe -m tests.test_service
"""

import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import core.service as sv
from config.settings import load_settings

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def main():
    tmp = Path(tempfile.mkdtemp())
    sv.LOCK, sv.SUP_LOCK, sv.HEARTBEAT, sv.STOP = tmp / "w.lock", tmp / "s.lock", tmp / "hb.json", tmp / "stop"
    st = load_settings()
    st["posting_times"] = ["10:00", "18:00", "20:00", "22:00", "23:30", "00:05"]

    # 1. only one farm at a time; the lock is free again after release
    a, b = sv.Lock(sv.LOCK), sv.Lock(sv.LOCK)
    assert a.acquire() and not b.acquire(), "second watch must not start"
    assert sv.farm_state()["running"]
    a.release()
    assert b.acquire() and (b.release() or True)
    assert not sv.farm_state()["running"] and "NOT running" in sv.describe()

    # 2. heartbeat
    sv.beat(next_post="2030-01-01 18:00", awake=False)
    s = sv.farm_state()
    assert s["last"] and s["age_s"] < 5 and s["next_post"] == "2030-01-01 18:00"

    # 3. keep awake: 15 min before a post, during an upload, never when posting is off
    st["posting_enabled"], st["keep_awake"], st["keep_awake_minutes"] = True, "posting", 15
    posts = [{"status": "scheduled", "scheduled_at": "2030-01-01 18:00"},
             {"status": "failed", "next_try": "2030-01-01T20:10:00"}]
    at = lambda hm: datetime.strptime(f"2030-01-01 {hm}", "%Y-%m-%d %H:%M")   # noqa: E731
    assert sv.next_post_time(st, posts) == "2030-01-01 18:00"
    assert not sv.should_stay_awake(st, posts, at("17:40"))
    assert sv.should_stay_awake(st, posts, at("17:45"))
    assert sv.should_stay_awake(st, [{"status": "publishing"}], at("03:00"))
    assert not sv.should_stay_awake(st, [], at("03:00")), "no clips: the PC may sleep"
    st["posting_enabled"] = False
    assert not sv.should_stay_awake(st, posts, at("17:59"))
    st["posting_enabled"] = True

    # 4. wake times = 10 min before each slot, past midnight handled
    assert sv.wake_times(st) == ["23:55", "09:50", "17:50", "19:50", "21:50", "23:20"], sv.wake_times(st)

    # 5. Task Scheduler XML: logon + one daily trigger per wake time, wakes the PC, restarts, no time limit
    root = ET.fromstring(sv.task_xml(st, "PC\\me").split("\n", 1)[1])
    trig = root.find("t:Triggers", NS)
    assert trig.find("t:LogonTrigger/t:UserId", NS).text == "PC\\me"
    starts = [e.text for e in trig.findall("t:CalendarTrigger/t:StartBoundary", NS)]
    assert starts == [f"2026-01-01T{t}:00" for t in sv.wake_times(st)]
    se = root.find("t:Settings", NS)
    assert se.find("t:WakeToRun", NS).text == "true"
    assert se.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert se.find("t:ExecutionTimeLimit", NS).text == "PT0S"
    assert se.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    ex = root.find("t:Actions/t:Exec", NS)
    assert ex.find("t:Command", NS).text.lower().endswith(("pythonw.exe", "python.exe"))
    assert ex.find("t:Arguments", NS).text.endswith("main.py\" service")
    st["wake_pc_for_posting"] = False
    assert "<WakeToRun>false</WakeToRun>" in sv.task_xml(st, "me")

    # 6. the refresh only reinstalls when the times really changed
    sv.INSTALLED = tmp / "autostart.json"
    assert sv.refresh_autostart(st) is None, "autostart not installed: nothing to refresh"
    print("all service tests passed")


if __name__ == "__main__":
    main()
