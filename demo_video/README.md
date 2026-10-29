# SIGNIX product demo

## Deliverables

- `SIGNIX_Product_Demo.mp4`: final H.264, 1920×1080, 30 FPS, AAC audio, 3:13.
- `narration_script.md`, `subtitles_en.srt`, `shot_list.md`.
- `production.json`: exact scene durations and narration sentences.
- `capture*.cjs`, `shots*.json`: source capture and edit markers.
- `prepare_media.py`, `render_video.py`, `narrate.ps1`: reproducible media pipeline.
- `SIGNIX_Demo_Summary.pdf`: actual exported demo report.
- `QC.md`: checks actually performed and remaining review limitations.

## Content and accuracy

This is a real application capture using an isolated demonstration SQLite database
and disk cache. No existing user database was edited. Weather endpoints were disabled
for deterministic mock fallback; the video explicitly labels demonstration inputs,
mock weather and preliminary estimates. Roof geometry is a simulated planning preview,
not surveyed geometry. The public-map roof patch is illustrative and must not be
interpreted as an assessment or residential classification of that named building.
Residential assistance is conditional. Business footage does not apply residential
subsidy, and narration discloses that demand-charge savings are not modelled.
The AI assistant scene was omitted because paid-provider credentials were disabled.
No stock rooftop film was available; actual satellite-map and app footage are used.

Narration uses the installed Microsoft Zira synthetic voice, not a human voice actor.
The quiet instrumental bed was synthesized locally from original sine-wave chords.
No third-party music or unlicensed stock footage was downloaded.

## Re-render from the existing assets (no recapture required)

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe demo_video/render_video.py
```

The script preserves existing rendered scene MP4s and rebuilds the final assembly.
Use `--force` to deliberately re-render scene intermediates. It never overwrites raw
capture files. Pillow is needed for generated graphics. FFmpeg is already in
`tools/imageio_ffmpeg/binaries/` (provisioned separately from application dependencies).

To regenerate narration/graphics/subtitles after changing the script:

```powershell
powershell -ExecutionPolicy Bypass -File demo_video/narrate.ps1
.\.venv\Scripts\python.exe demo_video/prepare_media.py
.\.venv\Scripts\python.exe demo_video/render_video.py --force
```

`prepare_media.py` uses existing Pillow/NumPy and installed Segoe UI fonts. SAPI voice
access and browser launching may require running outside a restricted tool sandbox.
Current source audio WAVs are supplied, so voice synthesis is not required to re-render.

## Optional fresh capture

Preserve the supplied raw footage before recapturing: capture scripts use fixed output
names. The demo server uses SIGNIX's established `run.py` entry point through
`start_demo.py`, with a dedicated database/cache and blank paid-provider credentials.
It uses port 5000; do not start it if another server occupies that port.

```powershell
powershell -ExecutionPolicy Bypass -File demo_video/start_demo.ps1
$env:PLAYWRIGHT_MODULE = "absolute/path/to/installed/playwright"
node demo_video/capture.cjs
node demo_video/capture_extra.cjs
node demo_video/capture_detail.cjs
node demo_video/capture_planner.cjs
```

Chrome, Playwright, and Playwright's FFmpeg binary must be installed. The scripts include
a local runtime fallback path; set PLAYWRIGHT_MODULE on another machine. Capturing
requires network access for public map tiles/CDN resources, but uses no paid AI or solar
provider. `capture_extra.cjs` prepares the commercial estimate through the real,
authenticated estimate endpoint and records its actual report. It does not inject result
values. Capture UI/layout changes may require selector or timing adjustments.

The PDF page image was rendered using installed pypdfium2. To regenerate it:

```powershell
python demo_video/render_pdf.py
```

Use a Python environment containing pypdfium2 for that optional step.

## Local working files

`session.json`, `demo.sqlite`, `.cache/`, server logs and raw browser recordings are
local production materials. Share the final MP4/SRT/script/shot list, not session cookies
or the demo database. They contain only demo data but are unnecessary for distribution.
No SIGNIX application code, schema or calculations were changed for video production.
