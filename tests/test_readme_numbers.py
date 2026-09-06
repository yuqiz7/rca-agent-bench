"""README's numbers must still be the repo's numbers.

readme_check.py recomputes every `<!-- GEN:key -->` body from HEAD; this test is
the gate that keeps a stale README from being committed. It reads HEAD rather than
the working tree, so a batch runner mid-write does not turn it red -- see the
module docstring of scripts/tools/readme_check.py.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(REPO, "scripts", "tools", "readme_check.py")


def _is_git_repo():
    return subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "HEAD"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _is_git_repo(), reason="readme_check reads values from HEAD")
def test_readme_numbers_match_repo():
    out = subprocess.run([sys.executable, CHECKER, "--check"],
                         capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stdout + out.stderr
