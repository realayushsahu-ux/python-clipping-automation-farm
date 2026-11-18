# Posting setup: YouTube Shorts + Instagram Reels (official APIs, free)

Nothing is published until **Settings → `posting_enabled` = yes** (default **no**).
Do the two setups below, test with one clip, then switch it on.

---

## A. YouTube (≈10 min)

Use the same Google Cloud project as the Sheet (`clipping-farm`).

1. https://console.cloud.google.com → **APIs & Services → Library** → **YouTube Data API v3** → Enable.
2. **APIs & Services → OAuth consent screen** → User type **External** → app name `Clipping Farm`,
   your e-mail → Save. Under **Audience / Test users** add the Google account that owns the channel.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID** → type **Desktop app** →
   Create → **Download JSON** → save it as
   `D:\python-clipping-automation-farm\secrets\client_secret.json`.
4. Connect (a browser opens once, sign in with the channel's account, allow):
   ```powershell
   .venv\Scripts\python.exe main.py auth youtube
   ```
   You should see `[ok] YouTube channel: <your channel>`. The token is saved in
   `secrets\youtube_token.json` (never uploaded to GitHub) and refreshes itself.

> **Important – private lock.** YouTube locks videos uploaded by an API project that Google has not
> audited to **private**. The farm still uploads and tells you in the Sheet ("YouTube set it to private").
> To post publicly, request the audit (free): *YouTube API Services – Audit and Quota Extension Form*
> (https://support.google.com/youtube/contact/yt_api_form). Locked videos can't be made public later,
> so until the audit is approved keep `youtube_privacy` = private for tests and upload the YouTube
> versions by hand in the app (then type **posted** in the Sheet, or run `main.py posted CLIP0005 --platform youtube`).
>
> While the consent screen is in **Testing** mode Google may expire the token after 7 days;
> just run `main.py auth youtube` again, or publish the consent screen (Audience → Publish app).

## B. Instagram (≈20 min)

The farm uploads the video file straight from your PC. Meta allows that only through
**Facebook Login** (an Instagram professional account connected to a Facebook Page).

1. Instagram app → Settings → **Account type and tools** → switch to **Professional** (Creator or Business).
2. Connect it to a **Facebook Page** (create a Page if you have none): Facebook Page settings →
   **Linked accounts → Instagram → Connect**.
3. https://developers.facebook.com → **My Apps → Create app** → use case
   **"Manage messaging & content on Instagram"** (or *Other → Business*) → add the **Instagram** product
   (*API setup with Facebook login*).
4. **Tools → Graph API Explorer** → choose your app → *User token* → add permissions
   `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`,
   `instagram_manage_comments` (for the first comment), `instagram_manage_insights` (views for
   the Performance tab) → **Generate Access Token** → allow.
5. Make it long-lived: **Tools → Access Token Debugger** → paste → **Extend Access Token**.
   Back in the Explorer, with that long-lived user token run
   `me/accounts?fields=name,access_token,instagram_business_account`
   → copy your Page's `access_token` (a Page token from a long-lived user token does not expire)
   and the `instagram_business_account.id`.
6. In `.env`:
   ```
   INSTAGRAM_API_HOST=graph.facebook.com
   INSTAGRAM_USER_ID=<instagram_business_account id>
   INSTAGRAM_ACCESS_TOKEN=<the Page access_token>
   ```
7. Test:
   ```powershell
   .venv\Scripts\python.exe main.py auth instagram
   ```
   → `[ok] Instagram account: @yourname`

While your Meta app is in *Development* mode it can post to accounts that have a role on the app
(you, as the app's admin) – that is all the farm needs.

---

## C. First post (by hand, one clip)

```powershell
.venv\Scripts\python.exe main.py publish CLIP0005 --platform youtube
.venv\Scripts\python.exe main.py publish CLIP0005 --platform instagram
```
A command you type is your go-ahead for that clip (it works even with `posting_enabled` = no).
The link appears in the output, in **Posted Reels**, and in the clip's `post_info.txt`; when every
platform is done the folder moves to `storage\posted_clips\`.

## D. Automatic posting

Settings tab → `posting_enabled` = **yes**. Then
`main.py publish --due` publishes everything whose post time has come (Phase 7 runs this inside
`main.py watch` every minute and starts the farm with Windows).

## What the farm does when something goes wrong

| What happened | Posting Schedule shows | What you do |
|---|---|---|
| network / server error / rate limit | `failed, will retry` | nothing (tries again, max 3) |
| token expired, video refused | `needs you` + reason in Ready to Post → Notes | fix it, type **retry** in Approve (or `main.py publish CLIP0005 --platform instagram --retry`) |
| connection lost while publishing ("maybe posted") | `needs you` (MAYBE POSTED) | look at the channel: there → **posted**, not there → **retry** |
| farm closed during an upload | `needs you` after 2 hours | same as above |

The same clip is never published twice on one platform: every clip × platform has one post row,
and a row is claimed before the upload starts.

Music: only post clips whose music is licensed for the platform (`assets/LICENSES.md`); or use the
`_NOMUSIC` version and add a sound inside the app.
