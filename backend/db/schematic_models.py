"""Phase 3 schematic persistence models."""
from __future__ import annotations

import enum

from sqlalchemy import JSON, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models import Base, _uuid


class SymbolType(str, enum.Enum):
    relay = "relay"
    contactor = "contactor"
    motor = "motor"
    sensor = "sensor"
    switch = "switch"
    fuse = "fuse"
    terminal = "terminal"
    connector = "connector"
    plc = "plc"
    transformer = "transformer"
    power_source = "power_source"
    ground = "ground"
    other = "other"


class WireType(str, enum.Enum):
    power = "power"
    control = "control"
    signal = "signal"
    ground = "ground"
    unknown = "unknown"


class SchematicNode(Base):
    __tablename__ = "schematic_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"), index=True)
    component_id: Mapped[str | None] = mapped_column(ForeignKey("components.id"), nullable=True)
    label: Mapped[str] = mapped_column(String, index=True)
    symbol_type: Mapped[SymbolType] = mapped_column(Enum(SymbolType))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SchematicEdge(Base):
    __tablename__ = "schematic_edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    from_node_id: Mapped[str] = mapped_column(ForeignKey("schematic_nodes.id"), index=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("schematic_nodes.id"), index=True)
    wire_type: Mapped[WireType] = mapped_column(Enum(WireType), default=WireType.unknown)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
