# Python Clipping Automation Farm

**An autonomous short-form video studio for gaming content.** Paste a gameplay or stream link into a Google
Sheet and the farm downloads it, finds the moments worth watching, edits vertical clips with captions,
hooks and an AI narrator, writes titles and hashtags, and publishes them to **YouTube Shorts** and
**Instagram Reels** on a schedule through the official APIs, then measures the results and learns from them.
It runs 24/7 on one Windows PC, starts with Windows, and needs no human between "paste link" and "posted".

### What it does
- **Moment picking:** Whisper transcript + loudness / chat-spike signals + an LLM editor that scores standalone moments
- **Editing:** 9:16 smart crop (face tracking for streamer layouts), cold open, punch-ins, colour grade, animated captions, music ducking, two-pass -14 LUFS loudness, quality check
- **AI narrator:** a free local voice (Kokoro) adds a spoken intro and an on-screen closing comment, so each clip adds original commentary
- **Packaging:** scroll-stopping first words, titles checked against real top-performing Shorts, 3-5 relevant hashtags, any game detected automatically
- **Publishing:** YouTube Data API + Instagram Graph API, posting slots, missed-slot recovery, never posts the same clip twice, optional manual approval
- **Growth loop:** views of every post, winners → rules the AI follows, daily trends finder (outliers vs. channel median), weekly audit with ONE fix
- **Control panel:** Google Sheets (paste links, approve, see the schedule, performance and trends)
- **Ops:** Windows Task Scheduler autostart + wake-to-post, supervisor with auto-restart, single-instance lock, 11 test suites, secret-scanning pre-commit hook

**Stack:** Python 3.13, FFmpeg, OpenCV, faster-whisper, yt-dlp, kokoro-onnx, OpenAI (gpt-5-mini) or Gemini, gspread, Google / Meta official APIs, SQLite.

### How it was built
Designed, specified, tested and operated by **Ayush** ([@realayushsahu-ux](https://github.com/realayushsahu-ux)):
product idea, the phase-by-phase roadmap, every rule the clips must follow, the account setups (Google Cloud,
YouTube, Meta/Instagram), live testing on real channels and the decisions after each result. The code was
written in pair-programming with **Claude (Anthropic)** as the AI engineer, which is why commits carry a
`Co-Authored-By: Claude` line. At runtime the farm does **not** use Claude.

### Responsible use
Clip only content you own or have permission to use (your own gameplay is best), credit creators, use
licensed music only, and follow YouTube's and Instagram's policies. The farm posts only through the
official APIs and never claims views or virality it does not have. You are responsible for what you publish.

### Licence
MIT, see [LICENSE](LICENSE). Third-party parts keep their own licences (`growth/yt_skills/LICENSE`, `assets/LICENSES.md`).

---

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
| 9 | GitHub repo; `.env`, `secrets/`, data, videos, models never committed; pre-commit guard `scripts/check_secrets.py` blocks any key/token/ID | ✅ live |

## GitHub (private) and secrets
- Keys, tokens and IDs live only in `.env` and `secrets/` (both git-ignored). `.env.example` shows the names.
- Every commit runs `scripts/check_secrets.py`: it blocks the commit if a real value from `.env` / `secrets/`
  or a key shape (sk-..., AIza..., EAA..., private key) is in a staged file.
- After cloning on a new PC, switch the guard on once:  `git config core.hooksPath .githooks`
- Check everything by hand:  `.venv\Scripts\python.exe scripts\check_secrets.py --all`
- Commits use your GitHub noreply e-mail (set for this repo), so your private e-mail stays hidden.

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
