# Python Clipping Automation Farm

Turns long gaming videos and streams into ready-to-post vertical clips (Instagram Reels,
YouTube Shorts): download → transcript → best moments → professional edit → hooks, captions,
hashtags → (soon) Google Sheets control panel, scheduling and official-API posting.

Runs fully on Python + FFmpeg + open-source models. AI text uses OpenAI (or Gemini) if you
add a key; with no key it still works in offline mode. **No Claude needed at runtime.**

## Quick start (Windows)

```powershell
cd D:\python-clipping-automation-farm
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # first time only
.venv\Scripts\python.exe main.py doctor                        # check everything
.venv\Scripts\python.exe main.py process "https://www.youtube.com/watch?v=VIDEO_ID"
```

Your 3 folders in `storage\`:

| Folder | What is in it |
|---|---|
| `clips_generated\` | **every** edited clip, one flat folder (`CLIP0004.mp4`, `CLIP0004_NOMUSIC.mp4`, `…_QC_FAILED.mp4`) |
| `ready_to_post\CLIP0004\` | clips that passed the quality check: the video + `post_info.txt` (post time, YouTube title + description, Instagram caption, hashtags, pin comment) |
| `posted_clips\CLIP0004\` | the same folder, moved here after it is published on every platform |

`storage\_system\` holds the farm's own working files; you never need to open it.

Clip length (`config\settings.json`): 30-60 s (`clip_min_seconds`, `clip_max_seconds`); funny/story moments
may run up to 1:45 (`long_clip_kinds`, `long_clip_max_seconds`). Post times come from `posting_times`
(best clip first, one clip per slot).

More: [docs/SETUP.md](docs/SETUP.md) (how to run and test) · [docs/DESIGN.md](docs/DESIGN.md) (architecture, Sheets, roadmap)

## Status

| Phase | Part | Status |
|---|---|---|
| 0 | Design, skeleton, config, database, logging | ✅ |
| 1 | Core pipeline without Claude (download, transcript, signals, moment picking, editor, QC) | ✅ testing |
| 2 | OpenAI / Gemini picking + packaging, channel voice (`config/voice.md`), YouTube-skills quality gate (`ai/quality.py`) | ✅ testing |
| 3 | Asset library (SFX, LUTs, light-leak drops) in `assets/` | ✅ (music still to add) |
| 4 | Google Sheets control panel (`main.py sheets setup`, `main.py watch`, docs/SHEETS_SETUP.md) | ✅ code + offline test, needs your Google setup |
| 5 | Approval (automatic by default), posting queue, Posted Reels, Folder links (`posting/queue.py`, `core/clip_server.py`) | ✅ code + tests |
| 6 | Posting: YouTube Shorts + Instagram Reels, official APIs, once-only, retries (`posting/`, docs/POSTING_SETUP.md) | ✅ live: first clip posted on both 2026-11-18 |
| 7 | Runs by itself: auto-start with Windows, wakes the PC for post times, restarts after a crash (`core/service.py`, docs/RUNNING.md) | ✅ live |
| 8 | Growth: views of every post, winners → AI learns their patterns (`growth/analytics.py`, `growth/learn.py`, Sheet tab Performance, `main.py stats`); trends finder: Shorts that beat their own channel's median + fresh GTA 6 streams to clip (`growth/trends.py`, Sheet tab Trends, `main.py trends`); weekly channel audit: the ONE fix for the week from measured facts (`growth/channel_audit.py`, Sheet tab Audit, `main.py audit`) ; AI voice commentary on every clip (free local Kokoro voice, `editor/voiceover.py`), GTA-6-only + title/hashtag rules from our first posts (`docs/YOUTUBE_LESSONS.md`) | ✅ parts 1-3 live; comments, planner next |

## Useful commands

```powershell
.venv\Scripts\python.exe main.py watch                      # run the farm from the Google Sheet (in this window)
.venv\Scripts\python.exe main.py autostart on               # run it by itself, with Windows (docs/RUNNING.md)
.venv\Scripts\python.exe main.py start | stop | status      # background farm
.venv\Scripts\python.exe main.py stats                      # views, winners, what the AI learned
.venv\Scripts\python.exe main.py trends                     # this week's niche ideas + streams to clip (--cached = no search)
.venv\Scripts\python.exe main.py audit                      # this week's ONE fix (optional: main.py auth youtube-analytics)
.venv\Scripts\python.exe main.py process "URL" --redo       # re-run a finished video (old clips kept)
.venv\Scripts\python.exe main.py remake CLIP0006            # new hook/title + re-render one clip
.venv\Scripts\python.exe main.py posted CLIP0004            # you uploaded it yourself → posted_clips
.venv\Scripts\python.exe main.py auth youtube               # connect YouTube (once), also: auth instagram
.venv\Scripts\python.exe main.py publish CLIP0005           # publish one clip now (--platform youtube)
.venv\Scripts\python.exe main.py serve                      # only the Folder links (watch includes them)
```

Posting is OFF until Settings → `posting_enabled` = yes ([docs/POSTING_SETUP.md](docs/POSTING_SETUP.md)).

Approval: `manual_approval` = **no** (default) → every clip that passes QC is approved and scheduled;
type `no` in the Sheet to drop one. Set it to `yes` in the Settings tab to approve each clip yourself.
The **Folder** link in the Sheet opens the clip's folder and plays the video (works while `main.py watch` runs).

Tests: `python -m tests.test_db` (never-post-twice), `python -m tests.test_package` (AI quality gate,
`--live` for a real API call), `python -m tests.test_sheets` (Sheets sync, offline), `python -m tests.test_queue` (approval → posted), `python -m tests.test_publish` (posting, fake platforms), `python -m tests.test_service` (auto-start pieces), `python -m tests.test_growth` (stats + winners), `python -m tests.test_trends` (trends finder, fake YouTube), `python -m tests.test_audit` (channel audit), `python -m tests.test_topic` (title/hashtag rules), `python -m tests.test_voice` (narrator),
`python -m tests.rerender VIDEO_ID CLIP0001` (try new editing presets).
