#!/usr/bin/env python3
"""Sync TikTok automation state into a separate MongoDB analytics database."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from pymongo import ASCENDING, MongoClient, ReplaceOne
    from pymongo.errors import OperationFailure, PyMongoError, ServerSelectionTimeoutError
except ImportError:  # pragma: no cover - handled at runtime
    ASCENDING = 1  # type: ignore[assignment]
    MongoClient = None  # type: ignore[assignment]
    ReplaceOne = None  # type: ignore[assignment]
    OperationFailure = Exception  # type: ignore[assignment]
    PyMongoError = Exception  # type: ignore[assignment]
    ServerSelectionTimeoutError = Exception  # type: ignore[assignment]


ROOT = Path(os.getenv("TIKTOK_AUTOMATION_ROOT", Path(__file__).resolve().parent))
SECRETS_ROOT = ROOT / ".secrets"
POST_QUEUE_PATH = SECRETS_ROOT / "post_queue.json"
SOURCE_QUEUE_PATH = SECRETS_ROOT / "source_queue.json"
AUTOMATION_STATE_PATH = SECRETS_ROOT / "automation_state.json"
TIKTOK_TOKENS_PATH = SECRETS_ROOT / "tiktok_tokens.json"
PERFORMANCE_METRICS_PATH = SECRETS_ROOT / "performance_metrics.json"
MONGO_ENV_PATH = SECRETS_ROOT / "mongo.env"

DATE_FIELDS = {
    "added_at",
    "asset_deleted_at",
    "created_at",
    "inbox_delivered_at",
    "last_run_at",
    "next_run_at",
    "posted_at",
    "recorded_at",
    "updated_at",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_time(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def safe_slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return cleaned or fallback


def add_date_fields(doc: dict[str, Any]) -> dict[str, Any]:
    for key in DATE_FIELDS:
        parsed = parse_time(doc.get(key))
        if parsed:
            doc[f"{key}_dt"] = parsed
    return doc


def queue_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pending": sum(1 for item in items if item.get("status") == "pending"),
        "active": sum(1 for item in items if item.get("status") in {"uploading", "processing", "sent_to_inbox"}),
        "making": sum(1 for item in items if item.get("status") in {"pending", "uploading", "processing"}),
        "inbox": sum(1 for item in items if item.get("status") == "sent_to_inbox"),
        "posted": sum(1 for item in items if item.get("status") == "posted"),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
    }


def source_counts(items: list[dict[str, Any]], source_id: str) -> dict[str, int]:
    matching = [item for item in items if str(item.get("source_id") or "") == source_id]
    counts = queue_counts(matching)
    counts["total"] = len(matching)
    return counts


class TikTokMongoSync:
    def __init__(self, *, dry_run: bool = False) -> None:
        load_env_file(MONGO_ENV_PATH)
        load_env_file(SECRETS_ROOT / "server.env")

        self.uri = (
            os.getenv("TIKTOK_MONGODB_URI", "").strip()
            or os.getenv("TIKTOK_MONGO_URI", "").strip()
            or os.getenv("MONGODB_URI", "").strip()
        )
        self.db_name = os.getenv("TIKTOK_MONGO_DB_NAME", "tiktok_video_analytics").strip()
        self.account_id = self._account_id()
        self.health_retention_days = int(os.getenv("TIKTOK_MONGO_HEALTH_RETENTION_DAYS", "90"))
        self.dry_run = dry_run

        if not self.uri:
            raise SystemExit("TIKTOK_MONGODB_URI is missing. Add it to .secrets/mongo.env first.")
        if MongoClient is None:
            raise SystemExit("pymongo is not installed. Run: python -m pip install 'pymongo[srv]>=4.7,<5'")

        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000)
        self.db = self.client[self.db_name]

    def _account_id(self) -> str:
        configured = os.getenv("TIKTOK_ACCOUNT_ID", "").strip()
        if configured:
            return safe_slug(configured, "tiktok_account")
        tokens = read_json(TIKTOK_TOKENS_PATH, {})
        profile = tokens.get("profile") if isinstance(tokens.get("profile"), dict) else {}
        display = str((profile or {}).get("display_name") or "").strip()
        open_id = str(tokens.get("open_id") or (profile or {}).get("open_id") or "").strip()
        return safe_slug(display or open_id, "tiktok_account")

    def ping(self) -> None:
        self.client.admin.command("ping")

    def ensure_indexes(self) -> None:
        self.db.tiktok_accounts.create_index([("account_id", ASCENDING)], unique=True)
        self.db.tiktok_sources.create_index([("account_id", ASCENDING), ("source_id", ASCENDING)], unique=True)
        self.db.tiktok_sources.create_index([("account_id", ASCENDING), ("status", ASCENDING)])
        self.db.tiktok_clips.create_index([("account_id", ASCENDING), ("clip_id", ASCENDING)], unique=True)
        self.db.tiktok_clips.create_index([("account_id", ASCENDING), ("source_id", ASCENDING), ("status", ASCENDING)])
        self.db.tiktok_clips.create_index([("posted_at_dt", ASCENDING)])
        self.db.tiktok_clip_metrics.create_index([("account_id", ASCENDING), ("metric_id", ASCENDING)], unique=True)
        self.db.tiktok_clip_metrics.create_index([("account_id", ASCENDING), ("clip_id", ASCENDING), ("recorded_at_dt", ASCENDING)])
        self.db.tiktok_clip_metrics.create_index([("recorded_at_dt", ASCENDING)])
        self.db.tiktok_automation_state.create_index([("account_id", ASCENDING)], unique=True)
        self.ensure_ttl_index(
            "tiktok_health_snapshots",
            "captured_at",
            max(1, self.health_retention_days) * 86400,
        )

    def ensure_ttl_index(self, collection_name: str, field: str, expire_after_seconds: int) -> None:
        collection = self.db[collection_name]
        index_name = f"{field}_ttl"
        existing = collection.index_information()
        for name, info in existing.items():
            if info.get("key", []) == [(field, ASCENDING)] and name != index_name:
                collection.drop_index(name)
        try:
            collection.create_index([(field, ASCENDING)], name=index_name, expireAfterSeconds=expire_after_seconds)
        except OperationFailure as exc:
            if getattr(exc, "code", None) != 85:
                raise
            collection.drop_index(index_name)
            collection.create_index([(field, ASCENDING)], name=index_name, expireAfterSeconds=expire_after_seconds)

    def bulk_replace(self, collection_name: str, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        if self.dry_run:
            return len(docs)
        ops = [ReplaceOne({"_id": doc["_id"]}, doc, upsert=True) for doc in docs]
        self.db[collection_name].bulk_write(ops, ordered=False)
        return len(docs)

    def sync(self) -> dict[str, int]:
        self.ensure_indexes()
        now = utc_now()
        post_queue = read_json(POST_QUEUE_PATH, {"items": []})
        source_queue = read_json(SOURCE_QUEUE_PATH, {"sources": []})
        automation_state = read_json(AUTOMATION_STATE_PATH, {})
        tokens = read_json(TIKTOK_TOKENS_PATH, {})
        metrics_state = read_json(PERFORMANCE_METRICS_PATH, {"items": []})

        items = [dict(item) for item in post_queue.get("items") or []]
        sources = [dict(source) for source in source_queue.get("sources") or []]
        metrics = [dict(item) for item in metrics_state.get("items") or []]
        counts = queue_counts(items)

        account_doc = {
            "_id": self.account_id,
            "account_id": self.account_id,
            "profile": self.safe_profile(tokens),
            "last_synced_at": now,
        }
        state_doc = add_date_fields(
            {
                "_id": self.account_id,
                "account_id": self.account_id,
                "enabled": bool(automation_state.get("enabled")),
                "interval_hours": int(automation_state.get("interval_hours") or 0),
                "next_run_at": automation_state.get("next_run_at") or "",
                "last_run_at": automation_state.get("last_run_at") or "",
                "last_error": automation_state.get("last_error") or "",
                "queue_counts": counts,
                "logs": list(automation_state.get("logs") or [])[-80:],
                "last_synced_at": now,
            }
        )
        health_doc = {
            "_id": f"{self.account_id}:{now.isoformat(timespec='seconds')}",
            "account_id": self.account_id,
            "captured_at": now,
            "enabled": bool(automation_state.get("enabled")),
            "interval_hours": int(automation_state.get("interval_hours") or 0),
            "queue_counts": counts,
            "last_error_present": bool(automation_state.get("last_error")),
        }

        source_docs = []
        for source in sources:
            source_id = str(source.get("id") or "")
            doc = add_date_fields(
                {
                    **source,
                    "_id": f"{self.account_id}:{source_id}",
                    "account_id": self.account_id,
                    "source_id": source_id,
                    "clip_counts": source_counts(items, source_id),
                    "last_synced_at": now,
                }
            )
            source_docs.append(doc)

        clip_docs = []
        for item in items:
            clip_id = str(item.get("id") or "")
            start = float(item.get("segment_start") or 0.0)
            end = float(item.get("segment_end") or 0.0)
            doc = add_date_fields(
                {
                    **item,
                    "_id": f"{self.account_id}:{clip_id}",
                    "account_id": self.account_id,
                    "clip_id": clip_id,
                    "duration_seconds": max(0.0, end - start),
                    "last_synced_at": now,
                }
            )
            clip_docs.append(doc)

        metric_docs = []
        for metric in metrics:
            metric_id = str(metric.get("id") or "")
            views = max(0, int(metric.get("views") or 0))
            likes = max(0, int(metric.get("likes") or 0))
            comments = max(0, int(metric.get("comments") or 0))
            saves = max(0, int(metric.get("saves") or 0))
            shares = max(0, int(metric.get("shares") or 0))
            average_watch_seconds = max(0.0, float(metric.get("average_watch_seconds") or 0.0))
            watched_full_rate = max(0.0, min(float(metric.get("watched_full_rate") or 0.0), 1.0))
            new_followers = max(0, int(metric.get("new_followers") or 0))
            total_play_time_seconds = max(0, int(metric.get("total_play_time_seconds") or 0))
            doc = add_date_fields(
                {
                    **metric,
                    "_id": f"{self.account_id}:{metric_id}",
                    "account_id": self.account_id,
                    "metric_id": metric_id,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "saves": saves,
                    "shares": shares,
                    "average_watch_seconds": average_watch_seconds,
                    "watched_full_rate": watched_full_rate,
                    "new_followers": new_followers,
                    "total_play_time_seconds": total_play_time_seconds,
                    "like_rate": (likes / views) if views else 0.0,
                    "engagement_rate": ((likes + comments + saves + shares) / views) if views else 0.0,
                    "follower_conversion_rate": (new_followers / views) if views else 0.0,
                    "last_synced_at": now,
                }
            )
            metric_docs.append(doc)

        synced = {
            "tiktok_accounts": self.bulk_replace("tiktok_accounts", [account_doc]),
            "tiktok_automation_state": self.bulk_replace("tiktok_automation_state", [state_doc]),
            "tiktok_sources": self.bulk_replace("tiktok_sources", source_docs),
            "tiktok_clips": self.bulk_replace("tiktok_clips", clip_docs),
            "tiktok_clip_metrics": self.bulk_replace("tiktok_clip_metrics", metric_docs),
            "tiktok_health_snapshots": self.bulk_replace("tiktok_health_snapshots", [health_doc]),
        }
        return synced

    def safe_profile(self, tokens: dict[str, Any]) -> dict[str, Any]:
        profile = tokens.get("profile") if isinstance(tokens.get("profile"), dict) else {}
        return {
            "display_name": str((profile or {}).get("display_name") or ""),
            "open_id": str(tokens.get("open_id") or (profile or {}).get("open_id") or ""),
            "token_scope": str(tokens.get("scope") or ""),
            "access_expires_at": str(tokens.get("access_expires_at") or ""),
            "refresh_expires_at": str(tokens.get("refresh_expires_at") or ""),
        }

    def collection_counts(self, collections: Iterable[str]) -> dict[str, int]:
        if self.dry_run:
            return {name: 0 for name in collections}
        return {name: int(self.db[name].count_documents({})) for name in collections}


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync TikTok automation state to MongoDB.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count docs without writing.")
    parser.add_argument("--counts", action="store_true", help="Print collection counts after sync.")
    args = parser.parse_args()

    try:
        syncer = TikTokMongoSync(dry_run=args.dry_run)
        syncer.ping()
        synced = syncer.sync()
    except (PyMongoError, ServerSelectionTimeoutError) as exc:
        print(f"MongoDB sync failed: {exc}", file=sys.stderr)
        return 1

    print("TikTok MongoDB analytics sync complete")
    for collection, count in synced.items():
        print(f"{collection}: synced={count}")
    if args.counts:
        print("--- collection_counts ---")
        for collection, count in syncer.collection_counts(synced.keys()).items():
            print(f"{collection}: count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
