"""Phase 1 Step 3 gate tests: whole rows, one-copy ~4,700-char prompt.

Audit (diagnosis-agent, p.8 + p.13): the prompt budget is about 4,700
characters (~1,250 tokens) — system ~1,200, session state ~800,
evidence ~2,500 (top 5 whole items, table rows first), verification
~200, separate knowledge block 0 — and the gate is a prompt at or
below about 5,000 characters with a grep finding no [:800], no [:1000]
and no 1,200-character cut on the diagnosis path. The old prompt
carried three copies of the same facts (evidence chunks + the
component/failure-mode list + the canonical graph JSON dump); this
step deletes the second and third copies and every truncation cut.

The Miller measurement below uses the REAL §6-5 table chunk (2,885
chars, rebuilt row-for-row from the frozen parser rows — the same
construction as scripts/smoke_reranker_miller.py), so the budget
number is measured on the chunk that used to fall off the 800-char
cut at character 3,418 of its old chunk.
"""
import json
from pathlib import Path

from backend.config import settings
from backend.diagnostics import agent as diag_agent
from backend.diagnostics.schema import DiagnosticState, EvidenceRef, Hypothesis
from backend.diagnostics.twin_verification import render_verification_block

REPO_ROOT = Path(__file__).resolve().parent.parent


def _miller_s65_chunk() -> str:
    """The real Miller §6-5 Troubleshooting table as one atomic chunk:
    21 rows, 2,885 chars. §6-5 proper is 21 rows (18 twin-expressible);
    38 is the twin-expressible count across ALL Miller troubleshooting
    tables — the two numbers must not be conflated."""
    rows = json.loads((REPO_ROOT / "frozen" / "miller_parser_rows.json").read_text())
    s65 = [r for r in rows if str(r.get("source_table", "")).startswith("6-5")]
    assert len(s65) == 21
    table = "\n".join(f"Trouble: {r['symptom']} Remedy: {r['remedy']}" for r in s65)
    assert len(table) == 2885
    return table


def _miller_turn1_state() -> DiagnosticState:
    return DiagnosticState(symptoms=["Low, high, or erratic wire speed."], turns_taken=0)


def _no_hypothesis_verification() -> str:
    return render_verification_block({"available": False, "reason": "no hypotheses to verify"})


# ---------------------------------------------------------------------------
# One copy, whole rows
# ---------------------------------------------------------------------------

def test_prompt_carries_each_evidence_chunk_whole_and_once():
    tail_marker = "TAIL-MARKER-12345"
    first = "Trouble: wire speed erratic. Remedy: check the drive rolls. " + "x" * 200 + tail_marker
    second = "Trouble: no output. Remedy: check the line switch. " + "y" * 200 + "TAIL-MARKER-67890"
    evidence = [
        EvidenceRef(chunk_id="c1", content=first, source_document="manual", page_number=31),
        EvidenceRef(chunk_id="c2", content=second, source_document="manual", page_number=32),
    ]
    prompt = diag_agent.build_diagnosis_prompt(_miller_turn1_state(), evidence, _no_hypothesis_verification())
    # Whole: the tail of each chunk survives (the old [:800] cut lost it).
    assert tail_marker in prompt
    assert "TAIL-MARKER-67890" in prompt
    # Once: each chunk's content and header appear exactly once — no
    # second copy hiding in a state dump or a knowledge block.
    assert prompt.count(first) == 1
    assert prompt.count(second) == 1
    assert prompt.count("[c1]") == 1
    assert prompt.count("[c2]") == 1


def test_prompt_has_no_second_or_third_copy():
    evidence = [EvidenceRef(chunk_id="c1", content="feed pressure step 27",
                            source_document="manual", page_number=42)]
    prompt = diag_agent.build_diagnosis_prompt(_miller_turn1_state(), evidence, _no_hypothesis_verification())
    assert "Known product knowledge" not in prompt
    assert "Known failure modes" not in prompt
    assert "Components (most relevant" not in prompt
    assert "Canonical knowledge" not in prompt
    assert "Approved senior-engineer knowledge" not in prompt
    assert "knowledge_block" not in prompt  # no unfilled placeholder left


def test_run_llm_turn_prompt_is_one_copy_whole(monkeypatch):
    """Through the real _run_llm_turn call path (gateway stubbed): a
    5,000-char chunk reaches the model whole and exactly once."""
    import json as _json

    captured = {}
    calls = {"n": 0}

    def fake_complete(task, messages, **kwargs):
        # Phase 1 Step 4: two calls — rank first, ask second.
        calls["n"] += 1
        if calls["n"] == 1:
            captured["prompt"] = messages[0]["content"]
            return (_json.dumps({
                "hypotheses": [
                    {"cause": "Feed Tensioner is too tight", "rationale": "p42",
                     "confidence": 0.5, "supporting_evidence_chunk_ids": ["c1"]},
                    {"cause": "Second cause", "rationale": "p42",
                     "confidence": 0.3, "supporting_evidence_chunk_ids": ["c1"]},
                    {"cause": "Third cause", "rationale": "p42",
                     "confidence": 0.2, "supporting_evidence_chunk_ids": ["c1"]},
                ],
                "eliminated_hypotheses": [],
            }), False)
        return (_json.dumps({
            "overall_confidence": 0.5,
            "next_step": {"type": "question", "content": "What did the tension check show?",
                          "rationale": "Separates the causes.", "safety_notes": []},
        }), False)

    monkeypatch.setattr(diag_agent.gateway, "complete_result", fake_complete)
    body = "Z" * 5000
    state = _miller_turn1_state()
    result = diag_agent._run_llm_turn(
        state, [EvidenceRef(chunk_id="c1", content=body, source_document="manual", page_number=42)])
    assert result["hypotheses"][0]["cause"] == "Feed Tensioner is too tight"
    assert captured["prompt"].count(body) == 1
    assert "… (truncated)" not in captured["prompt"]


# ---------------------------------------------------------------------------
# Budget: measured on a real Miller session
# ---------------------------------------------------------------------------

def test_miller_turn1_prompt_within_budget():
    """Miller turn 1, symptom miller-01: the top evidence item is the
    whole §6-5 table chunk (2,885 chars). Measured 4,486 chars
    (~1,122 tokens) at implementation time — inside the ~4,700-char
    target and below the audit's ~5,000-char gate. (Step 3 measured
    4,762 for the old single call; the rank call's shorter system
    prompt brings it down.) The deciding remedy (at offset 2,469 in
    the chunk, past every old cut) is present."""
    table = _miller_s65_chunk()
    distractor = "Spot Timer: the spot timer controls weld time. " * 20
    evidence = [
        EvidenceRef(chunk_id="s65", content=table, source_document="OM-230 693C", page_number=31),
        EvidenceRef(chunk_id="distractor", content=distractor, source_document="OM-230 693C", page_number=12),
    ]
    prompt = diag_agent.build_diagnosis_prompt(
        _miller_turn1_state(), evidence, _no_hypothesis_verification())
    assert len(prompt) <= diag_agent._PROMPT_TOTAL_BUDGET_CHARS  # audit gate: ~5,000
    assert len(prompt) / 4 <= 1250  # ~1,250-token budget, 4 chars/token
    assert table in prompt  # whole §6-5 chunk, not a cut of it
    assert table[-200:] in prompt  # its tail — past the old [:800] cut
    assert table[2469:2500] in prompt  # the deciding remedy's offset
    # The distractor does not fit the evidence budget once the table is
    # in, so it is dropped whole — never cut to fit.
    assert distractor not in prompt


def test_state_summary_is_typed_not_a_json_dump():
    state = DiagnosticState(
        symptoms=["Low, high, or erratic wire speed."],
        observations=["checked tension, still erratic"],
        hypotheses=[Hypothesis(cause="Insufficient wire feed pressure", rationale="p42",
                               confidence=0.45, supporting_evidence_chunk_ids=["c1"])],
        confidence=0.45, turns_taken=1)
    summary = diag_agent._state_summary(state)
    assert len(summary) <= 800  # audit budget for the state block
    assert "{" not in summary and "chunk_id" not in summary
    assert "Low, high, or erratic wire speed." in summary
    assert "Insufficient wire feed pressure (0.45)" in summary
    assert "checked tension, still erratic" in summary


# ---------------------------------------------------------------------------
# Whole-or-nothing evidence selection
# ---------------------------------------------------------------------------

def test_evidence_selection_drops_whole_items_never_cuts():
    small_a = EvidenceRef(chunk_id="a", content="A" * 600, source_document="m", page_number=1)
    small_b = EvidenceRef(chunk_id="b", content="B" * 600, source_document="m", page_number=2)
    huge = EvidenceRef(chunk_id="huge", content="H" * 5000, source_document="m", page_number=3)
    # A huge second item does not fit the budget: dropped whole.
    selected = diag_agent._select_evidence_items([small_a, huge, small_b])
    assert [e.chunk_id for e in selected] == ["a", "b"]
    assert all(e.content in ("A" * 600, "B" * 600) for e in selected)
    # A huge FIRST item is always included whole: the top-ranked table
    # chunk must never fall off a size cut (the Miller §6-5 failure).
    selected = diag_agent._select_evidence_items([huge, small_a])
    assert [e.chunk_id for e in selected] == ["huge"]
    assert selected[0].content == "H" * 5000


# ---------------------------------------------------------------------------
# No truncation cuts remain in the diagnosis prompt path
# ---------------------------------------------------------------------------

def test_no_truncation_cuts_in_diagnosis_prompt_path():
    for name in ("backend/diagnostics/agent.py", "backend/diagnostics/prompts.py"):
        source = (REPO_ROOT / name).read_text()
        assert "[:800]" not in source, name
        assert "[:1000]" not in source, name
        assert "_MAX_KNOWLEDGE_ITEM_CHARS" not in source, name
        assert "_load_product_knowledge" not in source, name
        assert "_knowledge_context" not in source, name
        assert "_compact_state_json" not in source, name
        assert "… (truncated)" not in source, name
        assert "knowledge_block" not in source, name


# ---------------------------------------------------------------------------
# Retrieval budgets + dead rerank route (Step 2 leftovers)
# ---------------------------------------------------------------------------

def test_retrieval_budgets_are_top_five_whole_items():
    assert settings.hybrid_top_k == 15  # candidates per leg (was 40)
    assert settings.final_top_k == 5    # whole items for the prompt (was 8)


def test_rerank_llm_route_removed():
    """Phase 1 Step 2 left dead rerank_model / rerank_provider
    OpenRouter entries in model_routes; Step 3 deletes them — the
    reranker is the local CrossEncoder (settings.reranker_model)."""
    assert "rerank" not in settings.model_routes
    assert "rerank" not in settings.task_max_tokens
    assert not hasattr(settings, "rerank_model")
    assert not hasattr(settings, "rerank_provider")
    assert settings.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
    assert settings.model_routes["diagnosis"]["max_tokens"] == 8192
