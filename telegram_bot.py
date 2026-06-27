from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


YOUTUBE_URL_PATTERN = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s<>()]+", re.IGNORECASE)
TRUE_VALUES = {"1", "true", "yes", "on"}
BOT_COMMANDS_VERSION = "2026-06-27.studio-metrics-v1"
BOT_COMMANDS = [
    {"command": "start", "description": "Connect this chat and show help"},
    {"command": "status", "description": "Show automation and inbox counts"},
    {"command": "queue", "description": "Show queued YouTube links and progress"},
    {"command": "clips", "description": "Show recent clip labels for metrics"},
    {"command": "metrics", "description": "Record views likes comments saves shares"},
    {"command": "studio", "description": "Record TikTok Studio watch metrics"},
    {"command": "syncmetrics", "description": "Auto-sync TikTok public video metrics"},
    {"command": "performance", "description": "Show recent TikTok performance"},
    {"command": "run", "description": "Start one automation run now"},
    {"command": "pause", "description": "Pause scheduled runs"},
    {"command": "resume", "description": "Resume the 8-hour schedule"},
    {"command": "posted", "description": "Mark oldest inbox video as posted"},
    {"command": "help", "description": "Show available commands"},
]
DEFAULT_AUTOMATION_INTERVAL_HOURS = 8


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def mask_secret(value: str | None, *, visible: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * max(0, len(value) - visible)}{value[-visible:]}"


class TelegramBotService:
    def __init__(self, root: Path, source_manager: Any, automation: Any) -> None:
        self.root = root
        self.sources = source_manager
        self.automation = automation
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.secrets_root / "telegram_config.json"
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="telegram-video-agent")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict[str, Any]:
        config = self.load_config()
        return {
            "enabled": bool(config.get("enabled")),
            "configured": bool(config.get("bot_token")),
            "connected": bool(config.get("chat_id")),
            "bot_token_preview": mask_secret(config.get("bot_token")),
            "chat_id": str(config.get("chat_id") or ""),
            "last_error": str(config.get("last_error") or ""),
            "last_update_at": str(config.get("last_update_at") or ""),
            "commands_installed": bool(config.get("commands_installed")),
            "commands_version": str(config.get("commands_version") or ""),
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self._read_config()
            token = str(payload.get("bot_token") or "").strip()
            chat_id = str(payload.get("chat_id") or "").strip()

            if token:
                config["bot_token"] = token
            if payload.get("clear_bot_token"):
                config["bot_token"] = ""
            if chat_id:
                config["chat_id"] = chat_id
            if payload.get("clear_chat_id"):
                config["chat_id"] = ""
            if "enabled" in payload:
                config["enabled"] = bool(payload.get("enabled"))

            config["updated_at"] = utc_now()
            self._write_config(config)
            saved_token = str(config.get("bot_token") or "").strip()
            enabled = bool(config.get("enabled"))
        if saved_token and enabled:
            try:
                self.install_command_menu(saved_token)
                self._mark_commands_installed()
            except Exception as exc:
                self._save_error(f"Telegram command menu setup failed: {exc}")
        return self.status()

    def load_config(self) -> dict[str, Any]:
        config = self._read_config()
        env_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        env_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        env_enabled = os.getenv("TELEGRAM_ENABLED", "").strip().lower()
        if env_token:
            config["bot_token"] = env_token
        if env_chat_id:
            config["chat_id"] = env_chat_id
        if env_enabled in TRUE_VALUES:
            config["enabled"] = True
        return {
            "bot_token": str(config.get("bot_token") or ""),
            "chat_id": str(config.get("chat_id") or ""),
            "enabled": bool(config.get("enabled")),
            "last_update_id": int(config.get("last_update_id") or 0),
            "last_error": str(config.get("last_error") or ""),
            "last_update_at": str(config.get("last_update_at") or ""),
            "commands_installed": bool(config.get("commands_installed")),
        }

    def notify(self, message: str) -> None:
        config = self.load_config()
        token = str(config.get("bot_token") or "")
        chat_id = str(config.get("chat_id") or "")
        if not config.get("enabled") or not token or not chat_id:
            return
        try:
            main_message, caption = self._extract_caption_copy_payload(message)
            self._send_message(token, chat_id, main_message)
            if caption:
                self._send_copyable_caption(token, chat_id, caption)
        except Exception as exc:
            self._save_error(str(exc))

    def install_command_menu(self, token: str | None = None) -> bool:
        config = self.load_config()
        bot_token = token or str(config.get("bot_token") or "")
        if not bot_token:
            return False
        self._telegram_request(
            bot_token,
            "setMyCommands",
            {
                "commands": BOT_COMMANDS,
                "scope": {"type": "default"},
            },
        )
        return True

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(3):
            config = self.load_config()
            token = str(config.get("bot_token") or "")
            if not config.get("enabled") or not token:
                continue

            try:
                if not config.get("commands_installed") or str(config.get("commands_version") or "") != BOT_COMMANDS_VERSION:
                    self.install_command_menu(token)
                    self._mark_commands_installed()
                response = self._telegram_request(
                    token,
                    "getUpdates",
                    {
                        "offset": int(config.get("last_update_id") or 0) + 1,
                        "timeout": 25,
                        "allowed_updates": ["message"],
                    },
                )
            except Exception as exc:
                self._save_error(str(exc))
                time.sleep(8)
                continue

            for update in response.get("result") or []:
                update_id = int(update.get("update_id") or 0)
                if update_id:
                    self._save_update_id(update_id)
                try:
                    self._handle_update(update, token)
                except Exception as exc:
                    self._save_error(str(exc))

    def _handle_update(self, update: dict[str, Any], token: str) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        text = str(message.get("text") or "").strip()
        if not chat_id or not text:
            return

        config = self.load_config()
        configured_chat_id = str(config.get("chat_id") or "").strip()
        if not configured_chat_id:
            self.save_config({"chat_id": chat_id, "enabled": True})
            configured_chat_id = chat_id
            self.install_command_menu(token)
            self._send_message(token, chat_id, "Telegram connected to Video Generator Agent.")

        if chat_id != configured_chat_id:
            self._send_message(token, chat_id, "This bot is already connected to another chat.")
            return

        self._handle_text(token, chat_id, text)

    def _handle_text(self, token: str, chat_id: str, text: str) -> None:
        command = text.split()[0].split("@", 1)[0].lower()
        if command in {"/start", "/help"}:
            self._send_message(token, chat_id, self._help_text())
            return
        if command == "/status":
            self._send_message(token, chat_id, self._status_text())
            return
        if command == "/queue":
            self._send_message(token, chat_id, self._queue_text())
            return
        if command == "/clips":
            self._send_message(token, chat_id, self._clips_text())
            return
        if command == "/performance":
            self._send_message(token, chat_id, self.automation.performance_summary_text())
            return
        if command == "/syncmetrics":
            result = self.automation.sync_public_video_metrics()
            if result.get("skipped"):
                self._send_message(
                    token,
                    chat_id,
                    "Auto metrics are installed but disabled until TikTok grants video.list in the developer app.",
                )
            elif result.get("ok"):
                self._send_message(
                    token,
                    chat_id,
                    f"Auto metrics sync complete. Matched {result.get('matched', 0)} video(s), "
                    f"recorded {result.get('recorded', 0)} metric snapshot(s).",
                )
            else:
                self._send_message(token, chat_id, f"Auto metrics sync failed: {result.get('error') or 'unknown error'}")
            return
        if command == "/metrics":
            self._send_message(token, chat_id, self._record_metrics_text(text))
            return
        if command == "/studio":
            self._send_message(token, chat_id, self._record_studio_metrics_text(text))
            return
        if command == "/run":
            self.automation.run_now()
            self._send_message(token, chat_id, "Automation run started now.")
            return
        if command == "/pause":
            self.automation.update_settings({"enabled": False})
            self._send_message(token, chat_id, "Automation paused.")
            return
        if command == "/resume":
            self.automation.update_settings(
                {"enabled": True, "interval_hours": DEFAULT_AUTOMATION_INTERVAL_HOURS, "next_run_at": utc_now()}
            )
            self._send_message(
                token,
                chat_id,
                f"Automation enabled. Next run starts now, then every {DEFAULT_AUTOMATION_INTERVAL_HOURS} hours.",
            )
            self.automation.run_now()
            return
        if command == "/posted":
            result = self.automation.mark_oldest_inbox_posted()
            self._send_message(token, chat_id, str(result.get("message") or "No update."))
            return

        urls = [url.rstrip(".,;") for url in YOUTUBE_URL_PATTERN.findall(text)]
        if not urls:
            self._send_message(token, chat_id, "Send me a YouTube link, or use /status and /queue.")
            return

        for url in urls:
            self.sources.add_source({"source_url": url, "planned_clips": 8})

        self.automation.update_settings(
            {"enabled": True, "interval_hours": DEFAULT_AUTOMATION_INTERVAL_HOURS, "next_run_at": utc_now()}
        )
        self.automation.run_now()
        count = len(urls)
        suffix = "" if count == 1 else "s"
        self._send_message(
            token,
            chat_id,
            f"Queued {count} YouTube link{suffix}. Each link is set to 8 videos. I started the first run now; after that it runs every {DEFAULT_AUTOMATION_INTERVAL_HOURS} hours.",
        )

    def _help_text(self) -> str:
        return (
            "Send a YouTube link and I will queue 8 short videos from it.\n\n"
            "/status - current automation counts\n"
            "/queue - source links and clip progress\n"
            "/clips - recent clip labels for metrics\n"
            "/metrics [clip] views likes comments saves shares - record TikTok results\n"
            "/studio [clip] views=16.4k likes=689 saves=51 shares=6 avg=17.9s full=37.41 followers=17 play=83h38m36s\n"
            "/syncmetrics - pull public TikTok views/likes automatically\n"
            "/performance - recent views and like-rate summary\n"
            "/run - start a run now\n"
            "/pause - stop scheduled runs\n"
            f"/resume - enable {DEFAULT_AUTOMATION_INTERVAL_HOURS}-hour schedule\n"
            "/posted - mark the oldest inbox video as posted"
        )

    def _status_text(self) -> str:
        status = self.automation.status()
        counts = status.get("queue_counts") or {}
        lines = [
            "Video Generator status",
            f"Automation: {'enabled' if status.get('enabled') else 'paused'}",
            f"Running now: {'yes' if status.get('running') else 'no'}",
            f"Next run: {status.get('next_run_at') or 'not scheduled'}",
            f"Queue: {counts.get('pending', 0)} pending, {counts.get('making', 0)} making/queued, {counts.get('inbox', 0)} in inbox, {counts.get('posted', 0)} posted, {counts.get('failed', 0)} failed",
            f"TikTok inbox API usage: {status.get('tiktok_remote_pending', 0)}/{status.get('tiktok_pending_cap', 0)}",
            str(status.get("performance_summary") or ""),
        ]
        if status.get("last_error"):
            lines.append(f"Last issue: {status['last_error']}")
        return "\n".join(lines)

    def _queue_text(self) -> str:
        sources = self.sources.list_sources()
        if not sources:
            return "No source links are queued."

        lines = ["Source queue"]
        for source in sources[:12]:
            title = source.get("title") or source.get("source_url") or "source"
            source_id = str(source.get("id") or "")
            lines.append(f"- {title}: {self.automation.source_progress_line(source_id)}")
        if len(sources) > 12:
            lines.append(f"...and {len(sources) - 12} more.")
        return "\n".join(lines)

    def _clips_text(self) -> str:
        lines = self.automation.recent_clip_lines()
        if not lines:
            return "No posted or inbox clips found yet."
        return "Recent clips for /metrics\n" + "\n".join(lines)

    def _record_metrics_text(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 3:
            return (
                "Usage:\n"
                "/metrics views likes [comments] [saves] [shares]\n"
                "/metrics clip_08 views likes [comments] [saves] [shares]\n"
                "Example: /metrics clip_08 1200 94 12 5 3 avg=17.9s full=37.41 followers=17"
            )

        payload = parts[1:]
        clip_ref = "latest"
        if payload and not self._looks_like_int(payload[0]):
            clip_ref = payload.pop(0)

        if len(payload) < 2:
            return "Please include at least views and likes. Example: /metrics clip_08 1200 94"

        try:
            views = self._parse_count(payload[0])
            likes = self._parse_count(payload[1])
            comments = self._parse_count(payload[2]) if len(payload) > 2 else 0
            saves = self._parse_count(payload[3]) if len(payload) > 3 else 0
            shares = self._parse_count(payload[4]) if len(payload) > 4 else 0
        except ValueError as exc:
            return str(exc)

        options, notes = self._parse_metric_options(payload[5:]) if len(payload) > 5 else ({}, "")
        try:
            metric = self.automation.record_performance_metrics(
                clip_ref=clip_ref,
                views=views,
                likes=likes,
                comments=comments,
                saves=saves,
                shares=shares,
                average_watch_seconds=float(options.get("average_watch_seconds") or 0.0),
                watched_full_rate=float(options.get("watched_full_rate") or 0.0),
                new_followers=int(options.get("new_followers") or 0),
                total_play_time_seconds=int(options.get("total_play_time_seconds") or 0),
                metric_source="tiktok_studio" if options else "manual",
                notes=notes,
            )
        except Exception as exc:
            return f"Metrics were not saved: {exc}"

        like_rate = float(metric.get("like_rate") or 0.0) * 100
        engagement_rate = float(metric.get("engagement_rate") or 0.0) * 100
        return (
            f"Saved metrics for {metric.get('clip_label') or metric.get('clip_id')}.\n"
            f"Views: {metric['views']}, likes: {metric['likes']}, comments: {metric['comments']}, "
            f"saves: {metric['saves']}, shares: {metric['shares']}.\n"
            f"Like rate: {like_rate:.1f}%, engagement: {engagement_rate:.1f}%."
            + self._studio_saved_suffix(metric)
        )

    def _record_studio_metrics_text(self, text: str) -> str:
        parts = text.split()
        if len(parts) < 2:
            return (
                "Usage:\n"
                "/studio [clip] views=16.4k likes=689 comments=0 saves=51 shares=6 "
                "avg=17.9s full=37.41 followers=17 play=83h38m36s"
            )

        payload = parts[1:]
        clip_ref = "latest"
        if payload and "=" not in payload[0]:
            clip_ref = payload.pop(0)

        try:
            options, notes = self._parse_metric_options(payload)
            views = self._required_metric_option(options, "views")
            likes = self._required_metric_option(options, "likes")
            comments = int(options.get("comments") or 0)
            saves = int(options.get("saves") or 0)
            shares = int(options.get("shares") or 0)
        except ValueError as exc:
            return str(exc)

        try:
            metric = self.automation.record_performance_metrics(
                clip_ref=clip_ref,
                views=views,
                likes=likes,
                comments=comments,
                saves=saves,
                shares=shares,
                average_watch_seconds=float(options.get("average_watch_seconds") or 0.0),
                watched_full_rate=float(options.get("watched_full_rate") or 0.0),
                new_followers=int(options.get("new_followers") or 0),
                total_play_time_seconds=int(options.get("total_play_time_seconds") or 0),
                metric_source="tiktok_studio",
                notes=notes,
            )
        except Exception as exc:
            return f"Studio metrics were not saved: {exc}"

        return (
            f"Saved TikTok Studio metrics for {metric.get('clip_label') or metric.get('clip_id')}.\n"
            f"Views: {metric['views']}, likes: {metric['likes']}, saves: {metric['saves']}, "
            f"shares: {metric['shares']}, followers: {metric.get('new_followers', 0)}."
            + self._studio_saved_suffix(metric)
        )

    def _parse_metric_options(self, tokens: list[str]) -> tuple[dict[str, Any], str]:
        options: dict[str, Any] = {}
        notes: list[str] = []
        aliases = {
            "v": "views",
            "view": "views",
            "views": "views",
            "like": "likes",
            "likes": "likes",
            "comment": "comments",
            "comments": "comments",
            "save": "saves",
            "saves": "saves",
            "share": "shares",
            "shares": "shares",
            "avg": "average_watch_seconds",
            "avg_watch": "average_watch_seconds",
            "average_watch": "average_watch_seconds",
            "watch": "average_watch_seconds",
            "full": "watched_full_rate",
            "full_watch": "watched_full_rate",
            "watched_full": "watched_full_rate",
            "followers": "new_followers",
            "new_followers": "new_followers",
            "follows": "new_followers",
            "play": "total_play_time_seconds",
            "playtime": "total_play_time_seconds",
            "total_play": "total_play_time_seconds",
        }
        for token in tokens:
            if "=" not in token:
                notes.append(token)
                continue
            raw_key, raw_value = token.split("=", 1)
            key = aliases.get(raw_key.strip().lower())
            value = raw_value.strip()
            if not key:
                notes.append(token)
                continue
            if key in {"views", "likes", "comments", "saves", "shares", "new_followers"}:
                options[key] = self._parse_count(value)
            elif key == "average_watch_seconds":
                options[key] = self._parse_seconds(value)
            elif key == "watched_full_rate":
                options[key] = self._parse_rate(value)
            elif key == "total_play_time_seconds":
                options[key] = int(round(self._parse_seconds(value)))
        return options, " ".join(notes).strip()

    def _required_metric_option(self, options: dict[str, Any], key: str) -> int:
        value = options.get(key)
        if value is None:
            raise ValueError(f"Missing {key}= value.")
        return int(value)

    def _studio_saved_suffix(self, metric: dict[str, Any]) -> str:
        parts: list[str] = []
        avg_watch = float(metric.get("average_watch_seconds") or 0.0)
        full_rate = float(metric.get("watched_full_rate") or 0.0)
        followers = int(metric.get("new_followers") or 0)
        play_time = int(metric.get("total_play_time_seconds") or 0)
        if avg_watch:
            parts.append(f"avg watch {avg_watch:.1f}s")
        if full_rate:
            parts.append(f"full watched {full_rate * 100:.1f}%")
        if followers:
            parts.append(f"+{followers} followers")
        if play_time:
            parts.append(f"play time {self._format_duration(play_time)}")
        return ("\nStudio: " + ", ".join(parts) + ".") if parts else ""

    def _looks_like_int(self, value: str) -> bool:
        try:
            self._parse_count(value)
            return True
        except ValueError:
            return False

    def _parse_count(self, value: str) -> int:
        raw = value.strip().lower().replace(",", "").replace("_", "")
        multiplier = 1
        if raw.endswith("k"):
            multiplier = 1000
            raw = raw[:-1]
        elif raw.endswith("m"):
            multiplier = 1000000
            raw = raw[:-1]
        try:
            number = float(raw)
        except ValueError as exc:
            raise ValueError(f"Could not read number: {value}") from exc
        if number < 0:
            raise ValueError("Metrics cannot be negative.")
        return int(round(number * multiplier))

    def _parse_seconds(self, value: str) -> float:
        raw = value.strip().lower().replace(",", ".")
        if not raw:
            raise ValueError("Empty time value.")
        if ":" in raw:
            parts = [float(part) for part in raw.split(":")]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
        match = re.fullmatch(
            r"(?:(?P<hours>\d+(?:\.\d+)?)h)?(?:(?P<minutes>\d+(?:\.\d+)?)m)?(?:(?P<seconds>\d+(?:\.\d+)?)s?)?",
            raw,
        )
        if match and any(match.group(name) for name in ("hours", "minutes", "seconds")):
            return (
                float(match.group("hours") or 0) * 3600
                + float(match.group("minutes") or 0) * 60
                + float(match.group("seconds") or 0)
            )
        return float(raw.rstrip("s"))

    def _parse_rate(self, value: str) -> float:
        raw = value.strip().replace(",", ".")
        had_percent = raw.endswith("%")
        if had_percent:
            raw = raw[:-1]
        number = float(raw)
        if number < 0:
            raise ValueError("Rate cannot be negative.")
        if had_percent or number > 1:
            number /= 100
        return min(number, 1.0)

    @staticmethod
    def _format_duration(seconds: int | float) -> str:
        total = max(0, int(round(float(seconds))))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h{minutes:02d}m{seconds:02d}s"
        if minutes:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"

    def _extract_caption_copy_payload(self, message: str) -> tuple[str, str]:
        marker = "Caption to paste in TikTok:\n"
        if marker not in message:
            return message, ""

        before, after = message.split(marker, 1)
        caption, separator, _footer = after.partition("\nTikTok inbox uploads")
        caption = caption.strip()
        main_message = before.rstrip()
        if separator:
            main_message = f"{main_message}\nCaption sent below. Use the copy button, then paste it in TikTok.".strip()
        return main_message or "Video sent to TikTok inbox.", caption

    def _send_copyable_caption(self, token: str, chat_id: str, caption: str) -> None:
        reply_markup = None
        if 1 <= len(caption) <= 256:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Copy caption",
                            "copy_text": {"text": caption},
                        }
                    ]
                ]
            }
        try:
            self._send_message(token, chat_id, caption, reply_markup=reply_markup)
        except RuntimeError as exc:
            if reply_markup and "copy" in str(exc).lower():
                self._send_message(token, chat_id, caption)
                return
            raise

    def _send_message(self, token: str, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        chunks = [text[index : index + 3900] for index in range(0, len(text), 3900)] or [text]
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if reply_markup is not None and index == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            self._telegram_request(token, "sendMessage", payload)

    def _telegram_request(self, token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{token}/{method}"
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urlopen(request, timeout=35) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(raw or str(exc)) from exc
        except URLError as exc:
            raise RuntimeError(f"Network error while contacting Telegram: {exc.reason}") from exc

        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("Telegram returned a non-JSON response.") from exc

        if not parsed.get("ok"):
            description = parsed.get("description") or parsed.get("error_code") or "Telegram request failed."
            raise RuntimeError(str(description))
        return parsed

    def _read_config(self) -> dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_config(self, payload: dict[str, Any]) -> None:
        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _save_update_id(self, update_id: int) -> None:
        with self._lock:
            config = self._read_config()
            config["last_update_id"] = max(int(config.get("last_update_id") or 0), update_id)
            config["last_update_at"] = utc_now()
            config["last_error"] = ""
            self._write_config(config)

    def _mark_commands_installed(self) -> None:
        with self._lock:
            config = self._read_config()
            config["commands_installed"] = True
            config["commands_version"] = BOT_COMMANDS_VERSION
            config["last_update_at"] = utc_now()
            self._write_config(config)

    def _save_error(self, message: str) -> None:
        with self._lock:
            config = self._read_config()
            config["last_error"] = message
            config["last_update_at"] = utc_now()
            self._write_config(config)
