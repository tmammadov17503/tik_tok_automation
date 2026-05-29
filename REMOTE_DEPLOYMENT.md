# Remote Deployment

This project does not need Sora for the YouTube-to-TikTok workflow. It runs as a normal video processing worker:

1. You add an approved YouTube/source URL.
2. The app downloads the source with `yt-dlp`.
3. It finds 30-second highlight windows from subtitles and audio.
4. It renders vertical clips with `ffmpeg`.
5. It burns captions from source subtitles or Whisper.
6. It uploads one clip at a time to the TikTok inbox/draft flow.

## Recommended Server Shape

Use a small Linux VPS with:

- 2+ vCPU, 4 GB RAM minimum.
- 20+ GB disk if you process long episodes.
- Ubuntu 22.04/24.04.
- A domain or subdomain with HTTPS, for example `video.example.com`.

Cloud IPs can be less reliable for YouTube downloads than your laptop. For content you own or are licensed to use, the best long-term version is to upload source files or use source URLs you control. YouTube links can still work, but some videos may require retries or may be blocked.

## Docker Deploy

On the server:

```bash
git clone <your-repo-url> video-generator-agent
cd video-generator-agent
docker compose up -d --build
```

The app listens only on localhost through Docker:

```text
http://127.0.0.1:8765
```

Before exposing it through a domain, set a dashboard password:

```bash
export VIDEO_AGENT_BASIC_AUTH_USER=admin
export VIDEO_AGENT_BASIC_AUTH_PASSWORD='choose-a-long-password'
docker compose up -d --build
```

Put Caddy or another HTTPS reverse proxy in front of it. Copy `deploy/Caddyfile.example`, replace `your-domain.example.com`, and point it to `127.0.0.1:8765`.

Persistent runtime data is stored in:

- `data/secrets`
- `data/output`
- `data/queued_clips`
- `data/models`

Do not commit those folders.

## TikTok Setup

In the TikTok developer app, set the redirect URI to the public HTTPS callback:

```text
https://your-domain.example.com/auth/tiktok/callback
```

Then open the remote app, save the TikTok credentials, and connect the TikTok account again.

The default scope is:

```text
user.info.basic,video.upload
```

That sends videos to the TikTok inbox/draft flow. Fully hands-off public posting requires TikTok `video.publish` approval and a direct-post flow, which is intentionally separate.

## Automation Settings

The server uses:

```text
TIKTOK_MAX_PENDING_SHARES=5
TIKTOK_UPLOAD_MAX_ATTEMPTS=4
VIDEO_AGENT_BASIC_AUTH_USER=admin
VIDEO_AGENT_BASIC_AUTH_PASSWORD=choose-a-long-password
OPENAI_API_KEY=optional-for-transcription-fallback
OPENAI_TRANSCRIBE_MODEL=whisper-1
```

TikTok limits pending inbox shares, so the worker now pauses uploads when the remote pending count reaches the cap. Failed temporary uploads are returned to the queue with backoff instead of staying stuck as `uploading`.

Source subtitles are used first because they are free and already timestamped. If a source has no subtitles and `OPENAI_API_KEY` is set, the worker extracts audio from each rendered clip and uses the OpenAI transcription API to create captions. Local Whisper remains optional for powerful local machines, but it is not installed by default on small servers.

## Non-Docker Deploy

Install system packages:

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv ffmpeg nodejs npm
```

Create a virtual environment:

```bash
cd /opt/video-generator-agent
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Copy `deploy/video-generator-agent.service.example` to `/etc/systemd/system/video-generator-agent.service`, edit paths if needed, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now video-generator-agent
sudo systemctl status video-generator-agent
```

## What You Still Do Manually

TikTok inbox upload still requires you to open TikTok and finish the post. That is TikTok's official review/draft flow for `video.upload`.
