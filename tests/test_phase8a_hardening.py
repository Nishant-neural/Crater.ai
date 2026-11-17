"""Phase 8A hardening: evidence provenance + fact-level validation.

Housekeeping fix (Phase 3A, 13 Nov 2026): this file previously imported
``invalid_fact_keys`` and ``validate_facts`` from
``backend.knowledge.validation`` — neither name exists there. The test
was wrong, not the module: the module exports
``validate_machine_model``, ``quarantine_machine_model`` and
``validate_machine_model_with_ledger``. The tests below assert the same
intent against the real API, and no production function was invented
to satisfy a test.

- Evidence retains its revision through the Phase 2 field
  ``source_revision`` (the document revision label, e.g. "Rev A").
- Procedures and failure modes are universal primitives visible in
  ``all_facts()``.
- Validation is fact-level: a model with two conflicting values for
  the same state is flagged by name (``conflicting state values for
  pump/running``), while a clean model reports no duplicate-entity or
  conflicting-state issues; the ledger variant returns the same
  issues plus an explicit ledger.
"""
from backend.knowledge.evidence import MachineEvidence
from backend.knowledge.machine_model import (
    MachineEntity, MachineFailureMode, MachineProcedure, MachineState,
    UniversalMachineModel,
)
from backend.knowledge.validation import (
    quarantine_machine_model,
    validate_machine_model,
    validate_machine_model_with_ledger,
)


def test_evidence_retains_revision():
    evidence = MachineEvidence(
        fact="Pump pressure is 4 bar",
        source_document="manual.pdf",
        page=12,
        source_revision="Rev A",
    )
    assert evidence.source_revision == "Rev A"


def test_procedure_and_failure_mode_are_universal_primitives():
    model = UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump", entity_type="pump")],
        procedures=[MachineProcedure(
            id="proc-1", name="Prime pump", procedure_type="maintenance",
            steps=["Open valve", "Start pump"],
        )],
        failure_modes=[MachineFailureMode(
            id="failure-1", name="Pump cavitation", symptom="Noise",
            possible_causes=["Low inlet pressure"],
        )],
    )
    facts = dict((kind, payload) for kind, _, payload in model.all_facts())
    assert "procedure" in facts
    assert "failure_mode" in facts


def test_validation_is_fact_level():
    model = UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump", entity_type="pump")],
        relations=[],
        states=[
            MachineState(entity_id="pump", name="running", value="true"),
            MachineState(entity_id="pump", name="running", value="false"),
        ],
    )
    issues = validate_machine_model(model)
    # Conflicting values for one state are flagged by entity/state name.
    assert any(
        "conflicting state values" in issue and "pump/running" in issue
        for issue in issues
    )
    # Nothing else about this small model is invalid: no duplicate
    # entity, no missing reference, no invalid fact key.
    assert not any("duplicate entity" in issue for issue in issues)
    assert not any("missing entity" in issue for issue in issues)

    ledger_issues, ledger = validate_machine_model_with_ledger(model)
    assert ledger_issues == issues
    assert isinstance(ledger, list)


def test_validation_passes_a_clean_model_and_quarantine_keeps_facts():
    model = UniversalMachineModel(
        entities=[MachineEntity(id="pump", name="Pump", entity_type="pump")],
        relations=[],
        states=[MachineState(entity_id="pump", name="running", value="true")],
    )
    assert validate_machine_model(model) == []
    kept, ledger = quarantine_machine_model(model)
    assert len(kept.entities) == 1
    assert kept.entities[0].name == "Pump"
    assert ledger == []
