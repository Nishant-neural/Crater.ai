"""General-purpose normalization at the LLM/legacy schema boundary.

Normalization is deliberately loss-aware: it maps known aliases, records warnings,
and keeps the original payload for any field that cannot be represented safely.
It does not claim that normalized values are factually correct.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
import hashlib
import json


ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("fact_id", "behavior_id", "behaviour_id", "event_id", "port_id", "quantity_id", "constraint_id", "procedure_id", "failure_id", "failure_mode_id", "relation_id"),
    "name": ("component", "label", "title", "alarm", "failure", "procedure_name", "entity_name"),
    "description": ("behavior", "behaviour", "effect", "function", "details", "text", "fact", "rule"),
    "subject_id": ("entity_id", "component_id", "source_entity_id", "source_id"),
    "subject_name": ("component", "entity_name", "subject", "name"),
    "entity_id": ("component_id", "subject_id"),
    "entity_type": ("type", "component_type", "kind"),
    "relation_type": ("type", "relationship", "relation"),
    "object_id": ("target_id", "to_id", "object_entity_id", "destination_id"),
    "object_name": ("target", "to_component", "to", "object"),
    "value": ("setting", "parameter_value", "observed_value"),
    "unit": ("units", "uom"),
    "steps": ("procedure_steps", "instructions", "actions"),
    "possible_causes": ("causes", "cause"),
    "symptoms": ("symptom", "indications"),
    "source_ids": ("source_id", "evidence_ids", "source_record_ids"),
    "evidence": ("evidence_items", "citations", "supporting_evidence"),
    "confidence": ("certainty", "score"),
}


def _stable_id(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return f"{kind}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def normalize_payload(payload: Any, *, kind: str = "fact",
                      aliases: dict[str, tuple[str, ...]] | None = None) -> dict[str, Any]:
    """Return a canonical copy with warnings and original payload retained if changed."""
    if not isinstance(payload, dict):
        return {"id": _stable_id(kind, {"value": payload}), "payload": {"value": payload},
                "normalization_warnings": ["payload_not_object"], "_original_payload": payload}
    original = deepcopy(payload)
    data = deepcopy(payload)
    warnings: list[str] = []
    for target, candidates in (aliases or ALIASES).items():
        if data.get(target) not in (None, "", []):
            continue
        for alias in candidates:
            candidate = data.get(alias)
            if candidate not in (None, "", []):
                data[target] = candidate
                warnings.append(f"mapped_alias:{alias}->{target}")
                break

    for key in ("source_ids", "entity_ids", "symptoms", "possible_causes", "steps", "evidence"):
        if key in data and data[key] is None:
            data[key] = []
            warnings.append(f"null_list_normalized:{key}")
        elif key in data and isinstance(data[key], str) and key != "evidence":
            data[key] = [data[key]]
            warnings.append(f"scalar_list_normalized:{key}")
    if "id" not in data or data["id"] in (None, ""):
        data["id"] = _stable_id(kind, original)
        warnings.append("generated_stable_id")
    if "source_ids" in data and isinstance(data["source_ids"], list):
        data["source_ids"] = [str(x) for x in data["source_ids"] if x not in (None, "")]
    if warnings:
        data["normalization_warnings"] = list(dict.fromkeys(data.get("normalization_warnings", []) + warnings))
        data["_original_payload"] = original
    return data


def unresolved_record(payload: Any, *, source_id: str | None, fact_type: str, reason: str) -> dict[str, Any]:
    """Create a serializable ledger record; never discard malformed extracted content."""
    return {
        "source_id": source_id or _stable_id(fact_type, payload if isinstance(payload, dict) else {"value": payload}),
        "fact_type": fact_type,
        "payload": deepcopy(payload) if isinstance(payload, dict) else {"value": payload},
        "reason": reason,
    }


def persist_unresolved_fact(session, *, revision_id: str | None, source_id: str | None,
                            fact_type: str, payload: Any, reason: str) -> None:
    """Idempotently persist an unresolved fact for later review/reprocessing."""
    from backend.db.models import UnresolvedFact
    record = unresolved_record(payload, source_id=source_id, fact_type=fact_type, reason=reason)
    row = session.query(UnresolvedFact).filter_by(
        revision_id=revision_id, source_id=record["source_id"], fact_type=fact_type
    ).first()
    if row is None:
        row = UnresolvedFact(revision_id=revision_id, source_id=record["source_id"],
                             fact_type=fact_type, payload=record["payload"], reason=reason, status="open")
        session.add(row)
    else:
        row.payload = record["payload"]
        row.reason = reason
        row.status = "open"
