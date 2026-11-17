"""Phase 5 (c) — library versioning: the twin pins its library version.

The compiler already records ``archetype_library_version`` in the twin
definition metadata (compiler.py). This module makes the pin explicit
and operable:

- ``pin_library_version`` — stamp the version a twin was compiled
  against (called at compile time; defaults to the current library).
- ``recompile_with_version`` — recompilation against an older version
  is explicit: it refuses unless the caller passes ``explicit=True``.
  (It records the version decision; running the compiler itself is the
  caller's job — this module never silently re-runs a compile.)
- ``rollback_library_version`` — a real operation: move a twin's pin
  back to an older known version, recorded as a ``library_version``
  twin event with the actor and reason. Rolling forward again is the
  same operation in the other direction.

Nothing here auto-promotes or auto-recompiles. Known versions are the
versions this tree has shipped; an unknown version is rejected, never
guessed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db.models import DigitalTwin, DigitalTwinEvent, TwinEventType
from backend.simulation.archetypes import ARCHETYPE_LIBRARY_VERSION

CURRENT_LIBRARY_VERSION = ARCHETYPE_LIBRARY_VERSION
KNOWN_LIBRARY_VERSIONS = (CURRENT_LIBRARY_VERSION,)


def pin_library_version(twin: Any, version: str | None = None) -> str:
    """Stamp the version this twin was compiled against."""
    version = version or CURRENT_LIBRARY_VERSION
    if version not in KNOWN_LIBRARY_VERSIONS:
        raise ValueError(f"unknown archetype-library version {version!r}; "
                         f"known: {list(KNOWN_LIBRARY_VERSIONS)}")
    twin.library_version = version
    return version


def recompile_with_version(twin: Any, version: str, explicit: bool = False) -> dict[str, Any]:
    """Record a recompilation decision against a (possibly older) version.

    Recompiling against anything other than the current library version
    is explicit: without ``explicit=True`` this raises instead of
    silently using an old library. The caller still runs the compiler;
    this records which version the recompile targets.
    """
    if version not in KNOWN_LIBRARY_VERSIONS:
        raise ValueError(f"unknown archetype-library version {version!r}; "
                         f"known: {list(KNOWN_LIBRARY_VERSIONS)}")
    if version != CURRENT_LIBRARY_VERSION and not explicit:
        raise ValueError(
            f"recompiling against older library version {version!r} "
            f"(current is {CURRENT_LIBRARY_VERSION!r}) requires explicit=True")
    return {"twin_id": getattr(twin, "id", None),
            "target_version": version,
            "explicit": explicit,
            "current_version": CURRENT_LIBRARY_VERSION,
            "note": ("Recompile against this version, then pin it with "
                     "pin_library_version; the version decision is recorded "
                     "here, the compile itself is not run by this module.")}


def rollback_library_version(db: Any, twin: DigitalTwin, target_version: str,
                             actor: str, reason: str) -> dict[str, Any]:
    """Roll a twin's library pin back to an older known version.

    A real operation: the pin moves, and a ``library_version`` twin
    event records the before/after, actor and reason, so the rollback
    is auditable. Rolling forward again is the same call with the
    newer version as the target.
    """
    if not actor or not actor.strip():
        raise ValueError("rollback requires a named actor")
    if not reason or not reason.strip():
        raise ValueError("rollback requires a reason")
    if target_version not in KNOWN_LIBRARY_VERSIONS:
        raise ValueError(f"unknown archetype-library version {target_version!r}; "
                         f"known: {list(KNOWN_LIBRARY_VERSIONS)}")
    previous = getattr(twin, "library_version", None) or CURRENT_LIBRARY_VERSION
    if target_version == previous:
        raise ValueError(f"twin is already pinned to {target_version!r}; "
                         f"nothing to roll back")
    twin.library_version = target_version
    event = DigitalTwinEvent(
        twin_id=twin.id, event_type=TwinEventType.library_version,
        command={"operation": "rollback_library_version",
                 "from_version": previous, "to_version": target_version,
                 "actor": actor, "reason": reason},
        state_before={"library_version": previous},
        state_after={"library_version": target_version},
        trace=[f"library pin {previous} -> {target_version} by {actor}: {reason}"],
    )
    db.add(event)
    db.commit()
    return {"twin_id": twin.id, "from_version": previous,
            "to_version": target_version, "actor": actor, "reason": reason,
            "event_id": event.id, "at": event.created_at.isoformat()
            if isinstance(event.created_at, datetime) else str(event.created_at)}
