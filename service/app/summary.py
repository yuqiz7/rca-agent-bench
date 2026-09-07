#!/usr/bin/env python3
"""summary.py -- the five-arm table, computed by compare_arms.metrics().

決策 037 item 3 settled this: /summary is the endpoint most likely to impress and
the one place the repo could end up with "the same metric, two implementations".
The mitigation chosen there was not "write tests that compare them" but "do not
write the second one". So everything below is selection and shaping; the moment
there are rows, the arithmetic is compare_arms.metrics() and nothing else. There
is no percentage, no mean and no p95 computed in this file.

WHY SELECTION IS THE HARD PART, NOT AGGREGATION
-----------------------------------------------
Step 2 backfilled 416 runs and 201 of them are repeat executions of the same
(card, arm, model): merged_*/baseline*.json re-aggregate earlier batches, and
devset / devset_v2 are re-runs of one another. A `GROUP BY arm` over that table
would count the same execution twice and report a number that matches no report
in the repo. So a summary must pick exactly one run per (card, arm):

  pick=latest            the newest succeeded run. What the service means by
                         "current".
  pick=run_ids           an explicit list. What you use to reproduce a published
                         table, whose arms are specific batches rather than
                         "whatever is newest".
  pick=published_all43   the run set behind ONE named published table, by name
                         instead of by 129 uuids. See PUBLISHED_ALL43 below.

All three are offered because they answer different questions, and quietly having
only `latest` would have made the service unable to reproduce its own repo's
numbers.
"""
import re

from . import harness, repo

# The run set behind artifacts/agent_runs/fivearm_all43_20260906/report.md
# (added in commit c54a6c8, 决策 034), keyed by (arm, model) and valued by the
# artefact directories those runs were written to. Every path here is the third
# segment of runs.artifact_path, which is where reimport recorded which batch a
# run came from.
#
# WHY A CONSTANT TABLE AND NOT A QUERY
# ------------------------------------
# There is no query that finds "the runs that table cites". The published table
# is five arms assembled from batches run on four different days, and the same
# (card, arm, model) appears in the database several times over -- devset and
# devset_v2 are re-runs of one another, and merged_*/baseline*.json re-aggregate
# earlier batches. `pick=latest` deliberately answers a different question
# ("what does the service think is current"), and it does NOT reproduce this
# table: for the agent arm it would pick up devset_20260828 and the service's own
# runs. Naming the batches is the only thing that reproduces a frozen number, and
# a name typed once here is checked by a test, where 129 uuids pasted into a URL
# are checked by nobody.
#
# The two haiku arms are listed as evidence_audit C7 locates them: the set*
# directories are the runs themselves, and merged_all43_haiku_20260906 is a
# re-aggregation of the same executions -- either reproduces the table, and using
# the set* ones keeps all four multi-batch arms spelled the same way.
#
# ADDING AN ARM HERE IS ADDING A CLAIM: that these batches hold exactly one run
# per card in cardset_all43.json, and that the numbers they aggregate to are the
# numbers in the report. That claim is checked against the live database, not by
# a unit test -- the two arm shapes make it impossible to check off disk, since
# the agent arms store one file per card while the rules and single_shot arms are
# a single baseline*.json holding all 43. What the tests do check is the part
# that can go wrong silently: gate 4 pins the pick into the OpenAPI enum and
# checks every directory named here exists, and the local DB gate pins the
# selector's semantics (batch scope, failed runs kept, newest wins).
PUBLISHED_ALL43 = {
    ("rules", None):                        ("merged_all43_20260906",),
    ("rules", "none"):                      ("merged_all43_20260906",),
    ("single_shot", "claude-sonnet-5"):     ("merged_all43_20260906",),
    ("agent", "claude-sonnet-5"):           ("devset_v2_20260828",
                                             "devset_v2_extra8_20260828",
                                             "holdout5_agent_20260828",
                                             "holdout11_agent_20260906"),
    ("single_shot", "claude-haiku-4-5"):    ("set27_haiku_20260906",
                                             "set5_haiku_20260906",
                                             "set11_haiku_20260906"),
    ("agent", "claude-haiku-4-5"):          ("set27_agent_haiku_20260906",
                                             "set5_agent_haiku_20260906",
                                             "set11_agent_haiku_20260906"),
}

PUBLISHED_PICKS = {"published_all43": ("all43", PUBLISHED_ALL43)}

_CARDSET_FILE = re.compile(r"^cardset_(?P<name>[A-Za-z0-9_]+)\.json$")


def available_cardsets() -> dict:
    """{name: path} discovered from scripts/baselines/cardset_*.json.

    The name is the filename with the prefix and suffix stripped -- all43,
    holdout16, 27. Matched against a whitelist regex and then looked up in this
    dict, so a `cardset=../../etc/passwd` never becomes a path.
    """
    out = {}
    for path in sorted(harness.CARDSETS_DIR.glob("cardset_*.json")):
        m = _CARDSET_FILE.match(path.name)
        if m:
            out[m.group("name")] = path
    return out


def load_cardset(name: str) -> list[str] | None:
    import json
    path = available_cardsets().get(name)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def parse_arms(spec: str) -> list[tuple[str, str | None]]:
    """"agent:claude-sonnet-5,single_shot:claude-haiku-4-5,rules" -> [(arm, model), ...]

    NOT compare_arms.parse_arm: that function's grammar is
    <label>:<rows|runs>:<source>, it resolves a filesystem path or a batch
    directory, and it returns rows read off disk. This service selects rows from
    Postgres, so there is nothing for it to parse here -- `agent:claude-sonnet-5`
    does not even have enough colons for its split. The aggregation is still
    entirely compare_arms'; only the arm spelling is local. See the report.
    """
    arms = []
    for chunk in (c.strip() for c in spec.split(",")):
        if not chunk:
            continue
        arm, _, model = chunk.partition(":")
        arms.append((arm.strip(), model.strip() or None))
    return arms


def _row_for_metrics(run: dict) -> dict:
    """A runs row in the shape compare_arms.metrics() reads.

    Only the four keys it touches -- answer, cost_usd, steps, wall_s -- and they
    are converted to plain floats. The columns are numeric() and come back as
    Decimal; metrics() does `sum(...)` starting at int 0 and `statistics.mean`,
    and Decimal + float raises. The JSON artefacts the CLI reads are plain floats,
    so converting here is what makes the two paths arithmetically identical rather
    than merely similar.
    """
    answer = None
    if run.get("answer_service") or run.get("answer_fault"):
        answer = {"service": run.get("answer_service"), "fault_type": run.get("answer_fault")}
    return {
        "answer": answer,
        "cost_usd": float(run["cost_usd"]) if run.get("cost_usd") is not None else 0.0,
        "steps": int(run["steps"]) if run.get("steps") is not None else 0,
        "wall_s": float(run["wall_s"]) if run.get("wall_s") is not None else 0.0,
    }


def published_arms_for(pick: str) -> dict | None:
    """The (arm, model) -> batches table a published pick names, or None."""
    entry = PUBLISHED_PICKS.get(pick)
    return None if entry is None else entry[1]


def published_cardset_for(pick: str) -> str | None:
    """The cardset a published pick is defined over.

    A published table is a claim about one specific card set: asking for
    `?cardset=holdout16&pick=published_all43` would silently compute a 16-card
    slice of a 43-card table and print it under the published table's name. main
    rejects the mismatch using this rather than letting the number out.
    """
    entry = PUBLISHED_PICKS.get(pick)
    return None if entry is None else entry[0]


def compute(cur, cardset_name: str, cards: list[str], arms, *, pick="latest", run_ids=None) -> dict:
    # Ground truth enters this function's local scope and leaves with the two
    # booleans metrics() derives from it. It is not returned, not logged, not
    # cached on the connection (§3).
    gts = {c: harness.ground_truth(c) for c in cards}
    metrics = harness.compare_arms().metrics

    explicit = None
    if pick == "run_ids":
        explicit = repo.runs_by_ids(cur, run_ids or [])
    published = published_arms_for(pick)

    out = []
    for arm, model in arms:
        if published is not None:
            chosen = repo.runs_in_artifact_dirs(
                cur, cards, arm, model, published.get((arm, model), ()))
        elif explicit is None:
            chosen = repo.latest_succeeded_per_card(cur, cards, arm, model)
        else:
            chosen = {}
            for run in explicit:
                if run["arm"] != arm or (model is not None and run["model"] != model):
                    continue
                if run["status"] != "succeeded" or run["card_id"] not in set(cards):
                    continue
                # An explicit list that names two runs of one card on one arm is a
                # caller mistake, not something to average over: keep the newest
                # and let `selected` show fewer runs than uuids handed in.
                prev = chosen.get(run["card_id"])
                if prev is None or (run["created_at"], run["run_id"]) > (prev["created_at"], prev["run_id"]):
                    chosen[run["card_id"]] = run

        rows = {c: _row_for_metrics(r) for c, r in chosen.items()}
        m = metrics(rows, cards, gts)
        out.append({"arm": arm, "model": model, "selected": len(rows), **m})

    return {"cardset": cardset_name, "n_cards": len(cards), "pick": pick, "arms": out}
