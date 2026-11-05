"""Durable rows for the episode plan and the immutable director package.

Both tables keep every revision.  A confirmed row is never edited in place: a
change creates a new revision and marks the earlier one stale/superseded so a
running production keeps the contract it actually started under.

The package is deliberately split into a root row and one row per episode.
A long drama may not be carried in a single SQLite JSON column, so the root
manifest holds the scope, the policy, the binding identity and the per-episode
manifest hashes, while each episode's shot list lives in its own row.  A reader
can verify one episode manifest against the hash pinned in the root without
loading every other episode.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shine.db import Base
from shine.models import new_id, utcnow


class DirectorEpisodePlan(Base):
    """One revision of the project's episode plan.

    ``DRAFT`` is an editable proposal; ``CONFIRMED`` is the episode-boundary
    contract a package may be built from.  Confirming a new revision marks the
    previous confirmed one ``SUPERSEDED`` instead of deleting it.
    """

    __tablename__ = "director_episode_plans"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_director_episode_plan_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    target_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    tolerance_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="derived", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), default="user", nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DirectorPackageVersion(Base):
    """One immutable snapshot of everything a production run may execute.

    ``status`` moves ``DRAFT → READY_FOR_CONFIRMATION → CONFIRMED``.  ``stale``
    is a separate flag: a re-frozen style, a re-cut shot or an unreachable
    workflow makes the package unusable without rewriting what was confirmed.
    """

    __tablename__ = "director_package_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_director_package_revision"),
        Index("ix_director_package_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    episode_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shot_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    episode_plan_id: Mapped[str | None] = mapped_column(ForeignKey("director_episode_plans.id"), nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="user", nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class DirectorPackageEpisode(Base):
    """The per-episode manifest of one package revision (the paged sub-manifest)."""

    __tablename__ = "director_package_episodes"
    __table_args__ = (
        UniqueConstraint("package_id", "episode_id", name="uq_director_package_episode"),
        UniqueConstraint("package_id", "position", name="uq_director_package_episode_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    package_id: Mapped[str] = mapped_column(
        ForeignKey("director_package_versions.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    shot_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    planned_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
