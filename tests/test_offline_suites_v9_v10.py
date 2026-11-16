"""Offline v9 / v10 no-degradation gate, run with the suite (Phase 3A).

The v8/v9/v10 no-degradation gate used to depend on hand-run scripts
(``~/workspace/verify-v9.py``, ``~/workspace/verify-tournament-v10.py``).
These tests run those exact scripts as subprocesses (the scripts are
self-contained: they copy ``crater-8.db`` to /tmp, re-integrate the copy
with a fake adjudicator, and assert the v9/v10 outcomes) and fail if a
script exits non-zero or reports any failure.

Scope note, stated plainly: the scripts hardcode their import path to
the ``project-review-batch-fix`` tree and their DB to
``~/workspace/user/files/crater-8.db`` — they verify that tree and that
DB copy, not this tree. The hermetic equivalents for THIS tree already
run in the suite (``test_verified_repair.py``,
``test_hypothesis_tournament.py``, ``test_visual_twin.py``, plus the
Phase 2 seed/dedupe tests). These wrapper tests exist so the original
gate still runs with the suite instead of only by hand; they are
skipped, not failed, when the scripts or the DB are absent (e.g. in a
packaged checkout without ``~/workspace``).
"""
import subprocess
import sys
from pathlib import Path

import pytest

WORKSPACE = Path.home() / "workspace"
V9 = WORKSPACE / "verify-v9.py"
V10 = WORKSPACE / "verify-tournament-v10.py"
DB = WORKSPACE / "user" / "files" / "crater-8.db"


def _run(script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=280, cwd="/tmp",
    )


@pytest.mark.skipif(not (V9.exists() and DB.exists()),
                    reason="verify-v9.py or crater-8.db not present")
def test_offline_v9_suite_passes():
    result = _run(V9)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-5:])
    assert result.returncode == 0, tail
    assert "0 failed" in result.stdout, tail


@pytest.mark.skipif(not (V10.exists() and DB.exists()),
                    reason="verify-tournament-v10.py or crater-8.db not present")
def test_offline_v10_tournament_suite_passes():
    result = _run(V10)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-5:])
    assert result.returncode == 0, tail
    assert "0 failed" in result.stdout, tail
