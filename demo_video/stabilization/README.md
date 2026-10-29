# Stabilized SIGNIX demo

The revised deliverable is `../SIGNIX_Product_Demo_Stabilized.mp4`. The original MP4,
source recordings, original renderer and narration/subtitle assets are preserved.

## Cause and correction

Inspection of consecutive original PDF frames showed a static title reversing by a
pixel while the edit was meant to move steadily. The previous `zoompan` operation
used integer crop dimensions/positions on YUV footage, followed by another resize.
Its continuously changing crop exposed rounding and chroma-grid stepping as jitter.
The sinusoidal zoom also kept the interface moving throughout each scene.

The recorded browser viewport itself is fixed. A stationary source-sidebar sample
had only 0.022 mean absolute adjacent-frame difference on a 0–255 scale, consistent
with minor video compression changes, rather than camera shake. No blanket vidstab
or optical-flow warping was applied.

Source files are 1920×1080 at 25 FPS. The old edit additionally slowed some footage
by up to 1.6× before converting it to 30 FPS. The report capture script also scrolled
in discrete approximately 55 ms steps. These choices compounded motion judder.

Changes:

- Fixed framing for interactive application footage; no continuous digital zoom.
- Keep interactions at recorded speed, with deliberate holds for narration.
- Replace the stepped finance/Business/comparison scrolls with short fades between
  actual stable recorded overview and detail positions. No results were rewritten.
- Fixed-position title layers with eased opacity reveals; remove rounded sliding
  offsets that made text creep a pixel at a time.
- One PDF push/hold/pull with a fixed focal point, quintic smoothstep easing and
  floating-point RGB bicubic affine sampling. No integer animated crop positions.
- Lanczos scaling for static framing; no sharpening, denoising, generative changes,
  frame interpolation, or claimed recovery of unrecorded detail.
- H.264 slow preset, CRF 17 intermediates and CRF 18 final, yuv420p, constant 30 FPS.
- Original AAC audio copied without re-encoding; original subtitle timing retained.

## Reproduce

Run from the project root:

```powershell
.\.venv\Scripts\python.exe demo_video/render_stabilized.py
.\.venv\Scripts\python.exe demo_video/verify_stabilized.py
node demo_video/review_stabilized.cjs
```

The renderer reuses completed revised scene intermediates. Use `--force` to rebuild
those intermediates. It uses the existing bundled FFmpeg, Pillow and installed fonts.
Verification additionally uses existing NumPy. Browser review uses installed Chrome
and Playwright; set `PLAYWRIGHT_MODULE` if the local bundled module path differs.
No application changes or dependency installation are needed.

`settings.json` records the transform and edit settings. `diagnosis.json` and the
consecutive-frame montage preserve original-motion evidence. `qc/` contains final
stream, frame-timestamp, audio-identity, visual and browser-playback checks.

## Limitations

25 FPS recorded motion converted to 30 FPS necessarily repeats some source frames.
That cadence and deliberate reading holds are retained rather than synthesizing
intermediate application frames. Small browser animations/cursor motion are limited
by the original capture. The brief top view crops the far end of the elongated roof;
the surrounding 3D views show the full illustrative footprint.

The demonstration still uses clearly labelled mock weather and illustrative inputs.
Microsoft Zira narration and the original quiet music remain. AI assistant footage
remains omitted. Browser review is muted and does not constitute a human listening
review; audio identity is checked against the original file. Review pronunciation and
mix on presentation speakers before public release.
