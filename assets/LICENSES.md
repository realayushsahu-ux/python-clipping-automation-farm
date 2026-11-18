# Asset licenses

Only put files here that you are allowed to use in videos you publish on Instagram and YouTube.
Auto-posted reels carry this audio and video inside the file, so an unlicensed song or clip can get
the reel muted, claimed or removed.

| Folder | Pack / source | License | Proof | Added |
|---|---|---|---|---|
| assets/sfx/whoosh, riser, shutter, slice, click, typing, misc | "Sound Pack" (bundle zip `sfx-…zip`) | **NOT INCLUDED in the zip – please confirm** | purchase receipt? | 2026-11-17 |
| assets/luts/ (40 .cube) | LUT PACK / LUTs Pack 2 / LUTs Pack 3 (`LUTS-…zip`) | **NOT INCLUDED – please confirm** | | 2026-11-17 |
| assets/overlays/light_leaks (DynamiXXX 01-20) | `Transitions-…zip` | **NOT INCLUDED – please confirm** | | 2026-11-17 |
| assets/overlays/flares, film_burn | `Transitions-…zip` | **NOT INCLUDED – please confirm** | | 2026-11-17 |
| assets/overlays/grain, assets/backgrounds/ | `Backgrounds-…-1-002.zip` (part 2 only) | **NOT INCLUDED – please confirm** | | 2026-11-17 |
| assets/transitions/ (paper green-screen, grids) | `Transitions-…zip` | **NOT INCLUDED – please confirm** | | 2026-11-17 |
| assets/music/ | — (no music in the bundles yet; generated beats are used) | | | |

Notes
- Content ID risk is mostly **music**. SFX, LUTs and visual overlays are rarely claimed, but you still
  need the right to use them commercially. Keep the shop receipt / license page with these notes.
- `backgrounds/motion_backgrounds_2/mixkit-…mp4` comes from Mixkit (free Mixkit license).

## Folders the editor reads
- `assets/music/<mood>/*.mp3|wav|m4a|ogg` → background music (least-used track first, never twice in a row)
- `assets/sfx/<kind>/*.wav` → sound effects, random pick. Used kinds: whoosh, riser, boom, glitch, shutter.
  Empty folder = generated sound (whoosh, boom, riser, glitch) or silence.
- `assets/luts/*.cube` → color look, chosen in `config/editing_presets.json` → color_grades → `lut`, `lut_strength`
- `assets/overlays/<folder>/*.mp4|mov` → drop transition (screen-blended), chosen in motion → `drop_overlay`
- `assets/transitions/`, `assets/backgrounds/` → stored for later styles (paper/green-screen/grid looks are
  for faceless/explainer reels, not used by the gaming presets)
- `assets/fonts/*.ttf` → extra caption fonts (install them in Windows too)
