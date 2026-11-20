"""The tabs of the control panel: names, columns, which columns YOU type in, dropdowns.

Columns are found by their header name, so you may reorder columns or add your own (the farm
ignores unknown columns in Video Input, and keeps your own notes in Ready to Post).
"""

VIDEO_INPUT = "Video Input"
PROCESSING = "Processing"
READY = "Ready to Post"
SCHEDULE = "Posting Schedule"
POSTED = "Posted Reels"
FAILED = "Failed Tasks"
SETTINGS = "Settings"
PERFORMANCE = "Performance"
TRENDS = "Trends"
AUDIT = "Audit"
GUIDE = "START HERE"

# tab order in the sheet + tab colour: green = you type here, blue = just look, grey = advanced
ORDER = [GUIDE, VIDEO_INPUT, SCHEDULE, READY, POSTED, PERFORMANCE, SETTINGS, PROCESSING, FAILED, TRENDS, AUDIT]
GREEN, BLUE, GREY, ORANGE = (0.20, 0.66, 0.33), (0.26, 0.52, 0.96), (0.62, 0.62, 0.62), (1.0, 0.6, 0.0)
TAB_COLORS = {GUIDE: ORANGE, VIDEO_INPUT: GREEN, READY: GREEN, SETTINGS: GREEN, SCHEDULE: BLUE, POSTED: BLUE,
              PERFORMANCE: BLUE, PROCESSING: GREY, FAILED: GREY, TRENDS: GREY, AUDIT: GREY}

TABS = {
    GUIDE: {
        # written by the farm on every sync: what is happening now + how to use the sheet
        "headers": ["CLIPPING FARM", ""],
        "widths": [300, 760],
    },
    VIDEO_INPUT: {
        # you type: URL (+ optional Clips Wanted, Language, Section, Layout, Platforms). Status: type retry / redo
        "headers": ["URL", "Clips Wanted", "Language", "Section", "Layout", "Platforms",
                    "Video ID", "Status", "Title", "Clips Made", "Added At", "Updated At", "Message"],
        "widths": [320, 90, 80, 130, 80, 140, 110, 100, 280, 80, 130, 130, 300],
        "dropdowns": {3: ["auto", "en", "hi"], 5: ["auto", "split", "full"],
                      8: ["new", "retry", "redo"]},
        "typed": [1, 2, 3, 4, 5, 6, 8],
    },
    PROCESSING: {
        "headers": ["Item", "Title", "Stage", "Progress", "Attempts", "Updated At", "Message"],
        "widths": [110, 280, 110, 220, 70, 130, 320],
    },
    READY: {
        # you type: Approve (yes / no / remake / posted), Post Time (YYYY-MM-DD HH:MM), Notes
        # Watch = link: plays the clip (needs `main.py watch` or `serve`); details are on the right
        "headers": ["Clip ID", "Watch", "YouTube Title", "Post Time", "Approve", "Status", "Notes", "Length (s)",
                    "Instagram Caption", "Hashtags", "YouTube Description", "Pin Comment", "On-screen Hook",
                    "Platforms", "Score", "Kind", "Creator", "Video ID"],
        "widths": [90, 130, 300, 130, 80, 150, 220, 70, 300, 260, 300, 240, 200, 130, 60, 80, 110, 110],
        "dropdowns": {5: ["yes", "no", "remake", "posted", "retry"]},
        "typed": [4, 5, 7],
    },
    SCHEDULE: {
        # one row per clip, next post first (view only: change times in Ready to Post)
        "headers": ["Post Time", "Day", "Clip ID", "YouTube Title", "Where", "Status", "Watch", "Problem"],
        "widths": [130, 110, 90, 340, 160, 170, 130, 320],
    },
    POSTED: {
        # Post URL: paste the link here for clips you posted by hand
        "headers": ["Posted At", "Clip ID", "YouTube Title", "Platform", "Post URL", "Views", "Likes", "Comments",
                    "Watch"],
        "widths": [130, 90, 320, 90, 320, 70, 70, 80, 130],
        "typed": [5],
    },
    FAILED: {
        "headers": ["Time", "Item", "Stage", "Error", "Next Action"],
        "widths": [140, 110, 100, 450, 300],
    },
    PERFORMANCE: {
        # every Short/Reel on your channels (also hand uploads), most views first; refreshed every few hours
        "headers": ["Platform", "Winner", "Views", "Likes", "Comments", "Shares", "Saves", "Length (s)",
                    "Published", "Title", "Clip ID", "Link", "Checked At"],
        "widths": [90, 70, 80, 70, 80, 70, 70, 80, 130, 320, 90, 120, 130],
    },
    TRENDS: {
        # Phase 8 trends finder: Shorts that beat their own channel's median this week (ideas) and fresh
        # streams/long videos with the most views per hour (sources: copy a link into Video Input to clip it)
        "headers": ["Type", "Beat Its Channel", "Views", "Views / Hour", "Age (h)", "Length (s)", "Channel",
                    "Title", "Title Formula", "Link", "Note"],
        "widths": [90, 110, 90, 90, 70, 80, 160, 360, 160, 110, 220],
    },
    AUDIT: {
        # Phase 8 weekly channel audit: the ONE fix for this week, what works, what not to do yet, the facts
        "headers": ["Section", "Finding", "Detail"],
        "widths": [170, 520, 420],
    },
    SETTINGS: {
        "headers": ["Key", "Value", "Meaning"],
        "widths": [220, 200, 520],
        "typed": [2],
    },
}

# Settings shown in the Settings tab (value from config/settings.json on first setup)
SETTINGS_ROWS = [
    ("posting_enabled", "yes = publish to YouTube/Instagram at the post times; no = never publish (safe switch)"),
    ("youtube_privacy", "public / unlisted / private for new YouTube Shorts"),
    ("manual_approval", "no = every clip is approved automatically (type no in Approve to drop one); "
                        "yes = nothing is posted until you type yes"),
    ("clips_per_video", "max clips per video (the farm makes 3-9 depending on quality)"),
    ("min_score", "moments scoring below this are dropped (1-100)"),
    ("clip_min_seconds", "shortest clip"),
    ("clip_max_seconds", "longest clip (normal moments)"),
    ("long_clip_kinds", "kinds of moment that may run longer, e.g. funny,story"),
    ("long_clip_max_seconds", "longest clip for those kinds (105 = 1:45)"),
    ("default_platforms", "instagram,youtube"),
    ("posts_per_day", "how many posts per day"),
    ("posting_times", "time slots (India time), e.g. 10:00,18:00,20:00,22:00,23:30; a slot with no clip is skipped"),
    ("keep_awake", "posting = PC stays awake from 15 min before a post until it is done; always; off"),
    ("wake_pc_for_posting", "yes = Windows wakes the PC 10 min before each posting time (main.py autostart on)"),
    ("winner_views_youtube", "a YouTube Short with at least this many views is a winner (the AI learns from it)"),
    ("winner_views_instagram", "an Instagram Reel with at least this many views is a winner"),
    ("learn_from_winners", "yes = new clips follow the patterns of your winners (config/winners.md)"),
    ("trends_enabled", "yes = once a day find what works in the niche (Trends tab, main.py trends)"),
    ("trend_queries", "search words for trends, comma separated, e.g. GTA 6,GTA 6 stream (each costs 2 searches)"),
    ("trend_channels", "extra channels to watch, comma separated: @handle or channel link (optional)"),
    ("trend_must_match", "a trend must name one of these words (title/description/tags), e.g. gta 6,gta vi"),
    ("trend_min_multiple", "an idea = a Short with at least this many times its channel's usual (median) views"),
    ("trend_max_searches_per_day", "YouTube searches per day for trends (100 quota units each)"),
    ("use_trends_in_prompts", "yes = hooks/titles may use this week's trending angles (config/trends.md)"),
    ("only_topic_videos", "no = any game works (titles/hashtags use the game the AI sees); yes = only the main "
                          "game (topic_words), others stop with a note"),
    ("topic_words", "words that mean the channel's game, comma separated, e.g. gta 6,gta vi,gta6"),
    ("voice_commentary", "yes = our AI narrator adds a short intro + a take at the end of every new clip (free, local)"),
    ("voice_name", "narrator voice: am_fenrir, am_michael, am_puck, af_heart, bm_george (Kokoro voices)"),
    ("audit_enabled", "yes = once a week a channel audit names ONE fix for the week (Audit tab, main.py audit)"),
    ("audit_every_days", "days between audits"),
    ("timezone", "e.g. Asia/Kolkata"),
    ("ai_provider", "openai / gemini / offline"),
    ("caption_language", "auto / en / hinglish"),
    ("ai_effort_moments", "how hard the AI thinks when PICKING the moments (what grabs viewers): medium (tested 2026-11-20: tighter, sharper picks; ~Rs 1 more per VIDEO) / low / high"),
    ("ai_effort_packaging", "how hard the AI thinks when writing titles/captions: low (chosen 2026-11-20: same quality, about half the cost) / medium / high"),
    ("editing_preset", "viral_auto (trending format: face on top or black card) / card / facecam_top / auto / streamer_split / game_full"),
    ("music_mode", "library / generated / none"),
    ("sheet_poll_seconds", "how often the farm reads this sheet"),
]
