"""Phase 2 checks for AI packaging + the YouTube-skills quality gate.

Run:  .venv\\Scripts\\python.exe -m tests.test_package          (fake AI, no key, no cost)
      .venv\\Scripts\\python.exe -m tests.test_package --live   (real OpenAI/Gemini from .env, ~1 cent)
"""

import json
import sys

import ai.base as base
from ai import quality
from ai.caption_generator import package
from config.settings import load_settings

MOMENT = {"kind": "funny", "hook_text": "Speed tried to ESCAPE the cops", "reason": "cops pull up mid-escape"}
INFO = {"channel": "IShowSpeed", "title": "GTA RP with the boys | day 3"}
TEXT = ("bro what is this some weird stuff right here no no no bro the cops are here bro go go go "
        "why is he following me bro I didn't even do anything")


class FakeAI(base.AIProvider):
    name = "fake"

    def __init__(self):
        self.systems = []

    def json(self, system, user, schema, max_tokens=4000, effort=None):
        self.systems.append(system)
        if "titles" in schema["properties"] and len(schema["properties"]) == 1:  # title fix round
            return {"titles": ["IShowSpeed vs the cops went WRONG 😭", "Speed's escape plan failed"]}
        return {
            "creator": "IShowSpeed",
            "hooks": ["This went VIRAL for a reason", "bro what is this some weird", "He did NOT see them coming 😭",
                      "Speed vs the COPS", "The cops were ALREADY waiting"],
            "hook_formula": "The Reveal",
            "youtube_titles": ["THE MOST INSANE CRAZY GTA RP MOMENT EVER HAPPENED TODAY ON STREAM WITH SPEED",
                               "this is crazy"],
            "instagram_caption": "Speed thought he was safe 💀\nThis clip has 10M views already\nWhat would you do? 👇",
            "youtube_description": "IShowSpeed tries to escape the cops in GTA RP.\nClip from his stream.",
            "search_queries": ["speed gta rp cops", "ishowspeed gta", "speed escape cops"],
            "hashtags": ["IShowSpeed", "#gta", "#viralvideo", "#gta", "gtarp", "#shorts"],
            "pin_comment": "Would you have stopped? 👇",
            "loop_line": "ends on him asking why, the first second shows why",
            "best_time_note": "evening, teen gamers",
        }


def fake_test():
    s = load_settings()
    fake = FakeAI()
    base.providers = lambda settings: [fake]
    p = package(MOMENT, INFO, TEXT, "en", s)
    print(json.dumps({k: p[k] for k in ("hook", "youtube_title", "hashtags", "instagram_caption", "quality")},
                     indent=1, ensure_ascii=False))
    assert "VIRAL" not in p["hook"] and "😭" not in p["hook"], "claims/emoji hooks must lose"
    assert p["hook"] != "bro what is this some weird", "hook must not repeat the spoken line"
    assert len(fake.systems) == 2, "a weak title must get exactly one rewrite round"
    assert p["youtube_title"].startswith(("IShowSpeed", "Speed")), p["youtube_title"]
    assert "10M views" not in p["instagram_caption"], "fake numbers must be stripped"
    assert "#viralvideo" not in p["hashtags"] and p["hashtags"].count("#gta") == 1
    assert "CHANNEL VOICE" in fake.systems[0], "config/voice.md must reach the AI"
    assert quality.lint_title("IShowSpeed did NOT see this coming 😭", "Speed vs the COPS", "IShowSpeed")["score"] >= 75
    # real bad hooks gpt-5-mini wrote on 2026-11-17 must lose to normal ones
    for bad in ("Speed did NOT THIS", "Who Told Him To DoThat?", "Wait For The Loud FAIL", "iShowSpeed did NOT expect"):
        assert quality.hook_fit(bad)[0] < quality.hook_fit("Speed did NOT expect THIS")[0], bad
    print("all package tests passed")


def live_test():
    s = load_settings()
    names = [p.name for p in base.providers(s)]
    print("providers:", names)
    if names == ["offline"]:
        print("No OPENAI_API_KEY / GEMINI_API_KEY in .env - add one first.")
        return
    p = package(MOMENT, INFO, TEXT, "en", s)
    print(json.dumps(p, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    live_test() if "--live" in sys.argv else fake_test()
