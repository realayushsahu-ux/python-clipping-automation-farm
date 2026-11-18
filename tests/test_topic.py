"""Lessons from the first posts, as rules: GTA 6 keyword first, 3-5 clean hashtags, no misleading tags,
off-topic videos stop before any work. Offline AI, no internet.

Run:  .venv\\Scripts\\python.exe -m tests.test_topic
"""

from ai import quality
from ai.caption_generator import finish_package, is_topic
from config.settings import load_settings
from core.pipeline import _check_topic
from core.retry import PermanentError


def main():
    st = load_settings()
    st["ai_provider"], st["ai_fallback_provider"] = "offline", "offline"

    # 1. topic words: GTA 6 in any spelling; GTA 5 is not GTA 6
    for t in ("GTA 6 Review", "gta6 first day", "GTA VI map", "Grand Theft Auto VI trailer", "GTA-6 BODYGUARD"):
        assert quality.on_topic(st, t), t
    for t in ("iShowSpeed's First Day In GTA 5 RP", "GTA V RP hospital", "Minecraft"):
        assert not quality.on_topic(st, t), t

    # 2. hashtags: game first, max 5, one filler at most, no repeats, misleading tag dropped
    tags = quality.clean_hashtags(["#gaming", "#shorts", "#ishowspeed", "#gta6", "#gamer", "#bodyguard", "#gta",
                                   "#viral", "#gta6"], 5, first="#gta6")
    assert tags == ["#gta6", "#gaming", "#ishowspeed", "#bodyguard", "#gta"], tags
    assert "#gta6" not in quality.clean_hashtags(["#gta6", "#gta5rp"], 5, drop={"#gta6"})

    # 3. title lint: keyword missing / late / misleading
    assert any(i[0] == "no-keyword" for i in quality.lint_title("Speed loses it in RP", keyword="GTA 6")["issues"])
    assert not any(i[0].startswith("no-key") for i in quality.lint_title("GTA 6 BODYGUARD job 😭", keyword="GTA 6")["issues"])
    assert quality.lint_title("IGN Reviews GTA 6 Visuals 😳", keyword="GTA 6")["score"] <= 60
    assert not any(i[0] == "keyword-late" for i in quality.lint_title("GTA-6 BODYGUARD job 😭", keyword="GTA 6")["issues"])
    assert any(i[0] == "vague" for i in quality.lint_title("GTA 6 clips give goosebumps", keyword="GTA 6")["issues"])
    assert any(i[0] == "misleading" for i in quality.lint_title("GTA 6 Speed RP day", wrong=("gta 6",))["issues"])

    # 3b. first 1-2 words: the openers of this week's biggest GTA 6 Shorts pass, weak/uploader openers fail
    for t in ("Is 30 FPS really a problem for GTA 6?", "Come with me to pick up GTA 6!", "POV: GTA 6 Release",
              "GTA 5 vs GTA 6: empty map?", "When GTA 6 Finally Comes Out", "NEW TV for GTA 6 went WRONG"):
        assert quality.opener(t)[0], t
        assert not any(i[0] in ("weak-opener", "keyword-late") for i in quality.lint_title(t, keyword="GTA 6")["issues"]), t
    for t in ("IGN Reviews GTA 6 Visuals", "The GTA 6 map", "Tiny visual moments in GTA 6", "Here's GTA 6 rain",
              "Just GTA 6 things"):
        assert not quality.opener(t)[0], t
        assert quality.lint_title(t, keyword="GTA 6")["score"] <= 60, t
    assert quality.hook_fit("Tiny details matter HERE")[0] < quality.hook_fit("You can rob BANKS now")[0]
    from ai.caption_generator import ig_caption
    assert ig_caption("The map is big\nWould you drive it?", "GTA 6 map vs GTA 5 😳").startswith("GTA 6 map vs")
    assert ig_caption("POV: GTA 6 rain hits different\nAgree?", "x").startswith("POV:")

    # 4. a GTA 6 clip: GTA 6 title wins, #gta6 first and in the title, ≤5 tags
    info = {"title": "IGN · Grand Theft Auto 6 (GTA 6) Review", "channel": "IGN"}
    out = {"creator": "IGN", "hooks": ["The cops did NOT see this"],
           "youtube_titles": ["Cops lose it in Vice City", "GTA 6 cop chase goes WRONG 😭", "Crazy moments"],
           "hashtags": ["#gaming", "#fyp", "#ign", "#vicecity", "#copchase", "#review", "#gta"],
           "youtube_description": "GTA 6 cop chase in Vice City.\nCredit: IGN"}
    res = finish_package(out, {"kind": "funny"}, info, "the police chase", "English", st)
    assert res["youtube_title"].startswith("GTA 6 cop chase") and res["youtube_title"].endswith("#gta6"), res
    assert res["hashtags"][0] == "#gta6" and len(res["hashtags"]) <= 5 and res["on_topic"], res["hashtags"]

    # 5. a GTA 5 clip: never GTA 6 in title or tags
    info5 = {"title": "iShowSpeed's First Day In GTA 5 RP", "channel": "Speed clips"}
    out5 = {"creator": "iShowSpeed", "hooks": ["Speed did NOT expect this"],
            "youtube_titles": ["GTA 6 Speed hospital trip", "iShowSpeed ends up in HOSPITAL 😭"],
            "hashtags": ["#gta6", "#ishowspeed", "#gta5rp"]}
    r5 = finish_package(out5, {"kind": "funny"}, info5, "hospital", "English", st)
    assert "gta 6" not in r5["youtube_title"].lower() and "#gta6" not in r5["hashtags"], r5
    assert not r5["on_topic"] and not is_topic(st, info5)

    # 5b. any other game: title names THAT game, its hashtag first, never #gta6
    outm = {"creator": "Technoblade", "game": "Minecraft", "hooks": ["He did NOT see the CREEPER"],
            "youtube_titles": ["The funniest fail ever 😭", "Minecraft creeper ruins his house 😭"],
            "hashtags": ["#gta6", "#technoblade", "#creeper", "#gaming"]}
    rm = finish_package(outm, {"kind": "fail"}, {"title": "Technoblade plays Minecraft", "channel": "x"},
                        "the creeper blew up the house", "English", st)
    assert rm["youtube_title"].startswith("Minecraft creeper") and rm["youtube_title"].endswith("#minecraft"), rm
    assert rm["hashtags"][0] == "#minecraft" and "#gta6" not in rm["hashtags"] and rm["game"] == "Minecraft", rm
    assert st["only_topic_videos"] is False                  # any game is welcome by default

    # 6. only_topic_videos = yes: off-topic video stops before any work; no → allowed
    st["only_topic_videos"] = True
    try:
        _check_topic(info5, st)
        raise AssertionError("GTA 5 video was not stopped")
    except PermanentError as exc:
        assert "only_topic_videos" in str(exc)
    _check_topic(info, st)
    st["only_topic_videos"] = False
    _check_topic(info5, st)
    print("test_topic: all checks passed")


if __name__ == "__main__":
    main()
