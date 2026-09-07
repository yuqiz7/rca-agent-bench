"""Shared pytest wiring for the repo's tests.

Two things only:

  - the repo root on sys.path, so `import service.app...` works from a plain
    `pytest tests/` in a checkout with nothing installed in development mode;
  - `--update-snapshot`, the deliberate escape hatch for the OpenAPI snapshot in
    tests/test_api_contract.py. It is a flag rather than an environment variable
    because it should show up in shell history: regenerating the snapshot is how
    an API contract change gets recorded, and it must be a thing someone typed.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def pytest_addoption(parser):
    parser.addoption("--update-snapshot", action="store_true", default=False,
                     help="rewrite tests/fixtures/openapi.json from the current app")
