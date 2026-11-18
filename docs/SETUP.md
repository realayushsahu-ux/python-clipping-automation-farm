# Setup, run and test

All commands are for **PowerShell**, run inside `D:\python-clipping-automation-farm`.

## 1. First-time setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```
It checks FFmpeg/Node/Python, creates `.venv` on D:, installs `requirements.txt`, downloads the
face model and Whisper models into `models\` (not C:), creates `.env` from `.env.example`, and
runs `doctor`. Missing tools: `winget install Gyan.FFmpeg`, `winget install OpenJS.NodeJS.LTS`,
`winget install Python.Python.3.12`, then open a NEW terminal.

## 2. Add your keys (optional, Phase 2)

Open `.env` in Notepad and set `OPENAI_API_KEY=...` (and/or `GEMINI_API_KEY=...`).
Without keys the farm runs in **offline mode** (its own scoring + template captions).

## 3. Check

```powershell
.venv\Scripts\python.exe main.py doctor
```
Every line should be `[ok]` (`[--]` = optional key not set yet).

## 4. Clip a video

```powershell
.venv\Scripts\python.exe main.py process "https://www.youtube.com/watch?v=VIDEO_ID"
```
Useful options:
| Option | Example | Meaning |
|---|---|---|
| `--section` | `0:20:00-0:40:00` | only this part (fast tests, long streams) |
| `--clips` | `5` | max clips (3-9 are made depending on quality) |
| `--layout` | `split` / `full` | force streamer split-screen or full-frame |
| `--language` | `en` / `hi` | force the spoken language |
| `--ai` | `offline` / `openai` / `gemini` | which AI for this run |
| `--music` | `library` / `generated` / `none` | background music source |

Re-running the same command **continues** where it stopped (each stage is cached) and never
makes duplicate clips.

## 5. See results

```powershell
.venv\Scripts\python.exe main.py status
explorer storage\ready_to_post
```
Each clip folder has the video, a no-music copy, `caption.txt` and `post.json`.
Logs: `logs\farm.log`. Database: `data\state.db`.

## 6. Test checklist
- [ ] `doctor` all ok
- [ ] `process` with `--section` of ~10 min finishes and shows clips as `ready`
- [ ] each clip: face visible, captions readable, sound OK, 20-40 s
- [ ] run the same `process` again → it says the video is already done (no duplicates)

## 7. When downloads break
YouTube changes often. Run `powershell -ExecutionPolicy Bypass -File scripts\update.ps1`.
Age-restricted videos only: set `YTDLP_COOKIES_FROM_BROWSER=firefox` in `.env` (Firefox signed in to YouTube).
