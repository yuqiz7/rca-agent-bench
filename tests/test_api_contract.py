#!/usr/bin/env python3
"""The service's wire contract: no card's answer may leave through the API.

PLACEHOLDER, DELIBERATELY. Design §6 makes the API contract CI's fourth gate, and
that gate is two things: these schema assertions plus an OpenAPI snapshot compared
byte for byte against tests/fixtures/openapi.json. The snapshot half lands in step
5, together with the CI change that installs fastapi -- today .github/workflows/ci.yml
installs only pytest and PyYAML, on purpose ("no gate may make a network call"),
so a test that needed fastapi to be importable would turn gate 1 red rather than
catch anything.

Hence two layers. The first parses service/app/models.py with `ast` and runs
everywhere, including a checkout with no service dependencies at all. The second
asks the real Pydantic models the same question and skips where fastapi is absent.
They check the same property from opposite sides: one that the source does not
DECLARE a truth field, one that the built model does not EXPOSE one.

Why this property and not some other: §1 says the service does not return ground
truth, and grading happens server-side precisely so the answer never crosses the
boundary. tests/test_no_leak.py holds that line at task.json and
scripts/agent/leak_check.py holds it at the bytes sent to the model. This holds it
at the HTTP response -- the third place a card's answer could get out, and the one
that did not exist before the service did.
"""
import ast
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_PY = os.path.join(REPO, "service", "app", "models.py")

# Same vocabulary as scripts/agent/leak_check.py FORBIDDEN_KEYS, minus the three
# that legitimately describe a card on the wire: class / target / primitive are
# what `GET /cards` is FOR (design §1 lists them by name as the returned fields).
# What is left is the set that decides the answer.
FORBIDDEN_FIELDS = {"ground_truth", "params", "aliases", "note", "answer_key", "gt"}

# The models a client can actually receive.
RESPONSE_MODELS = ("Card", "Run", "RunTrace", "Grade", "Answer", "ArmMetrics", "Summary")


def _declared_fields(class_name):
    tree = ast.parse(open(MODELS_PY, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}
    raise AssertionError(f"{class_name} is not defined in service/app/models.py")


@pytest.mark.parametrize("model_name", RESPONSE_MODELS)
def test_response_model_declares_no_truth_field(model_name):
    leaked = _declared_fields(model_name) & FORBIDDEN_FIELDS
    assert not leaked, f"{model_name} declares {sorted(leaked)}"


def test_card_is_exactly_the_snapshot_fields():
    """Closed over eight names, so a widened `cards` table cannot widen the response."""
    assert _declared_fields("Card") == {
        "card_id", "class_", "target", "primitive", "difficulty",
        "in_stock", "evidence_ok", "snapshot_at"}


@pytest.mark.parametrize("model_name", RESPONSE_MODELS)
def test_built_model_exposes_no_truth_field(model_name):
    pytest.importorskip("fastapi", reason="service deps land in CI with gate 4, step 5")
    import sys
    sys.path.insert(0, os.path.join(REPO, "service"))
    from app import models                                        # noqa: PLC0415
    fields = set(getattr(models, model_name).model_fields)
    aliases = {f.alias for f in getattr(models, model_name).model_fields.values() if f.alias}
    assert not (fields | aliases) & FORBIDDEN_FIELDS
