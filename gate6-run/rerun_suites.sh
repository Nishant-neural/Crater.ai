#!/bin/bash
cd ~/workspace/crater-refactor
export NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
PY=~/workspace/crater-phase1/.venv/bin/python
LOG=gate6-run/suites-rerun.log
: > $LOG
run() {
  local name="$1"; shift
  echo "=== $name ===" >> $LOG
  $PY -m pytest "$@" -q -p no:cacheprovider >> $LOG 2>&1
  echo "EXIT:$? $name" >> $LOG
}
run step1 tests/test_table_parser.py tests/test_source_router.py tests/test_phase1_pipeline.py tests/test_chunking.py
run step2 tests/test_reranker.py tests/test_phase1_hardening.py tests/test_verification_and_retrieval.py tests/test_diagnostics.py tests/test_diagnosis_robustness.py
run step2-wider tests/test_phase1_pipeline.py tests/test_phase4_backtracking.py tests/test_hypothesis_tournament.py tests/test_phase8e_routing.py tests/test_llm_openrouter_fix5.py
run step3-52 tests/test_diagnosis_prompt_budget.py tests/test_diagnosis_robustness.py tests/test_diagnostics.py tests/test_verify_before_conclusion.py
run adj54 tests/test_verify_before_conclusion.py tests/test_phase3a_oracle.py tests/test_diagnosis_prompt_budget.py tests/test_harness_scorer_reporting.py
run s29 tests/test_ingestion_gemini_fix.py tests/test_llm_provider.py
run s5 tests/test_phase456_followup_walk.py
run s22 tests/test_phase4_steals.py tests/test_phase5_learning.py
run step4-71 tests/test_diagnosis_rank_then_ask.py tests/test_diagnosis_prompt_budget.py tests/test_diagnosis_robustness.py tests/test_diagnostics.py tests/test_verify_before_conclusion.py
CRATER_TEST_POSTGRES_DSN="host=/tmp/pgrun port=55433 user=postgres dbname=postgres" $PY -m pytest tests/test_diagnosis_langgraph.py tests/test_twin_client.py tests/test_step5_cuts.py -q -p no:cacheprovider >> $LOG 2>&1
echo "EXIT:$? step5-26" >> $LOG
run phase3d tests/test_phase3d_layer2_layer3.py
run phase8a tests/test_phase8a_integration.py
echo "=== smoke ===" >> $LOG
$PY scripts/smoke_reranker_miller.py >> $LOG 2>&1
echo "EXIT:$? smoke" >> $LOG
echo "ALL DONE" >> $LOG
