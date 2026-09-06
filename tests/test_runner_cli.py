"""The batch runner's two abort ceilings must both be settable from the CLI.

Batch 8 was launched to get coverage, not yield, and asked for "do not abort".
--abort-after-recovered-failures took that; the gate-failure ceiling did not,
because it was a module constant with no flag, so half the request silently did
not happen (决策 033). This test is the thing that would have caught that: it
asserts both ceilings exist as options, default to their documented values, and
actually parse.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO, "scripts", "runner", "run_batch.py")


def _help():
    out = subprocess.run([sys.executable, RUNNER, "--help"],
                         capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_both_abort_ceilings_are_cli_options():
    h = _help()
    assert "--abort-after-recovered-failures" in h
    assert "--abort-after-gate-failures" in h


def test_gate_ceiling_defaults_to_the_module_constant():
    sys.path.insert(0, os.path.join(REPO, "scripts", "runner"))
    import run_batch                                          # noqa: PLC0415
    assert run_batch.BATCH_ABORT_AFTER_FAILURES == 3
    assert f"default: {run_batch.BATCH_ABORT_AFTER_FAILURES}" in _help()


def test_both_ceilings_parse_to_ints():
    # --cycles/--scenarios/--batch are three-way exclusive, so a parse-only check
    # still needs one of them; a nonexistent cycles file fails AFTER parsing.
    out = subprocess.run(
        [sys.executable, RUNNER, "--cycles", "/nonexistent-cycles.txt",
         "--abort-after-recovered-failures", "21",
         "--abort-after-gate-failures", "21"],
        capture_output=True, text=True, cwd=REPO)
    # argparse errors exit 2; anything else means the flags were accepted.
    assert out.returncode != 2, out.stderr
    assert "unrecognized arguments" not in out.stderr
