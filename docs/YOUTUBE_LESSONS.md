# YouTube: why 5 of our first 6 Shorts got under 6 views, and what the farm does now

Checked 2026-11-18 with the YouTube Data API on all 6 uploads of our test channel (2 days old at the time).

| Short | Views | What was wrong |
|---|---|---|
| GTA-6 BODYGUARD 😭✌️ #gta6 #gta | **1,291** | nothing big: GTA 6, ONE concrete role, short title, 41 s, first upload during GTA 6 launch hype |
| speed got trolled by chat 👍🤣🤯 #gta #gta6 #gamingshorts #gta6 … | 2 | vague title, #gta6 twice, hashtag stuffing, empty description, category "People & Blogs" |
| GTA6 clips give goosebumps 😯🤯🔥.......... | 4 | vague ("clips", "goosebumps"), audio language set to Portuguese, empty description |
| gta6 with speed-naked john bobby from ohio … | 5 | unclear title, 58 s, empty description |
| iShowSpeed's first day in GTA RP | 3 | **GTA 5 RP, not GTA 6**: wrong topic for this channel; an old stream hundreds of channels already clipped |
| iShowSpeed loses it in GTA V RP | 4 | same as above, vague "loses it" |

## The problems, in order of weight
1. **Wrong topic.** The two farm Shorts were GTA 5 RP clips on a GTA 6 channel, with "GTA V" in the title,
   in the week GTA 6 launches. Nobody searching GTA 6 sees them.
2. **Duplicate moments.** A famous old Speed stream has been clipped by hundreds of channels. YouTube shows
   new viewers the first/best copy, not the 300th. Fresh streams and new GTA 6 moments win.
3. **Vague titles.** "clips", "goosebumps", "loses it", "trolled": the viewer can't picture anything. The winner
   named one thing: BODYGUARD.
4. **Hashtag stuffing / repeats** (hand uploads): YouTube shows only 3 hashtags; 10+ mixed tags say nothing.
5. **Brand-new channel** (2 days, 0 subscribers): the first test audience is small. Normal, and it gets better
   only with posts that keep people watching.

## What the farm does now (automatic)
- `only_topic_videos` = yes: a video that is not about GTA 6 (title, description, tags) stops before any work,
  with a note in Video Input. Switch off in Settings to clip something else anyway.
- Titles: "GTA 6" first, then ONE concrete thing, under 50 characters; vague words are rejected and rewritten;
  "#gta6" is added at the end of the title. A non-GTA-6 clip can never get "GTA 6" or #gta6 (misleading tags
  can get a video removed).
- Hashtags: 3-5, the game first, at most one broad tag (#gaming), no repeats, no #viral/#fyp.
- `config/voice.md` has a "Lessons" section that every AI prompt reads; `config/winners.md` and
  `config/trends.md` add what worked here and what is trending this week.
- `main.py retitle all` gives every clip that is not posted yet a new title + hashtags with these rules
  (the video stays the same).

## What you do (5 minutes, YouTube Studio → Customization)
- Channel description: one line, e.g. "GTA 6 moments from the biggest streams, every day." + 2-3 keywords.
- Banner + profile picture that say GTA 6.
- A short, readable handle that says what the channel is about (not the auto-generated one).
- For uploads you do by hand: copy the title + hashtags from the Sheet (Ready to Post), category **Gaming**,
  language **English**.

## Monetization: the honest picture
- YouTube Partner Program needs **1,000 subscribers + 10 million Shorts views in 90 days**
  (or 4,000 watch hours of long videos in 12 months).
- YouTube's **reused content** policy: clips of other creators "with little or no narrative" are not allowed
  to earn money, **even with permission**, and it is judged for the whole channel. Reactions need voice
  commentary; edited clips need "a storyline and commentary".
- So a channel of plain streamer clips can grow views but will most likely be refused for monetization.
  The fix is real original value in every Short: our own voice commentary / a story around the clip.
  Built 2026-11-18: every new clip gets our AI narrator (see below).

## Voice commentary (built)
- Every clip: a 3-4 s spoken **intro** (context: who, where, what is at stake, never the payoff) and a short
  spoken **take** at the end (an opinion + a question for the comments). Clip sound drops under the narrator,
  the narrator's words appear as captions, the last frame holds for a moment if the take needs it.
- Cost: the lines come from the same AI call as the title (no extra call); the voice is **Kokoro**, free and
  local (`models/kokoro`, no key). Clips made before this get their lines with one small AI call.
- Quality gate (`editor/voiceover.py`): a line that is generic ("wait for it", "insane", "subscribe"), too long,
  makes a claim, repeats the hook, repeats another clip, or names nothing from the clip is dropped.
  No narrator beats a bad narrator.
- Settings tab: `voice_commentary` yes/no, `voice_name` (am_fenrir, am_michael, am_puck, af_heart, bm_george).
- Remake an older clip with the narrator: `main.py remake CLIP0007`.
- YouTube's "altered or synthetic content" label is for realistic fakes of real people/events; a narrator
  voice commenting on a clip is not that, so the farm does not set it.

Sources: YouTube Help: [reused content](https://support.google.com/youtube/answer/1311392),
[Partner Program eligibility](https://support.google.com/youtube/answer/72851),
[hashtags](https://support.google.com/youtube/answer/6390658).

## The first 1-2 words (added 2026-11-18, from data/trends.json)
How this week's biggest GTA 6 Shorts open (10x-800x their channel's usual views):
"**Is** 30 FPS really a problem for GTA 6?", "**Come with me** to pick up GTA 6!", "**We Bought** a NEW TV For GTA 6 😎",
"**When** GTA 6 Finally Comes Out 🤣", "**POV:** GTA 6 Release", "**GTA 5 vs GTA 6:** ...", "NEW TV For GTA 6 Went **WRONG** 💀".
Rules the farm now enforces on every title, on-screen hook and Instagram first line (`ai/quality.opener`):
- strong first word: a question, POV/When, I/We + action, a name on screen, a number, a CAPS word, "X vs Y"
- never "The / A / So / Here's / Just / Watch / Tiny / New ..." and never the uploader/reviewer ("IGN") first
- "GTA 6" within the first 40 characters (what a phone shows), one emotion emoji at the end
- Instagram shows one line before "more": a weak first caption line is replaced by the checked title
- the trends finder writes "how this week's top titles OPEN" into config/trends.md every day, so the AI
  follows what is working now, not last month
Age-restricted videos (e.g. IGN reviews): .env YTDLP_COOKIES_FROM_BROWSER=firefox; the farm then uses the
web_safari client (the default signed-in clients gave HTTP 403).

## Any game (2026-11-18)
`only_topic_videos` = no (default now): paste any gaming link. The AI names the game it sees (`game` in the
package); the title must name THAT game in the first 40 characters, its hashtag goes first (#minecraft,
#valorant, #bgmi...), and another game's name/hashtag (e.g. #gta6 on a Minecraft clip) is removed as
misleading. Same hooks, narrator, first-words and quality rules for every game. GTA 6 stays the main game
for the trends finder.

## Cost per clip (measured 2026-11-18, gpt-5-mini $0.25 / $2.00 per 1M tokens)
- packaging (hook, titles, caption, hashtags, narrator lines): ~3,200 input + ~3,900 output tokens = ~$0.0086
- finding moments: ~11,400 input + 3,000 output per 28-min video = ~$0.009, shared by its clips (~$0.001 each)
- sometimes one title-fix call: ~$0.0015
- = about $0.01 = **about Rs 0.9 per clip**; voice (Kokoro), editing, YouTube/Instagram APIs: free
- 5 clips a day = about Rs 5/day, Rs 140/month; trends/stats/audit add less than Rs 5/month

## Launch-day check (2026-11-19, GTA 6 release day)
Numbers at 12:00 IST (YouTube Data API; Instagram views need instagram_manage_insights, so only likes):
farm Shorts CLIP0004-0009 got 0-4 views each; the hand-upload "GTA-6 BODYGUARD" is at 1,295.
Instagram: 0-1 likes per Reel, CLIP0005 removed by Instagram. No YouTube claims, no Instagram restriction
(Account Status OK except monetization "Established presence" = new account, does not limit reach).

What was wrong, and what we can control:
1. **Two Reels never went up.** CLIP0007 (48 MB) and CLIP0009 (58 MB) got `ProcessingFailedError` from
   Instagram's upload server; every copy <= 38 MB went through. Fix: the Instagram copy is now capped at
   35 MB (`posting/instagram.ig_video_kbps`: 8 Mbps for short clips, lower for long ones; Instagram
   re-encodes Reels far below that anyway). Old oversized copies are re-made automatically.
2. **The narrator, our only original part, was often missing or sounded like a repost.** Intro lines were
   11-13 words (too long for the 4.2 s slot, so dropped: CLIP0007 had NO intro) and most started with
   "IGN ..." / "Here's IGN's take" / "This clip ...", which tells a new viewer "this is someone else's
   video" in the first second. Fixes: prompt asks 5-9 words that start with what we SEE; `check_line`
   drops intros starting with the uploader/"this clip"; max 10 words; saved packages are re-checked
   (`voiceover.recheck_saved`); if no intro survives, ONE small AI call writes new lines
   (`pipeline.needs_voice_retry`, ~Rs 0.2, once per clip). Today's unposted CLIP0010-0013 were remade.
3. **Source.** The IGN review (pre-launch, age-restricted, a reviewer talking) is the wrong source on launch
   day. The trends finder shows the biggest fresh GTA 6 streams today, incl. two Indian creators (Hindi
   audience = our audience). Pasting a source stays the user's choice.
4. **Cold start (not a bug).** 3-day-old accounts, 0 followers. YouTube first shows a Short to a small test
   group and widens only if people choose to watch instead of swiping ("viewed vs swiped away" in Studio).
   Instagram does not recommend accounts that mostly repost others' videos to non-followers; adding real
   commentary/voiceover is the stated way to count as transformed.

Sources: Instagram [original content ranking](https://www.engadget.com/instagrams-algorithm-overhaul-will-reward-original-content-and-penalize-aggregators-130018977.html),
[2026 reposts update](https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/),
[Trial reels (Meta)](https://about.fb.com/news/2024/12/trial-reels-try-content-non-followers/) (needs a
professional account and, per third-party guides, ~200 followers — not available to us yet),
[Shorts algorithm in YouTube's words](https://outlierkit.com/resources/youtube-shorts-algorithm/) (secondary),
Meta [resumable uploads](https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/).
Vendor blogs (15-45 s for gaming clips, peak in the first 1-2 s, sound-off readable hooks) are heuristics only.

## The trending format (studied 2026-11-20 from 19 viral GTA 6 Shorts/Reels the user picked, up to 924K likes)
Measured with ffmpeg/OpenCV/Whisper (scratch study, numbers are medians):
- **Topic**: a PREMISE a stranger gets in one line, visibly happening: "Airport reveal in GTA 6", "World's
  first 6 star wanted level", "IShowSpeed almost eaten by a GTA 6 alligator", "The NPCs are crazy in GTA 6".
  Streamer chatter / inside jokes ("wallet gaya") never.
- **First second**: the streamer is already reacting at 0.0 s ("No way we got a new train in GTA", "The heck
  is going on over here"). No intro, no narrator, no cold-open repeat.
- **Length** 45 s median (10 s - 3 min). **Cuts** 1.3 per 10 s median: reaction clips run uncut; edited ones
  cut dead air (shots 1.5-3 s). **Zooms**: quick 0.2-0.4 s moves of 2-6 %, a few 12-20 % pushes on peaks;
  half the clips have none. **Music**: mostly none (game audio fills gaps); 4-5 of 19 a quiet bed.
  **60 fps** at real speed. **-14 LUFS**.
- **Layouts**: (a) face on top ~34 %, gameplay below, title in a white/black rounded box on the seam, small
  white caps captions (1-3 words, one yellow keyword) under it; (b) black card: plain white title + emoji
  above the WHOLE uncut 16:9 frame, captions below.
- Farm: presets `viral_auto` (a or b automatically), `facecam_top`, `card`; motion/sound `pro`;
  `ai/vision.py` checks the title against 4 frames; music only from licensed tracks in assets/music.
- Bug found the same day: 60 fps sources were decoded frame-by-frame into 30 fps output = half-speed
  "slow motion" picture. Fixed with an fps filter in the decoder (editor/renderer.py).

## 2026-11-20: approved format locked

The user approved CLIP0036 and CLIP0042 ("that quality is superb, keep it like that"). Every setting that
shapes those clips is frozen in `config/approved_format.json`, and `python -m tests.test_format` fails if
`config/editing_presets.json` or `config/settings.json` drift from it. The approved format:
- `editing_preset` = `viral_auto`: streamer face on top when there is a facecam, otherwise the black card
- one plain title with an emoji in a white box for the whole clip
- small white captions with a yellow keyword
- the source frame rate (60 fps), so it plays at real speed
- quick 2–6 % zooms and trimmed silences
- the streamer's own voice only (`voice_commentary` = no) and no music (`music_mode` = none)
- the vision check fixes titles that don't match the frames

To change the format: make a sample, get the user's OK, then run `python -m tests.test_format --save`.
