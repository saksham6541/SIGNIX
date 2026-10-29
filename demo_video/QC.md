# Final video quality review

Reviewed 29 October 2026.

## Delivered file

- `SIGNIX_Product_Demo.mp4`
- Duration: 193.00 seconds (3:13).
- Video: H.264 High profile, progressive yuv420p, 1920 × 1080, 30 FPS.
- Audio: AAC LC, 48 kHz, mono. Narration and original quiet music bed included.
- Size: 61,437,491 bytes.
- SHA-256: `0C6833A589FF8B05031F3D89426BEBF62287A638EF7E472683EE6FD82861D0F5`.
- Fast-start MP4; English subtitles are burned in, with a separate SRT supplied.

## Checks actually completed

- Renderer completed with exit code 0.
- `verify_video.py` completed with exit code 0.
- Entire video and audio streams decoded through FFmpeg with no errors (`qc/decode.txt`).
- Stream metadata confirms the duration, codec, resolution and frame rate (`qc/streams.txt`).
- Audio measurement: mean -16.8 dBFS; maximum -1.4 dBFS, below digital clipping (`qc/audio.txt`).
- All 11 scene samples extracted and visually inspected in `qc/contact-sheet.jpg`.
- Additional full-size inspection of planner, finance, Business and PDF frames: titles, primary figures and subtitle backing remain readable. The 3D view shows the complete illustrative roof. The brief top-view close-up crops the far end of the elongated roof; the surrounding 3D views restore full context.
- The Business frame retains operating benefit, ROI and the demand-charge exclusion; it does not apply residential subsidy. The PDF close-up retains the original report pixels and conditional residential assistance disclosure.
- All 27 SRT cues match their source narration text and WAV durations, within 1 ms timestamp rounding. No narration cue exceeds its scene boundary.
- Source captures and source audio retained. No SIGNIX application code changed for production. Existing application working-tree changes were preserved.

FFmpeg's metadata-only `-i` probe emits “At least one output file must be specified”; this is expected for inspection. The separate complete decode succeeded.

## Editorial choices and limitations

- Final edit includes animated official branding and titles, eased digital zooms, real report-scroll pans, short fades, narration, subtitles and original synthesized music.
- An isolated demo database and disabled weather endpoints produced honest application mock-weather fallback. On-screen labels and narration identify demo inputs, prototype geometry, unverified shade and preliminary estimates.
- The map location is public imagery used for an illustrative footprint. It is not an assessment or residential classification of that building.
- Only the poorly framed planner take was replaced. Other existing usable captures were reused.
- The AI assistant is omitted because provider credentials were unavailable in the isolated production environment.
- No licensed rooftop stock film was available; actual application and map footage are used instead.
- Narration uses Microsoft Zira. No human voice actor or complete subjective listening review is claimed. Timing, audio levels and decode integrity were checked programmatically; review pronunciation and mix on the intended presentation speakers before public release.
- No application test suite was run for this video-production task.

## Repeat verification

From the repository root:

```powershell
.\.venv\Scripts\python.exe demo_video/verify_video.py
```

Share the final MP4, SRT and production documents. Do not distribute local session cookies, the demo database or server logs.
