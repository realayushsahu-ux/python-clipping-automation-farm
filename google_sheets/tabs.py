"""The 7 tabs of the control panel: names, columns, which columns YOU type in, dropdowns.

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

TABS = {
    VIDEO_INPUT: {
        # you type: URL (+ optional Clips Wanted, Language, Section, Layout, Platforms). Status: type retry / redo
        "headers": ["URL", "Clips Wanted", "Language", "Section", "Layout", "Platforms",
                    "Video ID", "Status", "Title", "Clips Made", "Added At", "Updated At", "Message"],
        "widths": [320, 90, 80, 130, 80, 140, 110, 100, 280, 80, 130, 130, 300],
        "dropdowns": {3: ["auto", "en", "hi"], 5: ["auto", "split", "full"],
                      8: ["new", "retry", "redo"]},
    },
    PROCESSING: {
        "headers": ["Item", "Title", "Stage", "Progress", "Attempts", "Updated At", "Message"],
        "widths": [110, 280, 110, 220, 70, 130, 320],
    },
    READY: {
        # you type: Approve (yes / no / remake / posted), Post Time (YYYY-MM-DD HH:MM), Notes
        # Folder = link: opens the clip's folder in Explorer and plays the video (needs `main.py watch` or `serve`)
        "headers": ["Clip ID", "Post Time", "Approve", "Status", "Folder", "Platforms", "On-screen Hook",
                    "YouTube Title", "Instagram Caption", "Hashtags", "YouTube Description", "Pin Comment",
                    "Length (s)", "Score", "Kind", "Creator", "Video ID", "Notes"],
        "widths": [90, 130, 80, 120, 150, 130, 200, 260, 300, 260, 300, 240, 70, 60, 80, 110, 110, 200],
        "dropdowns": {3: ["yes", "no", "remake", "posted", "retry"]},
    },
    SCHEDULE: {
        "headers": ["Post Time", "Clip ID", "Platform", "Status", "Attempts", "Last Error", "YouTube Title"],
        "widths": [130, 90, 90, 130, 70, 300, 280],
    },
    POSTED: {
        # Post URL: paste the link here for clips you posted by hand
        "headers": ["Posted At", "Clip ID", "Platform", "Post URL", "Folder", "External ID", "Title", "Views",
                    "Likes", "Comments"],
        "widths": [130, 90, 90, 300, 150, 160, 280, 70, 70, 80],
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
    ("editing_preset", "auto / streamer_split / game_full"),
    ("music_mode", "library / generated / none"),
    ("sheet_poll_seconds", "how often the farm reads this sheet"),
]
