from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


RESERVATION_TTL_HOURS = 6


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    reservation_id: str
    week_start: str
    estimated_credits: float
    shared_used_credits: float
    pipeline_used_credits: float
    shared_weekly_credit_budget: float
    pipeline_weekly_credit_budget: float


@dataclass(frozen=True)
class WeeklyUsage:
    week_start: str
    pipeline: str
    shared_used_credits: float
    pipeline_used_credits: float
    input_characters: int
    generation_count: int
    shared_weekly_credit_budget: float
    pipeline_weekly_credit_budget: float

    @property
    def shared_percent_used(self) -> float:
        if self.shared_weekly_credit_budget <= 0:
            return 0.0
        return min(100.0, self.shared_used_credits / self.shared_weekly_credit_budget * 100.0)

    @property
    def pipeline_percent_used(self) -> float:
        if self.pipeline_weekly_credit_budget <= 0:
            return 0.0
        return min(100.0, self.pipeline_used_credits / self.pipeline_weekly_credit_budget * 100.0)


def reserve_credits(
    path: Path,
    *,
    pipeline: str,
    input_characters: int,
    model: str,
    shared_weekly_credit_budget: float,
    pipeline_weekly_credit_budget: float,
    credits_per_character: float,
    now: datetime | None = None,
) -> BudgetDecision:
    moment = _utc_moment(now)
    week_start = _week_start(moment)
    pipeline_name = pipeline.strip().lower() or "unknown"
    characters = max(0, int(input_characters or 0))
    estimated = round(characters * max(0.0, float(credits_per_character or 0.0)), 3)
    shared_budget = max(0.0, float(shared_weekly_credit_budget or 0.0))
    pipeline_budget = max(0.0, float(pipeline_weekly_credit_budget or 0.0))

    if characters <= 0 or estimated <= 0:
        return _decision(
            allowed=False,
            reason="empty_request",
            week_start=week_start,
            estimated=estimated,
            shared_used=0.0,
            pipeline_used=0.0,
            shared_budget=shared_budget,
            pipeline_budget=pipeline_budget,
        )
    if shared_budget <= 0 or pipeline_budget <= 0:
        return _decision(
            allowed=False,
            reason="budget_disabled",
            week_start=week_start,
            estimated=estimated,
            shared_used=0.0,
            pipeline_used=0.0,
            shared_budget=shared_budget,
            pipeline_budget=pipeline_budget,
        )

    with closing(_connection(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _delete_stale_reservations(connection, moment)
        shared_used = _sum_credits(connection, week_start=week_start)
        pipeline_used = _sum_credits(connection, week_start=week_start, pipeline=pipeline_name)
        if pipeline_used + estimated > pipeline_budget:
            connection.commit()
            return _decision(
                allowed=False,
                reason="pipeline_weekly_budget",
                week_start=week_start,
                estimated=estimated,
                shared_used=shared_used,
                pipeline_used=pipeline_used,
                shared_budget=shared_budget,
                pipeline_budget=pipeline_budget,
            )
        if shared_used + estimated > shared_budget:
            connection.commit()
            return _decision(
                allowed=False,
                reason="shared_weekly_budget",
                week_start=week_start,
                estimated=estimated,
                shared_used=shared_used,
                pipeline_used=pipeline_used,
                shared_budget=shared_budget,
                pipeline_budget=pipeline_budget,
            )

        reservation_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO usage_events (
                id, created_at, week_start, pipeline, status,
                input_characters, credits, model
            ) VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)
            """,
            (
                reservation_id,
                moment.isoformat(),
                week_start,
                pipeline_name,
                characters,
                estimated,
                model.strip(),
            ),
        )
        connection.commit()
    return BudgetDecision(
        allowed=True,
        reason="reserved",
        reservation_id=reservation_id,
        week_start=week_start,
        estimated_credits=estimated,
        shared_used_credits=round(shared_used, 3),
        pipeline_used_credits=round(pipeline_used, 3),
        shared_weekly_credit_budget=shared_budget,
        pipeline_weekly_credit_budget=pipeline_budget,
    )


def commit_reservation(path: Path, reservation_id: str, *, actual_credits: float | None = None) -> None:
    if not reservation_id:
        return
    with closing(_connection(path)) as connection:
        if actual_credits is None:
            connection.execute(
                "UPDATE usage_events SET status = 'committed' WHERE id = ? AND status = 'reserved'",
                (reservation_id,),
            )
        else:
            connection.execute(
                """
                UPDATE usage_events
                SET status = 'committed', credits = ?
                WHERE id = ? AND status = 'reserved'
                """,
                (max(0.0, float(actual_credits)), reservation_id),
            )
        connection.commit()


def release_reservation(path: Path, reservation_id: str) -> None:
    if not reservation_id:
        return
    with closing(_connection(path)) as connection:
        connection.execute(
            "DELETE FROM usage_events WHERE id = ? AND status = 'reserved'",
            (reservation_id,),
        )
        connection.commit()


def load_weekly_usage(
    path: Path,
    *,
    pipeline: str,
    shared_weekly_credit_budget: float,
    pipeline_weekly_credit_budget: float,
    now: datetime | None = None,
) -> WeeklyUsage:
    moment = _utc_moment(now)
    week_start = _week_start(moment)
    pipeline_name = pipeline.strip().lower() or "unknown"
    with closing(_connection(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _delete_stale_reservations(connection, moment)
        shared_used = _sum_credits(connection, week_start=week_start)
        pipeline_used = _sum_credits(connection, week_start=week_start, pipeline=pipeline_name)
        row = connection.execute(
            """
            SELECT COALESCE(SUM(input_characters), 0), COUNT(*)
            FROM usage_events
            WHERE week_start = ? AND pipeline = ? AND status = 'committed'
            """,
            (week_start, pipeline_name),
        ).fetchone()
        connection.commit()
    return WeeklyUsage(
        week_start=week_start,
        pipeline=pipeline_name,
        shared_used_credits=round(shared_used, 3),
        pipeline_used_credits=round(pipeline_used, 3),
        input_characters=int(row[0] or 0),
        generation_count=int(row[1] or 0),
        shared_weekly_credit_budget=max(0.0, float(shared_weekly_credit_budget or 0.0)),
        pipeline_weekly_credit_budget=max(0.0, float(pipeline_weekly_credit_budget or 0.0)),
    )


def _connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            week_start TEXT NOT NULL,
            pipeline TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('reserved', 'committed')),
            input_characters INTEGER NOT NULL,
            credits REAL NOT NULL,
            model TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS usage_events_week_pipeline
        ON usage_events (week_start, pipeline, status)
        """
    )
    return connection


def _delete_stale_reservations(connection: sqlite3.Connection, moment: datetime) -> None:
    cutoff = (moment - timedelta(hours=RESERVATION_TTL_HOURS)).isoformat()
    connection.execute(
        "DELETE FROM usage_events WHERE status = 'reserved' AND created_at < ?",
        (cutoff,),
    )


def _sum_credits(
    connection: sqlite3.Connection,
    *,
    week_start: str,
    pipeline: str | None = None,
) -> float:
    query = (
        "SELECT COALESCE(SUM(credits), 0) FROM usage_events "
        "WHERE week_start = ? AND status IN ('reserved', 'committed')"
    )
    parameters: tuple[str, ...] = (week_start,)
    if pipeline is not None:
        query += " AND pipeline = ?"
        parameters = (week_start, pipeline)
    row = connection.execute(query, parameters).fetchone()
    return max(0.0, float(row[0] or 0.0))


def _utc_moment(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _week_start(moment: datetime) -> str:
    return (moment.date() - timedelta(days=moment.weekday())).isoformat()


def _decision(
    *,
    allowed: bool,
    reason: str,
    week_start: str,
    estimated: float,
    shared_used: float,
    pipeline_used: float,
    shared_budget: float,
    pipeline_budget: float,
) -> BudgetDecision:
    return BudgetDecision(
        allowed=allowed,
        reason=reason,
        reservation_id="",
        week_start=week_start,
        estimated_credits=estimated,
        shared_used_credits=round(shared_used, 3),
        pipeline_used_credits=round(pipeline_used, 3),
        shared_weekly_credit_budget=shared_budget,
        pipeline_weekly_credit_budget=pipeline_budget,
    )
