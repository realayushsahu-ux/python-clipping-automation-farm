"""Voice commentary rules: weak lines are dropped, timing/ducking are right, the AI lines flow into the package.
Offline (no AI, no TTS needed except the last check, which runs only if Kokoro is installed).

Run:  .venv\\Scripts\\python.exe -m tests.test_voice
"""

import numpy as np

from ai.caption_generator import finish_package
from config.settings import load_settings
from core.media import SR
from editor import voiceover as vo


def main():
    ctx = "IGN GTA 6 review the sunrise over Vice City and the police chase on the highway"

    # 1. the gate: generic, claims, too long/short, names nothing, repeats the hook → dropped
    assert vo.check_line("This GTA 6 sunrise beats every open world", "intro", ctx)[0]
    # 1b. (2026-11-19) intros that open with the uploader / "this clip" sound like a repost → dropped
    for bad in ["IGN says this GTA 6 sunrise beats every open world", "Here's IGN's take on the Vice City sunrise",
                "This clip proves the GTA 6 police chase is real"]:
        assert "repost" in vo.check_line(bad, "intro", ctx)[1], bad
    assert vo.check_line("IGN gets chased by GTA 6 police. Would you run", "take", ctx)[0]   # takes may name them
    assert "words" in vo.check_line("Vice City sunrise and the police chase on the highway tonight", "intro", ctx)[1]
    # 1c. one narrator retry per clip when no intro survived, never for offline packages
    from core.pipeline import needs_voice_retry
    assert needs_voice_retry({}, None) and needs_voice_retry({}, {"take_text": "x?"})
    assert not needs_voice_retry({}, {"intro": {}}) and not needs_voice_retry({"_voice_retry": True}, None)
    assert not needs_voice_retry({"_offline": True}, None)
    # 1d. saved packages are re-checked: a stale "IGN ..." intro gives way to a good alt, or to nothing
    p = {"voice_intro": "IGN tests GTA 6 car sounds", "voice_intro_alts": ["GTA 6 supercharger whine, full volume"]}
    assert vo.recheck_saved(p) and p["voice_intro"].startswith("GTA 6 supercharger") and p["voice_intro_alts"] == []
    p = {"voice_intro": "IGN's review shows GTA 6 removes the police radar."}
    assert vo.recheck_saved(p) and p["voice_intro"] == ""
    p = {"voice_intro": "Jason steals a yacht in broad daylight"}
    assert vo.recheck_saved(p) == [] and p["voice_intro"].startswith("Jason")
    for bad, why in [("Wait for it, this one is insane", "generic"), ("This went viral with a million views", "generic"),
                     ("Nice", "words"), ("You have to see what happens here today guys", "names nothing"),
                     ("one two three four five six seven eight nine ten eleven twelve thirteen sunrise", "words")]:
        line, reason = vo.check_line(bad, "intro", ctx)
        assert not line and why.split()[0] in reason, (bad, reason)
    assert vo.check_line("The police chase on the highway", "intro", ctx, hook="The police chase on the highway")[1]
    assert vo.check_line("Would you run from that police chase", "take", ctx)[0].endswith("?")
    assert vo.check_line("Vice City sunrise again here", "intro", ctx, used=["Vice City sunrise again here"])[1]

    # 2. pick_lines takes the first option that passes and says why the others were dropped
    r = vo.pick_lines({"voice_intro_options": ["Wait for it", "Speed meets the Vice City police for the first time"],
                       "voice_take_options": ["Like and subscribe", "Those police are smarter than most players. Would you run"]},
                      ctx + " speed first time players", "")
    assert r["voice_intro"].startswith("Speed meets") and r["voice_take"].endswith("?"), r
    assert len(r["voice_notes"]) == 2

    # 3. timing: intro from 0, take overlaps the end by 1.2 s, freeze only for what does not fit
    i_end, t0, extra = vo.plan({"intro": {"dur": 3.0}, "take": {"dur": 4.0}}, 40.0)
    assert abs(i_end - 3.15) < 1e-6 and abs(t0 - 38.8) < 1e-6 and abs(extra - 3.15) < 1e-6
    assert vo.plan({"intro": {"dur": 3.0}}, 40.0) == (3.15, None, 0.0)
    assert vo.plan({"take": {"dur": 2.0}}, 40.0)[2] == 0.0 + max(0.0, 38.8 + 2.0 + 0.35 - 40.0)

    # 4. ducking: clip sound low under the narrator, full elsewhere; caption words cover the line
    g = vo.duck_env(SR * 6, [(1.0, 3.0)], 0.22)
    assert g[0] == 1.0 and abs(g[int(2 * SR)] - 0.22) < 1e-9 and g[int(5 * SR)] == 1.0
    ws = vo.word_times("GTA 6 cops are fast", 1.0, 2.0, -1)
    assert ws[0]["t0"] == 1.0 and abs(ws[-1]["t1"] - 3.0) < 1e-9 and ws[-1]["pend"] and len(ws) == 5
    loud = vo.level_match(np.full((SR, 2), 0.5), np.full((SR, 2), 0.1))
    assert 0.13 < np.sqrt((loud ** 2).mean()) < 0.15          # 1.4 × the clip speech level

    # 5. package: AI options → checked lines in the result; switch off → prepare() returns None
    st = load_settings()
    st["ai_provider"], st["ai_fallback_provider"] = "offline", "offline"
    out = {"creator": "IGN", "hooks": ["The cops did NOT see this"], "youtube_titles": ["GTA 6 highway police chase 😭"],
           "hashtags": ["#ign"], "voice_intro_options": ["GTA 6 police take a highway chase"],
           "voice_take_options": ["Those cops drive better than me. Would you even try running"]}
    res = finish_package(out, {"kind": "funny"}, {"title": "IGN GTA 6 Review", "channel": "IGN"}, ctx, "English", st)
    assert res["voice_intro"].startswith("GTA 6 police") and res["voice_take"].endswith("?"), res
    st["voice_commentary"] = False
    assert vo.prepare(res, st) is None
    st["voice_commentary"] = True

    # 5b. end comment as TEXT (default): no take audio, on-screen parts over the last seconds, intro still spoken
    for t in ("a b c d e f g h", "a b c d e f g h i j k l m", "a b c d e f g h i j k l m n o p q r s"):   # 2-3 lines, no crash
        ps = vo.text_parts(t, 40.0)
        assert " ".join(p[2] for p in ps) == t and ps[-1][1] == 40.0, ps
    parts = vo.text_parts("This fixes a long GTA inconsistency. Would you rob one?", 40.0)
    assert parts[-1][1] == 40.0 and parts[0][0] >= 34.0 and parts[-1][2].endswith("?"), parts
    st["voice_take_mode"] = "text"
    if vo.available():
        v = vo.prepare(res, st)
        assert "take" not in v and v["take_text"] == res["voice_take"] and "intro" in v, v
    st["voice_take_mode"] = "off"
    if vo.available():
        assert "take_text" not in vo.prepare(res, st)
    st["voice_take_mode"] = "voice"

    # 6. real speech (only when Kokoro + models are installed)
    if vo.available():
        v = vo.prepare(res, st)
        assert v and 1.0 < v["intro"]["dur"] < vo.MAX_INTRO_S and v["intro"]["audio"].shape[1] == 2, v
        print(f"   kokoro: intro {v['intro']['dur']:.1f}s, take {v.get('take', {}).get('dur', 0):.1f}s")
    print("test_voice: all checks passed")


if __name__ == "__main__":
    main()
