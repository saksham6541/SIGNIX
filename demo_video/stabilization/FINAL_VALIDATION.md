# Final stabilization validation

## Result: automated full-length playback passed

The existing stabilized MP4 was not re-rendered during final validation.
A fresh Chrome session played all 193 seconds at normal 1x speed in a 1920 x 1080 viewport.

- Frames reported by Chrome: 5790.
- Dropped frames: 0.
- Corrupted frames: 0.
- Media/page errors: none. No stalled/waiting events after playback started.
- End event observed at 193 seconds; validator exit code 0.
- Prior full decode passed, with all 5,790 frames at consecutive 1/30-second timestamps.
- Original AAC stream is preserved byte-for-byte. Subtitle timings and narration assets are unchanged.

The earlier playback result remains in `qc/browser-playback.json`. That run completed
but had 70 dropped frames and a roughly 27-minute callback gap, so it was not accepted
as a clean pass. The uninterrupted repeat is recorded separately in
`qc/browser-playback-uninterrupted.json`; it had no dropped frames.

## Visual review and fixes

The stabilization edit removed quantized continuous zoom and title creep, replaced
stepped report scrolls with transitions between stable recorded views, and used a
fractional-pixel eased PDF close-up. No source recapture, generative alteration or
application change was required. Original video, source recordings and scripts remain.

Representative consecutive frames and browser screenshots were inspected across the
opening, mapping, planner, energy/financial reports, comparison, PDF and closing.
No additional defect requiring an edit was identified. Critical figures, subtitles,
Residential/Business distinctions and demo-data disclaimers remain readable.

This is an automated normal-speed playback check plus sampled visual/frame-by-frame
inspection, not a claim of continuous human audiovisual review. Browser playback was
muted. Audio identity and prior cue timing checks support synchronization; subjective
voice pronunciation and loudspeaker mix still warrant a final listening pass.
The 25 FPS source cadence and deliberate reading holds remain; no artificial motion
interpolation was introduced. The existing brief top-view crop is documented in README.

## Final file

- Path: `D:\Ajay\OneDrive\Desktop\High_cortisol\SIGNIX\demo_video\SIGNIX_Product_Demo_Stabilized.mp4`
- Resolution: 1920 x 1080, 16:9.
- Codec/pixel format: H.264 High / yuv420p.
- Frame rate: constant 30 FPS.
- Duration: 193.00 seconds (3:13).
- Size: 21,817,987 bytes (21.82 MB decimal).
- Overall bitrate: 904,373 bits/s (approximately 904 kb/s).
- SHA-256: `3db811cb1ee69c507e3a157087c137e1fe0492c8c0db3fcb23897358761ba625`.

Reproduce this validation without rendering:

```powershell
node demo_video/validate_playback.cjs
```

Only validation files were added during this continuation. No SIGNIX application code
was edited, no tests were run, and nothing was staged, committed, pushed or deployed.
