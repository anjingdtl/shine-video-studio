"""Durable auto-delivery state machine for finished production series.

The order is the durable unit of the automatic assembly work: it is created in
the same transaction that completes a series, so the work survives a crash
between the last shot and the assembly, and it carries a bounded number of
attempts before it is reported as failed instead of being silently dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shine.models import Base, new_id, utcnow


class DeliveryOrder(Base):
    """One auto-assembly task for one delivery scope version.

    A *series* order keeps the original behaviour: one reel per finished series
    version.  An *episode* order assembles one episode of a whole-film run whose
    clips come from several series, so it holds no single ``series_id`` and is
    identified by its run episode instead.  The scope columns are what makes the
    two kinds distinguishable — SQLite treats NULLs as distinct in a UNIQUE
    constraint, so the old ``(series_id, version)`` constraint alone could never
    prevent two episode orders for the same version.
    """

    __tablename__ = "delivery_orders"
    __table_args__ = (
        UniqueConstraint("series_id", "version", name="uq_delivery_order_series_version"),
        Index(
            "uq_delivery_order_scope_version",
            "scope_kind",
            "scope_id",
            "version",
            unique=True,
            sqlite_where=text("scope_id IS NOT NULL"),
        ),
        Index("ix_delivery_orders_runnable", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[str | None] = mapped_column(
        ForeignKey("production_series.id", ondelete="CASCADE"), index=True, nullable=True
    )
    scope_kind: Mapped[str] = mapped_column(String(24), default="series", nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(24), default="series_auto", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    shots_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edit_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edl_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    edl_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    edit_rules: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    subtitle_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subtitle_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
