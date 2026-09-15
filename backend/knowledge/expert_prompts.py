"""LLM prompts for the Phase 5 expert knowledge pipeline."""

EXPERT_INTERVIEW_PROMPT = """You are an expert interviewer helping capture tacit industrial service knowledge.
Interview a senior engineer about: {topic}

Product/revision context:
{context}

Transcript so far:
{transcript}

Ask ONE short, technically targeted question that uncovers knowledge that may not be present
in manuals. Prefer questions about:
- failure signatures and how to distinguish similar faults
- measurements/observations that confirm or rule out a cause
- common misdiagnoses and exceptions
- revision/model differences
- safest next test and repair
- conditions under which the expert's rule does NOT apply

Do not ask for personal information. Do not invent technical facts.
Return only the question text."""

EXPERT_EXTRACTION_PROMPT = """Extract atomic, reviewable technical knowledge from this senior-engineer interview.

Only extract claims explicitly supported by the expert's words. Do not turn speculation into fact.
Every item must cite the transcript turn IDs that support it and include a short exact quote.
Prefer actionable diagnostic rules over generic advice.

Transcript:
{transcript}

Return ONLY JSON:
{{
  "items": [
    {{
      "knowledge_type": "rule|failure_mode|diagnostic_test|repair|exception|heuristic",
      "title": "",
      "symptom": null,
      "trigger": null,
      "condition": null,
      "action": null,
      "expected_observation": null,
      "failure_mode": null,
      "safety_notes": [],
      "applicable_models": [],
      "applicable_revisions": [],
      "evidence_turn_ids": [],
      "confidence": 0.0,
      "source_quote": ""
    }}
  ]
}}"""
