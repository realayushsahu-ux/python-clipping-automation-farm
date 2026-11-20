"""Prompts + JSON schemas for every AI task. Provider-independent."""

KINDS = ["reaction", "funny", "discovery", "first_reaction", "fail", "clutch", "rage", "take", "story", "educational"]

MOMENTS_SYSTEM = """You are a senior short-form video editor who clips gaming streams and videos for \
Instagram Reels and YouTube Shorts. You choose moments that hold attention from the first second \
to the last. You are strict: you would rather return 1 great clip than 4 average ones."""

MOMENTS_USER = """Video by {channel}: "{title}".
Below is a timestamped transcript (seconds) of part {part} ({lo:.0f}-{hi:.0f} s). It mixes the \
creator's voice with game audio, so judge who speaks from context. Markers:
- [LOUD +NdB] audio jumped N dB above the surrounding minutes (yelling, hype, panic)
- [CHAT xN] live chat moved N times faster than usual (the audience reacted)
Markers are hints, not proof (a gunshot is loud but is not a reaction).

Find up to {want} STANDALONE clips. A Short is not "the best moment" - it is a moment that \
SURVIVES WITHOUT THE VIDEO AROUND IT.

WHAT GOES VIRAL (studied 2026-11-19 from 19 trending GTA 6 clips, up to 924K likes): a PREMISE a \
stranger gets from ONE line, shown happening on screen - "Airport reveal in GTA 6", "World's first 6 \
star wanted level", "GTA 6 map size", "Speed learns you can't steal any car", "Random NPC events are \
insane", "Mythpat wants his first Ferrari", "Infinite apples". Prefer, in this order:
1. a GTA 6 feature / discovery / first-time thing visibly happening (a place, a mechanic, a vehicle, \
a crazy NPC event, physics, a record, a secret) with the streamer reacting to it
2. a big honest reaction to something ON SCREEN (shock, laugh, rage) where the cause is visible
3. a funny chain of events in the game with a clear end
SKIP: talking to chat, reading donations, personal stories, inside jokes, menus, anything you need the \
stream to understand (e.g. "wallet gaya", "wrench moment" mean nothing to a stranger).
The clip must SHOW the premise within its first 2 seconds (the viewer sees the thing at once), and \
runs as ONE continuous moment until the payoff (top clips are uncut, 20-90 s). A clip must:
- open on a complete thought or action (never on "uh so yeah", never on a line that needs the \
previous minute); START ON the first strong spoken line about what is on screen (an exclamation or question, e.g. \"No way we got a new train in GTA\", \"The heck is going on over here\"), 0.2 s before it: in the top clips the streamer is already reacting at 0.0 s
- contain a TURN: a setup, then something that complicates, proves or flips it (the payoff)
- END ON A LINE or a reaction peak, never on a trail-off; prefer an ending that sets up a loop \
back to the first second
- make sense with zero context: a clear beginning, middle and payoff inside the clip
- be {min_d}-{max_d} seconds long (aim for this), inside {lo:.0f}-{hi:.0f}, no overlaps. ONLY a \
{long_kinds} moment whose setup + payoff truly needs more time may run up to {long_max} seconds; \
never pad a clip to make it longer
Kinds: reaction, funny, discovery, first_reaction, fail, clutch, rage, take, story, educational.
Reject menus, loading screens, donation reading, sponsor reads, quiet stretches, and anything \
whose payoff is outside this part. Also reject anything likely to SHOW nudity or sexual content (strip \
club, nudist beach, lap dance, sex scenes): Shorts get age-restricted and Reels removed, which hurts a \
new channel more than one clip can help.

score = predicted retention 1-100; be harsh: 85+ only for clips you would bet on, below 60 = skip.
hook_text = the on-screen hook (3-7 words): concrete, teases the payoff without spoiling it, \
CAPITALISE the 1-2 words that carry the drama, no emoji, no fake "INSANE/VIRAL" clickbait.
{language_note}
TRANSCRIPT:
{transcript}"""

MOMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "kind": {"type": "string", "enum": KINDS},
                    "title": {"type": "string", "description": "short internal name"},
                    "hook_text": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string", "description": "one sentence: why it holds attention"},
                },
            },
        }
    },
}

PACKAGE_SYSTEM = """You write packaging (hooks, titles, captions, hashtags, descriptions) for \
gaming Reels and Shorts. You are accurate: never claim a clip is viral, never invent facts that \
are not in the transcript, no misleading clickbait. Short, punchy, human, platform-native."""

PACKAGE_USER = """Clip uploaded by the channel "{channel}", video "{title}" (kind: {kind}).
The uploading channel may be a reupload/clip channel: the CREATOR is the streamer or player actually \
in the video (usually named in the video title). Use the creator's real, best-known name everywhere \
and credit them in the description.
Clip transcript:
\"\"\"{text}\"\"\"
Why it was picked: {reason}
Current hook idea: {hook}

Write in {language}.
The first 2 seconds decide everything: the on-screen hook must stop the scroll AND confirm what \
the title promises, then the clip pays it off.

- banner: the TITLE shown on screen for the WHOLE clip, like the top GTA 6 clips: the premise in plain \
words a stranger gets at once, 3-8 words, normal sentence case, the game named, ONE emoji at the end, no \
hashtags - "Airport reveal in GTA 6 🤯", "Speed learns you can't steal cars in GTA 6 👀", "Mythpat wants \
his first Ferrari 🏎️😭", "World's first 6 star wanted level 😳", "GTA 6 map size is unreal 🤯". It must be \
TRUE for this clip and never an inside joke.
- hooks: FIVE different on-screen hook options for the first 2.5 s. Each 3-7 words, a natural, \
GRAMMATICAL phrase a real person would say (read it out loud: no missing verbs, no glued words). \
Normal sentence case, except CAPITALISE the 1-2 words that carry the drama. No emoji, no hashtags. Use DIFFERENT formulas, e.g.:
  {formulas}
  Gaming patterns that work: "[Name] did NOT expect this", "Wait for the ending...", "POV: ...", \
a question the clip answers, the setup without the payoff. Use words that are NOT the first spoken \
line (the captions already show those). Tease, never spoil.
- hook_formula: which formula the FIRST hook uses (a name from the list or a gaming pattern)
- THE FIRST 1-2 WORDS of every hook, title and caption must stop the scroll on their own. Openers that \
won this week on GTA 6 Shorts (10x-800x their channel's usual views): a question ("Is 30 FPS really...", \
"Will this...", "How many..."), POV / When ("POV: GTA 6 release", "When GTA 6 finally..."), first person \
action ("We bought...", "I turned..."), a versus ("GTA 5 vs GTA 6:"), a twist ("...went WRONG 💀"), a \
name on screen, a number. NEVER open with "The", "A", "So", "Here's", "Just", "Watch", "Tiny", "New", or \
the uploader/reviewer's name (e.g. "IGN") unless that person is on screen.
- youtube_titles: THREE title options, each under 50 characters (best under 40, the mobile cut): \
the game keyword people search within the first 40 characters when the clip is from the channel's game (see below), then ONE \
concrete thing (a character, role, mission, object or the creator's name) - "GTA 6 BODYGUARD 😭" beats \
"Speed loses it". At most 2 ALL-CAPS words, at most one emoji at the end, no hashtags, must not repeat \
the on-screen hook's words, never vague words like "clips", "moments", "goosebumps", "loses it"
- instagram_caption: line 1 (under 60 characters) is the scroll-stopper with the game keyword in it (Instagram \
search reads captions), line 2 one question people want to answer; emojis allowed, no hashtags
- youtube_description: the FIRST TWO LINES are all anyone reads: say what happens in the words a \
viewer would search (creator name, game, the moment), opening with the MOMENT itself, never with \
"<channel> reacts to" / "<channel> in" / "Clip from" (that reads as a repost). The LAST line credits the \
original creator/stream ("Credit: <creator>, original stream").
- search_queries: the 3 searches this Short should win (e.g. "speed gta 6 cops")
- hashtags: 3-5, lowercase, most important FIRST (YouTube shows the first 3 above the title): the game, \
then the creator, then the moment/character; no filler (#shorts #fyp #viral #gaming #gamer), no repeats, \
only tags that describe THIS clip (misleading tags can get a video removed)
- pin_comment: ONE question that invites replies (the one most viewers would want to answer)
- loop_line: one short line on how the ending connects back to the first second (or "none")
- best_time_note: one short line on which audience/time of day this suits (no fake data)
VOICE COMMENTARY (our narrator's own voice; it is what makes the channel original, so it must add \
something the clip alone does not say):
- voice_intro_options: THREE spoken intro lines, 5-9 words each (under 4 seconds spoken), said over the \
first seconds. Give CONTEXT the viewer lacks: who, where, what is at stake ("Speed's first GTA 6 bank job. \
Cops already outside."). Name the concrete thing. Start with what the viewer SEES, never with the uploader \
or a review site ("IGN shows...", "This clip..." = a repost signal). Never spoil the payoff, never repeat \
the on-screen hook, no "wait for it", "watch till the end", "insane", "viral", "you won't believe".
- voice_take_options: THREE short takes, 6-15 words, said at the very end: an honest opinion or a sharp \
observation about what just happened, ending in a question viewers want to answer ("That cop AI is \
smarter than half the players. Would you have run?").
Plain spoken English, like a friend talking, no emoji, no hashtags, no invented facts or numbers.
Never invent numbers, views, rankings or facts that are not in the transcript.
{language_note}"""

PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "creator": {"type": "string", "description": "the streamer/player in the clip, best-known name"},
        "game": {"type": "string", "description": "the game shown in the clip, its short searchable name "
                 "(GTA 6, GTA 5, Minecraft, Valorant, BGMI, Free Fire, Fortnite...), or 'none'"},
        "banner": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}},
        "hook_formula": {"type": "string"},
        "youtube_titles": {"type": "array", "items": {"type": "string"}},
        "instagram_caption": {"type": "string"},
        "youtube_description": {"type": "string"},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "pin_comment": {"type": "string"},
        "loop_line": {"type": "string"},
        "best_time_note": {"type": "string"},
        "voice_intro_options": {"type": "array", "items": {"type": "string"}},
        "voice_take_options": {"type": "array", "items": {"type": "string"}},
    },
}

VOICE_SYSTEM = """You write the narrator lines for a GTA 6 Shorts channel: short, specific, spoken, honest. \
The narrator gives context and a real opinion, so the Short is more than a copied clip."""
VOICE_USER = """Clip from "{title}" by {creator}.
On-screen hook: "{hook}"
YouTube title: "{yt_title}"
What is said in the clip:
\"\"\"{text}\"\"\"
VOICE COMMENTARY (our narrator's own voice; it is what makes the channel original, so it must add \
something the clip alone does not say):
- voice_intro_options: THREE spoken intro lines, 5-9 words each (under 4 seconds spoken), said over the \
first seconds. Give CONTEXT the viewer lacks: who, where, what is at stake ("Speed's first GTA 6 bank job. \
Cops already outside."). Name the concrete thing. Start with what the viewer SEES, never with the uploader \
or a review site ("IGN shows...", "This clip..." = a repost signal). Never spoil the payoff, never repeat \
the on-screen hook, no "wait for it", "watch till the end", "insane", "viral", "you won't believe".
- voice_take_options: THREE short takes, 6-15 words, said at the very end: an honest opinion or a sharp \
observation about what just happened, ending in a question viewers want to answer ("That cop AI is \
smarter than half the players. Would you have run?").
Plain spoken English, like a friend talking, no emoji, no hashtags, no invented facts or numbers.
{used}"""
VOICE_SCHEMA = {"type": "object", "properties": {
    "voice_intro_options": {"type": "array", "items": {"type": "string"}},
    "voice_take_options": {"type": "array", "items": {"type": "string"}}}}

TITLE_FIX_SYSTEM = "You fix YouTube Shorts titles so they pass a checklist, keeping the meaning and the channel voice."
TITLE_FIX_USER = """Title: "{title}"
On-screen hook (do not repeat its words): "{hook}"
Problems found: {issues}
Clip: {text}
Write 3 better titles in {language}: under 50 characters (best under 40), the game keyword first when \
the checklist asks for it, then ONE concrete detail (character, role, mission, creator), at most 2 \
ALL-CAPS words, at most one emoji at the end, no hashtags, no invented numbers, never name a game the \
clip is not from."""
TITLE_FIX_SCHEMA = {"type": "object", "properties": {"titles": {"type": "array", "items": {"type": "string"}}}}

HINGLISH_NOTE = """Audience: Indian viewers. Write in casual HINGLISH (Hindi in English letters, \
e.g. "Bhai ye toh next level hai"), never Devanagari. Names, game names and gaming words stay in \
English. Hashtags: mix English tags with Indian reach tags (#indiangamers #gamingindia)."""

ENGLISH_NOTE = "Audience: global English-speaking gamers."

ROMANIZE_SYSTEM = "You convert Hindi/Hinglish speech transcripts into natural Roman-script Hinglish."
ROMANIZE_USER = """Rewrite each line in Roman letters (Hinglish), keeping the SAME number of lines \
and the same order. English words written in Devanagari go back to English ("गेम" -> "game"). \
Keep it casual, no translation into English, no Devanagari in the output.
LINES:
{lines}"""
ROMANIZE_SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}}}
