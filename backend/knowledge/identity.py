"""Identity rules for code-based global integration (Phase 2).

These are the single home of the merge rules, shared by the code-first
path (``code_first_integration.py``) and the legacy LLM path
(``global_integration.py``), so the two paths cannot drift apart again.

The standing rule (31 Oct 2026): code-based integration must NOT merge
distinct, similar-named components on name alone. The merge rule is
therefore narrow:

- same normalized name, AND
- no conflicting non-empty *hard* identity signal (part_number,
  location_description, voltage), AND
- compatible entity types, AND
- part-number compatible (see ``_part_compatible`` in
  ``code_first_integration.py``), AND
- not two distinct items of the same source list.

Descriptive prose -- notably ``function``, which the LLM paraphrases
("Adjusts wire feed tension" vs "Applies tension to wire feed" vs
"Applies pressure to wire feed") -- is NOT an identity signal and never
blocks a merge. The v9 narrow veto already said this for the LLM
adjudication path; the bug Phase 0 exposed was that the *code-first
grouping* path still used a broad "any differing property blocks the
merge" rule (``_props_conflict``), so same-name rows with paraphrased
functions split into separate subgroups and the merge was left to an
optional, fallible LLM adjudication call that never fired on the live
run. This module is the fix: grouping now uses the narrow rule directly,
in code, deterministically.
"""
from __future__ import annotations

import re
from typing import Any

# Hard identity signals: the properties that identify a physical
# component. Descriptive prose (notably `function`) is NOT one.
HARD_IDENTITY_KEYS = ("part_number", "location_description", "voltage")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def hard_identity_conflict(a: dict, b: dict) -> bool:
    """True when a hard identity signal differs between two property sets.

    Part number, location and voltage identify a physical component; a
    differing ``function`` string does not -- it is usually two
    paraphrases of the same job. Only non-empty values on both sides can
    conflict: an empty value is absence of information, not a different
    component.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    for key in HARD_IDENTITY_KEYS:
        va, vb = a.get(key), b.get(key)
        if not _is_empty(va) and not _is_empty(vb) and str(va) != str(vb):
            return True
    return False


# Entity types that carry no identity of their own: a generic bucket the
# extractor uses when it has no better type. A generic type never blocks
# a merge with a specific type; two different *specific* types do.
GENERIC_ENTITY_TYPES = {"component", "other", "unknown", ""}


def _norm_type(value: Any) -> str:
    # Same normalization as norm_name in code_first_integration: any run
    # of non-alphanumerics (spaces, underscores, hyphens) becomes one
    # space, so "Welding Equipment" and "welding_equipment" compare equal.
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def entity_types_compatible(a: Any, b: Any) -> bool:
    """True when two entity types may describe the same physical component.

    Identical types are compatible. A generic type (``component`` /
    ``other`` / ``unknown``) is compatible with any type -- it is the
    extractor saying "I don't know the kind", not a claim about the
    kind. Two different specific types (``motor`` vs ``sensor``,
    ``tensioner`` vs ``relay``) are not compatible: those are different
    kinds of thing that happen to share a name.
    """
    na, nb = _norm_type(a), _norm_type(b)
    if na == nb:
        return True
    if na in GENERIC_ENTITY_TYPES or nb in GENERIC_ENTITY_TYPES:
        return True
    return False
