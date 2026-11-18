# Python Clipping Automation Farm: Design

Status: Phase 0 (design + skeleton). Last updated 2026-11-17.

This document is the single source of truth for how the farm works. Every module, sheet
column and status value used in the code is defined here.

---

## 1. Goal

Paste a video URL into Google Sheets → the farm downloads it, finds the best moments,
edits professional vertical clips, writes hooks / captions / hashtags, queues them,
publishes them on schedule to Instagram Reels and YouTube Shorts, and keeps a full history.

**Hard rule: no Claude at runtime.** Claude is only used to help *build* the project.
The finished farm runs on Python, FFmpeg, open-source models and the paid/free APIs you
configure (OpenAI by default, Gemini as an alternative). If no AI key is set at all, the
farm still runs: it picks moments with its own scoring and writes simple template captions.

---

## 2. Requirements analysis

| Area | Requirement | How it is met |
|---|---|---|
| Control panel | Google Sheets with 7 tabs | `google_sheets/` module, service account, polling |
| Input | Detect new URLs automatically | Poll "Video Input" every N seconds; rows with empty status are new |
| Download | yt-dlp + FFmpeg | `downloader/`: audio-only for the whole video, video only for clip sections |
| Analysis | Transcript + find strong, complete moments | `analyzer/`: local faster-whisper, loudness curve, chat speed, AI picker |
| Editing | Professional reels, configurable | `editor/`: presets in `config/editing_presets.json` |
| AI text | Hooks, captions, hashtags, titles, SEO, times | `ai/`: provider interface (OpenAI / Gemini / offline) |
| YouTube skills | 11 growth features as modules | `growth/`: one module per feature (Phase 8) |
| Ready to post | Folder + sheet + clip ID | `storage/ready_to_post/<CLIP_ID>/` + "Ready to Post" tab |
| Posting | Official APIs, schedule, approval | `posting/`: YouTube Data API v3, Instagram Graph API |
| History | Posted folder + full history | `storage/posted_reels/` + "Posted Reels" tab + DB |
| Reliability | Retries, no double posts, restart-safe, logs | SQLite state DB + idempotency keys + `logs/` |
| Security | Keys in env, never hard-coded | `.env` (git-ignored) + `.env.example` |

---

## 3. Technical limitations (known up front)

1. **YouTube uploads start private-only.** API projects that Google has not audited can
   only upload *private* videos. Fix: apply for the YouTube API compliance audit early.
   Quota for uploads is ~100/day (more than enough).
2. **Instagram needs a Business account + Meta app.** Reels are published with the Graph
   API (container → publish). Limit ~100 API posts/24 h per account. The video must be
   reachable: either uploaded with Meta's resumable upload or from a public URL.
3. **Trending Instagram sounds cannot be attached through the API.** API-posted reels use
   the audio inside the file. Only licensed / royalty-free music goes into auto-posted
   files. Clips meant for a trending sound get status `manual_post` (the farm exports a
   `_NOMUSIC` version for you to post in the app).
4. **Copyright / reused content.** Clips of other creators can be claimed or demonetised.
   The farm adds real editing (transformative), but cannot remove this risk.
5. **yt-dlp breaks when YouTube changes.** Keep it updated (`scripts\update.ps1`).
   Logged-in cookies are only used for age-restricted videos (they cause HTTP 403 otherwise).
6. **The PC must be on** for scheduled posting. Downloads should stay on a home PC
   (YouTube blocks many cloud IPs). Posting can move to a small cloud server later.
7. **CPU rendering** takes ~3-4 min per 40 s clip. A GPU (NVENC) roughly halves it.
8. **Disk:** everything lives on D:. Temporary files are cleaned automatically; the
   source audio/sections are kept (never auto-deleted, per your rule).

---

## 4. Tools chosen

| Job | Tool | Why | Cost |
|---|---|---|---|
| Download | **yt-dlp** (+ yt-dlp-ejs, Node runtime) | Best maintained downloader, supports sections | Free |
| Video/audio processing | **FFmpeg** | Industry standard, libass captions, loudnorm, afftdn | Free |
| Speech to text | **faster-whisper** (local) | Word timestamps, runs on CPU/GPU, no API cost | Free |
| Face tracking | **OpenCV YuNet** | Small, fast, accurate | Free |
| Moment picking + writing | **OpenAI GPT-5 mini** (default) | Strict JSON output = never breaks the pipeline; cheap (~₹1-3 per long video) | Paid, tiny |
| Alternative AI | **Gemini Flash / Flash-Lite** | Free tier for testing, long context, good Hindi | Free tier / tiny |
| No-key fallback | **Built-in scorer** | Loudness + chat + keywords; template captions | Free |
| Control panel | **Google Sheets API** via **gspread** + service account | Free, simple, no OAuth popups | Free |
| State / history | **SQLite** (built into Python) | Restart-safe, prevents duplicates, works offline | Free |
| YouTube posting/comments/analytics | **YouTube Data API v3** (+ Analytics API) | Official | Free (quota) |
| Instagram posting/comments/insights | **Instagram Graph API** | Official | Free |
| Scheduler | Built-in loop (`main.py run`) + Windows Task Scheduler to start it at boot | No extra service needed | Free |

Prices change; check the official pricing pages before adding a card.

---

## 5. Architecture

```
            Google Sheets (control panel: you)
                 │  ▲
     new URLs    │  │  status, clip rows, results
                 ▼  │
        ┌──────────────────────┐        ┌───────────────┐
        │ google_sheets/sync   │◄──────►│ core/db.py    │  SQLite = the farm's memory
        └──────────────────────┘        │ (state.db)    │  (source of truth for status,
                 │                      └───────────────┘   IDs, posting history)
                 ▼                              ▲
        ┌──────────────────────┐                │ every stage reads/writes state
        │ core/pipeline.py     │────────────────┘
        └──────────────────────┘
          │        │         │           │
          ▼        ▼         ▼           ▼
   downloader/  analyzer/   ai/       editor/
   (yt-dlp)     (whisper,   (OpenAI,  (crop, captions,
                 loudness,   Gemini,   effects, sound,
                 chat, picker) offline) render, QC)
                                         │
                                         ▼
                          storage/ready_to_post/<CLIP_ID>/
                                         │
                       posting/scheduler.py (approval + schedule)
                          │                     │
                          ▼                     ▼
                posting/youtube_shorts   posting/instagram
                          │                     │
                          └──────► storage/posted_reels/<CLIP_ID>/ + history
                                         │
                              growth/ (comments, analytics, audit, trends, plan)
```

### How modules communicate

- Modules **never call each other in a chain**; `core/pipeline.py` calls them in order and
  passes plain Python objects (dataclasses / dicts).
- **Every stage writes its result to disk** in `storage/processing/<VIDEO_ID>/` (JSON +
  media) and records its status in SQLite. If the farm stops, the next run skips finished
  stages and continues. Re-running is always safe.
- **Google Sheets is a view + input**, not the source of truth. `google_sheets/sync.py`
  copies new input rows into SQLite and pushes SQLite status back to the sheet. If Google
  is down, processing and posting continue; the sheet catches up later.
- **AI is behind one interface** (`ai/base.py: AIProvider`). Pipeline code asks for
  `pick_moments(...)` and `package_clip(...)`; which provider answers is a setting.

---

## 6. Status values (state machine)

**Video** (`videos.status`):
`new → downloading → transcribing → analyzing → editing → done`
(any stage → `failed` after retries; `failed` rows can be retried by setting status to `retry` in the sheet)

**Clip** (`clips.status`):
`planned → rendering → rendered → qc_failed | ready → approved → scheduled → posting → posted`
plus `manual_post` (trending-sound clips you post yourself) and `rejected` (you said no).

**Post** (`posts.status`, one row per clip × platform):
`scheduled → publishing → posted | failed | needs_manual`

### Never posting twice

- Each post row has a unique `idempotency_key = CLIP_ID:platform`. The DB refuses a second row.
- Before uploading, the row is set to `publishing` and saved. If the farm crashes during an
  upload, the next run sees `publishing` and **checks the platform first** (search the
  channel/account for the clip's tag/ID) instead of uploading again.
- After success the external post ID + URL are saved and the status becomes `posted`.
  A `posted` row is never uploaded again, whatever the sheet says.

### Retries

Network/API errors: 3 tries with growing waits (10 s, 60 s, 5 min). After that the item
goes to `failed`, a row is added to the "Failed Tasks" tab, and the error is logged.
Invalid links fail immediately (no retries).

---

## 7. Clip IDs

`CLIP0001`, `CLIP0002`, … from a counter in SQLite (never reused, survives restarts).
Video IDs are the platform's ID (e.g. YouTube `lcetk9qIzrA`).

---

## 8. Google Sheets structure (7 tabs)

Row 1 = headers exactly as below. You only type in the **bold** columns; the farm fills the rest.

### 1. Video Input
| Column | Example | Notes |
|---|---|---|
| **URL** | https://youtube.com/watch?v=… | Paste here |
| **Clips Wanted** | 9 | Optional, default from Settings (max 9) |
| **Language** | auto / en / hi | Optional |
| **Section** | 0:20:00-0:40:00 | Optional: only clip this part |
| **Layout** | auto / split / full | Optional |
| **Platforms** | instagram,youtube | Optional, default from Settings |
| Video ID | lcetk9qIzrA | Farm |
| Status | new / downloading / … / done / failed | Farm (type `retry` to retry) |
| Title | iShowSpeed's First Day… | Farm |
| Clips Made | 8 | Farm |
| Added At / Updated At | 2026-11-17 13:40 | Farm |

### 2. Processing
Live view of running work: `Video ID | Stage | Progress | Started At | Updated At | Message`

### 3. Ready to Post
| Clip ID | Video ID | File | Duration | Score | Hook | Title | Caption | Hashtags | Description | Music | Status | **Approve** | Notes |
`Approve` = you type `yes` (or `no`) when manual approval is on.

### 4. Posting Schedule
| Clip ID | **Platform** | **Date** | **Time** | Status | Attempts | Last Error |
The farm suggests dates/times from Settings; you can edit them before they are due.

### 5. Posted Reels
| Clip ID | Platform | Posted At | Post URL | External ID | Title | Views (later) | Likes (later) | Comments (later) |

### 6. Failed Tasks
| Time | Item (Video/Clip ID) | Stage | Error | Attempts | Next Action |

### 7. Settings (key / value)
| Key | Value | Meaning |
|---|---|---|
| clips_per_video | 9 | Max clips per video (farm makes 3-9 depending on quality) |
| min_score | 70 | Drop moments scoring below this |
| ai_provider | openai | openai / gemini / offline |
| manual_approval | yes | Ask before publishing |
| default_platforms | instagram,youtube | |
| posts_per_day | 3 | Per platform |
| posting_times | 12:30,18:00,21:00 | Local time slots |
| editing_preset | streamer_split | Name in editing_presets.json |
| music_mode | library | library / generated / none |
| caption_language | auto | auto / en / hinglish |

Settings in the sheet override `config/settings.json`.

---

## 9. Database (SQLite, `data/state.db`)

```
videos(video_id PK, url, title, channel, duration, language, section, layout,
       clips_wanted, platforms, status, stage_detail, attempts, last_error,
       sheet_row, created_at, updated_at)
clips(clip_id PK, video_id FK, idx, start, end, score, kind, hook, title, caption,
      hashtags, description, music, layout, file, nomusic_file, qc, status,
      created_at, updated_at, UNIQUE(video_id, idx))
posts(id PK, clip_id FK, platform, scheduled_at, status, attempts, last_error,
      external_id, url, posted_at, idempotency_key UNIQUE)
events(id PK, at, level, item, stage, message)   -- history / audit log
counters(name PK, value)                          -- CLIP id counter
```

---

## 10. Folder layout

```
python-clipping-automation-farm/
├── main.py                     CLI: init | process URL | run | status
├── config/
│   ├── settings.py             loads settings.json + .env + sheet overrides
│   ├── settings.json           your defaults (safe to edit)
│   └── editing_presets.json    caption/font/animation/sfx/music/resolution presets
├── core/                       (added) shared plumbing
│   ├── paths.py  logger.py  db.py  ids.py  retry.py  media.py  pipeline.py
├── google_sheets/  sheets_reader.py  sheets_writer.py  sync.py
├── downloader/     video_downloader.py
├── analyzer/       transcript.py  signals.py  video_analyzer.py  hook_detector.py
├── editor/         clip_generator.py  captions.py  effects.py  sound.py  renderer.py  qc.py
├── ai/             base.py  openai_client.py  gemini_client.py  offline.py  prompts.py  caption_generator.py
├── posting/        youtube_shorts.py  instagram.py  scheduler.py
├── growth/         (Phase 8: comments, analytics, channel_audit, trends, planner, …)
├── assets/         fonts/  sfx/  music/  (your licensed bundles + LICENSES.md)
├── models/         whisper + face models (git-ignored)
├── storage/        input/ processing/ ready_to_post/ posted_reels/ failed/ sources/
├── data/           state.db (git-ignored)
├── logs/           farm.log (git-ignored)
├── docs/           DESIGN.md  SETUP.md  ROADMAP.md
├── scripts/        setup.ps1  update.ps1  run_farm.ps1
├── tests/
├── .env.example  .gitignore  requirements.txt  README.md
```

Changes vs. your suggested structure: added `core/` (database, logging, retries, pipeline),
`ai/offline.py` (no-key fallback), `editor/sound.py` + `editor/qc.py` (sound design and the
automatic quality check), `growth/` for the 11 YouTube skills, `storage/sources/` (source
audio/sections, never auto-deleted), `data/`, `models/`, `assets/`, `docs/`, `scripts/`, `tests/`.

---

## 11. Roadmap (each phase waits for your OK)

| Phase | Deliverable | Needs from you |
|---|---|---|
| 0 | This design, skeleton, env on D:, DB, logging | - |
| 1 | Core pipeline in the project, no Claude: download, transcript, signals, offline picker, editor port, `python main.py process URL` | - |
| 2 | AI layer: OpenAI + Gemini, moment picking + packaging (hooks, captions, hashtags, titles, SEO, times) | OpenAI key (and/or Gemini key) |
| 3 | Editor upgrade: presets file, licensed music/SFX library, noise reduction, silence removal, transitions, quality check | Your audio/editing bundles + their licenses |
| 4 | Google Sheets control panel (7 tabs, auto-create, sync) | Google Cloud service account JSON + sheet shared with it |
| 5 | Ready to Post / Posted Reels folders + approval flow (done: posting/queue.py, core/clip_server.py) | - |
| 6 | Posting: YouTube Shorts + Instagram Reels (done: posting/youtube_shorts.py, instagram.py, publisher.py) | YouTube OAuth client + audit request; Meta app + IG Business account |
| 7 | Scheduler: restart-safe, no double posts, Windows auto-start (done: core/service.py, publish thread in watch) | - |
| 8 | Growth modules (11 YouTube skills) | Your 11 skill files |
| 9 | Hardening, tests, docs, GitHub publish | GitHub account |
