#!/usr/bin/env python3
"""harness.py -- import the repo's evaluator, never re-implement it.

Design §4: "服务 import 现有实现，不复制一行". The service and the CLI must
produce digit-for-digit the same numbers, and the only way to guarantee that is
for there to be exactly one implementation of the judge. So this module does what
run_eval.py itself does -- append scripts/agent, scripts/harness, scripts/scenarios
and scripts/baselines to sys.path -- and re-exports the functions rather than
wrapping them in anything that could drift.

scripts/ is BIND-MOUNTED read-only, not COPYed into the image. A copy taken at
build time is a second version of the judge that can silently disagree with the
one on disk, which is precisely the failure §4 spends a paragraph avoiding; the
read-only mount also makes "服务只 import、不改" a filesystem fact rather than a
promise.

Importing run_eval pulls in run_agent, which imports `anthropic` at module level,
which is why the client is in service/requirements.txt even though nothing in
step 2 makes an API call.
"""
import os
import pathlib
import sys

# /repo in the container (this file is /repo/service/app/harness.py); overridable
# for running the service outside compose.
REPO = pathlib.Path(os.environ.get("RCA_REPO_ROOT") or pathlib.Path(__file__).resolve().parents[2])

for _sub in ("agent", "harness", "scenarios", "baselines"):
    _p = str(REPO / "scripts" / _sub)
    if _p not in sys.path:
        sys.path.append(_p)

SCENARIOS_DIR = REPO / "scenarios"
EVIDENCE_DIR = REPO / "evidence"
RUNS_ROOT = REPO / "artifacts" / "agent_runs"


def run_eval():
    """Imported lazily: a missing scripts/ mount should fail the call, not the boot."""
    import run_eval as _run_eval
    return _run_eval


def grade(answer, gt):
    return run_eval().grade(answer, gt)


def ground_truth(card_id):
    """The answer, in process memory only. It must never reach a column (§3)."""
    return run_eval().ground_truth(card_id)
