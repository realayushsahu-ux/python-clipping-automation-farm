# The 11 YouTube skills → where they go in the farm

Source: `youtube-agent-skill` by Jake Schincariol, **MIT licence** (copy in `growth/yt_skills/LICENSE`, keep it
when the repo goes public). Copied unchanged into `growth/yt_skills/`. The 6 Python tools need only plain
Python, so the farm runs them **without Claude**. The SKILL.md texts are rules; they become prompt rules for
OpenAI/Gemini and code checks.

| Skill | Use in the farm | Phase | How |
|---|---|---|---|
| **yt-shorts** | Picking moments + the first line of each Short | 2 | Rules go into the moment prompt: opens on a complete thought, has a turn, ends on a line (not a trail-off); write a NEW first line; on-screen text ≠ spoken words; a loop point |
| **yt-script** (`hookscore.py`, `hooks.json`) | Hook selection | 2 | AI writes 5 hooks from the 21 formulas → `hookscore.py` ranks them → best goes on screen. **Ranker only, never a gate**: it is tuned for talking-head YouTube hooks ("you", stakes, 9-24 words), so gaming reaction hooks score "WEAK" by design (tested: "He did NOT expect this 😭" = 29) |
| **yt-package** (`title.py`) | Title lint | 2 | Every YouTube title is checked: ≤60 chars (≤40 for mobile), ≤2 ALL-CAPS words, on-screen hook must not repeat the title, a name/number beats adjectives. Low score → AI rewrites once |
| **yt-seo** | Description + tags | 2 | First 2 lines = what the viewer gets, in search words; ≤15 tags (no stuffing); the 3 search queries it should win |
| **yt-edit** (`deadair.py`) | Editing | 3 | We already cut silences; add its FILLER ("um", "so yeah") and REPEAT (restarted sentence) detection to `editor/clip_generator.tighten` |
| **yt-comment** | Comment replies | 8 | `growth/comments.py`: sort comments into questions / corrections / praise / bait; draft replies under 30 words; suggest ONE to pin. Drafts land in Google Sheets; nothing is auto-posted unless you switch it on |
| **yt-viral** (`swipe.py`) | Trend finder | 8 | `growth/trends.py`: yt-dlp `--flat-playlist` on GTA/streamer clip channels → rank videos by views ÷ that channel's median → top outliers + their title formula → ideas tab |
| **yt-retention** (`retention.py`) | Learning from results | 8 | `growth/analytics.py`: YouTube Analytics API retention per clip → hook leak / cliffs → which hook styles keep viewers |
| **yt-audit** | Weekly report | 8 | `growth/channel_audit.py`: last 10 titles, first 2 s hooks, posting rhythm → ONE fix for the week |
| **yt-plan** | Posting plan | 7/8 | `growth/planner.py`. Their "3 uploads a week" is for long videos; for a Shorts clip farm we keep `posts_per_day` from settings |
| **yt-chapters** (`chapters.py`) | Not used for Shorts (Shorts have no chapters) | — | Kept for long videos later |
| `voice_template.md` | Channel voice | 2 | Becomes `config/voice.md` (GenZ gamer tone, Hinglish/English, words we never use, no fake claims). Read by every AI prompt |

Rules the farm adopts from the pack (they match your roadmap):
- **Never invent a number** ("1M views", "viral") — the prompts forbid it.
- **Nothing publishes by itself** unless `manual_approval` is off in Settings.
- **Tags barely matter**: we use few, relevant hashtags, not walls of them.
