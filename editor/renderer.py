"""Renders one finished clip: frames (crop, motion, grade, transitions) piped into FFmpeg,
captions burned with libass, sound design mixed and loudness-normalised.

Output: <out_dir>/<name>.mp4 and, if enabled, <name>_NOMUSIC.mp4 (same edit, no music:
for adding a trending sound inside the Instagram / YouTube app).
"""

import math
import subprocess
from pathlib import Path

import numpy as np

from core.logger import get_logger
from core.media import SR, pcm, run, write_wav
from editor import captions as cap
from editor import clip_generator as gen
from editor import effects, sound, voiceover

log = get_logger("renderer")


def _phrases(words):
    """Group words into phrases (punctuation or a pause ends a phrase)."""
    phrases, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w["w"].rstrip().endswith((".", "!", "?")) or nxt is None or nxt["t0"] - w["t1"] > 0.6:
            phrases.append(cur)
            cur = []
    return phrases


def resolve_preset(presets, name, layout_found=None):
    if name in (None, "", "auto"):
        name = "streamer_split" if layout_found == "split" else "game_full"
    p = dict(presets["presets"][name])
    p["name"] = name
    p["caption_style"] = presets["caption_styles"][p["captions"]]
    p["hook_style"] = presets["hook_overlays"][p.get("hook_overlay", "none")]
    p["motion_cfg"] = presets["motion"][p["motion"]]
    p["grade_cfg"] = presets["color_grades"][p["color_grade"]]
    p["sound_cfg"] = presets["sound"][p["sound"]]
    p["export_cfg"] = presets["export"]
    return p


def render_clip(src: Path, words_src: list, cs: float, ce: float, hook: str, presets: dict, settings,
                out_dir: Path, name: str, workdir: Path, variant: int = 0, preset_name="auto", layout="auto",
                music_mood=None, max_seconds=None, voice=None) -> dict:
    """words_src: [{w, s, e}] in seconds of `src`. cs/ce: clip start/end in `src`.
    voice: editor/voiceover.prepare() result (narrator intro + take) or None = no commentary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    # ---- analysis + preset
    cuts, faces, n_src = effects.analyse(src)
    lay = effects.plan_layout(layout, cuts, faces, n_src)
    P = resolve_preset(presets, preset_name, lay["layout"])
    if P["layout"] == "facecam_or_card":                 # face on top when a facecam is found, else card
        # face on top only for a clear, big facecam (small corner cams, e.g. CarryMinati, get locked onto game
        # faces in cutscenes → the whole frame on a black card instead, like the top clips of such streams)
        cam = lay.get("cam")
        big = (cam is not None and lay.get("cam_kind") == "big"
               and float(cam[2]) >= float(P.get("min_cam_face", 0.09))) or (layout == "split" and cam is not None)
        P["layout"] = "split" if (lay["layout"] == "split" and big) else "card"
    if lay.get("layout") == "split":
        lay["cam_mult"], lay["cam_min"] = float(P.get("cam_face_mult", 3.3)), float(P.get("cam_min_crop", 0.28))
        if P["layout"] == "split":
            sp = presets["presets"].get("facecam_top", {})
            P["title_cfg"] = sp.get("title_cfg", P.get("title_cfg"))
            P["caption_style"] = presets["caption_styles"][P.get("split_captions") or sp.get("captions", P["captions"])]
    if P["layout"] != lay["layout"] and P["layout"] in ("split", "full"):
        lay = effects.plan_layout(P["layout"], cuts, faces, n_src)
    W, H = P["resolution"]
    from core.media import probe as _probe
    _vs = next(x for x in _probe(src)["streams"] if x["codec_type"] == "video")
    _n, _d = (_vs.get("avg_frame_rate") or _vs.get("r_frame_rate") or "30/1").split("/")
    SRC_FPS = float(_n) / max(float(_d), 1.0) or 30.0
    FPS = P.get("fps", 30)
    if FPS == "source":                                  # 60 fps gameplay stays 60 fps (smoother)
        from core.media import probe as _probe
        vs0 = next(x for x in _probe(src)["streams"] if x["codec_type"] == "video")
        num, den = (vs0.get("avg_frame_rate") or vs0.get("r_frame_rate") or "30/1").split("/")
        FPS = 60 if float(num) / max(float(den), 1) >= 50 else 30
    title_band = None
    if P["layout"] == "card":
        from editor import titleband
        vh = int(round(W * 9 / 16))
        vy = int(H * P.get("card_video_y", 0.5)) - vh // 2
        bg = np.zeros((H, W, 3), np.uint8)
        tb = P.get("title_cfg", {})
        band = titleband.render(hook, W, **tb) if hook else None
        if band is not None:
            titleband.paste(bg, band, max(40, vy - band.shape[0] - int(P.get("title_gap", 34))))
        n_fx = max(n_src, 1)
        lay = {"layout": "card", "bg": bg, "vy": vy, "vh": vh, "fx": np.full(n_fx, 0.5), "fy": np.full(n_fx, 0.5),
               "fs": np.full(n_fx, 0.1)}
    elif P.get("title_cfg") and hook:
        from editor import titleband
        title_band = titleband.render(hook, W, **P["title_cfg"])
    mo, so = P["motion_cfg"], P["sound_cfg"]

    aud = pcm(src, SR, channels=2)
    hop = SR // 10
    mono = aud.mean(1)
    env = 20 * np.log10(np.sqrt((mono[: len(mono) // hop * hop].reshape(-1, hop) ** 2).mean(1)) + 1e-6)

    words = [{"w": w["w"], "t0": w["s"], "t1": w["e"]} for w in words_src]
    phrases = _phrases(words)
    for p in phrases:
        for i, w in enumerate(p):
            w["pend"] = i == len(p) - 1

    # ---- decisions
    max_len = float(max_seconds or settings["final_max_seconds"])
    a, b = gen.pick_window(phrases, env, cs, ce, max_len, min(float(settings.get("clip_min_seconds", 22)), ce - cs))
    main = gen.tighten(a, b, words, env, mo["silence_min_gap"], mo["silence_keep"], mo.get("cut_silences", True))
    cold = gen.pick_cold(a, b, words, env, mo["cold_open_seconds"]) if mo.get("cold_open") else None
    segs = ([cold] if cold else []) + main
    timeline, f_out = [], 0
    for s, e in segs:
        nf = int(round((e - s) * FPS))
        timeline.append((s, nf, f_out))
        f_out += nf
    total_f = f_out
    dur = total_f / FPS
    main_start = timeline[1][2] / FPS if cold else 0.0

    # ---- captions
    w_out = []
    for si, (s, nf, fo) in enumerate(timeline):
        e = s + nf / FPS
        for w in words:
            if s <= w["t0"] < e - 0.05:
                w_out.append({"w": w["w"], "t0": fo / FPS + w["t0"] - s, "t1": fo / FPS + min(w["t1"], e) - s,
                              "pend": w["pend"], "seg": si})
    # ---- narrator (voice commentary): intro over the start, take at the end (+ a short freeze if needed)
    clip_dur = dur
    intro_end, take_start, extra = voiceover.plan(voice, dur) if voice else (0.0, None, 0.0)
    extra_f = int(math.ceil(extra * FPS))
    dur = (total_f + extra_f) / FPS
    if voice:
        keep = [w for w in w_out if w["t0"] >= intro_end and (take_start is None or w["t0"] < take_start)]
        vw = []
        if voice.get("intro"):
            vw += voiceover.word_times(voice["intro"]["text"], 0.0, voice["intro"]["dur"], -1)
        if voice.get("take"):
            vw += voiceover.word_times(voice["take"]["text"], take_start, voice["take"]["dur"], -2)
        w_out = sorted(keep + vw, key=lambda w: w["t0"])

    cs_style = P["caption_style"]
    cw = cap.CaptionWriter(cs_style, W, H)
    top_h = int(H * P.get("top_panel_ratio", 0.55))
    cam_top = bool(P.get("cam_on_top")) and lay["layout"] == "split"
    seam = (H - top_h) if cam_top else top_h
    cap_y = seam if (cs_style["position_y"] == "seam" and lay["layout"] == "split") else (
        cs_style["position_y"] if isinstance(cs_style["position_y"], (int, float)) else int(H * 0.59))
    if lay["layout"] == "card":
        cap_y = lay["vy"] + lay["vh"] + int(P.get("card_caption_gap", 150))
    # face on top: hook + closing text go into the gameplay just under the captions, never over the face
    hook_y = seam + int(H * 0.15) if cam_top else P["hook_style"].get("position_y", 330)
    if P["hook_style"].get("enabled") and hook and not P.get("title_cfg"):
        cw.add_hook(hook, hook_y, P["hook_style"]["seconds"])
    if voice and voice.get("take_text"):        # closing comment as on-screen text (no second voice)
        for t0, t1, part in voiceover.text_parts(voice["take_text"], clip_dur):
            cw.add_hook(part, hook_y, t1 - t0, start=t0)
    cw.add_chunks(cap.make_chunks(w_out, cs_style["max_words"], cs_style["max_chars"]), cap_y)
    ass_name = f"{name}.ass"
    cw.save(workdir / ass_name)

    # ---- motion points
    src_t = np.zeros(total_f)
    for s, nf, fo in timeline:
        src_t[fo: fo + nf] = s + np.arange(nf) / FPS
    env_out = env[np.clip((src_t * 10).astype(int), 0, len(env) - 1)]
    punches, shakes = gen.punch_points(env_out, FPS, clip_dur, main_start, bool(cold), mo.get("punch_every_s", 4))

    # ---- audio
    n = int(round(dur * SR))
    dia = np.zeros((n, 2))
    for s, nf, fo in timeline:
        i0, ln = int(s * SR), int(nf / FPS * SR)
        piece = aud[i0: i0 + ln].astype(np.float64)
        fade = min(int(0.012 * SR), len(piece) // 2)
        if fade:
            ramp = np.linspace(0, 1, fade)[:, None]
            piece[:fade] *= ramp
            piece[len(piece) - fade:] *= ramp[::-1]
        o = int(fo / FPS * SR)
        dia[o: o + len(piece)] += piece[: n - o]
    if so.get("noise_reduction"):
        try:
            dia = sound.denoise(dia, workdir)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"noise reduction skipped: {exc}")
    narr = np.zeros((n, 2))
    if voice:                                  # clip sound ducked under the narrator, narrator a bit above it
        wins = []
        duck = np.ones(n)
        for kind, t0 in (("intro", 0.0), ("take", take_start)):
            vo = voice.get(kind)
            if not vo or t0 is None:
                continue
            a_v = voiceover.level_match(vo["audio"], dia)
            i0 = int(t0 * SR)
            ln = min(len(a_v), n - i0)
            if ln > 0:
                narr[i0: i0 + ln] += a_v[:ln]
                low = float(settings.get("voice_duck_intro" if kind == "intro" else "voice_duck", 0.22))
                duck = np.minimum(duck, voiceover.duck_env(n, [(t0 - 0.05, t0 + vo["dur"] + 0.1)], low))
        dia = dia * duck[:, None]           # intro: clip speech almost silent, so only ONE voice talks
    sfx_lib = sound.SFX(seed=variant)
    sfx = np.zeros((n, 2))

    # library overlay (light leak / flare / film burn) on the drop, replaces the plain white flash
    drop_ov = None
    if cold and mo.get("drop_overlay"):
        import random
        ov_path = effects.pick_overlay(mo["drop_overlay"], random.Random(variant * 7 + 3))
        if ov_path:
            try:
                drop_ov = effects.Overlay(ov_path, W, H, FPS, mo.get("drop_overlay_seconds", 1.0),
                                          mo.get("drop_overlay_strength", 1.0))
            except Exception as exc:  # noqa: BLE001
                log.warning(f"overlay {ov_path.name} skipped: {exc}")

    def put(sig, t, g):
        i = int(t * SR)
        if i < 0:
            sig, i = sig[-i:], 0
        if i < n and len(sig) and g > 0:
            sig = sig[: n - i]
            sfx[i: i + len(sig)] += sig * g

    if cold:
        put(sfx_lib.get("riser", 1.0), main_start - 1.0, so["sfx_riser_before_drop"])
        put(sfx_lib.get("boom"), main_start, so["sfx_boom_on_drop"])
        put(sfx_lib.get("whoosh", 0.5), main_start - 0.25, 0.5)
        put(sfx_lib.get("glitch"), main_start, so["sfx_glitch_on_drop"])
        if drop_ov is not None:
            put(sfx_lib.get("shutter", 0.6), main_start - 0.02, so.get("sfx_shutter_on_drop", 0.0))
    for si, (s, nf, fo) in enumerate(timeline):
        if si > (1 if cold else 0):
            put(sfx_lib.get("whoosh", 0.3), fo / FPS - 0.15, so["sfx_whoosh_on_cuts"])
    for t, _ in punches:
        put(sfx_lib.get("whoosh", 0.28), t - 0.14, so["sfx_whoosh_on_punch"])
    for t in shakes:
        put(sfx_lib.get("boom", 0.7), t, so["sfx_boom_on_shake"])

    music, music_name = sound.music_for(max(dur - main_start, 1.0), so.get("music_mode") or settings["music_mode"],
                                        variant, music_mood)
    mus = np.zeros((n, 2))
    if music is not None:
        m0 = int(main_start * SR)
        mus[m0: m0 + len(music)] = music[: n - m0]
    tail = np.ones(n)
    tl = min(int(0.35 * SR), n)
    tail[n - tl:] = np.linspace(1, 0, tl)
    gain = so["music_volume"] * sound.duck_curve(dia + narr, so["music_duck_under_speech"]) * tail
    wav_m, wav_n = workdir / f"{name}_mix.wav", workdir / f"{name}_nomusic.wav"
    raw_m, raw_n = workdir / f"{name}_mix_raw.wav", workdir / f"{name}_nomusic_raw.wav"
    write_wav(raw_m, sound.soft_limit(dia + narr + mus * gain[:, None] + sfx))
    write_wav(raw_n, sound.soft_limit(dia + narr + sfx))
    for a_in, a_out in ((raw_m, wav_m), (raw_n, wav_n)):  # exact -14 LUFS (two-pass), then mux as is
        sound.loudnorm_2pass(a_in, a_out, so["loudness_lufs"], so["true_peak_db"])
        a_in.unlink(missing_ok=True)

    # ---- video
    ex = P["export_cfg"]
    out_path = out_dir / f"{name}.mp4"
    look = effects.lut_filter(P["grade_cfg"], workdir)
    log.info(f"{name}: layout {lay['layout']}, {FPS} fps (source {SRC_FPS:.0f}), {dur:.1f}s, {len(main)} part(s), cold open {'yes' if cold else 'no'}, "
             f"{len(punches)} punch-ins, music {music_name}, LUT {P['grade_cfg'].get('lut') or 'none'}, "
             f"drop overlay {drop_ov.name if drop_ov else 'none'}")
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-i", wav_m.name, "-vf", f"{look}ass={ass_name}", "-c:v", ex["video_codec"], "-preset", ex["preset"],
         "-crf", str(ex["crf"]), "-pix_fmt", "yuv420p", "-ar", "48000", "-c:a", "aac",
         "-b:a", ex["audio_bitrate"], "-movflags", "+faststart", "-shortest", str(out_path)],
        stdin=subprocess.PIPE, cwd=str(workdir))
    grade = effects.Grade(P["grade_cfg"], W, H)
    jitter = np.random.default_rng(3).standard_normal((total_f, 3))
    probe_w, probe_h = None, None
    last_img = None
    try:
        for si, (s, nf, fo) in enumerate(timeline):
            # fps filter: the source may be 60 fps while we output 30 (or the reverse). Without it every output
            # frame took the NEXT source frame → half-speed "slow motion" picture over normal-speed sound (2026-11-19)
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{s:.3f}", "-i", str(src), "-an",
                                    "-vf", f"fps={FPS}", "-frames:v", str(nf), "-f", "rawvideo", "-pix_fmt", "bgr24",
                                    "-"],
                                   stdout=subprocess.PIPE)
            if probe_w is None:
                from core.media import probe
                vs = next(x for x in probe(src)["streams"] if x["codec_type"] == "video")
                probe_w, probe_h = int(vs["width"]), int(vs["height"])
            fsize = probe_w * probe_h * 3
            seg_zoom = 1.0 if si % 2 == (1 if cold else 0) else mo["jump_cut_zoom"]
            last = None
            for j in range(nf):
                buf = dec.stdout.read(fsize)
                if len(buf) == fsize:
                    last = np.frombuffer(buf, np.uint8).reshape(probe_h, probe_w, 3)
                frame = last if last is not None else np.zeros((probe_h, probe_w, 3), np.uint8)
                k = fo + j
                t = k / FPS
                sf = min(int(round(src_t[k] * SRC_FPS)), len(lay["fx"]) - 1)   # analysis runs on source frames
                z = seg_zoom * (1 + mo["slow_push"] * j / max(nf, 1))
                for tp, _ in punches:
                    d = t - tp
                    if -0.07 <= d < 1.5:
                        z *= 1 + mo["punch_zoom"] * min(1, (d + 0.07) / 0.07) * math.exp(-max(d, 0) / mo["punch_decay"])
                if t < mo["intro_seconds"]:
                    z *= mo["intro_zoom"] - (mo["intro_zoom"] - 1) * _ease(t / mo["intro_seconds"])
                if cold and 0 <= t - main_start < 0.35:
                    z *= 1.18 - 0.18 * _ease((t - main_start) / 0.35)
                dx = dy = rot = 0.0
                for tp in shakes:
                    d = t - tp
                    if 0 <= d < 0.45:
                        amp = math.exp(-d / 0.14)
                        dx += mo["shake_px"] * amp * jitter[k, 0]
                        dy += mo["shake_px"] * amp * jitter[k, 1]
                        rot += mo["shake_rotate_deg"] * amp * jitter[k, 2]
                img = effects.render_frame(frame, lay, sf, z, dx, dy, rot, W, H, P.get("top_panel_ratio", 0.55),
                                           bool(P.get("cam_on_top")))
                img = grade(img)
                if title_band is not None:               # facecam_top: the title stays on the seam
                    titleband.paste(img, title_band, int(P.get("title_y", H - top_h)) - title_band.shape[0] // 2)
                if cold:
                    img = effects.transition(img, t - main_start,
                                             mo.get("flash_transition", True) and drop_ov is None,
                                             mo.get("glitch_transition", True))
                    if drop_ov is not None:
                        img = drop_ov.apply(img, t - main_start + mo.get("drop_overlay_lead", 0.1))
                last_img = img.tobytes()
                enc.stdin.write(last_img)
            dec.stdout.close()
            dec.wait()
        if extra_f and last_img is not None:   # the narrator's take needs a moment: hold the last frame
            for _ in range(extra_f):
                enc.stdin.write(last_img)
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode:
        raise RuntimeError(f"encoding failed for {name}")
    result = {"file": str(out_path), "layout": lay["layout"], "preset": P["name"], "duration": round(dur, 1),
              "music": music_name, "lut": P["grade_cfg"].get("lut"), "drop_overlay": drop_ov.name if drop_ov else None, "punches": len(punches), "shakes": len(shakes), "cold_open": bool(cold),
              "cuts": len(main) - 1, "caption_words": len(w_out)}
    if settings.get("export_nomusic_copy", True):
        nom = out_dir / f"{name}_NOMUSIC.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", out_path, "-i", wav_n, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-ar", "48000", "-c:a", "aac", "-b:a", ex["audio_bitrate"], "-movflags", "+faststart",
             "-shortest", nom])
        result["nomusic_file"] = str(nom)
    for f in (wav_m, wav_n):
        f.unlink(missing_ok=True)
    return result


def _ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3
