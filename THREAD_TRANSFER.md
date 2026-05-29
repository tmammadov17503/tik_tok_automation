# Thread Transfer

This file captures the working context from the current Codex thread and stores it inside the `Video_Generator_Agent` project folder.

## User goal

Build a low-cost, mostly automatic TikTok-style short video workflow with a simple "Start" button and a visual flow similar to n8n.

## Original manual flow described by the user

1. Find videos on YouTube.
2. Put the link into Vizard.
3. Export the processed video.
4. Send it to an online watermark-removal site.
5. Post the final result to TikTok.

## Direction chosen for this project

The workflow was redesigned into a safer local pipeline:

- Input a local file or URL.
- Download locally when `yt-dlp` is available.
- Plan clip timestamps automatically or accept manual timestamps.
- Render vertical 9:16 output locally with `ffmpeg`.
- Optionally generate captions with the `whisper` CLI.
- Produce metadata and export artifacts for review and posting.

## Important guardrails

- No third-party watermark removal was implemented.
- The workflow should only be used with content the user owns or is licensed to publish.
- TikTok automation is limited by official TikTok developer requirements.

## Files created in this project

- `app.py`
- `workflow.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `README.md`
- `PROJECT_CONTEXT.md`
- `start_video_generator.ps1`

## Current state

- The local web app boots successfully.
- The workflow UI is available at `http://127.0.0.1:8765`.
- The API can accept runs and produce output artifacts.
- A smoke test completed successfully.
- The machine currently does not have `ffmpeg`, `ffprobe`, `yt-dlp`, or `whisper` installed, so the real media-processing steps are scaffolded but not active yet.

## How to open the project

```powershell
cd C:\Users\ASUS\Documents\Apps\oracle\Video_Generator_Agent
.\start_video_generator.ps1
```

Then open:

- `http://127.0.0.1:8765`

## Immediate next step

Install the free local tools required for end-to-end execution:

- `ffmpeg`
- `ffprobe`
- `yt-dlp`
- `whisper`

After that, the workflow can be turned from a working UI scaffold into a full downloader-renderer-captioner pipeline.
