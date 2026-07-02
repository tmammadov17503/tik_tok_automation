from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import secrets
import threading
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from telegram_bot import TelegramBotService
from tiktok_automation import AutomationController
from workflow import WorkflowPipeline, detect_tools


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
OUTPUT_ROOT = ROOT / "output"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
PKCE_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
DEFAULT_TIKTOK_HASHTAGS = [
    "#fyp",
    "#\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438",
    "#relatable",
    "#recommendations",
    "#\u0440\u0435\u043a\u0438",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_after(seconds: int | float | None) -> str | None:
    if not seconds:
        return None
    target = datetime.now(timezone.utc) + timedelta(seconds=float(seconds))
    return target.isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def mask_secret(value: str | None, *, visible: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * max(0, len(value) - visible)}{value[-visible:]}"


def basic_auth_credentials() -> tuple[str, str] | None:
    password = os.getenv("VIDEO_AGENT_BASIC_AUTH_PASSWORD", "").strip()
    if not password:
        return None
    user = os.getenv("VIDEO_AGENT_BASIC_AUTH_USER", "admin").strip() or "admin"
    return user, password


class RuntimeConfigManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.path = self.secrets_root / "runtime_config.json"
        self._lock = threading.Lock()

    def load_config(self) -> dict[str, str]:
        config = self._read()
        saved_openai_key = str(config.get("openai_api_key") or "").strip()
        saved_transcribe_model = str(config.get("openai_transcribe_model") or "").strip()
        env_openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        env_transcribe_model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "").strip()
        openai_key = saved_openai_key or env_openai_key
        return {
            "openai_api_key": openai_key,
            "openai_key_source": "saved" if saved_openai_key else ("environment" if env_openai_key else ""),
            "openai_transcribe_model": saved_transcribe_model or env_transcribe_model or "whisper-1",
        }

    def status(self) -> dict[str, Any]:
        config = self.load_config()
        key = config.get("openai_api_key") or ""
        return {
            "openai_configured": bool(key),
            "openai_api_key_preview": mask_secret(key),
            "openai_key_source": config.get("openai_key_source") or "",
            "openai_transcribe_model": config.get("openai_transcribe_model") or "whisper-1",
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self._read()
            openai_api_key = str(payload.get("openai_api_key") or "").strip()
            transcribe_model = str(payload.get("openai_transcribe_model") or "").strip()

            if openai_api_key:
                config["openai_api_key"] = openai_api_key
            if payload.get("clear_openai_api_key"):
                config["openai_api_key"] = ""
                os.environ.pop("OPENAI_API_KEY", None)
            if transcribe_model:
                config["openai_transcribe_model"] = transcribe_model
            config["updated_at"] = utc_now()
            self._write(config)

        self.apply_environment()
        return self.status()

    def apply_environment(self) -> None:
        config = self.load_config()
        if config.get("openai_api_key"):
            os.environ["OPENAI_API_KEY"] = config["openai_api_key"]
        if config.get("openai_transcribe_model"):
            os.environ["OPENAI_TRANSCRIBE_MODEL"] = config["openai_transcribe_model"]

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def json_request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, data: bytes | None = None) -> dict[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "VideoGeneratorAgent/0.2 (+https://158.180.17.172.nip.io)",
    }
    request_headers.update(headers or {})
    request = Request(url, data=data, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": "http_error", "error_description": raw or str(exc)}
        message = payload.get("error_description") or payload.get("message") or raw or str(exc)
        raise RuntimeError(f"TikTok HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while contacting TikTok: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError("TikTok returned a non-JSON response.") from exc


def raw_request(url: str, *, method: str, headers: dict[str, str], data: bytes) -> int:
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request, timeout=120) as response:
            response.read()
            return response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(body or str(exc)) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while uploading to TikTok: {exc.reason}") from exc


def latest_output_run(output_root: Path) -> Path | None:
    candidates = [path for path in output_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def discover_postable_clips(output_root: Path) -> list[Path]:
    run_dir = latest_output_run(output_root)
    if run_dir is None:
        return []

    captioned = sorted(run_dir.glob("*_captioned.mp4"))
    if captioned:
        return captioned

    return sorted(
        [
            path
            for path in run_dir.glob("*.mp4")
            if path.is_file() and "source" not in path.name.lower()
        ]
    )


@dataclass
class JobState:
    job_id: str
    created_at: str = field(default_factory=utc_now)
    status: str = "queued"
    logs: list[str] = field(default_factory=list)
    artifacts: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    updated_at: str = field(default_factory=utc_now)
    output_dir: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str) -> None:
        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        with self.lock:
            self.logs.append(timestamped)
            self.updated_at = utc_now()

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.updated_at = utc_now()

    def set_error(self, error: str) -> None:
        with self.lock:
            self.error = error
            self.status = "failed"
            self.updated_at = utc_now()

    def add_artifact(self, label: str, relative_path: str) -> None:
        with self.lock:
            route_path = relative_path.replace("\\", "/")
            self.artifacts.append(
                {
                    "label": label,
                    "path": relative_path,
                    "url": f"/api/artifacts/{self.job_id}/{route_path}",
                }
            )
            self.updated_at = utc_now()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "job_id": self.job_id,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "status": self.status,
                "logs": list(self.logs),
                "artifacts": list(self.artifacts),
                "error": self.error,
                "output_dir": self.output_dir,
            }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create(self) -> JobState:
        job = JobState(job_id=uuid.uuid4().hex[:10])
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)


class SourceQueueManager:
    VALID_CONTENT_MODES = {"growth", "monetization"}
    VALID_ACCOUNT_PROFILES = {"main_ru", "future_en"}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.path = self.secrets_root / "source_queue.json"
        self._lock = threading.Lock()

    def list_sources(self) -> list[dict[str, Any]]:
        payload = self._read()
        sources = payload.get("sources") or []
        return sorted(
            [self._normalize_entry(dict(item)) for item in sources],
            key=lambda item: item.get("updated_at") or item.get("added_at") or "",
            reverse=True,
        )

    def add_source(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            raise ValueError("Please enter a source URL.")

        content_mode = self._normalize_content_mode(payload.get("content_mode") or payload.get("mode"))
        account_profile = self._normalize_account_profile(payload.get("account_profile"))
        audience_language = self._normalize_audience_language(
            payload.get("audience_language"),
            account_profile=account_profile,
        )
        default_planned = 4 if content_mode == "monetization" else 8
        planned_clips = max(1, min(int(payload.get("planned_clips") or default_planned), 20))
        title = str(payload.get("title") or "").strip()

        with self._lock:
            data = self._read()
            sources = data.setdefault("sources", [])
            existing = next(
                (
                    item
                    for item in sources
                    if str(item.get("source_url") or "").strip() == source_url
                    and self._normalize_content_mode(item.get("content_mode")) == content_mode
                    and self._normalize_account_profile(item.get("account_profile")) == account_profile
                ),
                None,
            )
            now = utc_now()

            if existing is not None:
                existing["planned_clips"] = max(int(existing.get("planned_clips") or 0), planned_clips)
                existing["content_mode"] = content_mode
                existing["account_profile"] = account_profile
                existing["audience_language"] = audience_language
                if title:
                    existing["title"] = title
                existing["updated_at"] = now
                existing.update(self._normalize_entry(existing))
            else:
                sources.append(
                    self._normalize_entry(
                        {
                            "id": uuid.uuid4().hex[:10],
                            "source_url": source_url,
                            "title": title,
                            "planned_clips": planned_clips,
                            "content_mode": content_mode,
                            "account_profile": account_profile,
                            "audience_language": audience_language,
                            "posted_clips": 0,
                            "added_at": now,
                            "updated_at": now,
                        }
                    )
                )

            self._write(data)

        return self.list_sources()

    def increment_posted(self, source_id: str, count: int = 1) -> list[dict[str, Any]]:
        if count <= 0:
            count = 1

        with self._lock:
            data = self._read()
            sources = data.setdefault("sources", [])
            target_index = next((index for index, item in enumerate(sources) if item.get("id") == source_id), None)
            target = sources[target_index] if target_index is not None else None
            if target is None:
                raise ValueError("Source entry not found.")
            target["posted_clips"] = int(target.get("posted_clips") or 0) + count
            target["updated_at"] = utc_now()
            sources[target_index] = self._normalize_entry(target)
            self._write(data)

        return self.list_sources()

    def mark_source_failure(self, source_id: str, error: str, *, max_failures: int = 2) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            sources = data.setdefault("sources", [])
            target_index = next((index for index, item in enumerate(sources) if item.get("id") == source_id), None)
            if target_index is None:
                return None

            target = sources[target_index]
            failures = int(target.get("download_failures") or 0) + 1
            target["download_failures"] = failures
            target["last_error"] = str(error or "").strip()[:1000]
            target["updated_at"] = utc_now()
            if failures >= max(1, max_failures):
                target["status"] = "failed"
                target["failed_at"] = utc_now()
            sources[target_index] = self._normalize_entry(target)
            self._write(data)
            return sources[target_index]

    def clear_source_failure(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            sources = data.setdefault("sources", [])
            target_index = next((index for index, item in enumerate(sources) if item.get("id") == source_id), None)
            if target_index is None:
                return None

            target = sources[target_index]
            target["download_failures"] = 0
            target["last_error"] = ""
            if target.get("status") == "failed":
                target.pop("failed_at", None)
                target["status"] = ""
            target["updated_at"] = utc_now()
            sources[target_index] = self._normalize_entry(target)
            self._write(data)
            return sources[target_index]

    def remove_source(self, source_id: str) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            sources = data.setdefault("sources", [])
            filtered = [item for item in sources if item.get("id") != source_id]
            data["sources"] = filtered
            self._write(data)
        return self.list_sources()

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"sources": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _normalize_content_mode(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"money", "monetize", "monetisation", "monetization", "creator_rewards", "long"}:
            return "monetization"
        if text in self.VALID_CONTENT_MODES:
            return text
        return "growth"

    def _normalize_account_profile(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"future_en", "english", "en", "english_account"}:
            return "future_en"
        return "main_ru"

    def _normalize_audience_language(self, value: Any, *, account_profile: str) -> str:
        text = str(value or "").strip().lower()
        if text.startswith("en"):
            return "en"
        if text.startswith("ru"):
            return "ru"
        return "en" if account_profile == "future_en" else "ru"

    def _normalize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        content_mode = self._normalize_content_mode(entry.get("content_mode"))
        account_profile = self._normalize_account_profile(entry.get("account_profile"))
        audience_language = self._normalize_audience_language(
            entry.get("audience_language"),
            account_profile=account_profile,
        )
        planned = max(1, int(entry.get("planned_clips") or 8))
        posted = max(0, int(entry.get("posted_clips") or 0))
        remaining = max(0, planned - posted)
        status = "done" if remaining == 0 else ("active" if posted > 0 else "queued")
        stored_status = str(entry.get("status") or "").strip().lower()
        if stored_status in {"failed", "skipped"}:
            status = stored_status
        if account_profile == "future_en" and status != "done":
            status = "parked"
        return {
            "id": str(entry.get("id") or uuid.uuid4().hex[:10]),
            "source_url": str(entry.get("source_url") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
            "planned_clips": planned,
            "posted_clips": posted,
            "remaining_clips": remaining,
            "content_mode": content_mode,
            "mode_label": "Monetization" if content_mode == "monetization" else "Growth",
            "account_profile": account_profile,
            "account_profile_label": "Future English account" if account_profile == "future_en" else "Film Box Official RU",
            "audience_language": audience_language,
            "status": status,
            "download_failures": max(0, int(entry.get("download_failures") or 0)),
            "last_error": str(entry.get("last_error") or "").strip(),
            "failed_at": str(entry.get("failed_at") or "").strip(),
            "added_at": str(entry.get("added_at") or utc_now()),
            "updated_at": str(entry.get("updated_at") or entry.get("added_at") or utc_now()),
        }


class TikTokAuthManager:
    AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
    USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name,avatar_url"
    QR_GET_URL = "https://open.tiktokapis.com/v2/oauth/get_qrcode/"
    QR_CHECK_URL = "https://open.tiktokapis.com/v2/oauth/check_qrcode/"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.config_path = self.secrets_root / "tiktok_client.json"
        self.token_path = self.secrets_root / "tiktok_tokens.json"
        self.pending_path = self.secrets_root / "tiktok_pending_auth.json"
        self._lock = threading.Lock()

    def default_config(self) -> dict[str, str]:
        return {
            "client_key": "",
            "client_secret": "",
            "redirect_uri": "http://127.0.0.1:8765/auth/tiktok/callback",
            "scopes": "user.info.basic,video.upload",
        }

    def _read_json_file(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_json_file(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _delete_file(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def load_config(self) -> dict[str, str]:
        config = self.default_config()
        config.update({k: str(v) for k, v in self._read_json_file(self.config_path).items() if v is not None})
        config["redirect_uri"] = config.get("redirect_uri") or self.default_config()["redirect_uri"]
        config["scopes"] = self._normalize_scopes(config.get("scopes") or self.default_config()["scopes"])
        return config

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self.load_config()
            client_key = str(payload.get("client_key") or "").strip()
            client_secret = str(payload.get("client_secret") or "").strip()
            redirect_uri = str(payload.get("redirect_uri") or "").strip()
            scopes = str(payload.get("scopes") or "").strip()

            if client_key:
                config["client_key"] = client_key
            if client_secret:
                config["client_secret"] = client_secret
            if redirect_uri:
                config["redirect_uri"] = redirect_uri
            if scopes:
                config["scopes"] = self._normalize_scopes(scopes)

            self._write_json_file(self.config_path, config)
        return self.status()

    def status(self) -> dict[str, Any]:
        config = self.load_config()
        tokens = self._read_json_file(self.token_path)
        pending = self._read_json_file(self.pending_path)
        profile = tokens.get("profile") or {}
        access_expires_at = tokens.get("access_expires_at")
        refresh_expires_at = tokens.get("refresh_expires_at")
        access_expires_dt = iso_to_datetime(access_expires_at)
        refresh_expires_dt = iso_to_datetime(refresh_expires_at)
        now = datetime.now(timezone.utc)

        access_valid = bool(tokens.get("access_token")) and (
            access_expires_dt is None or access_expires_dt > now
        )
        refresh_valid = bool(tokens.get("refresh_token")) and (
            refresh_expires_dt is None or refresh_expires_dt > now
        )

        return {
            "configured": bool(config.get("client_key") and config.get("client_secret") and config.get("redirect_uri")),
            "connected": access_valid,
            "can_refresh": refresh_valid,
            "client_key_preview": mask_secret(config.get("client_key")),
            "has_client_secret": bool(config.get("client_secret")),
            "redirect_uri": config.get("redirect_uri"),
            "scopes": config.get("scopes"),
            "token_scope": tokens.get("scope") or "",
            "access_expires_at": access_expires_at,
            "refresh_expires_at": refresh_expires_at,
            "open_id": tokens.get("open_id"),
            "profile": {
                "display_name": profile.get("display_name"),
                "avatar_url": profile.get("avatar_url"),
                "open_id": profile.get("open_id") or tokens.get("open_id"),
            },
            "pending": bool(pending.get("state")),
            "default_hashtags": list(DEFAULT_TIKTOK_HASHTAGS),
        }

    def build_authorize_url(self) -> str:
        config = self.load_config()
        missing = [
            name
            for name in ("client_key", "client_secret", "redirect_uri")
            if not config.get(name)
        ]
        if missing:
            raise ValueError(f"Save TikTok settings first: missing {', '.join(missing)}.")

        redirect_host = urlparse(config["redirect_uri"]).hostname or ""
        use_pkce = redirect_host in {"localhost", "127.0.0.1"}
        code_verifier = self._generate_code_verifier() if use_pkce else ""
        state = secrets.token_urlsafe(24)
        pending = {
            "state": state,
            "created_at": utc_now(),
            "redirect_uri": config["redirect_uri"],
            "flow": "desktop" if use_pkce else "web",
        }
        if code_verifier:
            pending["code_verifier"] = code_verifier
        self._write_json_file(self.pending_path, pending)

        params = {
            "client_key": config["client_key"],
            "response_type": "code",
            "scope": config["scopes"],
            "redirect_uri": config["redirect_uri"],
            "state": state,
            "disable_auto_auth": "1",
        }
        if code_verifier:
            params["code_challenge"] = hashlib.sha256(code_verifier.encode("utf-8")).hexdigest()
            params["code_challenge_method"] = "S256"
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    def start_qr_authorization(self) -> dict[str, Any]:
        config = self.load_config()
        missing = [name for name in ("client_key", "client_secret") if not config.get(name)]
        if missing:
            raise ValueError(f"Save TikTok settings first: missing {', '.join(missing)}.")

        pending = self._read_json_file(self.pending_path)
        pending_created = iso_to_datetime(str(pending.get("created_at") or ""))
        if (
            pending.get("flow") == "qr"
            and pending.get("qr_data_url")
            and pending_created
            and pending_created > datetime.now(timezone.utc) - timedelta(minutes=2)
        ):
            return {
                "status": "new",
                "message": "Scan this QR code with the TikTok app, then confirm access on your phone.",
                "qr_data_url": pending["qr_data_url"],
                "scopes": pending.get("scope") or config["scopes"],
                "reused": True,
            }

        state = secrets.token_urlsafe(24)
        client_ticket = secrets.token_urlsafe(24)
        response = self._post_form(
            self.QR_GET_URL,
            {
                "client_key": config["client_key"],
                "scope": config["scopes"],
                "state": state,
            },
        )
        if response.get("error"):
            raise RuntimeError(response.get("error_description") or response["error"])

        qr_token = str(response.get("token") or "").strip()
        scan_url = str(response.get("scan_qrcode_url") or "").strip()
        if not qr_token or not scan_url:
            raise RuntimeError("TikTok did not return a QR authorization token.")

        scan_url = self._set_query_value(scan_url, "client_ticket", client_ticket)
        qr_data_url = self._make_qr_data_url(scan_url)
        pending = {
            "state": state,
            "flow": "qr",
            "created_at": utc_now(),
            "qr_token": qr_token,
            "client_ticket": client_ticket,
            "scope": config["scopes"],
            "qr_data_url": qr_data_url,
        }
        self._write_json_file(self.pending_path, pending)

        return {
            "status": "new",
            "message": "Scan this QR code with the TikTok app, then confirm access on your phone.",
            "qr_data_url": qr_data_url,
            "scopes": config["scopes"],
        }

    def check_qr_authorization(self) -> dict[str, Any]:
        pending = self._read_json_file(self.pending_path)
        if pending.get("flow") != "qr":
            return {"status": "not_started", "message": "Start QR connect first."}

        config = self.load_config()
        try:
            response = self._post_form(
                self.QR_CHECK_URL,
                {
                    "client_key": config["client_key"],
                    "client_secret": config["client_secret"],
                    "token": str(pending.get("qr_token") or ""),
                },
            )
        except RuntimeError as exc:
            if "TikTok HTTP 403" in str(exc):
                return {
                    "status": "waiting",
                    "message": "TikTok temporarily blocked one QR status check. Keep the QR open; the app will retry.",
                }
            raise
        if response.get("error"):
            raise RuntimeError(response.get("error_description") or response["error"])

        status = str(response.get("status") or "").strip() or "unknown"
        if status in {"new", "scanned"}:
            return {
                "status": status,
                "message": "Waiting for confirmation in the TikTok app."
                if status == "scanned"
                else "Waiting for QR scan.",
            }
        if status == "expired":
            self._delete_file(self.pending_path)
            return {"status": "expired", "message": "QR code expired. Start QR connect again."}
        if status == "utilised":
            self._delete_file(self.pending_path)
            return {"status": "utilised", "message": "This QR code was already used."}
        if status != "confirmed":
            return {"status": status, "message": f"TikTok returned QR status: {status}."}

        if response.get("client_ticket") != pending.get("client_ticket"):
            self._delete_file(self.pending_path)
            raise RuntimeError("TikTok QR ticket check failed. Start QR connect again.")

        code, redirect_uri = self._extract_qr_code(response)
        if not code:
            raise RuntimeError("TikTok confirmed the QR code but did not return an authorization code.")

        payload = self._exchange_code_for_token(
            config=config,
            code=code,
            redirect_uri="",
            fallback_redirect_uri=redirect_uri or config.get("redirect_uri") or "",
        )
        profile = self.fetch_profile(str(payload.get("access_token") or ""))
        payload["profile"] = profile
        self._write_json_file(self.token_path, payload)
        self._delete_file(self.pending_path)
        return {
            "status": "connected",
            "message": "TikTok account connected.",
            "profile": profile,
        }

    def disconnect(self) -> None:
        config = self.load_config()
        tokens = self._read_json_file(self.token_path)
        access_token = str(tokens.get("access_token") or "").strip()
        if config.get("client_key") and config.get("client_secret") and access_token:
            try:
                self._post_form(
                    self.REVOKE_URL,
                    {
                        "client_key": config["client_key"],
                        "client_secret": config["client_secret"],
                        "token": access_token,
                    },
                )
            except Exception:
                pass

        self._delete_file(self.token_path)
        self._delete_file(self.pending_path)

    def refresh_access_token(self) -> dict[str, Any]:
        config = self.load_config()
        tokens = self._read_json_file(self.token_path)
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not refresh_token:
            raise ValueError("No TikTok refresh token is stored yet.")

        response = self._post_form(
            self.TOKEN_URL,
            {
                "client_key": config["client_key"],
                "client_secret": config["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        payload = self._normalize_token_response(response)
        payload["profile"] = tokens.get("profile") or {}
        self._write_json_file(self.token_path, payload)
        return payload

    def get_valid_access_token(self) -> str:
        tokens = self._read_json_file(self.token_path)
        access_token = str(tokens.get("access_token") or "").strip()
        access_expires_at = iso_to_datetime(str(tokens.get("access_expires_at") or ""))
        refresh_expires_at = iso_to_datetime(str(tokens.get("refresh_expires_at") or ""))
        now = datetime.now(timezone.utc)

        if access_token and (access_expires_at is None or access_expires_at > now + timedelta(minutes=2)):
            return access_token

        if tokens.get("refresh_token") and (refresh_expires_at is None or refresh_expires_at > now):
            refreshed = self.refresh_access_token()
            return str(refreshed.get("access_token") or "").strip()

        raise RuntimeError("TikTok access has expired. Reconnect the TikTok account in the app.")

    def handle_callback(self, query: dict[str, list[str]]) -> dict[str, Any]:
        error = self._first_query_value(query, "error")
        if error:
            self._delete_file(self.pending_path)
            description = self._first_query_value(query, "error_description") or error
            raise RuntimeError(description)

        code = self._first_query_value(query, "code")
        state = self._first_query_value(query, "state")
        if not code or not state:
            raise RuntimeError("TikTok callback is missing the authorization code or state.")

        pending = self._read_json_file(self.pending_path)
        if not pending or pending.get("state") != state:
            raise RuntimeError("TikTok state check failed. Start the connect flow again.")

        config = self.load_config()
        payload = self._exchange_code_for_token(
            config=config,
            code=code,
            redirect_uri=config["redirect_uri"],
            code_verifier=str(pending.get("code_verifier") or ""),
        )
        profile = self.fetch_profile(str(payload.get("access_token") or ""))
        payload["profile"] = profile
        self._write_json_file(self.token_path, payload)
        self._delete_file(self.pending_path)
        return payload

    def fetch_profile(self, access_token: str) -> dict[str, Any]:
        if not access_token:
            return {}

        response = json_request(
            self.USER_INFO_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
        )
        user = ((response.get("data") or {}).get("user")) or {}
        return {
            "open_id": user.get("open_id"),
            "display_name": user.get("display_name"),
            "avatar_url": user.get("avatar_url"),
        }

    def _post_form(self, url: str, payload: dict[str, str]) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        return json_request(
            url,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
            data=body,
        )

    def _exchange_code_for_token(
        self,
        *,
        config: dict[str, str],
        code: str,
        redirect_uri: str,
        code_verifier: str = "",
        fallback_redirect_uri: str = "",
    ) -> dict[str, Any]:
        token_payload = {
            "client_key": config["client_key"],
            "client_secret": config["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
        }
        if redirect_uri:
            token_payload["redirect_uri"] = redirect_uri
        if code_verifier:
            token_payload["code_verifier"] = code_verifier

        response = self._post_form(self.TOKEN_URL, token_payload)
        description = str(response.get("error_description") or response.get("error") or "").lower()
        if (
            fallback_redirect_uri
            and not token_payload.get("redirect_uri")
            and response.get("error")
            and ("redirect" in description or "malformed" in description)
        ):
            token_payload["redirect_uri"] = fallback_redirect_uri
            response = self._post_form(self.TOKEN_URL, token_payload)
        return self._normalize_token_response(response)

    def _extract_qr_code(self, response: dict[str, Any]) -> tuple[str, str]:
        code = str(response.get("code") or "").strip()
        redirect_uri = str(response.get("redirect_uri") or "").strip()
        if code.startswith("http://") or code.startswith("https://"):
            redirect_uri = code
            code = ""
        if redirect_uri:
            parsed = urlparse(redirect_uri)
            values = parse_qs(parsed.query)
            code = code or self._first_query_value(values, "code") or ""
            redirect_uri = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return code, redirect_uri

    def _set_query_value(self, url: str, key: str, value: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query[key] = [value]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query, doseq=True),
                parsed.fragment,
            )
        )

    def _make_qr_data_url(self, value: str) -> str:
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError as exc:
            raise RuntimeError("Install the qrcode package to use TikTok QR connect.") from exc

        image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
        buffer = BytesIO()
        image.save(buffer)
        return "data:image/svg+xml;base64," + b64encode(buffer.getvalue()).decode("ascii")

    def _normalize_scopes(self, raw: str) -> str:
        scopes = [item.strip() for item in raw.replace(" ", "").split(",") if item.strip()]
        if not scopes:
            scopes = ["user.info.basic", "video.upload"]
        for required_scope in ("user.info.basic", "video.upload"):
            if required_scope not in scopes:
                scopes.append(required_scope)
        return ",".join(dict.fromkeys(scopes))

    def _normalize_token_response(self, response: dict[str, Any]) -> dict[str, Any]:
        if response.get("error"):
            raise RuntimeError(response.get("error_description") or response["error"])

        payload = response.get("data") if isinstance(response.get("data"), dict) else response
        access_token = str(payload.get("access_token") or "").strip()
        refresh_token = str(payload.get("refresh_token") or "").strip()

        if not access_token:
            raise RuntimeError("TikTok did not return an access token.")

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": str(payload.get("token_type") or "Bearer"),
            "scope": str(payload.get("scope") or ""),
            "open_id": str(payload.get("open_id") or ""),
            "issued_at": utc_now(),
            "access_expires_at": utc_after(payload.get("expires_in")),
            "refresh_expires_at": utc_after(payload.get("refresh_expires_in")),
            "expires_in": payload.get("expires_in"),
            "refresh_expires_in": payload.get("refresh_expires_in"),
        }

    def _generate_code_verifier(self, length: int = 64) -> str:
        return "".join(secrets.choice(PKCE_UNRESERVED) for _ in range(length))

    def _first_query_value(self, query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key) or []
        return values[0] if values else None


JOBS = JobRegistry()
RUNTIME = RuntimeConfigManager(ROOT)
RUNTIME.apply_environment()
PIPELINE = WorkflowPipeline(ROOT)
TIKTOK = TikTokAuthManager(ROOT)
SOURCES = SourceQueueManager(ROOT)
AUTOMATION = AutomationController(ROOT, PIPELINE, TIKTOK, SOURCES, DEFAULT_TIKTOK_HASHTAGS)
TELEGRAM = TelegramBotService(ROOT, SOURCES, AUTOMATION)
AUTOMATION.set_notifier(TELEGRAM.notify)


def run_job(job: JobState, payload: dict[str, Any]) -> None:
    job.set_status("running")
    job.log("Workflow accepted. Preparing pipeline.")
    try:
        result = PIPELINE.run(job, payload)
        if result.output_dir:
            job.output_dir = str(result.output_dir)
        for label, path in result.artifacts:
            job.add_artifact(label, path)
        try:
            AUTOMATION.on_workflow_completed(payload, result.output_dir)
        except Exception as exc:
            job.log(f"Automation queue sync skipped: {exc}")
        job.set_status("completed")
        job.log("Workflow finished.")
    except Exception as exc:  # pragma: no cover
        job.log("Workflow failed.")
        job.log(str(exc))
        job.set_error(str(exc))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "VideoGeneratorAgent/0.2"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._is_authorized():
            self._send_auth_required()
            return

        if path == "/":
            self._serve_file(WEB_ROOT / "index.html")
            return
        if path in {"/app.js", "/styles.css"}:
            self._serve_file(WEB_ROOT / path.lstrip("/"))
            return
        if path == "/legal-styles.css":
            self._serve_file(ROOT / "legal-styles.css")
            return
        if path in {"/privacy.html", "/terms.html"}:
            self._serve_file(ROOT / path.lstrip("/"))
            return
        if path == "/auth/tiktok/callback":
            self._handle_tiktok_callback(parse_qs(parsed.query))
            return
        if path == "/api/status":
            self._send_json(
                {
                    "service": "video-generator-agent",
                    "version": "0.2",
                    "tools": detect_tools(),
                    "rules": {
                        "watermark_removal": "not_supported",
                        "rights_required": True,
                    },
                }
            )
            return
        if path == "/api/tiktok/status":
            self._send_json(TIKTOK.status())
            return
        if path == "/api/config/status":
            self._send_json(RUNTIME.status())
            return
        if path == "/api/telegram/status":
            self._send_json(TELEGRAM.status())
            return
        if path == "/api/tiktok/qr/status":
            try:
                self._send_json(TIKTOK.check_qr_authorization())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/sources":
            self._send_json({"sources": SOURCES.list_sources()})
            return
        if path == "/api/automation/status":
            self._send_json(AUTOMATION.status())
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = JOBS.get(job_id)
            if not job:
                self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(job.snapshot())
            return
        if path.startswith("/api/artifacts/"):
            self._serve_artifact(path)
            return
        self._send_json({"error": "Route not found."}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._is_authorized():
            self._send_auth_required()
            return
        body = self._read_json_body()

        if parsed.path == "/api/run":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return

            job = JOBS.create()
            worker = threading.Thread(target=run_job, args=(job, body), daemon=True)
            worker.start()
            self._send_json({"job_id": job.job_id, "status": job.status}, status=HTTPStatus.ACCEPTED)
            return

        if parsed.path == "/api/config":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(RUNTIME.save_config(body))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/telegram/config":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(TELEGRAM.save_config(body))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/tiktok/config":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(TIKTOK.save_config(body))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/tiktok/connect":
            try:
                authorize_url = TIKTOK.build_authorize_url()
                self._send_json({"authorize_url": authorize_url})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/tiktok/qr/start":
            try:
                self._send_json(TIKTOK.start_qr_authorization())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/tiktok/disconnect":
            TIKTOK.disconnect()
            self._send_json(TIKTOK.status())
            return

        if parsed.path == "/api/sources":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json({"sources": SOURCES.add_source(body)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path.startswith("/api/sources/") and parsed.path.endswith("/increment"):
            source_id = parsed.path.split("/")[3]
            count = 1
            if body is not None:
                try:
                    count = int(body.get("count") or 1)
                except Exception:
                    count = 1
            try:
                self._send_json({"sources": SOURCES.increment_posted(source_id, count)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path.startswith("/api/sources/") and parsed.path.endswith("/remove"):
            source_id = parsed.path.split("/")[3]
            try:
                self._send_json({"sources": SOURCES.remove_source(source_id)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/automation/config":
            if body is None:
                self._send_json({"error": "Invalid JSON body."}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_json(AUTOMATION.update_settings(body))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/automation/run-now":
            try:
                self._send_json(AUTOMATION.run_now())
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        self._send_json({"error": "Route not found."}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _is_authorized(self) -> bool:
        public_paths = {
            "/auth/tiktok/callback",
            "/privacy.html",
            "/terms.html",
            "/legal-styles.css",
        }
        if urlparse(self.path).path in public_paths:
            return True

        credentials = basic_auth_credentials()
        if credentials is None:
            return True

        expected_user, expected_password = credentials
        header = self.headers.get("Authorization", "")
        if not header.lower().startswith("basic "):
            return False

        try:
            decoded = b64decode(header.split(" ", 1)[1]).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return False

        return secrets.compare_digest(user, expected_user) and secrets.compare_digest(password, expected_password)

    def _send_auth_required(self) -> None:
        data = b"Authentication required."
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Video Generator Agent"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, document: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = document.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "File not found."}, status=HTTPStatus.NOT_FOUND)
            return

        content = path.read_bytes()
        content_type, _ = mimetypes.guess_type(path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_artifact(self, route_path: str) -> None:
        parts = route_path.split("/")
        if len(parts) < 5:
            self._send_json({"error": "Bad artifact route."}, status=HTTPStatus.BAD_REQUEST)
            return

        job_id = parts[3]
        relative_path = unquote("/".join(parts[4:]))
        job = JOBS.get(job_id)
        if not job or not job.output_dir:
            self._send_json({"error": "Job not found."}, status=HTTPStatus.NOT_FOUND)
            return

        safe_base = Path(job.output_dir).resolve()
        target = (safe_base / relative_path).resolve()
        if safe_base not in target.parents and target != safe_base:
            self._send_json({"error": "Artifact path rejected."}, status=HTTPStatus.FORBIDDEN)
            return

        self._serve_file(target)

    def _handle_tiktok_callback(self, query: dict[str, list[str]]) -> None:
        try:
            payload = TIKTOK.handle_callback(query)
            profile = payload.get("profile") or {}
            name = profile.get("display_name") or "TikTok account"
            document = self._build_callback_page(
                title="TikTok Connected",
                message=f"{html.escape(name)} is now connected to the local video agent.",
                tone="ok",
            )
            self._send_html(document)
        except Exception as exc:
            document = self._build_callback_page(
                title="TikTok Connection Failed",
                message=str(exc),
                tone="bad",
            )
            self._send_html(document, status=HTTPStatus.BAD_REQUEST)

    def _build_callback_page(self, *, title: str, message: str, tone: str) -> str:
        accent = "#1f7a4d" if tone == "ok" else "#9e4234"
        safe_title = html.escape(title)
        safe_message = html.escape(message)
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: linear-gradient(180deg, #fbf7ef 0%, #f0e5d1 100%);
      color: #1a1712;
      display: grid;
      place-items: center;
      min-height: 100vh;
      padding: 24px;
    }}
    .card {{
      width: min(560px, 100%);
      background: #fffaf2;
      border: 1px solid #d4c4a4;
      border-radius: 24px;
      box-shadow: 0 14px 30px rgba(72, 53, 28, 0.09);
      padding: 28px;
    }}
    .eyebrow {{
      color: {accent};
      text-transform: uppercase;
      letter-spacing: 0.16em;
      font-size: 0.8rem;
      margin: 0 0 12px;
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: 2rem;
    }}
    p {{
      color: #645b4e;
      line-height: 1.6;
    }}
    a {{
      display: inline-block;
      margin-top: 12px;
      color: #b34d2e;
      font-weight: 700;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <main class="card">
    <p class="eyebrow">Video Generator Agent</p>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
    <a href="/">Return to the app</a>
  </main>
</body>
</html>
"""


def main() -> None:
    host = os.getenv("VIDEO_AGENT_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("VIDEO_AGENT_PORT", "8765"))
    except Exception:
        port = 8765
    AUTOMATION.start()
    TELEGRAM.start()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Video Generator Agent is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        TELEGRAM.stop()
        AUTOMATION.stop()
        server.server_close()


if __name__ == "__main__":
    main()
