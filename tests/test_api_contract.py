#!/usr/bin/env python3
"""Gate 4 (design §6): the API contract cannot change without someone saying so.

OFFLINE, AND THAT IS THE REQUIREMENT. This file builds the FastAPI app in-process
and asks it questions. It opens no database connection, makes no network call,
needs no ANTHROPIC_API_KEY, and runs with the anthropic client NOT INSTALLED --
service/app/harness.py imports run_agent, single_shot_llm and keyword_heuristic
inside functions precisely so that importing the app does not drag in the model
client. CI's first hard rule is that no gate may touch the network or the VM, and
a contract test that needed a live service would not be a gate, it would be a
second smoke test.

CHANGING THE CONTRACT MEANS COMMITTING THE SNAPSHOT.
tests/fixtures/openapi.json is compared byte for byte. If you intend to change a
path, a field, a status code or a description, run

    pytest tests/test_api_contract.py --update-snapshot

and COMMIT THE REGENERATED FILE IN THE SAME COMMIT as the change. A diff of that
file is the review artefact for "what did the API just do to its clients"; a red
gate with no snapshot diff means the contract moved by accident. Same idea as
gate 2 for the card generator: the check is not that the output is good, it is
that the output is the output someone signed for.

Skips cleanly where fastapi is absent (CI installs only pytest and PyYAML until
step 6 adds gate 4), so this file never turns gate 1 red on a minimal checkout.
"""
import ast
import json
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_PY = os.path.join(REPO, "service", "app", "models.py")
SNAPSHOT = os.path.join(REPO, "tests", "fixtures", "openapi.json")

# Same vocabulary as scripts/agent/leak_check.py FORBIDDEN_KEYS, minus the three
# that legitimately describe a card on the wire: class / target / primitive are
# what GET /cards is FOR (design §1 names them as the returned fields). What is
# left is the set that decides the answer.
FORBIDDEN_FIELDS = {"ground_truth", "params", "aliases", "note", "answer_key", "gt"}

# Every model a client can receive.
RESPONSE_MODELS = ("Card", "Run", "RunTrace", "Grade", "Answer", "ArmMetrics", "Summary")


def _app():
    pytest.importorskip("fastapi", reason="service deps; CI installs them with gate 4")
    from service.app.main import app                                  # noqa: PLC0415
    return app


def _snapshot_bytes(app):
    # sort_keys so a dict-ordering change in fastapi cannot masquerade as a
    # contract change; indent so the diff a reviewer reads is line-oriented.
    return (json.dumps(app.openapi(), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


# --------------------------------------------------------------------------- #
# 1. source-level: what models.py DECLARES. Runs with nothing installed.
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# 2. model-level: what pydantic actually builds.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("model_name", RESPONSE_MODELS)
def test_built_model_exposes_no_truth_field(model_name):
    _app()
    from service.app import models                                    # noqa: PLC0415
    model = getattr(models, model_name)
    names = set(model.model_fields) | {f.alias for f in model.model_fields.values() if f.alias}
    assert not names & FORBIDDEN_FIELDS


def test_run_create_rejects_an_unknown_arm():
    _app()
    from pydantic import ValidationError                              # noqa: PLC0415
    from service.app.models import RunCreate                          # noqa: PLC0415
    with pytest.raises(ValidationError):
        RunCreate(card_id="crash-cart-01", arm="telepathy")
    for arm in ("agent", "single_shot", "rules"):
        assert RunCreate(card_id="crash-cart-01", arm=arm).arm == arm


def test_run_create_requires_card_id_and_allows_a_null_model():
    _app()
    from pydantic import ValidationError                              # noqa: PLC0415
    from service.app.models import RunCreate                          # noqa: PLC0415
    with pytest.raises(ValidationError):
        RunCreate(arm="rules")
    body = RunCreate(card_id="crash-cart-01", arm="rules")
    # None is not "unset": §2 says it means models.primary from config.yaml, and
    # the service resolves it at submit time rather than storing a null model.
    assert body.model is None and body.idempotency_key is None


def test_error_envelope_shape():
    _app()
    from service.app.models import ErrorDetail, ErrorResponse         # noqa: PLC0415
    body = ErrorResponse(error=ErrorDetail(code="card_not_found", message="m",
                                           detail={"card_id": "x"}))
    assert body.model_dump() == {"error": {"code": "card_not_found", "message": "m",
                                           "detail": {"card_id": "x"}}}
    # detail is optional; code and message are not.
    assert ErrorResponse(error=ErrorDetail(code="c", message="m")).error.detail is None


def test_evidence_codes_are_two_distinct_codes():
    """修正 C: a leaking pack and an absent pack are different problems."""
    _app()
    from service.app.models import ERROR_CODES                        # noqa: PLC0415
    assert "evidence_leak" in ERROR_CODES and "evidence_missing" in ERROR_CODES


def test_page_of_runs_carries_items_and_cursor():
    _app()
    from service.app.models import Page, Run                          # noqa: PLC0415
    page = Page[Run](items=[], next_cursor=None)
    assert page.model_dump() == {"items": [], "next_cursor": None}


# --------------------------------------------------------------------------- #
# 3. the snapshot
# --------------------------------------------------------------------------- #

def test_openapi_matches_the_committed_snapshot(request):
    app = _app()
    current = _snapshot_bytes(app)
    if request.config.getoption("--update-snapshot"):
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "wb") as fh:
            fh.write(current)
        pytest.skip(f"snapshot rewritten ({len(current)} bytes) -- commit it")
    assert os.path.exists(SNAPSHOT), \
        "no snapshot yet; run pytest tests/test_api_contract.py --update-snapshot"
    with open(SNAPSHOT, "rb") as fh:
        committed = fh.read()
    assert current == committed, (
        "the OpenAPI contract changed. If that was intended, rerun with "
        "--update-snapshot and commit tests/fixtures/openapi.json in the same commit.")


def test_the_endpoint_set_is_the_designed_one():
    """§2's eight endpoints, plus the local-only admin one. A new path is a review."""
    app = _app()
    spec = app.openapi()
    assert set(spec["paths"]) == {
        "/runs", "/runs/{run_id}", "/runs/{run_id}/trace",
        "/cards", "/cards/{card_id}", "/summary", "/healthz", "/admin/reimport"}
    assert sum(len(v) for v in spec["paths"].values()) == 9      # /runs carries GET+POST
    assert "ground_truth" not in {
        prop for schema in spec["components"]["schemas"].values()
        for prop in schema.get("properties", {})}


# --------------------------------------------------------------------------- #
# pick=published_all43 -- the named run set behind a published table
# --------------------------------------------------------------------------- #

def _published():
    pytest.importorskip("fastapi", reason="service deps; CI installs them with gate 4")
    from service.app import summary                                   # noqa: PLC0415
    return summary


def test_published_pick_is_in_the_wire_enum():
    """The pick a client may send and the pick the service knows must be one list.

    Summary.pick is a Literal, so a value the endpoint accepts but the response
    model does not know about would serialise-error at the end of a request that
    already did all its work -- and only for that pick, which is the kind of hole
    a snapshot diff shows and a smoke test on `latest` never reaches.
    """
    app = _app()
    summary = _published()
    enum = set(app.openapi()["components"]["schemas"]["Summary"]["properties"]["pick"]["enum"])
    assert set(summary.PUBLISHED_PICKS) <= enum
    assert {"latest", "run_ids", "published_all43"} == enum


def test_published_pick_names_directories_that_exist():
    """Every batch named in PUBLISHED_ALL43 is on disk, under the cardset it claims.

    Offline half of the claim: this catches a typo or a renamed batch directory in
    CI. The other half -- that those batches hold one run per card and aggregate
    to the published numbers -- needs the database and is verified there.
    """
    summary = _published()
    runs_root = os.path.join(REPO, "artifacts", "agent_runs")
    for pick, (cardset, table) in summary.PUBLISHED_PICKS.items():
        assert os.path.exists(os.path.join(
            REPO, "scripts", "baselines", f"cardset_{cardset}.json")), pick
        assert summary.published_cardset_for(pick) == cardset
        assert table, f"{pick} names no arms"
        for (arm, model), dirs in table.items():
            assert dirs, f"{pick}: arm {arm}/{model} names no batch"
            for d in dirs:
                assert os.path.isdir(os.path.join(runs_root, d)), f"{pick}: missing batch {d}"


def test_unknown_pick_is_not_silently_accepted():
    summary = _published()
    assert summary.published_arms_for("latest") is None
    assert summary.published_arms_for("run_ids") is None
    assert summary.published_cardset_for("nope") is None
