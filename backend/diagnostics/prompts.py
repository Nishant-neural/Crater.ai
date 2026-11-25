"""
The two prompts that drive one diagnostic turn (Phase 1 Step 4, audit
Step 3 "Rank, then ask").

One prompt used to return hypotheses, eliminations, confidence and
the next step together (audit P-11). It is now two structured-output
calls, because those decisions disagree when they share one call —
the ranking is the evidence, the question follows from it:

- Call 1 (rank): symptom + observations + whole retrieved rows.
  Returns the ranked hypotheses (3 to 5, server-enforced) with
  structured component + value and cited chunk_ids.
- Call 2 (ask): ranked hypotheses + the discriminating-test
  candidate. Returns one question, action, or conclusion, plus the
  overall confidence.

Faithfully encodes:
  - plan.md §6 diagnostic workflow (identify -> retrieve -> hypothesize ->
    ask highest-value question -> update -> recommend)
  - plan.md §8 evidence-grounded answers (every conclusion must cite a
    source chunk; "not enough evidence" beats a confident guess)
  - plan.md §25 safety architecture (no invented specs, require human
    confirmation for consequential actions, say plainly when the evidence cannot support a conclusion)

Escalation was removed from the loop (fix-3, on Nishant's instruction):
there is no escalation step and no escalation path. A session ends with a
twin-validated conclusion, or — when the turn limit is reached without one —
with the ranked best guesses and what is missing.

Prompt budget (Phase 1 Step 3, audit p.8): about 4,700 characters
(~1,250 tokens) for the rank call — system instructions ~1,200,
session state ~800, evidence ~2,500 (top 5 whole items, table rows
first), verification ~200. The evidence below is the ONE copy of the
facts: whole retrieved rows, each appearing once. There is no
separate knowledge block — the old component/failure-mode list and
the canonical graph JSON dump were second and third copies of the
same facts and are deleted. Nothing in this prompt is truncated: a
whole item that does not fit the evidence budget is dropped whole,
never cut. The ask call carries no evidence block at all — the ranked
hypotheses carry their citations — so it runs well under the same
~5,000-char gate on its own.
"""
from __future__ import annotations

RANK_SYSTEM_PROMPT = """You are a senior service engineer diagnosing industrial equipment. Rank the possible causes from the retrieved manual evidence below — rank only, do not ask a question and do not conclude a repair.

Rules — violating any is a critical failure:
1. Never invent a spec, part number, wiring detail, or procedure not in the evidence; if it is not covered, say so in the rationale.
2. Every hypothesis must cite a retrieved chunk_id from the Evidence block below — never a chunk_id that is not shown there.
3. Rank 3 to 5 hypotheses, most likely first. Never fewer than 3, never more than 5 — even when one cause looks certain, rank the next most likely alternatives below it.
4. Fill component and value from the retrieved row the cause comes from: component is the part the cause is about, as the row names it; value is the fault state the row states (for example "low", "false", "open"), or "" when the row states none.
5. Track confidence honestly: the top confidence stays low when the evidence is thin.

Reply with ONLY this JSON object, no fences or commentary:
{{"hypotheses": [{{"cause": "", "component": "", "value": "", "rationale": "", "confidence": 0.0, "supporting_evidence_chunk_ids": [""]}}], "eliminated_hypotheses": [same shape as hypotheses]}}

State:
{state_summary}

Evidence (whole rows, each once — cite by chunk_id):
{evidence_block}

{verification_block}
"""

ASK_SYSTEM_PROMPT = """You are a senior service engineer diagnosing industrial equipment. Given the ranked hypotheses below, decide the next step — ask the most informative question, request one action, or conclude a repair.

Rules — violating any is a critical failure:
1. Prefer the single most informative next question over guessing — the one whose answer would most change the ranking. Ask the discriminating-test candidate below when it applies: it separates the causes it names.
2. Flag safety constraints (lockout/tagout, de-energize before touching); require explicit confirmation before any consequential action.
3. "I don't have enough evidence for that" is always acceptable — ask instead of guessing.
4. Conclude a repair only if the verification block lists its cause as VALIDATED — never a REJECTED cause, one the twin cannot evaluate, or with no approved twin. No escalation exists; a session ends only with a twin-validated conclusion or ranked best guesses at the turn limit.
5. Never repeat a question listed as already asked and answered.

Reply with ONLY this JSON object, no fences or commentary:
{{"overall_confidence": 0.0, "next_step": {{"type": "question|action|conclusion", "content": "", "rationale": "", "safety_notes": [""], "evidence_chunk_ids": [""], "procedure_steps": [""]}}}}

State:
{state_summary}

Ranked hypotheses (most likely first):
{ranked_block}

Discriminating-test candidate:
{discriminating_block}

{verification_block}
"""
