from __future__ import annotations

import json
import mimetypes
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


UPLOAD_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_FETCH_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
VIDEO_LIST_URL = (
    "https://open.tiktokapis.com/v2/video/list/"
    "?fields=id,create_time,share_url,video_description,duration,like_count,comment_count,share_count,view_count,title"
)
UPLOAD_ACTIVE_STATES = {"pending", "uploading", "processing", "sent_to_inbox"}
GENERATION_BLOCKING_STATES = {"pending", "uploading", "processing"}
REMOTE_TIKTOK_PENDING_STATES = {"uploading", "processing", "sent_to_inbox"}
POST_QUEUE_KEEP_STATES = {"pending", "uploading", "processing", "sent_to_inbox", "posted"}
DEFAULT_TIKTOK_MAX_PENDING_SHARES = 5
DEFAULT_UPLOAD_MAX_ATTEMPTS = 4
DEFAULT_AUTOMATION_INTERVAL_HOURS = 4
DEFAULT_SOURCE_MAX_FAILURES = 2
DEFAULT_TIKTOK_PROCESSING_TIMEOUT_HOURS = 2
DEFAULT_TIKTOK_PUBLIC_USERNAME = "film.box.official"
TIKTOK_MIN_CHUNK_SIZE = 5 * 1024 * 1024
TIKTOK_MAX_CHUNK_SIZE = 64 * 1024 * 1024
TIKTOK_UPLOAD_CHUNK_SIZE = 32 * 1024 * 1024
SOURCE_QUALITY_MIN_POSTED = 6
SOURCE_QUALITY_MIN_AVG_VIEWS = 150
SOURCE_QUALITY_MIN_TOP_VIEWS = 300
SOURCE_QUALITY_MIN_FOLLOWERS = 1
CONTENT_MODE_GROWTH = "growth"
CONTENT_MODE_MONETIZATION = "monetization"
ACCOUNT_PROFILE_MAIN_RU = "main_ru"
ACCOUNT_PROFILE_FUTURE_EN = "future_en"
CONTENT_MODE_ALIASES = {
    "money": CONTENT_MODE_MONETIZATION,
    "monetize": CONTENT_MODE_MONETIZATION,
    "monetisation": CONTENT_MODE_MONETIZATION,
    "monetization": CONTENT_MODE_MONETIZATION,
    "creator_rewards": CONTENT_MODE_MONETIZATION,
    "long": CONTENT_MODE_MONETIZATION,
    "growth": CONTENT_MODE_GROWTH,
    "short": CONTENT_MODE_GROWTH,
}
ACCOUNT_PROFILE_ALIASES = {
    "main": ACCOUNT_PROFILE_MAIN_RU,
    "main_ru": ACCOUNT_PROFILE_MAIN_RU,
    "ru": ACCOUNT_PROFILE_MAIN_RU,
    "russian": ACCOUNT_PROFILE_MAIN_RU,
    "film_box": ACCOUNT_PROFILE_MAIN_RU,
    "film_box_official": ACCOUNT_PROFILE_MAIN_RU,
    "future_en": ACCOUNT_PROFILE_FUTURE_EN,
    "english": ACCOUNT_PROFILE_FUTURE_EN,
    "en": ACCOUNT_PROFILE_FUTURE_EN,
    "english_account": ACCOUNT_PROFILE_FUTURE_EN,
}
MODE_PROFILES = {
    CONTENT_MODE_GROWTH: {
        "label": "Growth",
        "clip_prefix": "clip",
        "clip_duration_sec": 30,
        "default_planned_clips": 8,
    },
    CONTENT_MODE_MONETIZATION: {
        "label": "Monetization",
        "clip_prefix": "money",
        "clip_duration_sec": 72,
        "default_planned_clips": 8,
    },
}
CANONICAL_TIKTOK_HASHTAGS = [
    "#fyp",
    "#\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0438",
    "#relatable",
    "#recommendations",
    "#\u0440\u0435\u043a\u0438",
]
CAPTION_EMOJI = "\U0001F609"
RETRYABLE_ERROR_HINTS = (
    "network error",
    "timed out",
    "timeout",
    "try again later",
    "temporarily",
    "too many requests",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "server error",
    "eof occurred",
    "connection reset",
)


def repair_mojibake(text: str) -> str:
    try:
        fixed = text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed if fixed.count("\ufffd") <= text.count("\ufffd") else text


def normalize_tiktok_hashtag(tag: str) -> str:
    clean = repair_mojibake(str(tag or "").strip())
    if not clean:
        return ""
    if not clean.startswith("#"):
        clean = f"#{clean}"
    if clean.lower() in {"#fyp", "#f\u0443\u0440"}:
        return "#fyp"
    return clean


def normalize_tiktok_hashtags(hashtags: list[str] | tuple[str, ...] | None) -> list[str]:
    source = list(hashtags or CANONICAL_TIKTOK_HASHTAGS)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in source:
        tag = normalize_tiktok_hashtag(str(raw_tag))
        key = tag.casefold()
        if tag and key not in seen:
            normalized.append(tag)
            seen.add(key)
    return normalized or list(CANONICAL_TIKTOK_HASHTAGS)


def compact_number(value: int | float) -> str:
    number = float(value)
    for suffix, divisor in (("M", 1_000_000), ("K", 1_000)):
        if abs(number) >= divisor:
            compact = number / divisor
            text = f"{compact:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(int(number))


def normalize_content_mode(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    return CONTENT_MODE_ALIASES.get(key, CONTENT_MODE_GROWTH)


def content_mode_profile(value: Any) -> dict[str, Any]:
    return MODE_PROFILES[normalize_content_mode(value)]


def content_mode_label(value: Any) -> str:
    return str(content_mode_profile(value).get("label") or "Growth")


def clip_label_prefix(value: Any) -> str:
    return str(content_mode_profile(value).get("clip_prefix") or "clip")


def normalize_account_profile(value: Any) -> str:
    key = str(value or "").strip().lower().replace("-", "_")
    return ACCOUNT_PROFILE_ALIASES.get(key, ACCOUNT_PROFILE_MAIN_RU)


def account_profile_label(value: Any) -> str:
    profile = normalize_account_profile(value)
    return "Future English account" if profile == ACCOUNT_PROFILE_FUTURE_EN else "Film Box Official RU"


def normalize_audience_language(value: Any, *, account_profile: Any = None) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("en"):
        return "en"
    if text.startswith("ru"):
        return "ru"
    return "en" if normalize_account_profile(account_profile) == ACCOUNT_PROFILE_FUTURE_EN else "ru"
NON_RETRYABLE_ERROR_HINTS = (
    "scope_not_authorized",
    "access_token_invalid",
    "url_ownership_unverified",
    "spam_risk_user_banned_from_posting",
    "user is banned",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_after(seconds: int | float) -> str:
    target = datetime.now(timezone.utc) + timedelta(seconds=float(seconds))
    return target.isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def is_retryable_tiktok_error(error: BaseException) -> bool:
    message = str(error).lower()
    if any(hint in message for hint in NON_RETRYABLE_ERROR_HINTS):
        return False
    return any(hint in message for hint in RETRYABLE_ERROR_HINTS)


def is_youtube_download_blocker(error: str, source_url: str = "") -> bool:
    message = str(error or "").lower()
    source = str(source_url or "").lower()
    is_youtube_source = "youtu.be/" in source or "youtube.com/" in source or "youtube" in message or "[youtube]" in message
    if "timed out" in message or "timeout" in message:
        return False
    permanent_errors = (
        "this video is unavailable",
        "video unavailable",
        "private video",
        "has been removed",
        "account associated with this video has been terminated",
        "members-only content",
        "premieres in",
    )
    if is_youtube_source and any(hint in message for hint in permanent_errors):
        return False

    blockers = (
        "sign in to confirm",
        "not a bot",
        "youtube blocked the download request",
        "cookies",
        "n challenge solving failed",
        "only images are available",
        "requested format is not available",
        "po token",
        "gvs po token",
    )
    generic_yt_dlp_failure = "yt-dlp" in message or "downloading web player api json" in message
    return is_youtube_source and (generic_yt_dlp_failure or any(blocker in message for blocker in blockers))


def is_youtube_download_timeout(error: str, source_url: str = "") -> bool:
    message = str(error or "").lower()
    source = str(source_url or "").lower()
    is_youtube_source = "youtu.be/" in source or "youtube.com/" in source or "youtube" in message or "[youtube]" in message
    return is_youtube_source and ("timed out" in message or "timeout" in message)


def with_retry(operation: Any, *, label: str, attempts: int = 3) -> Any:
    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except RuntimeError as exc:
            last_error = exc
            if attempt >= attempts or not is_retryable_tiktok_error(exc):
                raise
            time.sleep(min(60, 4 * attempt * attempt))

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{label} failed without an error message.")


def json_http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=UTF-8")

    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"message": raw or str(exc)}
        raise RuntimeError(
            parsed.get("message")
            or (parsed.get("error") or {}).get("message")
            or parsed.get("error_description")
            or raw
            or str(exc)
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while contacting TikTok: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError("TikTok returned a non-JSON response.") from exc


def tiktok_upload_plan(total: int) -> tuple[int, int]:
    if total <= 0:
        raise RuntimeError("Clip file is empty.")
    if total < TIKTOK_MIN_CHUNK_SIZE or total <= TIKTOK_MAX_CHUNK_SIZE:
        return total, 1

    chunk_size = TIKTOK_UPLOAD_CHUNK_SIZE
    total_chunks = total // chunk_size
    if total_chunks < 2:
        total_chunks = 2
    if total_chunks > 1000:
        raise RuntimeError("Clip file is too large for TikTok chunked upload.")
    return chunk_size, total_chunks


def upload_binary_chunk(
    url: str,
    data: bytes,
    *,
    content_type: str,
    first_byte: int,
    total: int,
) -> int:
    if not data:
        raise RuntimeError("Cannot upload an empty TikTok media chunk.")
    last_byte = first_byte + len(data) - 1
    request = Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
            "Content-Range": f"bytes {first_byte}-{last_byte}/{total}",
        },
    )
    try:
        with urlopen(request, timeout=900) as response:
            response.read()
            return getattr(response, "status", 200)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(body or str(exc)) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while uploading media: {exc.reason}") from exc


def upload_file_chunks(url: str, clip_path: Path, *, content_type: str, chunk_size: int, total_chunks: int) -> None:
    total = clip_path.stat().st_size
    with clip_path.open("rb") as handle:
        for index in range(total_chunks):
            first_byte = index * chunk_size
            expected_size = total - first_byte if index == total_chunks - 1 else chunk_size
            data = handle.read(expected_size)
            status_code = with_retry(
                lambda data=data, first_byte=first_byte: upload_binary_chunk(
                    url,
                    data,
                    content_type=content_type,
                    first_byte=first_byte,
                    total=total,
                ),
                label=f"TikTok media upload chunk {index + 1}/{total_chunks}",
            )
            if status_code not in {200, 201, 206}:
                raise RuntimeError(f"TikTok media upload returned unexpected status {status_code}.")


class PostQueueManager:
    def __init__(self, root: Path, default_hashtags: list[str]) -> None:
        self.root = root
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.path = self.secrets_root / "post_queue.json"
        self.asset_root = root / "queued_clips"
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.default_hashtags = normalize_tiktok_hashtags(default_hashtags)
        self._lock = threading.Lock()

    def list_items(self) -> list[dict[str, Any]]:
        data = self._read()
        items = [self._normalize_item(dict(item)) for item in data.get("items") or []]
        return sorted(items, key=lambda item: item.get("created_at") or "", reverse=False)

    def enqueue_clip_files(
        self,
        source_entry: dict[str, Any],
        clip_paths: list[Path],
        *,
        start_index: int | None = None,
        segments: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not clip_paths:
            return self.list_items()

        with self._lock:
            data = self._read()
            items = data.setdefault("items", [])
            existing_keys = {
                (str(item.get("source_id") or ""), str(item.get("original_name") or ""))
                for item in items
                if str(item.get("status") or "") in POST_QUEUE_KEEP_STATES
            }

            for offset, clip_path in enumerate(clip_paths):
                segment = (segments or [])[offset] if offset < len(segments or []) else {}
                original_name = clip_path.name
                clip_label = clip_path.stem
                if start_index is not None:
                    sequence = max(1, start_index + offset)
                    suffix = clip_path.suffix.lower() or ".mp4"
                    prefix = clip_label_prefix(source_entry.get("content_mode"))
                    original_name = f"{prefix}_{sequence:02d}_vertical_captioned{suffix}"
                    clip_label = f"{prefix}_{sequence:02d}_vertical_captioned"

                key = (str(source_entry.get("id") or ""), original_name)
                if key in existing_keys:
                    continue

                item_id = uuid.uuid4().hex[:12]
                stored_path = self.asset_root / f"{item_id}{clip_path.suffix.lower() or '.mp4'}"
                shutil.copy2(clip_path, stored_path)
                now = utc_now()
                content_mode = normalize_content_mode(source_entry.get("content_mode"))
                account_profile = normalize_account_profile(source_entry.get("account_profile"))
                audience_language = normalize_audience_language(
                    source_entry.get("audience_language"),
                    account_profile=account_profile,
                )
                items.append(
                    self._normalize_item(
                        {
                            "id": item_id,
                            "source_id": source_entry.get("id"),
                            "source_url": source_entry.get("source_url"),
                            "source_title": source_entry.get("title") or "Saved source",
                            "content_mode": content_mode,
                            "account_profile": account_profile,
                            "audience_language": audience_language,
                            "clip_label": clip_label,
                            "clip_path": str(stored_path),
                            "original_name": original_name,
                            "segment_start": segment.get("start_seconds"),
                            "segment_end": segment.get("end_seconds"),
                            "segment_excerpt": segment.get("excerpt") or "",
                            "status": "pending",
                            "hashtags": list(self.default_hashtags),
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                )

            self._write(data)

        return self.list_items()

    def allocated_count_for_source(self, source_id: str) -> int:
        count = 0
        for item in self.list_items():
            if item.get("source_id") == source_id and item.get("status") in UPLOAD_ACTIVE_STATES:
                count += 1
        return count

    def next_pending(self, source_id: str | None = None) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for item in self.list_items():
            if source_id is not None and item.get("source_id") != source_id:
                continue
            next_attempt_at = iso_to_datetime(str(item.get("next_attempt_at") or ""))
            if item.get("status") == "pending" and (next_attempt_at is None or next_attempt_at <= now):
                return item
        return None

    def active_items(self) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for item in self.list_items():
            status = item.get("status")
            if status in {"uploading", "processing", "sent_to_inbox"}:
                active.append(item)
                continue
            if (
                status == "failed"
                and item.get("publish_id")
                and item.get("tiktok_status") in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}
            ):
                active.append(item)
        return active

    def remote_pending_count(self) -> int:
        return sum(1 for item in self.list_items() if item.get("status") in REMOTE_TIKTOK_PENDING_STATES)

    def items_for_source(self, source_id: str) -> list[dict[str, Any]]:
        return [item for item in self.list_items() if item.get("source_id") == source_id]

    def update_item(self, item_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            items = data.setdefault("items", [])
            for index, item in enumerate(items):
                if item.get("id") != item_id:
                    continue
                updated = dict(item)
                updated.update(changes)
                updated["updated_at"] = utc_now()
                items[index] = self._normalize_item(updated)
                self._write(data)
                return dict(items[index])
        return None

    def delete_asset_for_item(self, item_id: str) -> None:
        with self._lock:
            data = self._read()
            items = data.setdefault("items", [])
            for index, item in enumerate(items):
                if item.get("id") != item_id:
                    continue
                updated = dict(item)
                clip_path = Path(str(updated.get("clip_path") or "")).resolve() if updated.get("clip_path") else None
                if clip_path and clip_path.exists():
                    try:
                        clip_path.unlink()
                    except Exception:
                        pass
                updated["clip_path"] = ""
                updated["asset_deleted_at"] = utc_now()
                updated["updated_at"] = utc_now()
                items[index] = self._normalize_item(updated)
                self._write(data)
                return

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"items": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _normalize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        hashtags = normalize_tiktok_hashtags(item.get("hashtags") or self.default_hashtags)
        content_mode = normalize_content_mode(item.get("content_mode"))
        account_profile = normalize_account_profile(item.get("account_profile"))
        return {
            "id": str(item.get("id") or uuid.uuid4().hex[:12]),
            "source_id": str(item.get("source_id") or ""),
            "source_url": str(item.get("source_url") or ""),
            "source_title": str(item.get("source_title") or "Saved source"),
            "content_mode": content_mode,
            "mode_label": content_mode_label(content_mode),
            "account_profile": account_profile,
            "account_profile_label": account_profile_label(account_profile),
            "audience_language": normalize_audience_language(
                item.get("audience_language"),
                account_profile=account_profile,
            ),
            "clip_label": str(item.get("clip_label") or "Queued clip"),
            "clip_path": str(item.get("clip_path") or ""),
            "original_name": str(item.get("original_name") or ""),
            "segment_start": safe_float(item.get("segment_start"), 0.0),
            "segment_end": safe_float(item.get("segment_end"), 0.0),
            "segment_excerpt": str(item.get("segment_excerpt") or ""),
            "status": str(item.get("status") or "pending"),
            "publish_id": str(item.get("publish_id") or ""),
            "tiktok_status": str(item.get("tiktok_status") or ""),
            "error": str(item.get("error") or ""),
            "attempts": max(0, safe_int(item.get("attempts"), 0)),
            "next_attempt_at": str(item.get("next_attempt_at") or ""),
            "hashtags": hashtags,
            "created_at": str(item.get("created_at") or utc_now()),
            "updated_at": str(item.get("updated_at") or item.get("created_at") or utc_now()),
            "posted_at": str(item.get("posted_at") or ""),
            "inbox_delivered_at": str(item.get("inbox_delivered_at") or ""),
            "asset_deleted_at": str(item.get("asset_deleted_at") or ""),
            "tiktok_video_id": str(item.get("tiktok_video_id") or ""),
            "tiktok_share_url": str(item.get("tiktok_share_url") or ""),
            "tiktok_create_time": safe_int(item.get("tiktok_create_time"), 0),
            "auto_metrics_at": str(item.get("auto_metrics_at") or ""),
        }


class PerformanceMetricsStore:
    def __init__(self, root: Path, post_queue: PostQueueManager) -> None:
        self.root = root
        self.post_queue = post_queue
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.path = self.secrets_root / "performance_metrics.json"
        self._lock = threading.Lock()

    def list_metrics(self) -> list[dict[str, Any]]:
        data = self._read()
        items = [self._normalize_metric(dict(item)) for item in data.get("items") or []]
        return sorted(items, key=lambda item: item.get("recorded_at") or "", reverse=False)

    def recent_metrics(self, limit: int = 10) -> list[dict[str, Any]]:
        return sorted(
            self.latest_metrics_by_clip(),
            key=lambda item: item.get("recorded_at") or "",
            reverse=True,
        )[: max(1, limit)]

    def latest_metrics_by_clip(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for metric in self.list_metrics():
            key = self._metric_identity(metric)
            latest[key] = self._merge_metric_snapshot(latest.get(key), metric)
        return list(latest.values())

    def top_metrics(self, limit: int = 5) -> list[dict[str, Any]]:
        return sorted(
            self.latest_metrics_by_clip(),
            key=lambda item: int(item.get("views") or 0),
            reverse=True,
        )[: max(1, limit)]

    def record_metrics(
        self,
        *,
        clip_ref: str,
        views: int,
        likes: int,
        comments: int = 0,
        saves: int = 0,
        shares: int = 0,
        average_watch_seconds: float = 0.0,
        watched_full_rate: float = 0.0,
        new_followers: int = 0,
        total_play_time_seconds: int = 0,
        metric_source: str = "manual",
        notes: str = "",
    ) -> dict[str, Any]:
        clip = self.resolve_clip(clip_ref)
        if clip is None:
            raise ValueError(f"Could not find clip '{clip_ref}'. Use /clips to see recent clip labels.")

        metric = self._normalize_metric(
            {
                "id": uuid.uuid4().hex[:12],
                "clip_id": clip.get("id") or "",
                "clip_label": clip.get("clip_label") or "",
                "source_id": clip.get("source_id") or "",
                "source_url": clip.get("source_url") or "",
                "source_title": clip.get("source_title") or "",
                "content_mode": clip.get("content_mode") or CONTENT_MODE_GROWTH,
                "views": max(0, int(views)),
                "likes": max(0, int(likes)),
                "comments": max(0, int(comments)),
                "saves": max(0, int(saves)),
                "shares": max(0, int(shares)),
                "average_watch_seconds": max(0.0, float(average_watch_seconds or 0.0)),
                "watched_full_rate": max(0.0, min(float(watched_full_rate or 0.0), 1.0)),
                "new_followers": max(0, int(new_followers or 0)),
                "total_play_time_seconds": max(0, int(total_play_time_seconds or 0)),
                "notes": notes.strip(),
                "recorded_at": utc_now(),
                "metric_source": metric_source,
                "tiktok_video_id": clip.get("tiktok_video_id") or "",
                "tiktok_share_url": clip.get("tiktok_share_url") or "",
                "tiktok_create_time": clip.get("tiktok_create_time") or 0,
                "video_description": clip.get("video_description") or "",
                "segment_start": clip.get("segment_start"),
                "segment_end": clip.get("segment_end"),
                "segment_excerpt": clip.get("segment_excerpt") or "",
            }
        )
        with self._lock:
            data = self._read()
            items = data.setdefault("items", [])
            items.append(metric)
            data["items"] = items[-1000:]
            self._write(data)
        return metric

    def record_api_metrics(self, clip: dict[str, Any], video: dict[str, Any]) -> dict[str, Any] | None:
        return self.record_public_video_metrics(
            clip,
            video,
            metric_source="tiktok_api",
            notes="Auto-synced from TikTok Display API.",
        )

    def record_public_video_metrics(
        self,
        clip: dict[str, Any],
        video: dict[str, Any],
        *,
        metric_source: str,
        notes: str,
    ) -> dict[str, Any] | None:
        video_id = str(video.get("id") or "").strip()
        if not video_id:
            return None

        views = max(0, safe_int(video.get("view_count"), 0))
        likes = max(0, safe_int(video.get("like_count"), 0))
        comments = max(0, safe_int(video.get("comment_count"), 0))
        shares = max(0, safe_int(video.get("share_count"), 0))
        clip_id = str(clip.get("id") or "")

        with self._lock:
            data = self._read()
            items = [self._normalize_metric(dict(item)) for item in data.setdefault("items", [])]
            existing = [
                item
                for item in items
                if str(item.get("clip_id") or "") == clip_id
                and str(item.get("tiktok_video_id") or "") == video_id
                and str(item.get("metric_source") or "") == metric_source
            ]
            latest = sorted(existing, key=lambda item: item.get("recorded_at") or "")[-1] if existing else None
            if latest and all(
                safe_int(latest.get(field), 0) == value
                for field, value in {
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "shares": shares,
                }.items()
            ):
                return latest

            metric = self._normalize_metric(
                {
                    "id": uuid.uuid4().hex[:12],
                    "clip_id": clip_id,
                    "clip_label": clip.get("clip_label") or "",
                    "source_id": clip.get("source_id") or "",
                    "source_url": clip.get("source_url") or "",
                    "source_title": clip.get("source_title") or "",
                    "content_mode": clip.get("content_mode") or CONTENT_MODE_GROWTH,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "saves": 0,
                    "shares": shares,
                    "notes": notes,
                    "recorded_at": utc_now(),
                    "metric_source": metric_source,
                    "tiktok_video_id": video_id,
                    "tiktok_share_url": video.get("share_url") or "",
                    "tiktok_create_time": safe_int(video.get("create_time"), 0),
                    "video_description": video.get("video_description") or video.get("title") or "",
                    "segment_start": clip.get("segment_start"),
                    "segment_end": clip.get("segment_end"),
                    "segment_excerpt": clip.get("segment_excerpt") or "",
                }
            )
            items.append(metric)
            data["items"] = items[-5000:]
            self._write(data)
            return metric

    def resolve_clip(self, clip_ref: str) -> dict[str, Any] | None:
        ref = str(clip_ref or "").strip().lower()
        candidates = [
            item
            for item in self.post_queue.list_items()
            if item.get("status") in {"posted", "sent_to_inbox"}
        ]
        candidates = sorted(
            candidates,
            key=lambda item: item.get("posted_at") or item.get("inbox_delivered_at") or item.get("updated_at") or "",
            reverse=True,
        )
        if not ref or ref in {"latest", "last", "recent"}:
            return candidates[0] if candidates else None

        normalized_ref = ref.replace("-", "_")
        for item in candidates:
            fields = [
                str(item.get("id") or ""),
                str(item.get("clip_label") or ""),
                str(item.get("original_name") or ""),
            ]
            for field in fields:
                normalized_field = field.lower().replace("-", "_")
                if normalized_ref == normalized_field or normalized_ref in normalized_field:
                    return item
        return None

    def recent_clip_lines(self, limit: int = 8) -> list[str]:
        candidates = [
            item
            for item in self.post_queue.list_items()
            if item.get("status") in {"posted", "sent_to_inbox"}
        ]
        candidates = sorted(
            candidates,
            key=lambda item: item.get("posted_at") or item.get("inbox_delivered_at") or item.get("updated_at") or "",
            reverse=True,
        )
        lines: list[str] = []
        for item in candidates[: max(1, limit)]:
            when = item.get("posted_at") or item.get("inbox_delivered_at") or item.get("updated_at") or ""
            status = item.get("status") or ""
            label = item.get("clip_label") or item.get("id") or "clip"
            lines.append(f"- {label} [{content_mode_label(item.get('content_mode'))}] ({status}, {when})")
        return lines

    def summary_line(self, limit: int = 10) -> str:
        metrics = self.recent_metrics(limit)
        if not metrics:
            return "No TikTok performance metrics recorded yet."
        total_views = sum(int(item.get("views") or 0) for item in metrics)
        total_likes = sum(int(item.get("likes") or 0) for item in metrics)
        total_comments = sum(int(item.get("comments") or 0) for item in metrics)
        total_saves = sum(int(item.get("saves") or 0) for item in metrics)
        total_shares = sum(int(item.get("shares") or 0) for item in metrics)
        like_rate = (total_likes / total_views * 100) if total_views else 0.0
        engagement = ((total_likes + total_comments + total_saves + total_shares) / total_views * 100) if total_views else 0.0
        return (
            f"Last {len(metrics)} metrics: {compact_number(total_views)} views, "
            f"{compact_number(total_likes)} likes, like rate {like_rate:.1f}%, "
            f"engagement {engagement:.1f}%."
        )

    def insights_text(self, limit: int = 10) -> str:
        metrics = self.latest_metrics_by_clip()
        if not metrics:
            return "No TikTok performance metrics recorded yet."

        views = sorted(int(item.get("views") or 0) for item in metrics)
        total_views = sum(views)
        median_views = self._median(views)
        avg_views = total_views / len(metrics) if metrics else 0.0
        top = self.top_metrics(5)
        source_rows = self.source_scorecard()
        weak_rows = [
            row
            for row in source_rows
            if row["clips"] >= 3 and row["avg_views"] < 120 and row["like_rate"] < 0.05
        ]

        lines = [
            self.summary_line(limit),
            "",
            f"Tracked clips: {len(metrics)} | Total tracked views: {compact_number(total_views)} | "
            f"Avg: {compact_number(avg_views)} | Median: {compact_number(median_views)}",
            "",
            "Top clips",
        ]
        for index, metric in enumerate(top, start=1):
            views_count = int(metric.get("views") or 0)
            likes = int(metric.get("likes") or 0)
            like_rate = float(metric.get("like_rate") or 0.0) * 100
            multiplier = views_count / median_views if median_views else 0.0
            label = metric.get("clip_label") or metric.get("clip_id") or "clip"
            mode_label = content_mode_label(metric.get("content_mode"))
            lines.append(
                f"{index}. {label} [{mode_label}]: {compact_number(views_count)} views, "
                f"{compact_number(likes)} likes, {like_rate:.1f}% like rate"
                + (f", {multiplier:.1f}x median" if multiplier >= 2 else "")
            )
            share_url = str(metric.get("tiktok_share_url") or "").strip()
            if index == 1 and share_url:
                lines.append(f"   TikTok: {share_url}")
            moment = self._metric_moment_line(metric)
            if moment:
                lines.append(f"   {moment}")
            studio = self._metric_studio_line(metric)
            if studio:
                lines.append(f"   Studio: {studio}")

        if source_rows:
            lines.extend(["", "Best sources"])
            for row in source_rows[:5]:
                lines.append(
                    f"- {self._short_source(row['source_url'])} [{content_mode_label(row['content_mode'])}]: "
                    f"{row['clips']} clips, "
                    f"{compact_number(row['views'])} views total, "
                    f"{compact_number(row['avg_views'])} avg, {row['like_rate'] * 100:.1f}% like rate"
                    + (f", +{compact_number(row['new_followers'])} followers" if row["new_followers"] else "")
                )

        recommendation = self._recommendation_line(top[0] if top else None, source_rows, weak_rows, median_views)
        if recommendation:
            lines.extend(["", "Next move", recommendation])

        return "\n".join(lines)

    def source_scorecard(self) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for metric in self.latest_metrics_by_clip():
            source_url = str(metric.get("source_url") or metric.get("source_id") or "unknown")
            content_mode = normalize_content_mode(metric.get("content_mode"))
            row_key = f"{content_mode}:{source_url}"
            row = rows.setdefault(
                row_key,
                {
                    "source_url": source_url,
                    "source_title": str(metric.get("source_title") or ""),
                    "content_mode": content_mode,
                    "clips": 0,
                    "views": 0,
                    "likes": 0,
                    "comments": 0,
                    "saves": 0,
                    "shares": 0,
                    "new_followers": 0,
                    "watch_weighted_seconds": 0.0,
                    "watch_weighted_views": 0,
                    "avg_views": 0.0,
                    "like_rate": 0.0,
                    "engagement_rate": 0.0,
                    "average_watch_seconds": 0.0,
                },
            )
            row["clips"] += 1
            row["views"] += int(metric.get("views") or 0)
            row["likes"] += int(metric.get("likes") or 0)
            row["comments"] += int(metric.get("comments") or 0)
            row["saves"] += int(metric.get("saves") or 0)
            row["shares"] += int(metric.get("shares") or 0)
            row["new_followers"] += int(metric.get("new_followers") or 0)
            avg_watch = float(metric.get("average_watch_seconds") or 0.0)
            metric_views = int(metric.get("views") or 0)
            if avg_watch and metric_views:
                row["watch_weighted_seconds"] += avg_watch * metric_views
                row["watch_weighted_views"] += metric_views

        for row in rows.values():
            views = max(0, int(row["views"]))
            row["avg_views"] = views / max(1, int(row["clips"]))
            row["like_rate"] = (int(row["likes"]) / views) if views else 0.0
            row["engagement_rate"] = (
                (int(row["likes"]) + int(row["comments"]) + int(row["saves"]) + int(row["shares"])) / views
                if views
                else 0.0
            )
            watch_views = max(0, int(row["watch_weighted_views"]))
            row["average_watch_seconds"] = (float(row["watch_weighted_seconds"]) / watch_views) if watch_views else 0.0

        return sorted(
            rows.values(),
            key=lambda row: (float(row["avg_views"]), float(row["like_rate"]), int(row["views"])),
            reverse=True,
        )

    def _metric_identity(self, metric: dict[str, Any]) -> str:
        return (
            str(metric.get("clip_id") or "")
            or str(metric.get("tiktok_video_id") or "")
            or str(metric.get("clip_label") or "")
            or str(metric.get("id") or "")
        )

    def _merge_metric_snapshot(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        if previous is None:
            return current
        merged = {**previous, **current}
        for field in (
            "saves",
            "average_watch_seconds",
            "watched_full_rate",
            "new_followers",
            "total_play_time_seconds",
            "segment_start",
            "segment_end",
            "segment_excerpt",
        ):
            current_value = current.get(field)
            previous_value = previous.get(field)
            if (current_value in {"", None, 0, 0.0}) and previous_value not in {"", None, 0, 0.0}:
                merged[field] = previous_value
        merged["like_rate"] = (int(merged.get("likes") or 0) / int(merged.get("views") or 0)) if int(merged.get("views") or 0) else 0.0
        merged["engagement_rate"] = (
            (
                int(merged.get("likes") or 0)
                + int(merged.get("comments") or 0)
                + int(merged.get("saves") or 0)
                + int(merged.get("shares") or 0)
            )
            / int(merged.get("views") or 0)
            if int(merged.get("views") or 0)
            else 0.0
        )
        return merged

    @staticmethod
    def _median(values: list[int]) -> float:
        if not values:
            return 0.0
        midpoint = len(values) // 2
        if len(values) % 2:
            return float(values[midpoint])
        return (float(values[midpoint - 1]) + float(values[midpoint])) / 2.0

    @staticmethod
    def _short_source(source_url: str) -> str:
        text = str(source_url or "unknown")
        if "youtu.be/" in text:
            return text.split("youtu.be/", 1)[1].split("?", 1)[0]
        if "v=" in text:
            return text.split("v=", 1)[1].split("&", 1)[0]
        return text[:42] + ("..." if len(text) > 42 else "")

    def _metric_moment_line(self, metric: dict[str, Any]) -> str:
        excerpt = repair_mojibake(str(metric.get("segment_excerpt") or "")).strip()
        if excerpt:
            return f"Moment: {excerpt[:130]}"

        description = repair_mojibake(str(metric.get("video_description") or "")).strip()
        hook = description.split("#", 1)[0].strip()
        if hook:
            return f"Hook: {hook[:130]}"

        start = safe_float(metric.get("segment_start"), 0.0)
        end = safe_float(metric.get("segment_end"), 0.0)
        if end > start:
            return f"Timing: {self._format_seconds(start)}-{self._format_seconds(end)}"
        return ""

    def _metric_studio_line(self, metric: dict[str, Any]) -> str:
        parts: list[str] = []
        avg_watch = float(metric.get("average_watch_seconds") or 0.0)
        full_rate = float(metric.get("watched_full_rate") or 0.0)
        followers = int(metric.get("new_followers") or 0)
        play_time = int(metric.get("total_play_time_seconds") or 0)
        saves = int(metric.get("saves") or 0)
        if avg_watch:
            parts.append(f"avg watch {avg_watch:.1f}s")
        if full_rate:
            parts.append(f"full watch {full_rate * 100:.1f}%")
        if saves:
            parts.append(f"{compact_number(saves)} saves")
        if followers:
            parts.append(f"+{compact_number(followers)} followers")
        if play_time:
            parts.append(f"play time {self._format_duration(play_time)}")
        return ", ".join(parts)

    @staticmethod
    def _format_seconds(value: float) -> str:
        total = max(0, int(round(float(value))))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_duration(seconds: int | float) -> str:
        total = max(0, int(round(float(seconds))))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h{minutes:02d}m"
        if minutes:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"

    def _recommendation_line(
        self,
        top_metric: dict[str, Any] | None,
        source_rows: list[dict[str, Any]],
        weak_rows: list[dict[str, Any]],
        median_views: float,
    ) -> str:
        if not top_metric:
            return ""
        top_views = int(top_metric.get("views") or 0)
        top_source = str(top_metric.get("source_url") or "")
        if top_views >= max(1000, median_views * 5):
            studio_line = ""
            if float(top_metric.get("watched_full_rate") or 0.0) >= 0.35 or int(top_metric.get("new_followers") or 0) >= 10:
                studio_line = " Retention/follower quality is strong, so this is a real repeat candidate."
            return (
                f"Clone the pattern from {self._short_source(top_source)} first: queue more scenes with the same "
                "dialogue tension/curiosity style, and keep weak sources paused until we have another winner."
                + studio_line
            )
        if source_rows and float(source_rows[0].get("like_rate") or 0.0) >= 0.07:
            return (
                f"Prioritize sources similar to {self._short_source(str(source_rows[0].get('source_url') or ''))}; "
                "the views are smaller, but the audience reaction is strong."
            )
        if weak_rows:
            return "A few sources are below 120 average views with weak like rate; replace those with stronger hooks."
        return "Keep the 4-hour monetization cadence and add fresh sources; the account is still building a reliable baseline."

    def _normalize_metric(self, metric: dict[str, Any]) -> dict[str, Any]:
        views = max(0, safe_int(metric.get("views"), 0))
        likes = max(0, safe_int(metric.get("likes"), 0))
        comments = max(0, safe_int(metric.get("comments"), 0))
        saves = max(0, safe_int(metric.get("saves"), 0))
        shares = max(0, safe_int(metric.get("shares"), 0))
        average_watch_seconds = max(0.0, safe_float(metric.get("average_watch_seconds"), 0.0))
        watched_full_rate = max(0.0, min(safe_float(metric.get("watched_full_rate"), 0.0), 1.0))
        new_followers = max(0, safe_int(metric.get("new_followers"), 0))
        total_play_time_seconds = max(0, safe_int(metric.get("total_play_time_seconds"), 0))
        like_rate = (likes / views) if views else 0.0
        engagement_rate = ((likes + comments + saves + shares) / views) if views else 0.0
        follower_conversion_rate = (new_followers / views) if views else 0.0
        duration = max(0.0, safe_float(metric.get("segment_end"), 0.0) - safe_float(metric.get("segment_start"), 0.0))
        average_watch_rate = (average_watch_seconds / duration) if duration else 0.0
        return {
            "id": str(metric.get("id") or uuid.uuid4().hex[:12]),
            "clip_id": str(metric.get("clip_id") or ""),
            "clip_label": str(metric.get("clip_label") or ""),
            "source_id": str(metric.get("source_id") or ""),
            "source_url": str(metric.get("source_url") or ""),
            "source_title": str(metric.get("source_title") or ""),
            "content_mode": normalize_content_mode(metric.get("content_mode")),
            "views": views,
            "likes": likes,
            "comments": comments,
            "saves": saves,
            "shares": shares,
            "like_rate": like_rate,
            "engagement_rate": engagement_rate,
            "average_watch_seconds": average_watch_seconds,
            "watched_full_rate": watched_full_rate,
            "new_followers": new_followers,
            "total_play_time_seconds": total_play_time_seconds,
            "follower_conversion_rate": follower_conversion_rate,
            "average_watch_rate": average_watch_rate,
            "notes": str(metric.get("notes") or ""),
            "recorded_at": str(metric.get("recorded_at") or utc_now()),
            "metric_source": str(metric.get("metric_source") or "manual"),
            "tiktok_video_id": str(metric.get("tiktok_video_id") or ""),
            "tiktok_share_url": str(metric.get("tiktok_share_url") or ""),
            "tiktok_create_time": safe_int(metric.get("tiktok_create_time"), 0),
            "video_description": str(metric.get("video_description") or ""),
            "segment_start": safe_float(metric.get("segment_start"), 0.0),
            "segment_end": safe_float(metric.get("segment_end"), 0.0),
            "segment_excerpt": str(metric.get("segment_excerpt") or ""),
        }

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"items": []}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class TikTokPublisher:
    def __init__(self, auth_manager: Any) -> None:
        self.auth = auth_manager

    def upload_draft(self, clip_path: Path) -> dict[str, Any]:
        access_token = self.auth.get_valid_access_token()
        if not access_token:
            raise RuntimeError("TikTok is not connected or the access token is unavailable.")

        clip_size = clip_path.stat().st_size
        if clip_size <= 0:
            raise RuntimeError("Clip file is empty.")

        chunk_size, total_chunk_count = tiktok_upload_plan(clip_size)
        init_payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": clip_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            }
        }
        response = with_retry(
            lambda: json_http_request(
                UPLOAD_INIT_URL,
                method="POST",
                headers={"Authorization": f"Bearer {access_token}"},
                payload=init_payload,
            ),
            label="TikTok upload initialization",
        )
        error = response.get("error") or {}
        if error.get("code") and error.get("code") != "ok":
            raise RuntimeError(error.get("message") or error.get("code"))

        data = response.get("data") or {}
        publish_id = str(data.get("publish_id") or "").strip()
        upload_url = str(data.get("upload_url") or "").strip()
        if not publish_id or not upload_url:
            raise RuntimeError("TikTok did not return publish_id or upload_url.")

        content_type = mimetypes.guess_type(clip_path.name)[0] or "video/mp4"
        upload_file_chunks(
            upload_url,
            clip_path,
            content_type=content_type,
            chunk_size=chunk_size,
            total_chunks=total_chunk_count,
        )

        status = self.fetch_status(publish_id)
        return {
            "publish_id": publish_id,
            "status": status.get("status") or "PROCESSING_UPLOAD",
            "fail_reason": status.get("fail_reason") or "",
        }

    def fetch_status(self, publish_id: str) -> dict[str, Any]:
        access_token = self.auth.get_valid_access_token()
        if not access_token:
            raise RuntimeError("TikTok is not connected or the access token is unavailable.")

        response = with_retry(
            lambda: json_http_request(
                STATUS_FETCH_URL,
                method="POST",
                headers={"Authorization": f"Bearer {access_token}"},
                payload={"publish_id": publish_id},
            ),
            label="TikTok status fetch",
        )
        error = response.get("error") or {}
        if error.get("code") and error.get("code") != "ok":
            raise RuntimeError(error.get("message") or error.get("code"))
        return dict(response.get("data") or {})

    def list_public_videos(self, *, max_count: int = 20, cursor: int | None = None) -> list[dict[str, Any]]:
        access_token = self.auth.get_valid_access_token()
        if not access_token:
            raise RuntimeError("TikTok is not connected or the access token is unavailable.")

        payload: dict[str, Any] = {"max_count": max(1, min(int(max_count), 20))}
        if cursor is not None:
            payload["cursor"] = cursor

        response = json_http_request(
            VIDEO_LIST_URL,
            method="POST",
            headers={"Authorization": f"Bearer {access_token}"},
            payload=payload,
        )
        error = response.get("error") or {}
        if error.get("code") and error.get("code") != "ok":
            raise RuntimeError(error.get("message") or error.get("code"))

        data = response.get("data") or {}
        videos = data.get("videos") or []
        return [dict(video) for video in videos if isinstance(video, dict)]

    def list_public_profile_videos(self, username: str, *, max_count: int = 20) -> list[dict[str, Any]]:
        clean_username = str(username or "").strip().lstrip("@")
        if not clean_username:
            return []
        try:
            from yt_dlp import YoutubeDL
        except Exception as exc:
            raise RuntimeError("yt-dlp is not available for public profile metrics.") from exc

        url = f"https://www.tiktok.com/@{clean_username}"
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": True,
            "playlistend": max(1, min(int(max_count), 50)),
            "socket_timeout": 20,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                )
            },
        }
        try:
            with YoutubeDL(options) as ydl:
                data = ydl.extract_info(url, download=False)
        except Exception as exc:
            raise RuntimeError(f"Public TikTok profile metrics unavailable: {exc}") from exc

        videos: list[dict[str, Any]] = []
        for entry in (data or {}).get("entries") or []:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id") or "").strip()
            if not video_id:
                continue
            videos.append(
                {
                    "id": video_id,
                    "create_time": safe_int(entry.get("timestamp"), 0),
                    "share_url": entry.get("url") or f"https://www.tiktok.com/@{clean_username}/video/{video_id}",
                    "video_description": entry.get("description") or entry.get("title") or "",
                    "duration": safe_float(entry.get("duration"), 0.0),
                    "view_count": safe_int(entry.get("view_count"), 0),
                    "like_count": safe_int(entry.get("like_count"), 0),
                    "comment_count": safe_int(entry.get("comment_count"), 0),
                    "share_count": safe_int(entry.get("repost_count"), 0),
                }
            )
        return videos


class AutomationJobProxy:
    def __init__(self, controller: "AutomationController", source_entry: dict[str, Any]) -> None:
        self.controller = controller
        self.source_entry = source_entry
        self.job_id = f"auto{uuid.uuid4().hex[:6]}"
        self.output_dir: str | None = None

    def log(self, message: str) -> None:
        label = self.source_entry.get("title") or self.source_entry.get("source_url") or "saved source"
        self.controller.append_log(f"{label}: {message}")


class AutomationController:
    def __init__(
        self,
        root: Path,
        pipeline: Any,
        auth_manager: Any,
        source_manager: Any,
        default_hashtags: list[str],
    ) -> None:
        self.root = root
        self.pipeline = pipeline
        self.auth = auth_manager
        self.sources = source_manager
        self.post_queue = PostQueueManager(root, default_hashtags)
        self.metrics = PerformanceMetricsStore(root, self.post_queue)
        self.publisher = TikTokPublisher(auth_manager)
        self.state_path = root / ".secrets" / "automation_state.json"
        self.source_cache_root = root / ".secrets" / "source_cache"
        self.source_cache_root.mkdir(parents=True, exist_ok=True)
        self.max_pending_shares = env_int(
            "TIKTOK_MAX_PENDING_SHARES",
            DEFAULT_TIKTOK_MAX_PENDING_SHARES,
            minimum=1,
            maximum=DEFAULT_TIKTOK_MAX_PENDING_SHARES,
        )
        self.max_upload_attempts = env_int("TIKTOK_UPLOAD_MAX_ATTEMPTS", DEFAULT_UPLOAD_MAX_ATTEMPTS, minimum=1)
        self.processing_timeout_hours = env_int(
            "TIKTOK_PROCESSING_TIMEOUT_HOURS",
            DEFAULT_TIKTOK_PROCESSING_TIMEOUT_HOURS,
            minimum=1,
            maximum=24,
        )
        self._lock = threading.Lock()
        self._running = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.notifier: Any | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._recover_after_restart()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="video-agent-automation")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def set_notifier(self, notifier: Any) -> None:
        self.notifier = notifier

    def notify(self, message: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier(message)
        except Exception:
            return

    def _caption_hint_for_item(self, item: dict[str, Any]) -> str:
        hashtags = normalize_tiktok_hashtags(item.get("hashtags") or self.post_queue.default_hashtags)
        hook = (
            self._monetization_caption_hook_for_item(item)
            if normalize_content_mode(item.get("content_mode")) == CONTENT_MODE_MONETIZATION
            else self._caption_hook_for_item(item)
        )
        tag_line = f"{' '.join(hashtags)} {CAPTION_EMOJI}".strip()
        return f"{hook}\n{tag_line}".strip() if hook else tag_line

    def _monetization_caption_hook_for_item(self, item: dict[str, Any]) -> str:
        excerpt = repair_mojibake(str(item.get("segment_excerpt") or "")).strip()
        excerpt_key = excerpt.casefold()
        label_seed = (
            str(item.get("source_id") or "")
            + str(item.get("clip_label") or "")
            + str(item.get("segment_start") or "")
        )
        hooks = [
            "Разбор сцены: почему этот момент так цепляет? Досмотри до конца 👀",
            "Сцена выглядит простой, но смысл раскрывается ближе к концу 🫢",
            "Вот почему этот диалог хочется пересмотреть еще раз 👀",
            "Момент, где обычный разговор внезапно становится серьезным 😳",
            "Эта сцена работает именно из-за финальной реплики 👀",
        ]
        if any(token in excerpt_key for token in ("почему", "зачем", "как ", "?")):
            hooks = [
                "Разбор сцены: вопрос звучит просто, но ответ меняет всё 👀",
                "Вот почему этот вопрос цепляет сильнее, чем кажется 😳",
                "Сцена строится на одном вопросе, и он решает всё 🫢",
            ]
        elif any(token in excerpt_key for token in ("деньги", "власть", "работ", "план")):
            hooks = [
                "Разбор сцены: обычный план быстро превращается в проблему 👀",
                "Вот где разговор про планы становится слишком жизненным 😳",
                "Сцена цепляет тем, как спокойно начинается конфликт 👀",
            ]
        elif any(token in excerpt_key for token in ("люб", "сердц", "чувств")):
            hooks = [
                "Разбор сцены: тут эмоции сказали больше, чем слова 🫢",
                "Вот почему этот момент попадает прямо в чувства 😳",
                "Сцена держится на эмоциях, которые сложно скрыть 👀",
            ]
        return self._stable_choice(hooks, label_seed + excerpt_key)

    def _caption_hook_for_item(self, item: dict[str, Any]) -> str:
        excerpt = repair_mojibake(str(item.get("segment_excerpt") or "")).strip()
        excerpt_key = excerpt.casefold()
        label_seed = (
            str(item.get("source_id") or "")
            + str(item.get("clip_label") or "")
            + str(item.get("segment_start") or "")
        )

        high_retention_hooks = [
            "Этот диалог надо досмотреть до конца 👀",
            "Сцена держит до последней секунды 🫢",
            "Дальше начинается самое интересное 😳",
            "Тот самый диалог, где всё пошло не так 👀",
            "Досмотри, там самый сок 👀",
        ]
        if not excerpt_key:
            return self._stable_choice(high_retention_hooks, label_seed)

        priority_keyword_hooks = [
            (
                ("почему", "зачем", "как ", "?"),
                [
                    "Этот диалог надо досмотреть до конца 👀",
                    "Вопрос, после которого всё меняется 😳",
                    "Ответ будет совсем не таким, как кажется 🫢",
                ],
            ),
            (
                ("стой", "тихо", "подожди", "стоп"),
                [
                    "С этого момента сцена резко меняется 👀",
                    "Вот тут становится по-настоящему напряжённо 😳",
                    "Дальше начинается самое интересное 🫢",
                ],
            ),
            (
                ("деньги", "власть", "работ", "план"),
                [
                    "Обычный разговор внезапно стал серьёзным 👀",
                    "Этот момент звучит слишком жизненно 😳",
                    "Вот здесь уже пахнет проблемами 🫢",
                ],
            ),
        ]
        for tokens, hooks in priority_keyword_hooks:
            if any(token in excerpt_key for token in tokens):
                return self._stable_choice(hooks, label_seed + excerpt_key)

        keyword_hooks = [
            (
                ("почему", "зачем", "как ", "?", "why", "how"),
                [
                    "Вопрос, после которого всё меняется 😳",
                    "Сейчас будет ответ, который цепляет 👀",
                ],
            ),
            (
                ("стой", "тихо", "подожди", "стоп", "wait", "stop"),
                [
                    "Вот тут сцена резко становится напряженной 😳",
                    "С этого момента уже не оторваться 👀",
                ],
            ),
            (
                ("деньги", "власть", "работ", "план", "money", "power", "plan"),
                [
                    "Когда обычный разговор становится серьезным 😳",
                    "Этот момент звучит слишком жизненно 👀",
                ],
            ),
            (
                ("люб", "сердц", "чувств", "love", "heart"),
                [
                    "Тут эмоции сказали больше, чем слова 🫠",
                    "Этот момент попадает прямо в чувства 😳",
                ],
            ),
            (
                ("!", "серьезно", "правда", "real"),
                [
                    "Вот это поворот, конечно 😳",
                    "Момент, который хочется пересмотреть 👀",
                ],
            ),
        ]
        for tokens, hooks in keyword_hooks:
            if any(token in excerpt_key for token in tokens):
                return self._stable_choice(hooks, label_seed + excerpt_key)

        generic_hooks = [
            "Этот диалог надо досмотреть до конца 👀",
            "Вот здесь начинается самое интересное 😳",
            "Сцена, которая цепляет с первых секунд 🫢",
            "Тот самый момент, когда всё становится понятно 👀",
            "Этот момент слишком жизненный 😳",
            "Кажется, дальше будет только хуже 🫣",
            "Вот за такие сцены мы и любим кино 👀",
            "Сначала смешно, потом уже не очень 😳",
        ]
        return self._stable_choice(generic_hooks, label_seed + excerpt_key)

    def _stable_choice(self, values: list[str], seed: str) -> str:
        if not values:
            return ""
        score = sum((index + 1) * ord(char) for index, char in enumerate(seed or "caption"))
        return values[score % len(values)]

    def _caption_paste_message(self, item: dict[str, Any]) -> str:
        caption = self._caption_hint_for_item(item)
        mode_label = content_mode_label(item.get("content_mode"))
        return (
            f"Caption to paste in TikTok ({mode_label}):\n"
            f"{caption}\n"
            "TikTok inbox uploads do not prefill captions, so paste this before publishing."
        )

    def _source_cache_dir(self, source_id: str) -> Path:
        safe_id = "".join(char for char in source_id if char.isalnum() or char in {"-", "_"}) or "source"
        return self.source_cache_root / safe_id

    def _cached_source_path(self, source_id: str) -> Path | None:
        cache_dir = self._source_cache_dir(source_id)
        for suffix in (".mp4", ".mkv", ".mov", ".webm", ".m4v"):
            candidate = cache_dir / f"source{suffix}"
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate.resolve()
        return None

    def _used_segments_for_source(self, source_id: str) -> list[dict[str, float]]:
        ranges: list[dict[str, float]] = []
        for item in self.post_queue.items_for_source(source_id):
            if item.get("status") not in POST_QUEUE_KEEP_STATES:
                continue
            start = safe_float(item.get("segment_start"), 0.0)
            end = safe_float(item.get("segment_end"), 0.0)
            if end > start:
                ranges.append({"start": start, "end": end})
        return ranges

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        queue_items = self.post_queue.list_items()
        auth_status = self.auth.status()
        remote_pending_count = self.post_queue.remote_pending_count()
        return {
            "enabled": bool(state.get("enabled")),
            "interval_hours": int(state.get("interval_hours") or DEFAULT_AUTOMATION_INTERVAL_HOURS),
            "next_run_at": state.get("next_run_at"),
            "last_run_at": state.get("last_run_at"),
            "last_error": state.get("last_error") or "",
            "logs": list(state.get("logs") or []),
            "queue_items": queue_items,
            "queue_counts": {
                "pending": sum(1 for item in queue_items if item.get("status") == "pending"),
                "active": sum(1 for item in queue_items if item.get("status") in {"uploading", "processing", "sent_to_inbox"}),
                "making": sum(1 for item in queue_items if item.get("status") in {"pending", "uploading", "processing"}),
                "inbox": sum(1 for item in queue_items if item.get("status") == "sent_to_inbox"),
                "posted": sum(1 for item in queue_items if item.get("status") == "posted"),
                "failed": sum(1 for item in queue_items if item.get("status") == "failed"),
                "monetization_inbox": sum(
                    1
                    for item in queue_items
                    if item.get("status") == "sent_to_inbox"
                    and normalize_content_mode(item.get("content_mode")) == CONTENT_MODE_MONETIZATION
                ),
                "monetization_posted": sum(
                    1
                    for item in queue_items
                    if item.get("status") == "posted"
                    and normalize_content_mode(item.get("content_mode")) == CONTENT_MODE_MONETIZATION
                ),
            },
            "performance_summary": self.metrics.summary_line(),
            "auto_metrics": {
                "token_has_video_list": "video.list" in str(auth_status.get("token_scope") or ""),
                "public_profile_fallback": True,
                "public_profile_username": os.getenv("TIKTOK_PUBLIC_USERNAME", DEFAULT_TIKTOK_PUBLIC_USERNAME),
                "last_sync_at": state.get("last_metrics_sync_at") or "",
                "last_error": state.get("last_metrics_sync_error") or "",
                "last_matched": safe_int(state.get("last_metrics_sync_matched"), 0),
                "last_recorded": safe_int(state.get("last_metrics_sync_recorded"), 0),
            },
            "running": self._running.locked(),
            "tiktok_pending_cap": self.max_pending_shares,
            "tiktok_remote_pending": remote_pending_count,
            "can_upload_more_to_tiktok": remote_pending_count < self.max_pending_shares,
            "draft_only": "video.publish" not in str(auth_status.get("token_scope") or ""),
        }

    def source_progress(self, source_id: str) -> dict[str, Any]:
        source_entry = next((item for item in self.sources.list_sources() if item.get("id") == source_id), None)
        items = self.post_queue.items_for_source(source_id)
        planned = int((source_entry or {}).get("planned_clips") or 0)
        posted = int((source_entry or {}).get("posted_clips") or 0)
        inbox = sum(1 for item in items if item.get("status") == "sent_to_inbox")
        queued = sum(1 for item in items if item.get("status") == "pending")
        making = sum(1 for item in items if item.get("status") in {"uploading", "processing"})
        failed = sum(1 for item in items if item.get("status") == "failed")
        ready = min(planned, posted + inbox) if planned else posted + inbox
        return {
            "source": source_entry,
            "planned": planned,
            "posted": posted,
            "inbox": inbox,
            "ready": ready,
            "queued": queued,
            "making": making,
            "failed": failed,
            "remaining": max(0, planned - ready - queued - making) if planned else 0,
        }

    def source_progress_line(self, source_id: str) -> str:
        progress = self.source_progress(source_id)
        source_entry = progress.get("source")
        if source_entry is None:
            return "Source complete."
        if source_entry.get("status") in {"failed", "skipped"}:
            error = str(source_entry.get("last_error") or "source failed").strip()
            if len(error) > 160:
                error = error[:157] + "..."
            return f"{content_mode_label(source_entry.get('content_mode'))}: skipped after source failure. {error}"
        if source_entry.get("status") == "parked":
            error = str(source_entry.get("last_error") or "").strip()
            if len(error) > 160:
                error = error[:157] + "..."
            reason = f" {error}" if error else ""
            return f"{content_mode_label(source_entry.get('content_mode'))}: parked until source access is fixed.{reason}"
        source_profile = normalize_account_profile(source_entry.get("account_profile"))
        if source_profile != self._active_account_profile():
            return (
                f"{content_mode_label(source_entry.get('content_mode'))}: parked for "
                f"{account_profile_label(source_profile)}; not running on this account."
            )
        planned = progress["planned"]
        ready = progress["ready"]
        mode_label = content_mode_label(source_entry.get("content_mode"))
        return (
            f"{mode_label}: {ready}/{planned} ready, {progress['inbox']} in TikTok inbox, "
            f"{progress['queued'] + progress['making']} making/queued, {progress['remaining']} left."
        )

    def mark_oldest_inbox_posted(self) -> dict[str, Any]:
        inbox_items = [
            item
            for item in self.post_queue.list_items()
            if item.get("status") == "sent_to_inbox"
        ]
        if not inbox_items:
            return {"ok": False, "message": "No TikTok inbox video is waiting to be marked as posted."}

        item = sorted(inbox_items, key=lambda value: value.get("inbox_delivered_at") or value.get("updated_at") or "")[0]
        label = str(item.get("clip_label") or "video")
        source_id = str(item.get("source_id") or "")
        self._mark_item_posted(item, "MANUAL_POSTED", notify=False)
        return {
            "ok": True,
            "message": f"Marked {label} as posted. {self.source_progress_line(source_id)}",
        }

    def record_performance_metrics(
        self,
        *,
        clip_ref: str,
        views: int,
        likes: int,
        comments: int = 0,
        saves: int = 0,
        shares: int = 0,
        average_watch_seconds: float = 0.0,
        watched_full_rate: float = 0.0,
        new_followers: int = 0,
        total_play_time_seconds: int = 0,
        metric_source: str = "manual",
        notes: str = "",
    ) -> dict[str, Any]:
        return self.metrics.record_metrics(
            clip_ref=clip_ref,
            views=views,
            likes=likes,
            comments=comments,
            saves=saves,
            shares=shares,
            average_watch_seconds=average_watch_seconds,
            watched_full_rate=watched_full_rate,
            new_followers=new_followers,
            total_play_time_seconds=total_play_time_seconds,
            metric_source=metric_source,
            notes=notes,
        )

    def recent_clip_lines(self, limit: int = 8) -> list[str]:
        return self.metrics.recent_clip_lines(limit)

    def performance_summary_text(self, limit: int = 10) -> str:
        self.sync_public_video_metrics()
        return self.metrics.insights_text(limit)

    def sync_public_video_metrics(self) -> dict[str, Any]:
        return self._sync_public_video_metrics()

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            enabled = bool(payload.get("enabled"))
            interval_hours = max(
                1,
                min(
                    int(payload.get("interval_hours") or state.get("interval_hours") or DEFAULT_AUTOMATION_INTERVAL_HOURS),
                    24,
                ),
            )
            state["enabled"] = enabled
            state["interval_hours"] = interval_hours
            if enabled:
                next_run = payload.get("next_run_at") or utc_now()
                state["next_run_at"] = str(next_run)
            else:
                state["next_run_at"] = None
            self._write_state(state)
        return self.status()

    def ensure_scheduled(self, *, interval_hours: int = DEFAULT_AUTOMATION_INTERVAL_HOURS) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            interval_hours = max(1, min(int(interval_hours or DEFAULT_AUTOMATION_INTERVAL_HOURS), 24))
            state["enabled"] = True
            state["interval_hours"] = interval_hours
            if not str(state.get("next_run_at") or "").strip():
                state["next_run_at"] = utc_after(interval_hours * 60 * 60)
            self._write_state(state)
        return self.status()

    def run_now(self) -> dict[str, Any]:
        threading.Thread(target=self._run_cycle, kwargs={"forced": True}, daemon=True).start()
        return self.status()

    def on_workflow_completed(self, request: dict[str, Any], output_dir: Path) -> None:
        source_id = str(request.get("source_queue_id") or "").strip()
        source_url = str(request.get("source_original_url") or request.get("source_value") or "").strip()
        if source_id and hasattr(self.sources, "clear_source_failure"):
            try:
                self.sources.clear_source_failure(source_id)
            except Exception:
                pass
        source_entry = None
        if source_id:
            source_entry = next((item for item in self.sources.list_sources() if item.get("id") == source_id), None)
        if source_entry is None and source_url:
            source_entry = next(
                (item for item in self.sources.list_sources() if str(item.get("source_url") or "").strip() == source_url),
                None,
            )
        if source_entry is None:
            return

        pending_count = self.post_queue.allocated_count_for_source(str(source_entry.get("id") or ""))
        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        remaining = max(0, planned - posted - pending_count)
        if remaining <= 0:
            return

        clip_paths = sorted(output_dir.glob("*_captioned.mp4"))
        captioned = bool(clip_paths)
        if not clip_paths:
            clip_paths = sorted(output_dir.glob("*_vertical.mp4"))
        clip_paths = clip_paths[:remaining]
        if not clip_paths:
            return

        segments = self._read_segments_for_output(output_dir)
        start_index = posted + pending_count + 1
        self.post_queue.enqueue_clip_files(
            source_entry,
            clip_paths,
            start_index=start_index,
            segments=segments,
        )
        caption_note = "with subtitles" if captioned else "without subtitles"
        mode_label = content_mode_label(source_entry.get("content_mode"))
        self.append_log(
            f"Queued {len(clip_paths)} {mode_label.lower()} clip(s) from {source_entry.get('title') or source_url} for TikTok upload."
        )
        self.notify(
            f"Created {len(clip_paths)} {mode_label.lower()} video(s) {caption_note} from {source_entry.get('title') or source_url}. "
            f"{self.source_progress_line(str(source_entry.get('id') or ''))}"
        )

    def _read_segments_for_output(self, output_dir: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads((output_dir / "segments.json").read_text(encoding="utf-8"))
        except Exception:
            return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def append_log(self, message: str) -> None:
        with self._lock:
            state = self._read_state()
            logs = list(state.get("logs") or [])
            stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
            logs.append(stamped)
            state["logs"] = logs[-40:]
            self._write_state(state)

    def _recover_after_restart(self) -> None:
        recovered = 0
        failed = 0

        for item in self.post_queue.list_items():
            item_id = str(item.get("id") or "")
            clip_path = Path(str(item.get("clip_path") or "")).resolve() if item.get("clip_path") else None
            status = str(item.get("status") or "")
            publish_id = str(item.get("publish_id") or "").strip()

            if clip_path and not clip_path.exists() and status in {"pending", "uploading", "processing"}:
                self.post_queue.update_item(
                    item_id,
                    status="failed",
                    error="Queued clip file was missing after restart.",
                )
                failed += 1
                continue

            if status in {"uploading", "processing"} and not publish_id:
                self.post_queue.update_item(
                    item_id,
                    status="pending",
                    error="Recovered after restart and returned to pending upload.",
                )
                recovered += 1
                continue

            if status == "sent_to_inbox" and not publish_id:
                self.post_queue.update_item(
                    item_id,
                    status="pending",
                    error="Recovered after restart and returned to pending upload.",
                )
                recovered += 1

        if recovered:
            self.append_log(f"Restart recovery returned {recovered} interrupted queue item(s) to pending.")
        if failed:
            self.append_log(f"Restart recovery marked {failed} queue item(s) as failed because files were missing.")

    def _loop(self) -> None:
        while not self._stop_event.wait(15):
            try:
                self._run_cycle(forced=False)
            except Exception as exc:
                self._set_last_error(str(exc))

    def _run_cycle(self, *, forced: bool) -> None:
        if not self._running.acquire(blocking=False):
            return
        try:
            state = self._read_state()
            if not forced:
                if not state.get("enabled"):
                    return

            status_updates = self._refresh_remote_statuses()

            if not forced:
                next_run_at = iso_to_datetime(str(state.get("next_run_at") or ""))
                if next_run_at and next_run_at > datetime.now(timezone.utc):
                    return

            self._sync_public_video_metrics()

            if status_updates:
                self.append_log(
                    f"Automation cycle refreshed {status_updates} TikTok status update(s); "
                    "waiting for the next cooldown before starting another clip."
                )
            else:
                sequence_source = self._current_sequence_source()
                sequence_source_id = str(sequence_source.get("id") or "") if sequence_source else None
                next_item = self.post_queue.next_pending(sequence_source_id)

                if next_item is None:
                    self._generate_from_next_source()
                    sequence_source = self._current_sequence_source()
                    sequence_source_id = str(sequence_source.get("id") or "") if sequence_source else None
                    next_item = self.post_queue.next_pending(sequence_source_id)

                if next_item is not None:
                    remote_pending_count = self.post_queue.remote_pending_count()
                    if remote_pending_count >= self.max_pending_shares:
                        cap_message = (
                            f"TikTok pending inbox cap reached ({remote_pending_count}/{self.max_pending_shares}); "
                            "waiting before uploading another clip."
                        )
                        self.append_log(cap_message)
                        self.notify(cap_message)
                    else:
                        self._upload_queue_item(next_item)
                else:
                    self.append_log("Automation cycle found no pending clips to upload.")

            with self._lock:
                state = self._read_state()
                state["last_run_at"] = utc_now()
                interval_hours = int(state.get("interval_hours") or DEFAULT_AUTOMATION_INTERVAL_HOURS)
                state["next_run_at"] = (
                    datetime.now(timezone.utc) + timedelta(hours=interval_hours)
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
                state["last_error"] = ""
                self._write_state(state)
        except Exception as exc:
            self._set_last_error(str(exc))
            self.append_log(f"Automation cycle failed: {exc}")
            self.notify(f"Automation run failed: {exc}")
            with self._lock:
                state = self._read_state()
                state["last_run_at"] = utc_now()
                interval_hours = int(state.get("interval_hours") or DEFAULT_AUTOMATION_INTERVAL_HOURS)
                state["next_run_at"] = (
                    datetime.now(timezone.utc) + timedelta(hours=interval_hours)
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_state(state)
        finally:
            self._running.release()

    def _refresh_remote_statuses(self) -> int:
        status_updates = 0
        for item in self.post_queue.active_items():
            publish_id = str(item.get("publish_id") or "").strip()
            if not publish_id:
                continue
            try:
                remote = self.publisher.fetch_status(publish_id)
            except Exception as exc:
                self.append_log(f"Status refresh failed for {item.get('clip_label')}: {exc}")
                continue

            remote_status = str(remote.get("status") or "").strip()
            fail_reason = str(remote.get("fail_reason") or "").strip()

            if remote_status == "PUBLISH_COMPLETE":
                self._mark_item_posted(item, remote_status)
                status_updates += 1
            elif remote_status == "SEND_TO_USER_INBOX":
                was_already_delivered = item.get("status") == "sent_to_inbox"
                self.post_queue.update_item(
                    str(item.get("id")),
                    status="sent_to_inbox",
                    tiktok_status=remote_status,
                    inbox_delivered_at=utc_now(),
                    error="",
                )
                if not was_already_delivered:
                    mode_label = content_mode_label(item.get("content_mode"))
                    self.notify(
                        f"{mode_label} video sent to TikTok inbox: {item.get('clip_label') or 'generated clip'}. "
                        f"{self.source_progress_line(str(item.get('source_id') or ''))}\n"
                        f"{self._caption_paste_message(item)}"
                    )
                    status_updates += 1
            elif remote_status in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}:
                if item.get("status") == "failed":
                    continue
                processing_started_at = iso_to_datetime(
                    str(item.get("created_at") or item.get("updated_at") or "")
                )
                processing_timeout = timedelta(hours=self.processing_timeout_hours)
                if (
                    processing_started_at is not None
                    and datetime.now(timezone.utc) - processing_started_at > processing_timeout
                ):
                    message = (
                        f"{item.get('clip_label') or 'generated clip'} stayed in TikTok {remote_status} "
                        f"for more than {self.processing_timeout_hours} hour(s); marking it stale so the source can continue."
                    )
                    self.post_queue.update_item(
                        str(item.get("id")),
                        status="failed",
                        tiktok_status=remote_status,
                        error=message,
                    )
                    self.append_log(message)
                    self.notify(message)
                    continue
                self.post_queue.update_item(
                    str(item.get("id")),
                    status="processing",
                    tiktok_status=remote_status,
                    error="",
                )
            elif remote_status == "FAILED":
                self.post_queue.update_item(
                    str(item.get("id")),
                    status="failed",
                    tiktok_status=remote_status,
                    error=fail_reason or "TikTok reported a failed publish.",
                )
                message = f"{item.get('clip_label')} failed on TikTok: {fail_reason or 'unknown reason'}."
                self.append_log(message)
                self.notify(message)
                status_updates += 1
        return status_updates

    def _sync_public_video_metrics(self) -> dict[str, Any]:
        token_scope = str((self.auth.status() or {}).get("token_scope") or "")
        metric_source = "tiktok_api"
        notes = "Auto-synced from TikTok Display API."
        try:
            if "video.list" in token_scope:
                videos = self.publisher.list_public_videos(max_count=20)
            else:
                metric_source = "public_profile"
                notes = "Auto-synced from public TikTok profile metadata."
                videos = self.publisher.list_public_profile_videos(
                    os.getenv("TIKTOK_PUBLIC_USERNAME", DEFAULT_TIKTOK_PUBLIC_USERNAME),
                    max_count=50,
                )
        except Exception as exc:
            message = str(exc)
            if "scope" not in message.lower() and "permission" not in message.lower():
                self.append_log(f"Auto metrics sync failed: {message}")
            self._write_metrics_sync_state(error=message, matched=0, recorded=0)
            return {"ok": False, "error": message, "matched": 0, "recorded": 0}

        matches = self._match_public_videos_to_clips(videos)
        recorded = 0
        auto_posted = 0
        recorded_metrics: list[dict[str, Any]] = []
        for clip, video in matches:
            video_id = str(video.get("id") or "").strip()
            if not video_id:
                continue

            update = {
                "tiktok_video_id": video_id,
                "tiktok_share_url": str(video.get("share_url") or ""),
                "tiktok_create_time": safe_int(video.get("create_time"), 0),
                "auto_metrics_at": utc_now(),
            }
            if clip.get("status") == "sent_to_inbox":
                posted_item = dict(clip)
                posted_item.update(update)
                self._mark_item_posted(posted_item, "PUBLIC_VIDEO_LIST", notify=False)
                auto_posted += 1

            updated_clip = self.post_queue.update_item(str(clip.get("id") or ""), **update) or {**clip, **update}
            metric = self.metrics.record_public_video_metrics(
                updated_clip,
                video,
                metric_source=metric_source,
                notes=notes,
            )
            if metric:
                recorded += 1
                recorded_metrics.append(metric)

        if matches:
            self.append_log(f"Auto metrics synced {recorded} public TikTok metric snapshot(s).")
        self._notify_metric_breakouts(recorded_metrics)
        self._write_metrics_sync_state(error="", matched=len(matches), recorded=recorded)
        return {"ok": True, "matched": len(matches), "recorded": recorded, "auto_posted": auto_posted}

    def _notify_metric_breakouts(self, metrics: list[dict[str, Any]]) -> None:
        candidates = [
            metric
            for metric in metrics
            if self._is_breakout_metric(metric)
        ]
        if not candidates:
            return

        candidates = sorted(candidates, key=lambda item: int(item.get("views") or 0), reverse=True)
        with self._lock:
            state = self._read_state()
            notified = {str(item) for item in state.get("notified_breakout_video_ids") or []}
            fresh = []
            for metric in candidates:
                key = str(metric.get("tiktok_video_id") or metric.get("clip_id") or "").strip()
                if key and key not in notified:
                    fresh.append(metric)
                    notified.add(key)
            if not fresh:
                return
            state["notified_breakout_video_ids"] = sorted(notified)[-100:]
            self._write_state(state)

        for metric in fresh[:3]:
            views = int(metric.get("views") or 0)
            likes = int(metric.get("likes") or 0)
            like_rate = float(metric.get("like_rate") or 0.0) * 100
            label = metric.get("clip_label") or "clip"
            source = PerformanceMetricsStore._short_source(str(metric.get("source_url") or ""))
            message = (
                f"Trend alert: {label} reached {compact_number(views)} views "
                f"with {compact_number(likes)} likes ({like_rate:.1f}% like rate).\n"
                f"Source pattern: {source}\n"
                "Next: queue more scenes with the same dialogue tension/curiosity style."
            )
            share_url = str(metric.get("tiktok_share_url") or "").strip()
            if share_url:
                message += f"\nTikTok: {share_url}"
            self.notify(message)

    def _is_breakout_metric(self, metric: dict[str, Any]) -> bool:
        views = int(metric.get("views") or 0)
        like_rate = float(metric.get("like_rate") or 0.0)
        return views >= 3000 or (views >= 1000 and like_rate >= 0.03)

    def _write_metrics_sync_state(self, *, error: str, matched: int, recorded: int) -> None:
        with self._lock:
            state = self._read_state()
            state["last_metrics_sync_at"] = utc_now()
            state["last_metrics_sync_error"] = error
            state["last_metrics_sync_matched"] = max(0, int(matched))
            state["last_metrics_sync_recorded"] = max(0, int(recorded))
            self._write_state(state)

    def _match_public_videos_to_clips(self, videos: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        clips = [
            item
            for item in self.post_queue.list_items()
            if item.get("status") in {"posted", "sent_to_inbox"}
        ]
        if not clips or not videos:
            return []

        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        used_clip_ids: set[str] = set()
        used_video_ids: set[str] = set()

        for video in videos:
            video_id = str(video.get("id") or "").strip()
            if not video_id:
                continue
            direct = next(
                (
                    clip
                    for clip in clips
                    if str(clip.get("tiktok_video_id") or "") == video_id
                    and str(clip.get("id") or "") not in used_clip_ids
                ),
                None,
            )
            if direct:
                matches.append((direct, video))
                used_clip_ids.add(str(direct.get("id") or ""))
                used_video_ids.add(video_id)

        remaining_videos = sorted(
            [video for video in videos if str(video.get("id") or "") not in used_video_ids],
            key=lambda video: safe_int(video.get("create_time"), 0),
        )
        for video in remaining_videos:
            video_id = str(video.get("id") or "").strip()
            best_clip: dict[str, Any] | None = None
            best_score: float | None = None
            for clip in clips:
                clip_id = str(clip.get("id") or "")
                if not clip_id or clip_id in used_clip_ids:
                    continue
                linked_video_id = str(clip.get("tiktok_video_id") or "")
                if linked_video_id and linked_video_id != video_id:
                    continue

                score = self._public_video_clip_match_score(video, clip)
                if score is None:
                    continue
                if best_score is None or score < best_score:
                    best_score = score
                    best_clip = clip

            if best_clip is not None:
                matches.append((best_clip, video))
                used_clip_ids.add(str(best_clip.get("id") or ""))
                used_video_ids.add(video_id)

        return matches

    def _public_video_clip_match_score(self, video: dict[str, Any], clip: dict[str, Any]) -> float | None:
        create_time = safe_int(video.get("create_time"), 0)
        if create_time <= 0:
            return None

        video_dt = datetime.fromtimestamp(create_time, tz=timezone.utc)
        clip_dt = iso_to_datetime(
            str(clip.get("posted_at") or clip.get("inbox_delivered_at") or clip.get("updated_at") or "")
        )
        if clip_dt is None:
            return None

        delta_hours = abs((video_dt - clip_dt).total_seconds()) / 3600.0
        hashtag_hits = self._public_video_hashtag_hits(video)
        if delta_hours > 72 and hashtag_hits < 2:
            return None

        video_duration = safe_float(video.get("duration"), 0.0)
        clip_duration = max(
            0.0,
            safe_float(clip.get("segment_end"), 0.0) - safe_float(clip.get("segment_start"), 0.0),
        )
        duration_delta = abs(video_duration - clip_duration) if video_duration and clip_duration else 0.0
        if duration_delta > 12 and hashtag_hits < 2:
            return None

        return delta_hours + (duration_delta * 3.0) - (hashtag_hits * 6.0)

    def _public_video_hashtag_hits(self, video: dict[str, Any]) -> int:
        description = repair_mojibake(
            str(video.get("video_description") or video.get("title") or "")
        ).casefold()
        return sum(1 for tag in CANONICAL_TIKTOK_HASHTAGS if tag.casefold() in description)

    def _ordered_sources(self) -> list[dict[str, Any]]:
        active_profile = self._active_account_profile()
        return sorted(
            [
                source
                for source in self.sources.list_sources()
                if normalize_account_profile(source.get("account_profile")) == active_profile
            ],
            key=lambda item: (
                str(item.get("added_at") or ""),
                str(item.get("id") or ""),
            ),
        )

    def _active_account_profile(self) -> str:
        return normalize_account_profile(os.getenv("TIKTOK_ACCOUNT_PROFILE", ACCOUNT_PROFILE_MAIN_RU))

    def _source_quality_snapshot(self, source_entry: dict[str, Any]) -> dict[str, Any]:
        source_id = str(source_entry.get("id") or "")
        source_url = str(source_entry.get("source_url") or "")
        metrics = [
            metric
            for metric in self.metrics.latest_metrics_by_clip()
            if str(metric.get("source_id") or "") == source_id
            or (source_url and str(metric.get("source_url") or "") == source_url)
        ]
        views = [safe_int(metric.get("views"), 0) for metric in metrics]
        total_views = sum(views)
        return {
            "clips": len(metrics),
            "views": total_views,
            "avg_views": (total_views / len(metrics)) if metrics else 0.0,
            "top_views": max(views) if views else 0,
            "likes": sum(safe_int(metric.get("likes"), 0) for metric in metrics),
            "saves": sum(safe_int(metric.get("saves"), 0) for metric in metrics),
            "shares": sum(safe_int(metric.get("shares"), 0) for metric in metrics),
            "followers": sum(safe_int(metric.get("new_followers"), 0) for metric in metrics),
        }

    def _maybe_retire_weak_source(self, source_entry: dict[str, Any]) -> bool:
        source_id = str(source_entry.get("id") or "")
        if not source_id:
            return False

        items = self.post_queue.items_for_source(source_id)
        generating_left = any(item.get("status") in {"pending", "uploading", "processing"} for item in items)
        if generating_left:
            return False

        posted = int(source_entry.get("posted_clips") or 0)
        snapshot = self._source_quality_snapshot(source_entry)
        clips_with_metrics = max(posted, int(snapshot.get("clips") or 0))
        if clips_with_metrics < SOURCE_QUALITY_MIN_POSTED:
            return False

        if (
            float(snapshot.get("avg_views") or 0.0) >= SOURCE_QUALITY_MIN_AVG_VIEWS
            or int(snapshot.get("top_views") or 0) >= SOURCE_QUALITY_MIN_TOP_VIEWS
            or int(snapshot.get("followers") or 0) >= SOURCE_QUALITY_MIN_FOLLOWERS
            or int(snapshot.get("saves") or 0) > 0
            or int(snapshot.get("shares") or 0) > 0
        ):
            return False

        label = source_entry.get("title") or source_entry.get("source_url") or "source"
        self.sources.remove_source(source_id)
        shutil.rmtree(self._source_cache_dir(source_id), ignore_errors=True)
        message = (
            f"Stopped weak source after {clips_with_metrics} clip(s): "
            f"{compact_number(snapshot.get('avg_views') or 0)} average views, "
            f"top {compact_number(snapshot.get('top_views') or 0)}. Advancing to the next queued source."
        )
        self.append_log(f"{label}: {message}")
        self.notify(message)
        return True

    def _source_is_finished(self, source_entry: dict[str, Any]) -> bool:
        source_id = str(source_entry.get("id") or "")
        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        active_left = any(
            item.get("status") in UPLOAD_ACTIVE_STATES
            for item in self.post_queue.items_for_source(source_id)
        )
        return posted >= planned and not active_left

    def _current_sequence_source(self) -> dict[str, Any] | None:
        for source_entry in self._ordered_sources():
            if source_entry.get("status") in {"failed", "skipped", "parked"}:
                continue
            if self._source_is_finished(source_entry):
                self._maybe_finalize_source(str(source_entry.get("id") or ""))
                continue
            if self._maybe_retire_weak_source(source_entry):
                continue
            return source_entry
        return None

    def _generate_from_next_source(self) -> None:
        source_entry = self._pick_source_for_generation()
        if source_entry is None:
            return

        source_id = str(source_entry.get("id") or "")
        if any(
            item.get("status") in GENERATION_BLOCKING_STATES
            for item in self.post_queue.items_for_source(source_id)
        ):
            self.append_log(
                f"Waiting for {source_entry.get('title') or source_entry.get('source_url')} "
                "to finish the current generated clip before creating another one."
            )
            return

        pending_count = self.post_queue.allocated_count_for_source(source_id)
        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        remaining = max(0, planned - posted - pending_count)
        if remaining <= 0:
            return

        cache_dir = self._source_cache_dir(source_id)
        cached_source = self._cached_source_path(source_id)
        source_url = str(source_entry.get("source_url") or "")
        excluded_segments = self._used_segments_for_source(source_id)
        content_mode = normalize_content_mode(source_entry.get("content_mode"))
        account_profile = normalize_account_profile(source_entry.get("account_profile"))
        audience_language = normalize_audience_language(
            source_entry.get("audience_language"),
            account_profile=account_profile,
        )
        profile = content_mode_profile(content_mode)
        clip_duration_sec = int(source_entry.get("clip_duration_sec") or profile["clip_duration_sec"])
        clip_duration_sec = max(60, min(90, clip_duration_sec)) if content_mode == CONTENT_MODE_MONETIZATION else max(10, min(60, clip_duration_sec))
        caption_hint = self._caption_hint_for_item(
            {
                "source_id": source_id,
                "content_mode": content_mode,
                "account_profile": account_profile,
                "audience_language": audience_language,
                "hashtags": self.post_queue.default_hashtags,
            }
        )
        request = {
            "project_name": source_entry.get("title") or "Queued Source",
            "topic": source_entry.get("title") or "Queued source clip",
            "source_mode": "local_file" if cached_source else "remote_url",
            "source_value": str(cached_source) if cached_source else source_url,
            "source_queue_id": source_id,
            "source_original_url": source_url,
            "source_cache_dir": str(cache_dir),
            "excluded_segments": excluded_segments,
            "segments": "",
            "clip_duration_sec": clip_duration_sec,
            "clips_count": 1,
            "selection_offset": 0 if excluded_segments else posted + pending_count,
            "frame_rate": "source",
            "language": audience_language,
            "whisper_model": "small",
            "add_captions": True,
            "publish_mode": "tiktok_api",
            "rights_confirmed": True,
            "hashtags": list(self.post_queue.default_hashtags),
            "caption_hint": caption_hint,
            "content_mode": content_mode,
            "account_profile": account_profile,
            "audience_language": audience_language,
        }
        job = AutomationJobProxy(self, source_entry)
        self.notify(
            f"Generating {profile['label'].lower()} video from {source_entry.get('title') or source_entry.get('source_url')} "
            f"({clip_duration_sec}s). "
            f"{self.source_progress_line(source_id)}"
        )
        try:
            result = self.pipeline.run(job, request)
        except Exception as exc:
            self._record_source_generation_failure(source_entry, exc)
            return
        self.on_workflow_completed(request, result.output_dir)

    def _record_source_generation_failure(self, source_entry: dict[str, Any], exc: Exception) -> None:
        source_id = str(source_entry.get("id") or "")
        label = source_entry.get("title") or source_entry.get("source_url") or "source"
        error = str(exc)
        source_url = str(source_entry.get("source_url") or "")
        if is_youtube_download_timeout(error, source_url):
            if source_id and hasattr(self.sources, "defer_source_failure"):
                try:
                    self.sources.defer_source_failure(source_id, error)
                except Exception:
                    pass
            message = (
                f"Source download timed out for {label}. "
                "Parked in queue so it is not lost; it can be retried after increasing the download timeout."
            )
            self.append_log(message)
            self.notify(f"{message}\nReason: {error[:700]}")
            return

        if is_youtube_download_blocker(error, source_url):
            if source_id and hasattr(self.sources, "defer_source_failure"):
                try:
                    self.sources.defer_source_failure(source_id, error)
                except Exception:
                    pass
            message = (
                f"Source download is blocked by YouTube auth/challenge for {label}. "
                "Parked in queue; paste the same link again after YouTube cookies or extraction tokens are refreshed."
            )
            self.append_log(message)
            self.notify(f"{message}\nReason: {error[:700]}")
            return

        max_failures = env_int(
            "SOURCE_MAX_FAILURES",
            DEFAULT_SOURCE_MAX_FAILURES,
            minimum=1,
            maximum=5,
        )
        updated = None
        if source_id and hasattr(self.sources, "mark_source_failure"):
            try:
                updated = self.sources.mark_source_failure(source_id, error, max_failures=max_failures)
            except Exception:
                updated = None

        failures = int((updated or source_entry).get("download_failures") or 0)
        if (updated or {}).get("status") == "failed":
            if source_id:
                shutil.rmtree(self._source_cache_dir(source_id), ignore_errors=True)
            message = (
                f"Skipped source after {failures}/{max_failures} failed generation attempt(s): {label}. "
                "It was removed from the source queue; the next queued source will be tried on the next cycle."
            )
            self.append_log(message)
            self.notify(f"{message}\nReason: {error[:700]}")
        else:
            message = f"Source generation failed {failures}/{max_failures} for {label}; it will retry once before being skipped."
            self.append_log(message)
            self.notify(f"{message}\nReason: {error[:700]}")

    def _pick_source_for_generation(self) -> dict[str, Any] | None:
        source_entry = self._current_sequence_source()
        if source_entry is None:
            return None

        source_id = str(source_entry.get("id") or "")
        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        pending = self.post_queue.allocated_count_for_source(source_id)
        if planned - posted - pending > 0:
            return source_entry

        self.append_log(
            f"Waiting for {source_entry.get('title') or source_entry.get('source_url')} to finish "
            "before starting the next source."
        )
        return None

    def _upload_queue_item(self, item: dict[str, Any]) -> None:
        item_id = str(item.get("id") or "")
        clip_path = Path(str(item.get("clip_path") or "")).resolve()
        if not clip_path.exists():
            self.post_queue.update_item(item_id, status="failed", error="Queued clip file is missing.")
            message = f"{item.get('clip_label')} could not be uploaded because the clip file is missing."
            self.append_log(message)
            self.notify(message)
            return

        self.post_queue.update_item(item_id, status="uploading", error="")
        self.notify(f"Uploading {item.get('clip_label') or 'generated clip'} to TikTok inbox.")
        try:
            result = self.publisher.upload_draft(clip_path)
        except Exception as exc:
            attempts = max(0, safe_int(item.get("attempts"), 0)) + 1
            message = str(exc)
            if attempts < self.max_upload_attempts and is_retryable_tiktok_error(exc):
                delay_seconds = min(3 * 60 * 60, 15 * 60 * attempts * attempts)
                self.post_queue.update_item(
                    item_id,
                    status="pending",
                    attempts=attempts,
                    next_attempt_at=utc_after(delay_seconds),
                    error=message,
                )
                delay_minutes = int(delay_seconds / 60)
                self.append_log(
                    f"{item.get('clip_label')} upload hit a temporary issue and will retry in "
                    f"{delay_minutes} minute(s): {message}"
                )
                self.notify(
                    f"{item.get('clip_label') or 'generated clip'} upload will retry in {delay_minutes} minute(s): {message}"
                )
                return

            self.post_queue.update_item(
                item_id,
                status="failed",
                attempts=attempts,
                next_attempt_at="",
                error=message,
            )
            self.append_log(f"{item.get('clip_label')} upload failed: {message}")
            self.notify(f"{item.get('clip_label') or 'generated clip'} upload failed: {message}")
            return

        status = str(result.get("status") or "").strip()
        if status == "PUBLISH_COMPLETE":
            updated_item = dict(item)
            updated_item["publish_id"] = result.get("publish_id")
            self._mark_item_posted(updated_item, status)
            return

        queue_status = "sent_to_inbox" if status == "SEND_TO_USER_INBOX" else "processing"
        self.post_queue.update_item(
            item_id,
            status=queue_status,
            publish_id=result.get("publish_id"),
            tiktok_status=status,
            inbox_delivered_at=utc_now() if queue_status == "sent_to_inbox" else "",
            attempts=0,
            next_attempt_at="",
            error=result.get("fail_reason") or "",
        )
        self.append_log(f"{item.get('clip_label')} was sent to TikTok with status {status or queue_status}.")
        if queue_status == "sent_to_inbox":
            mode_label = content_mode_label(item.get("content_mode"))
            self.notify(
                f"{mode_label} video sent to TikTok inbox: {item.get('clip_label') or 'generated clip'}. "
                f"{self.source_progress_line(str(item.get('source_id') or ''))}\n"
                f"{self._caption_paste_message(item)}"
            )

    def _mark_item_posted(self, item: dict[str, Any], remote_status: str, *, notify: bool = True) -> None:
        item_id = str(item.get("id") or "")
        source_id = str(item.get("source_id") or "")
        publish_id = item.get("publish_id") or ""

        changes = {
            "status": "posted",
            "publish_id": publish_id,
            "tiktok_status": remote_status,
            "posted_at": utc_now(),
            "attempts": 0,
            "next_attempt_at": "",
            "error": "",
        }
        for key in ("tiktok_video_id", "tiktok_share_url", "tiktok_create_time", "auto_metrics_at"):
            if item.get(key):
                changes[key] = item.get(key)
        self.post_queue.update_item(item_id, **changes)
        self.post_queue.delete_asset_for_item(item_id)

        if source_id:
            try:
                self.sources.increment_posted(source_id, 1)
            except Exception:
                self.append_log(
                    f"{item.get('clip_label')} was posted after its source was already retired from the queue."
                )
            else:
                self._maybe_finalize_source(source_id)

        self.append_log(f"{item.get('clip_label')} completed on TikTok and was removed from local queued storage.")
        if notify:
            label = item.get("clip_label") or "latest"
            self.notify(f"Posted confirmed for {label}. {self.source_progress_line(source_id)}")

    def _maybe_finalize_source(self, source_id: str) -> None:
        source_entry = next((item for item in self.sources.list_sources() if item.get("id") == source_id), None)
        if source_entry is None:
            return

        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        active_left = any(
            item.get("status") in UPLOAD_ACTIVE_STATES for item in self.post_queue.items_for_source(source_id)
        )
        if posted >= planned and not active_left:
            self.sources.remove_source(source_id)
            shutil.rmtree(self._source_cache_dir(source_id), ignore_errors=True)
            self.append_log(f"{source_entry.get('title') or source_entry.get('source_url')} finished and was removed from the source queue.")

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "enabled": False,
                "interval_hours": DEFAULT_AUTOMATION_INTERVAL_HOURS,
                "next_run_at": None,
                "last_run_at": None,
                "last_error": "",
                "logs": [],
            }

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _set_last_error(self, message: str) -> None:
        with self._lock:
            state = self._read_state()
            state["last_error"] = message
            self._write_state(state)
