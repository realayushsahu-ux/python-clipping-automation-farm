# Google Sheets control panel: one-time setup (about 10 minutes, free)

The farm logs in to Google as a **service account**: a robot account that only sees the sheets
you share with it. Nothing else in your Google account is touched.

## 1. Google Cloud project + Sheets API
1. Open https://console.cloud.google.com and sign in with the Google account that will own the sheet.
2. Top bar → project picker → **New project** → name `clipping-farm` → Create, then select it.
3. Search bar → **Google Sheets API** → **Enable**.

## 2. Service account + key
1. Menu → **IAM & Admin → Service Accounts** → **Create service account**.
   Name `farm-sheets` → Create and continue → skip the roles → Done.
2. Click the new account → **Keys** tab → **Add key → Create new key → JSON** → Create.
   A `.json` file downloads.
3. Move it to `D:\python-clipping-automation-farm\secrets\service_account.json`
   (the `secrets` folder is never uploaded to GitHub). Treat it like a password.
4. Open the file in Notepad and copy the `client_email` value
   (looks like `farm-sheets@clipping-farm.iam.gserviceaccount.com`).

> If Google says *"Service account key creation is disabled"*, your account belongs to an
> organisation (school/work) with that policy on. Use a personal Gmail account instead.

## 3. The sheet
1. Go to https://sheets.new, name it `Clipping Farm Control Panel`.
2. **Share** → paste the `client_email` → role **Editor** → untick "Notify" → Share.
3. Copy the sheet ID from the address bar:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_PART`**`/edit`

## 4. Tell the farm
In `D:\python-clipping-automation-farm\.env`:
```
GOOGLE_SERVICE_ACCOUNT_FILE=secrets/service_account.json
GOOGLE_SHEET_ID=THIS_LONG_PART
```

## 5. Test and create the 7 tabs
```powershell
cd D:\python-clipping-automation-farm
.venv\Scripts\python.exe main.py sheets check     # connection test
.venv\Scripts\python.exe main.py sheets setup     # creates the 7 tabs and fills them
```

## How you use it

| Tab | You type | The farm writes |
|---|---|---|
| **Video Input** | `URL` (+ optional Clips Wanted, Language, Section like `0:20:00-0:40:00`, Layout, Platforms). In `Status` type `retry` (failed video) or `redo` (make new clips again) | Video ID, Status, Title, Clips Made, times, Message |
| **Processing** | – | what is running right now |
| **Ready to Post** | `Approve`: **yes** / **no** / **remake** / **posted** · `Post Time` (`2026-11-18 18:00`) · `Notes` | status, **Folder** link, hook, titles, caption, hashtags, description, pin comment, length, score |
| **Posting Schedule** | – | every approved clip × platform with its time and status |
| **Posted Reels** | `Post URL` for clips you posted by hand | every published post, Folder link |
| **Failed Tasks** | – | what went wrong and what to do |
| **Settings** | `Value` column | – (your values override `config/settings.json`) |

- Approval is **automatic** by default (Settings → `manual_approval` = `no`): every clip that passes the
  quality check shows `yes` and gets scheduled. Set it to `yes` to approve each clip yourself.
- **no** = removed from Ready to Post, its posts cancelled (video stays in `clips_generated`) ·
  **remake** = new hook/title + re-render (same clip ID) · **posted** = you uploaded it yourself: it is
  marked posted on every platform (never posted again) and its folder moves to `posted_clips`.
- **Folder** (`▶ open CLIP0004`) opens the clip's folder in Explorer and plays the video in your browser.
  It is served by `main.py watch` on http://localhost:8765 (only this PC can open it).
- A post time that passes while the farm is off moves to the next free slot.
- The farm reads the sheet every `sheet_poll_seconds` (60 s). Edits in farm columns are overwritten.

Run the farm (keeps going until you press Ctrl+C):
```powershell
.venv\Scripts\python.exe main.py watch
```
