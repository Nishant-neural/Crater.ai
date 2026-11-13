"""Repair-case registry + Tier gating + per-case coverage (Phase 1).

The 9 OMNIPRO 220 top repair cases from
``omni-top-repair-cases-v1.md`` as data: symptom, causes with fix and
discriminating test, Tier 1 pages, library types. Each case is one row
of the manual's own troubleshooting tables -- no case invented.

Tier gating is a filter on the extraction step only: Tier 1 pages are
extracted always, Tier 2 only when a case/repair needs it (flag on the
job, default off in Phase 1), Tier 3 never extracted into the model.
Chunking and vector indexing are unchanged -- retrieval is not the
model. Tier labels are recorded on the chunk ``extra`` so the decision
is auditable.

Coverage is computed from a snapshot on demand; no new DB table.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from backend.knowledge.machine_model import UniversalMachineModel

_DEFAULT_PATH = Path(__file__).with_name("repair_cases_omnipro.json")

# Tiers, as integers (1 = Tier 1, ...). A chunk spanning pages takes the
# best (lowest) tier of any page it covers, so a Tier 1 page in a
# cross-page chunk is never gated out by its neighbours.
TIER_1 = 1
TIER_2 = 2
TIER_3 = 3


class RepairCause(BaseModel):
    cause: str
    fix: str | None = None
    test: str | None = None
    components: list[str] = Field(default_factory=list)


class RepairCase(BaseModel):
    id: str
    rank: int
    symptom: str
    table_page: int
    tier1_pages: list[int] = Field(default_factory=list)
    referenced_pages: list[int] = Field(default_factory=list)
    causes: list[RepairCause] = Field(default_factory=list)
    library_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_case(self):
        if not self.tier1_pages:
            raise ValueError(f"repair case {self.id} cites no Tier 1 pages")
        if not self.causes:
            raise ValueError(f"repair case {self.id} has no causes")
        for cause in self.causes:
            if not (cause.fix or cause.test):
                raise ValueError(
                    f"repair case {self.id}: cause {cause.cause!r} has neither fix nor test"
                )
        return self


class RepairCaseRegistry(BaseModel):
    product_manufacturer: str
    product_family: str
    product_model: str
    document: str
    page_count: int
    tier1_pages: list[int] = Field(default_factory=list)
    tier3_pages: list[int] = Field(default_factory=list)
    tier3_note: str = ""
    source: str = ""
    cases: list[RepairCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_registry(self):
        if not self.cases:
            raise ValueError("repair-case registry has no cases")
        return self

    def tier_for_page(self, page_number: int | None) -> int:
        """Tier of one page number. Unknown pages are Tier 2 (lazy)."""
        if page_number is None:
            return TIER_2
        if page_number in self.tier1_pages:
            return TIER_1
        if page_number in self.tier3_pages:
            return TIER_3
        return TIER_2


def load_registry(path: str | Path | None = None) -> RepairCaseRegistry:
    """Load the OMNIPRO registry (default: the JSON next to this module)."""
    with open(path or _DEFAULT_PATH, encoding="utf-8") as fh:
        return RepairCaseRegistry.model_validate(json.load(fh))


def registry_matches_product(registry: RepairCaseRegistry, manufacturer, family, model) -> bool:
    """Case-insensitive product match, so the registry only gates the
    machine it was written for. Other products keep v12 behaviour."""
    def _norm(value) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    return (
        _norm(manufacturer) == _norm(registry.product_manufacturer)
        and _norm(family) == _norm(registry.product_family)
        and _norm(model) == _norm(registry.product_model)
    )


def tier_for_chunk(chunk, registry: RepairCaseRegistry) -> int:
    """Tier of a chunk (Chunk row or PendingChunk).

    Uses every page the chunk covers (``extra["pages"]``, else
    ``page_number``): Tier 1 if any page is Tier 1, Tier 3 only if
    every covered page is Tier 3, else Tier 2.
    """
    extra = getattr(chunk, "extra", None) or {}
    pages = extra.get("pages") or [getattr(chunk, "page_number", None)]
    tiers = [registry.tier_for_page(p) for p in pages if p is not None]
    if not tiers:
        return TIER_2
    if TIER_1 in tiers:
        return TIER_1
    if all(t == TIER_3 for t in tiers):
        return TIER_3
    return TIER_2


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower().rstrip(". ")


def _text_matches(a: str, b: str) -> bool:
    """True when two short texts say the same thing, loosely.

    Normalised containment either way, or token overlap >= 0.5 of the
    shorter text (overlap coefficient -- a long procedure step can
    contain a short cause without sharing half its own words).
    """
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = set(re.findall(r"[a-z0-9]+", na))
    tb = set(re.findall(r"[a-z0-9]+", nb))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def coverage_for_case(case: RepairCase, snapshot: UniversalMachineModel) -> dict:
    """Per-cause coverage of one case against a canonical-model snapshot.

    For each cause: whether a failure mode, a procedure, a part, or a
    parameter is bound in the model. ``bound`` is failure-mode-or-
    procedure; part/parameter are reported alongside, not required.
    """
    entity_names = [e.name for e in snapshot.entities]
    quantity_names = [q.name for q in snapshot.quantities]
    procedure_texts = [p.name for p in snapshot.procedures] + [
        step for p in snapshot.procedures for step in (p.steps or [])
    ]
    cause_rows: list[dict] = []
    for cause in case.causes:
        fm_bound = any(
            _text_matches(cause.cause, fm_cause)
            for fm in snapshot.failure_modes
            for fm_cause in (fm.possible_causes or [])
        ) or any(_text_matches(case.symptom, fm.name) for fm in snapshot.failure_modes)
        proc_bound = any(
            _text_matches(cause.cause, text) for text in procedure_texts
        )
        part_bound = bool(cause.components) and any(
            _text_matches(component, name)
            for component in cause.components
            for name in entity_names
        )
        param_bound = any(
            _text_matches(cause.cause, name) for name in quantity_names
        )
        cause_rows.append({
            "cause": cause.cause,
            "failure_mode_bound": fm_bound,
            "procedure_bound": proc_bound,
            "part_bound": part_bound,
            "parameter_bound": param_bound,
            "bound": fm_bound or proc_bound,
            "page": case.table_page,
        })
    bound = sum(1 for row in cause_rows if row["bound"])
    return {
        "case_id": case.id,
        "symptom": case.symptom,
        "causes": cause_rows,
        "bound_causes": bound,
        "total_causes": len(cause_rows),
        "coverage": bound / len(cause_rows) if cause_rows else 0.0,
    }


def coverage_for_registry(registry: RepairCaseRegistry, snapshot: UniversalMachineModel) -> list[dict]:
    """Per-case coverage for every case in the registry."""
    return [coverage_for_case(case, snapshot) for case in registry.cases]
