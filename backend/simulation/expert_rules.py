"""Expert seed rules: hand-checked machine behaviour, resolved by name at compile time.

A seed is a small JSON file under ``golden_rules/`` (one per machine family).
It carries states and simulation rules that an expert wrote from the manual --
never inferred by the LLM, never derived from a relation. Every rule carries
its manual evidence (verbatim quotes + page numbers).

Resolution happens only in the compile route, into a copy of the canonical
model, by entity NAME, unique match only:
- exactly one canonical entity with that name -> the state/rule is applied;
- zero, or two-plus (the duplicate-entity case, e.g. five "Feed Tensioner"
  rows in the OMNIPRO snapshot) -> that state/rule is SKIPPED and the
  compile warnings name the ambiguity. The seed is never guessed into an
  identity, and a skipped seed never blocks compilation.
The applied seed is flagged in the twin's warnings and metadata, so a twin
that behaves because of expert rules says so.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.knowledge.machine_model import MachineState, UniversalMachineModel

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden_rules"


def _norm(name: Any) -> str:
    return " ".join(str(name or "").strip().lower().split())


def load_seed_for_product(manufacturer: str | None, model: str | None) -> dict[str, Any] | None:
    """Return the golden-rules seed whose product block matches, else None."""
    if not _GOLDEN_DIR.is_dir():
        return None
    for path in sorted(_GOLDEN_DIR.glob("*.json")):
        try:
            seed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        product = seed.get("product") or {}
        if (_norm(product.get("manufacturer")) == _norm(manufacturer)
                and _norm(product.get("model")) == _norm(model)):
            return seed
    return None


def _state_value_str(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def apply_expert_seed(
    model: UniversalMachineModel, seed: dict[str, Any]
) -> tuple[UniversalMachineModel, list[str], dict[str, Any]]:
    """Merge a seed into a copy of the canonical model.

    Returns (model_copy, warnings, applied). ``applied`` lists what resolved
    and what was skipped, for the twin metadata.
    """
    seed_id = str(seed.get("id") or "expert-seed")
    merged = model.model_copy(deep=True)
    warnings: list[str] = []
    applied: dict[str, Any] = {
        "seed_id": seed_id,
        "seed_name": seed.get("name") or "",
        "states_applied": [],
        "rules_applied": [],
        "skipped": [],
    }

    def resolve(name: str) -> tuple[Any | None, list[Any]]:
        candidates = [e for e in merged.entities if _norm(e.name) == _norm(name)]
        if len(candidates) == 1:
            return candidates[0], candidates
        return None, candidates

    for state in seed.get("states") or []:
        entity_name = str(state.get("entity") or "")
        state_name = str(state.get("name") or "")
        if not entity_name or not state_name:
            warnings.append(f"Expert seed {seed_id}: skipped a state with no entity or name.")
            applied["skipped"].append({"kind": "state", "reason": "missing entity or name"})
            continue
        entity, candidates = resolve(entity_name)
        if entity is None:
            reason = (f"no entity named {entity_name!r}" if not candidates
                      else f"{len(candidates)} entities named {entity_name!r}; ambiguous")
            warnings.append(
                f"Expert seed {seed_id}: state {entity_name}.{state_name} skipped ({reason}).")
            applied["skipped"].append({"kind": "state", "entity": entity_name,
                                       "name": state_name, "reason": reason})
            continue
        existing = {s.name.strip().lower() for s in merged.states if s.entity_id == entity.id}
        if state_name.strip().lower() in existing:
            # Extracted facts win: the seed never overwrites what the
            # manual extraction already said about this entity.
            applied["skipped"].append({"kind": "state", "entity": entity_name,
                                       "name": state_name, "reason": "already in model"})
            continue
        merged.states.append(MachineState(
            source_ids=[f"expert-seed:{seed_id}"],
            entity_id=entity.id,
            name=state_name,
            value=_state_value_str(state.get("value")),
            evidence=list(state.get("evidence") or []),
        ))
        applied["states_applied"].append(f"{entity.id}.{state_name}")

    for rule in seed.get("rules") or []:
        rule_id = str(rule.get("id") or "")
        conditions = rule.get("conditions") or {}
        effects = rule.get("effects") or {}
        if not isinstance(conditions, dict) or not conditions or not isinstance(effects, dict) or not effects:
            warnings.append(f"Expert seed {seed_id}: rule {rule_id or rule.get('name') or '?'} skipped (no conditions/effects).")
            applied["skipped"].append({"kind": "rule", "id": rule_id, "reason": "no conditions/effects"})
            continue
        rewritten: dict[str, dict[str, Any]] = {}
        bad: str | None = None
        for side, mapping in (("conditions", conditions), ("effects", effects)):
            out: dict[str, Any] = {}
            for path, value in mapping.items():
                if not isinstance(path, str) or "." not in path:
                    bad = f"bad path {path!r}"
                    break
                head, field_name = path.split(".", 1)
                entity, candidates = resolve(head)
                if entity is None:
                    reason = (f"no entity named {head!r}" if not candidates
                              else f"{len(candidates)} entities named {head!r}; ambiguous")
                    bad = reason
                    break
                key = field_name.strip().lower().replace(" ", "_")
                out[f"{entity.id}.{key}"] = value
            if bad:
                break
            rewritten[side] = out
        if bad:
            warnings.append(f"Expert seed {seed_id}: rule {rule_id or rule.get('name') or '?'} skipped ({bad}).")
            applied["skipped"].append({"kind": "rule", "id": rule_id, "reason": bad})
            continue
        merged.simulation_rules.append({
            "id": rule_id or f"seed-rule:{len(merged.simulation_rules) + 1}",
            "name": str(rule.get("name") or rule_id or "Expert seed rule"),
            "conditions": rewritten["conditions"],
            "effects": rewritten["effects"],
            "description": str(rule.get("description") or ""),
            "safety_notes": list(rule.get("safety_notes") or []),
            "source_ids": [f"expert-seed:{seed_id}"],
            "evidence": list(rule.get("evidence") or []),
        })
        applied["rules_applied"].append(rule_id or str(rule.get("name") or ""))

    return merged, warnings, applied
