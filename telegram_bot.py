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
BOT_COMMANDS = [
    {"command": "start", "description": "Connect this chat and show help"},
    {"command": "status", "description": "Show automation and inbox counts"},
    {"command": "queue", "description": "Show queued YouTube links and progress"},
    {"command": "run", "description": "Start one automation run now"},
    {"command": "pause", "description": "Pause scheduled 6-hour runs"},
    {"command": "resume", "description": "Resume the 6-hour schedule"},
    {"command": "posted", "description": "Mark oldest inbox video as posted"},
    {"command": "help", "description": "Show available commands"},
]


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
            self._send_message(token, chat_id, message)
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
                if not config.get("commands_installed"):
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
        if command == "/run":
            self.automation.run_now()
            self._send_message(token, chat_id, "Automation run started now.")
            return
        if command == "/pause":
            self.automation.update_settings({"enabled": False})
            self._send_message(token, chat_id, "Automation paused.")
            return
        if command == "/resume":
            self.automation.update_settings({"enabled": True, "interval_hours": 6, "next_run_at": utc_now()})
            self._send_message(token, chat_id, "Automation enabled. Next run starts now, then every 6 hours.")
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

        self.automation.update_settings({"enabled": True, "interval_hours": 6, "next_run_at": utc_now()})
        self.automation.run_now()
        count = len(urls)
        suffix = "" if count == 1 else "s"
        self._send_message(
            token,
            chat_id,
            f"Queued {count} YouTube link{suffix}. Each link is set to 8 videos. I started the first run now; after that it runs every 6 hours.",
        )

    def _help_text(self) -> str:
        return (
            "Send a YouTube link and I will queue 8 short videos from it.\n\n"
            "/status - current automation counts\n"
            "/queue - source links and clip progress\n"
            "/run - start a run now\n"
            "/pause - stop scheduled runs\n"
            "/resume - enable 6-hour schedule\n"
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

    def _send_message(self, token: str, chat_id: str, text: str) -> None:
        chunks = [text[index : index + 3900] for index in range(0, len(text), 3900)] or [text]
        for chunk in chunks:
            self._telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": chunk})

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
            config["last_update_at"] = utc_now()
            self._write_config(config)

    def _save_error(self, message: str) -> None:
        with self._lock:
            config = self._read_config()
            config["last_error"] = message
            config["last_update_at"] = utc_now()
            self._write_config(config)
