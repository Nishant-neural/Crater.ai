"""Knowledge-model-aware, revision-aware and graph-expanded retrieval."""
from __future__ import annotations

from dataclasses import dataclass
import re
from sqlalchemy.orm import Session
from backend.db.models import MachineKnowledgeModelSnapshot, Product, Revision

_TOKEN_RE = re.compile(r"[A-Za-z0-9_:.\-/]+")

@dataclass
class RetrievedKnowledge:
    kind: str
    item_id: str
    score: float
    payload: dict
    product_id: str
    revision_id: str
    revision_label: str
    retrieval_path: str = "direct"


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _item_id(item: dict) -> str:
    return str(item.get("id") or item.get("entity_id") or item.get("source_id") or "")


def _text(item: dict) -> str:
    return " ".join(str(v) for v in item.values() if isinstance(v, (str, int, float)))


def _latest_snapshot(session: Session, revision_id: str):
    return session.query(MachineKnowledgeModelSnapshot).filter_by(revision_id=revision_id).order_by(MachineKnowledgeModelSnapshot.version.desc()).first()


def retrieve_machine_knowledge(session: Session, query: str, *, product_id: str, revision_id: str, top_k: int = 12, graph_hops: int = 2) -> list[RetrievedKnowledge]:
    """Retrieve direct semantic-ish matches, then expand through canonical relations.

    Vector/BM25 retrieval remains the source-document path; this function is the
    canonical knowledge path. The graph is built from the persisted canonical
    snapshot, so retrieval is entity/relation/state/failure/procedure aware.
    """
    revision = session.get(Revision, revision_id)
    if revision is None or revision.product_id != product_id:
        return []
    snapshot = _latest_snapshot(session, revision_id)
    if snapshot is None:
        return []
    model = snapshot.model or {}
    kinds = ("entities", "relations", "ports", "quantities", "states", "events", "behaviors", "constraints", "procedures", "failure_modes", "conflicts", "unresolved_facts")
    items: list[tuple[str, dict]] = [(kind, item) for kind in kinds for item in model.get(kind, []) if isinstance(item, dict)]
    q = _tokens(query)

    scored: dict[tuple[str, str], RetrievedKnowledge] = {}
    entity_seed_ids: set[str] = set()
    for kind, item in items:
        text = _text(item)
        overlap = len(q & _tokens(text))
        # exact identifier/part-number mentions get a strong deterministic boost
        exact = 2.0 if any(tok in text.lower() for tok in q if len(tok) >= 3 and ("-" in tok or ":" in tok)) else 0.0
        score = float(overlap) + exact
        if score > 0:
            hit = RetrievedKnowledge(kind, _item_id(item), score, item, product_id, revision_id, revision.label, "direct")
            scored[(kind, hit.item_id)] = hit
            if kind == "entities": entity_seed_ids.add(hit.item_id)
            entity_seed_ids.update(str(x) for x in item.get("entity_ids", []) if x)
            entity_seed_ids.update(x for x in (item.get("subject_id"), item.get("object_id"), item.get("entity_id")) if x)

    # Build a bidirectional topology graph from canonical relations.
    adjacency: dict[str, set[str]] = {}
    relation_by_pair: dict[tuple[str, str], list[dict]] = {}
    for r in model.get("relations", []):
        a, b = str(r.get("subject_id", "")), str(r.get("object_id", ""))
        if not a or not b: continue
        adjacency.setdefault(a, set()).add(b); adjacency.setdefault(b, set()).add(a)
        relation_by_pair.setdefault((a, b), []).append(r)
        relation_by_pair.setdefault((b, a), []).append(r)

    # Attach all typed knowledge to the direct seed entities before expanding.
    frontier = set(entity_seed_ids)
    visited = set(frontier)
    for node in list(frontier):
        for kind, item in items:
            attached = {str(item.get("entity_id"))} | {str(x) for x in item.get("entity_ids", [])} | {str(item.get("subject_id")), str(item.get("object_id"))}
            if node in attached:
                key = (kind, _item_id(item))
                scored.setdefault(key, RetrievedKnowledge(kind, _item_id(item), 1.0, item, product_id, revision_id, revision.label, "entity:seed"))

    for depth in range(1, max(0, graph_hops) + 1):
        nxt: set[str] = set()
        for node in frontier:
            nxt.update(adjacency.get(node, set()) - visited)
        for node in nxt:
            visited.add(node)
            # entity itself
            for kind, item in items:
                if kind == "entities" and _item_id(item) == node:
                    key = (kind, node)
                    scored.setdefault(key, RetrievedKnowledge(kind, node, max(0.5, 1.0 / depth), item, product_id, revision_id, revision.label, f"graph:{depth}"))
            # all typed knowledge attached to this entity
            for kind, item in items:
                attached = {str(item.get("entity_id"))} | {str(x) for x in item.get("entity_ids", [])} | {str(item.get("subject_id")), str(item.get("object_id"))}
                if node in attached:
                    key = (kind, _item_id(item))
                    bonus = 1.0 / (depth + 1)
                    if key not in scored or scored[key].score < bonus:
                        scored[key] = RetrievedKnowledge(kind, _item_id(item), bonus, item, product_id, revision_id, revision.label, f"graph:{depth}")
        frontier = nxt
        if not frontier: break

    # Include connecting relations for every graph-expanded pair.
    for (a, b), rels in relation_by_pair.items():
        if a in visited and b in visited:
            for r in rels:
                key = ("relations", _item_id(r))
                scored.setdefault(key, RetrievedKnowledge("relations", _item_id(r), 0.75, r, product_id, revision_id, revision.label, "graph:relation"))

    return sorted(scored.values(), key=lambda x: (-x.score, x.kind, x.item_id))[:top_k]
