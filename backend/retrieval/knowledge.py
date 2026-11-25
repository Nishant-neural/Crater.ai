"""Knowledge-model-aware, revision-aware and graph-expanded retrieval.

Phase 3 diagnosis-agent extraction (contracts README §1.1, §3.3):
the canonical model is fetched from ingestion's HTTP API
(``GET /v1/knowledge/revisions/{id}/model``), never read from
ingestion's tables — the old ``MachineKnowledgeModelSnapshot`` /
``Product`` / ``Revision`` DB reads are deleted. The scoring and
2-hop graph expansion below are unchanged: they operate on the model
dict, wherever it came from. A lookup failure raises
``IngestionUnavailableError`` (mapped to 503 with ingestion named);
an unknown revision / product mismatch returns no hits, as before.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from backend.retrieval import ingestion_client as ingestion_client_mod

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


def retrieve_machine_knowledge(query: str, *, product_id: str, revision_id: str, top_k: int = 12, graph_hops: int = 2, client=None) -> list[RetrievedKnowledge]:
    """Retrieve direct semantic-ish matches, then expand through canonical relations.

    Vector/BM25 retrieval remains the source-document path; this function is the
    canonical knowledge path. The graph is built from the canonical model
    fetched from ingestion's HTTP API, so retrieval is
    entity/relation/state/failure/procedure aware. ``client`` is the
    ingestion client (tests inject a MockTransport-backed one); None
    builds one from settings. Raises IngestionUnavailableError when
    ingestion cannot answer.
    """
    client = client or ingestion_client_mod.get_ingestion_client()
    revisions = client.list_revisions(product_id)
    if revisions is None:
        return []
    revision = next((r for r in revisions if r.get("id") == revision_id), None)
    if revision is None:
        return []
    revision_label = revision.get("label") or ""
    payload = client.get_model(revision_id)
    if payload is None:
        return []
    model = payload.get("model") or {}
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
            hit = RetrievedKnowledge(kind, _item_id(item), score, item, product_id, revision_id, revision_label, "direct")
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
                scored.setdefault(key, RetrievedKnowledge(kind, _item_id(item), 1.0, item, product_id, revision_id, revision_label, "entity:seed"))

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
                    scored.setdefault(key, RetrievedKnowledge(kind, node, max(0.5, 1.0 / depth), item, product_id, revision_id, revision_label, f"graph:{depth}"))
            # all typed knowledge attached to this entity
            for kind, item in items:
                attached = {str(item.get("entity_id"))} | {str(x) for x in item.get("entity_ids", [])} | {str(item.get("subject_id")), str(item.get("object_id"))}
                if node in attached:
                    key = (kind, _item_id(item))
                    bonus = 1.0 / (depth + 1)
                    if key not in scored or scored[key].score < bonus:
                        scored[key] = RetrievedKnowledge(kind, _item_id(item), bonus, item, product_id, revision_id, revision_label, f"graph:{depth}")
        frontier = nxt
        if not frontier: break

    # Include connecting relations for every graph-expanded pair.
    for (a, b), rels in relation_by_pair.items():
        if a in visited and b in visited:
            for r in rels:
                key = ("relations", _item_id(r))
                scored.setdefault(key, RetrievedKnowledge("relations", _item_id(r), 0.75, r, product_id, revision_id, revision_label, "graph:relation"))

    return sorted(scored.values(), key=lambda x: (-x.score, x.kind, x.item_id))[:top_k]
