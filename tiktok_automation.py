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
UPLOAD_ACTIVE_STATES = {"pending", "uploading", "processing", "sent_to_inbox"}
REMOTE_TIKTOK_PENDING_STATES = {"uploading", "processing", "sent_to_inbox"}
POST_QUEUE_KEEP_STATES = {"pending", "uploading", "processing", "sent_to_inbox", "posted"}
DEFAULT_TIKTOK_MAX_PENDING_SHARES = 5
DEFAULT_UPLOAD_MAX_ATTEMPTS = 4
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


def is_retryable_tiktok_error(error: BaseException) -> bool:
    message = str(error).lower()
    if any(hint in message for hint in NON_RETRYABLE_ERROR_HINTS):
        return False
    return any(hint in message for hint in RETRYABLE_ERROR_HINTS)


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


def upload_binary(url: str, data: bytes, *, content_type: str) -> int:
    total = len(data)
    request = Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(total),
            "Content-Range": f"bytes 0-{max(0, total - 1)}/{total}",
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


class PostQueueManager:
    def __init__(self, root: Path, default_hashtags: list[str]) -> None:
        self.root = root
        self.secrets_root = root / ".secrets"
        self.secrets_root.mkdir(parents=True, exist_ok=True)
        self.path = self.secrets_root / "post_queue.json"
        self.asset_root = root / "queued_clips"
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.default_hashtags = list(default_hashtags)
        self._lock = threading.Lock()

    def list_items(self) -> list[dict[str, Any]]:
        data = self._read()
        items = [self._normalize_item(dict(item)) for item in data.get("items") or []]
        return sorted(items, key=lambda item: item.get("created_at") or "", reverse=False)

    def enqueue_clip_files(self, source_entry: dict[str, Any], clip_paths: list[Path]) -> list[dict[str, Any]]:
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

            for clip_path in clip_paths:
                key = (str(source_entry.get("id") or ""), clip_path.name)
                if key in existing_keys:
                    continue

                item_id = uuid.uuid4().hex[:12]
                stored_path = self.asset_root / f"{item_id}{clip_path.suffix.lower() or '.mp4'}"
                shutil.copy2(clip_path, stored_path)
                now = utc_now()
                items.append(
                    self._normalize_item(
                        {
                            "id": item_id,
                            "source_id": source_entry.get("id"),
                            "source_url": source_entry.get("source_url"),
                            "source_title": source_entry.get("title") or "Saved source",
                            "clip_label": clip_path.stem,
                            "clip_path": str(stored_path),
                            "original_name": clip_path.name,
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

    def next_pending(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for item in self.list_items():
            next_attempt_at = iso_to_datetime(str(item.get("next_attempt_at") or ""))
            if item.get("status") == "pending" and (next_attempt_at is None or next_attempt_at <= now):
                return item
        return None

    def active_items(self) -> list[dict[str, Any]]:
        return [item for item in self.list_items() if item.get("status") in {"uploading", "processing", "sent_to_inbox"}]

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
        hashtags = item.get("hashtags") or self.default_hashtags
        return {
            "id": str(item.get("id") or uuid.uuid4().hex[:12]),
            "source_id": str(item.get("source_id") or ""),
            "source_url": str(item.get("source_url") or ""),
            "source_title": str(item.get("source_title") or "Saved source"),
            "clip_label": str(item.get("clip_label") or "Queued clip"),
            "clip_path": str(item.get("clip_path") or ""),
            "original_name": str(item.get("original_name") or ""),
            "status": str(item.get("status") or "pending"),
            "publish_id": str(item.get("publish_id") or ""),
            "tiktok_status": str(item.get("tiktok_status") or ""),
            "error": str(item.get("error") or ""),
            "attempts": max(0, safe_int(item.get("attempts"), 0)),
            "next_attempt_at": str(item.get("next_attempt_at") or ""),
            "hashtags": [str(tag) for tag in hashtags if str(tag).strip()],
            "created_at": str(item.get("created_at") or utc_now()),
            "updated_at": str(item.get("updated_at") or item.get("created_at") or utc_now()),
            "posted_at": str(item.get("posted_at") or ""),
            "inbox_delivered_at": str(item.get("inbox_delivered_at") or ""),
            "asset_deleted_at": str(item.get("asset_deleted_at") or ""),
        }


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

        init_payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": clip_size,
                "chunk_size": clip_size,
                "total_chunk_count": 1,
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
        clip_bytes = clip_path.read_bytes()
        status_code = with_retry(
            lambda: upload_binary(upload_url, clip_bytes, content_type=content_type),
            label="TikTok media upload",
        )
        if status_code not in {200, 201, 206}:
            raise RuntimeError(f"TikTok media upload returned unexpected status {status_code}.")

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
        self.publisher = TikTokPublisher(auth_manager)
        self.state_path = root / ".secrets" / "automation_state.json"
        self.max_pending_shares = env_int(
            "TIKTOK_MAX_PENDING_SHARES",
            DEFAULT_TIKTOK_MAX_PENDING_SHARES,
            minimum=1,
            maximum=DEFAULT_TIKTOK_MAX_PENDING_SHARES,
        )
        self.max_upload_attempts = env_int("TIKTOK_UPLOAD_MAX_ATTEMPTS", DEFAULT_UPLOAD_MAX_ATTEMPTS, minimum=1)
        self._lock = threading.Lock()
        self._running = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._recover_after_restart()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="video-agent-automation")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        queue_items = self.post_queue.list_items()
        auth_status = self.auth.status()
        remote_pending_count = self.post_queue.remote_pending_count()
        return {
            "enabled": bool(state.get("enabled")),
            "interval_hours": int(state.get("interval_hours") or 6),
            "next_run_at": state.get("next_run_at"),
            "last_run_at": state.get("last_run_at"),
            "last_error": state.get("last_error") or "",
            "logs": list(state.get("logs") or []),
            "queue_items": queue_items,
            "queue_counts": {
                "pending": sum(1 for item in queue_items if item.get("status") == "pending"),
                "active": sum(1 for item in queue_items if item.get("status") in {"uploading", "processing", "sent_to_inbox"}),
                "posted": sum(1 for item in queue_items if item.get("status") == "posted"),
                "failed": sum(1 for item in queue_items if item.get("status") == "failed"),
            },
            "tiktok_pending_cap": self.max_pending_shares,
            "tiktok_remote_pending": remote_pending_count,
            "can_upload_more_to_tiktok": remote_pending_count < self.max_pending_shares,
            "draft_only": "video.publish" not in str(auth_status.get("token_scope") or ""),
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            enabled = bool(payload.get("enabled"))
            interval_hours = max(1, min(int(payload.get("interval_hours") or state.get("interval_hours") or 6), 24))
            state["enabled"] = enabled
            state["interval_hours"] = interval_hours
            if enabled:
                next_run = payload.get("next_run_at") or utc_now()
                state["next_run_at"] = str(next_run)
            else:
                state["next_run_at"] = None
            self._write_state(state)
        return self.status()

    def run_now(self) -> dict[str, Any]:
        threading.Thread(target=self._run_cycle, kwargs={"forced": True}, daemon=True).start()
        return self.status()

    def on_workflow_completed(self, request: dict[str, Any], output_dir: Path) -> None:
        if str(request.get("source_mode") or "") != "remote_url":
            return

        source_url = str(request.get("source_value") or "").strip()
        if not source_url:
            return

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

        clip_paths = sorted(output_dir.glob("*_captioned.mp4"))[:remaining]
        if not clip_paths:
            return

        self.post_queue.enqueue_clip_files(source_entry, clip_paths)
        self.append_log(
            f"Queued {len(clip_paths)} clip(s) from {source_entry.get('title') or source_url} for TikTok upload."
        )

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
                next_run_at = iso_to_datetime(str(state.get("next_run_at") or ""))
                if next_run_at and next_run_at > datetime.now(timezone.utc):
                    return

            self._refresh_remote_statuses()

            next_item = self.post_queue.next_pending()
            if next_item is None:
                self._generate_from_next_source()
                next_item = self.post_queue.next_pending()

            if next_item is not None:
                remote_pending_count = self.post_queue.remote_pending_count()
                if remote_pending_count >= self.max_pending_shares:
                    self.append_log(
                        f"TikTok pending inbox cap reached ({remote_pending_count}/{self.max_pending_shares}); "
                        "waiting before uploading another clip."
                    )
                else:
                    self._upload_queue_item(next_item)
            else:
                self.append_log("Automation cycle found no pending clips to upload.")

            with self._lock:
                state = self._read_state()
                state["last_run_at"] = utc_now()
                interval_hours = int(state.get("interval_hours") or 6)
                state["next_run_at"] = (
                    datetime.now(timezone.utc) + timedelta(hours=interval_hours)
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
                state["last_error"] = ""
                self._write_state(state)
        except Exception as exc:
            self._set_last_error(str(exc))
            self.append_log(f"Automation cycle failed: {exc}")
        finally:
            self._running.release()

    def _refresh_remote_statuses(self) -> None:
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
            elif remote_status == "SEND_TO_USER_INBOX":
                self.post_queue.update_item(
                    str(item.get("id")),
                    status="sent_to_inbox",
                    tiktok_status=remote_status,
                    inbox_delivered_at=utc_now(),
                    error="",
                )
            elif remote_status in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}:
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
                self.append_log(f"{item.get('clip_label')} failed on TikTok: {fail_reason or 'unknown reason'}.")

    def _generate_from_next_source(self) -> None:
        source_entry = self._pick_source_for_generation()
        if source_entry is None:
            return

        source_id = str(source_entry.get("id") or "")
        pending_count = self.post_queue.allocated_count_for_source(source_id)
        planned = int(source_entry.get("planned_clips") or 0)
        posted = int(source_entry.get("posted_clips") or 0)
        remaining = max(0, planned - posted - pending_count)
        if remaining <= 0:
            return

        request = {
            "project_name": source_entry.get("title") or "Queued Source",
            "topic": source_entry.get("title") or "Queued source clip",
            "source_mode": "remote_url",
            "source_value": source_entry.get("source_url") or "",
            "segments": "",
            "clip_duration_sec": 30,
            "clips_count": min(remaining, 8),
            "frame_rate": "60",
            "language": "auto",
            "whisper_model": "small",
            "add_captions": True,
            "publish_mode": "tiktok_api",
            "rights_confirmed": True,
        }
        job = AutomationJobProxy(self, source_entry)
        result = self.pipeline.run(job, request)
        self.on_workflow_completed(request, result.output_dir)

    def _pick_source_for_generation(self) -> dict[str, Any] | None:
        for source_entry in self.sources.list_sources():
            source_id = str(source_entry.get("id") or "")
            planned = int(source_entry.get("planned_clips") or 0)
            posted = int(source_entry.get("posted_clips") or 0)
            pending = self.post_queue.allocated_count_for_source(source_id)
            if planned - posted - pending > 0:
                return source_entry
        return None

    def _upload_queue_item(self, item: dict[str, Any]) -> None:
        item_id = str(item.get("id") or "")
        clip_path = Path(str(item.get("clip_path") or "")).resolve()
        if not clip_path.exists():
            self.post_queue.update_item(item_id, status="failed", error="Queued clip file is missing.")
            self.append_log(f"{item.get('clip_label')} could not be uploaded because the clip file is missing.")
            return

        self.post_queue.update_item(item_id, status="uploading", error="")
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
                return

            self.post_queue.update_item(
                item_id,
                status="failed",
                attempts=attempts,
                next_attempt_at="",
                error=message,
            )
            self.append_log(f"{item.get('clip_label')} upload failed: {message}")
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

    def _mark_item_posted(self, item: dict[str, Any], remote_status: str) -> None:
        item_id = str(item.get("id") or "")
        source_id = str(item.get("source_id") or "")
        publish_id = item.get("publish_id") or ""

        self.post_queue.update_item(
            item_id,
            status="posted",
            publish_id=publish_id,
            tiktok_status=remote_status,
            posted_at=utc_now(),
            attempts=0,
            next_attempt_at="",
            error="",
        )
        self.post_queue.delete_asset_for_item(item_id)

        if source_id:
            self.sources.increment_posted(source_id, 1)
            self._maybe_finalize_source(source_id)

        self.append_log(f"{item.get('clip_label')} completed on TikTok and was removed from local queued storage.")

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
            self.append_log(f"{source_entry.get('title') or source_entry.get('source_url')} finished and was removed from the source queue.")

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "enabled": False,
                "interval_hours": 6,
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
