"""Phase 6 digital-twin persistence models."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models import Base, _uuid


class TwinStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class TwinEventType(str, enum.Enum):
    command = "command"
    transition = "transition"
    reset = "reset"


class DigitalTwin(Base):
    __tablename__ = "digital_twins"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(JSON)
    state: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[TwinStatus] = mapped_column(Enum(TwinStatus), default=TwinStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DigitalTwinEvent(Base):
    __tablename__ = "digital_twin_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    twin_id: Mapped[str] = mapped_column(ForeignKey("digital_twins.id"), index=True)
    event_type: Mapped[TwinEventType] = mapped_column(Enum(TwinEventType))
    command: Mapped[dict] = mapped_column(JSON)
    state_before: Mapped[dict] = mapped_column(JSON)
    state_after: Mapped[dict] = mapped_column(JSON)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
