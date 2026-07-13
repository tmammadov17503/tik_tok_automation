# TikTok Automation Worker

This repository contains the Film Box Official automation workspace.

It has two parts:

- Public legal pages for the TikTok developer app: `index.html`, `privacy.html`, `terms.html`.
- The private automation worker: Python server, queue UI, YouTube/source intake, clip rendering, captions, and TikTok inbox upload.

The worker does not use Sora. It processes existing approved source videos:

```text
source URL -> yt-dlp -> highlight selection -> ffmpeg vertical clips -> captions -> TikTok inbox
```

Captions use source subtitles first. If no source subtitles are available, the remote worker can use OpenAI transcription when `OPENAI_API_KEY` is configured.

The separate English worker can also create original story reels. ElevenLabs is the premium narration layer, while OpenAI TTS is the automatic fallback. The English TikTok worker and YouTube agent use one SQLite ledger configured with `ELEVENLABS_SHARED_LEDGER_PATH`. Recommended Starter-plan limits are a 6,000-credit combined weekly budget, with 4,000 credits reserved for TikTok and 2,000 for YouTube. Failed requests release their reservation, so provider errors do not consume the local allowance.

## Local Run

```powershell
python app.py
```

Then open:

```text
http://127.0.0.1:8765
```

## Remote Deployment

Use `REMOTE_DEPLOYMENT.md` for the server setup.

Runtime secrets and generated video files are intentionally ignored by git:

- `.secrets/`
- `.models/`
- `output/`
- `queued_clips/`
- `data/`

## TikTok Callback

For local testing:

```text
http://127.0.0.1:8765/auth/tiktok/callback
```

For remote deployment:

```text
https://your-domain.example.com/auth/tiktok/callback
```
