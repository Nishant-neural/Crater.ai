"""Library-type binding rule (Phase 2).

Phase 2 adds only the ``library_type`` *field* and this binding rule --
the three library contracts themselves (ports, states, parameters,
behaviour templates) are Phase 3. The rule is deliberately narrow:

- aliases propose a match (the alias lists below, taken from
  ``library-v1-schema-draft.md``);
- exactly one matching library type binds; zero matches or two-plus
  matches leave ``library_type`` unset (``None``) -- ambiguous bindings
  are unresolved, never guessed, and a human may dispose of them later.

Matching is on the normalized entity *name* only. An entity whose type
says "relay" but whose name is "K1" does not bind: the name carries no
evidence about which library type it is, and type-based binding is a
Phase 3 decision, not a Phase 2 guess. Alias lists are kept tight for
the same reason: "Feed Tensioner" is a *part* of the wire feeder, not
the feeder itself, so it is not an alias for ``wire_feeder``; bare
"Motor" is not an alias for ``dc_motor`` (a machine has several motors,
and AC vs DC is not in the name).
"""
from __future__ import annotations

from typing import Any

# library_type -> alias names (normalized: lowercase, single spaces).
# Keep these tight: an alias must name the library component itself,
# not one of its parts and not a generic word.
LIBRARY_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "wire_feeder": (
        "wire feeder",
        "wire feed mechanism",
        "wire feed unit",
        "wire feed assembly",
    ),
    "dc_motor": (
        "dc motor",
        "wire feed motor",
        "feed motor",
    ),
    "relay": (
        "relay",
        "power relay",
        "control relay",
    ),
}

LIBRARY_TYPES = tuple(LIBRARY_TYPE_ALIASES)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def bind_library_type(name: str | None, entity_type: str | None = None) -> str | None:
    """Library type for an entity name, or None when it does not bind.

    Unique match only: the name must appear in exactly one type's alias
    list. ``entity_type`` is accepted for callers that have it but is
    not used for matching in Phase 2 (see module docstring).
    """
    normalized = _norm(name)
    if not normalized:
        return None
    matches = [
        library_type
        for library_type, aliases in LIBRARY_TYPE_ALIASES.items()
        if normalized in {_norm(alias) for alias in aliases}
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def assign_library_types(model: Any) -> dict[str, Any]:
    """Set ``library_type`` on a model's entities where binding is unique.

    Entities that do not bind keep ``library_type = None``. Returns a
    small report: how many bound per type, and which names were
    ambiguous (matched two-plus types) so a human can dispose of them.
    The model itself is mutated in place; the report is the audit trail.
    """
    bound: dict[str, int] = {}
    ambiguous: list[str] = []
    for entity in getattr(model, "entities", []) or []:
        normalized = _norm(getattr(entity, "name", ""))
        matches = [
            library_type
            for library_type, aliases in LIBRARY_TYPE_ALIASES.items()
            if normalized in {_norm(alias) for alias in aliases}
        ]
        if len(matches) == 1:
            entity.library_type = matches[0]
            bound[matches[0]] = bound.get(matches[0], 0) + 1
        elif len(matches) > 1:
            ambiguous.append(getattr(entity, "name", ""))
            entity.library_type = None
        else:
            entity.library_type = None
    return {"bound": bound, "ambiguous": sorted(set(ambiguous))}
