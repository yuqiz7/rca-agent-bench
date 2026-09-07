#!/usr/bin/env python3
"""runner.py -- execute one claimed run on one of the three arms (design §4).

IMPORTS, NEVER COPIES. Every arm below is the same function the CLI calls:
run_agent.run_card, single_shot_llm.run_card, keyword_heuristic.run, and
run_eval.grade over run_eval.ground_truth. §4 requires the service's numbers to be
digit-for-digit the CLI's, and the only construction that guarantees that is a
single implementation. Nothing here re-derives a prompt, a price, a breaker or a
judgement -- max_steps and max_usd_per_card come out of scripts/agent/config.yaml
through harness.load_config(), and the service does not override them.

DOUBLE WRITE, IN THIS ORDER (§3)
--------------------------------
    1. run the arm
    2. write the JSON evidence artefact with run_agent.write_result()
    3. write the Postgres rows

The order is the rule: "JSON 是可以没有数据库行的，数据库行不可以没有 JSON". If
step 3 fails, the run is marked failed with the reason and the JSON stays on
disk, which is the state §3 says is recoverable -- POST /admin/reimport reads it
back. The reverse would leave a database row pointing at a file that does not
exist, and nothing in the repo can repair that.

WHY THE ARTEFACT DIRECTORY IS service_<date>_<hex8> AND NOT service_<date>
--------------------------------------------------------------------------
write_result(result, run_id) writes runs_root/<run_id>/<card_id>.json -- the file
name is the CARD, not the run. A directory shared by a whole day would therefore
give two runs of the same card the same path, which collides with the
(artifact_path, card_id) unique index from step 2 and, worse, would have the
second run silently overwrite the first one's evidence. The uuid suffix is the
run's own id, so the path is unique by construction while `ls agent_runs/` still
groups by day.
"""
import pathlib
import traceback
from datetime import datetime, timezone

from . import harness, reimport, repo

# Terminal reasons that mean the arm did its job; same set the backfill uses, so
# a service-created run and a re-imported one get the same status for the same
# outcome.
PRODUCTIVE_TERMINATED = reimport.PRODUCTIVE_TERMINATED


def artifact_run_id(run_id, now=None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"service_{now:%Y%m%d}_{str(run_id).replace('-', '')[:8]}"


def _execute_arm(arm, card_id, model, config, prices, art_run_id):
    """Call the arm. Returns its result dict, unmodified."""
    if arm == "agent":
        return harness.run_agent().run_card(
            card_id, model=model, config=config, prices=prices, run_id=art_run_id)
    if arm == "single_shot":
        return harness.single_shot_llm().run_card(
            card_id, model=model, config=config, prices=prices, run_id=art_run_id)
    if arm == "rules":
        # No model, no key, no cost. keyword_heuristic.run takes neither a model
        # nor a config: it reads the pack and scores it.
        return harness.keyword_heuristic().run(card_id)
    raise ValueError(f"unknown arm {arm!r}")


def _children(arm, result):
    """Child rows, through the SAME mapping the backfill uses (step 2).

    duration_ms and latency_s stay NULL. Checked against the live return values,
    not just the artefacts on disk: run_agent's transcript entries carry step,
    stop_reason, usage, cumulative_cost_usd, text and tool_calls, and no timing;
    single_shot and rules report one wall_s for the whole run. Timing them here
    would mean editing run_agent, which §4 forbids, so the columns stay empty
    rather than carrying a number the evaluator never measured.
    """
    if arm == "agent":
        return reimport.agent_children(result)
    if arm == "single_shot":
        return reimport.single_shot_children(result)
    return [{"step_no": 1, "duration_ms": None}], [], []


def execute(conn, run: dict) -> dict:
    """Run one claimed row to completion. Never raises: a worker must not die of a card."""
    run_id, card_id, arm = run["run_id"], run["card_id"], run["arm"]
    art_run_id = artifact_run_id(run_id)
    artifact_path = None
    try:
        config = harness.load_config()
        prices = harness.load_prices()
        model = None if arm == "rules" else (run["model"] or harness.primary_model(config))
        result = _execute_arm(arm, card_id, model, config, prices, art_run_id)

        # 2. JSON first, always.
        abs_path = harness.run_agent().write_result(result, art_run_id)
        artifact_path = str(pathlib.Path(abs_path).relative_to(harness.REPO))

        answer = result.get("answer") or None
        # The answer is read here, graded here, and never written anywhere but the
        # two booleans in `grades`. ground_truth() is process memory only (§3).
        grade = harness.grade(answer, harness.ground_truth(card_id))
        grade = {"top1_ok": bool(grade["top1_ok"]), "service_ok": bool(grade["service_ok"])}

        steps, model_calls, tool_calls = _children(arm, result)
        terminated = result.get("terminated")
        if arm == "single_shot":
            counters = {"api_calls": result.get("api_calls") or 0,
                        "parse_failures": result.get("parse_failures") or 0}
        else:
            counters = result.get("counters")

        fields = {
            # rules calls no model and the column is NOT NULL -- same "none" the
            # backfill writes, so the two sources agree in a GROUP BY.
            "model": "none" if arm == "rules" else result.get("model") or model,
            "status": "succeeded" if (not result.get("error")
                                      and terminated in PRODUCTIVE_TERMINATED) else "failed",
            "finished_at": datetime.now(timezone.utc),
            "answer_service": (answer or {}).get("service"),
            "answer_fault": (answer or {}).get("fault_type"),
            "terminated": terminated,
            "steps": result.get("steps"),
            "cost_usd": result.get("cost_usd") if arm != "rules" else 0.0,
            "wall_s": result.get("wall_s"),
            "counters": counters,
            "error": result.get("error"),
            "artifact_path": artifact_path,
        }
        # 3. Postgres.
        repo.finish_run(conn, run_id, fields, grade=grade, steps=steps,
                        model_calls=model_calls, tool_calls=tool_calls)
        return {"run_id": str(run_id), "status": fields["status"], "terminated": terminated,
                "cost_usd": fields["cost_usd"], "artifact_path": artifact_path}

    except Exception as exc:                     # noqa: BLE001 -- the worker survives every card
        detail = f"{type(exc).__name__}: {exc}"
        try:
            repo.finish_run(conn, run_id, {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc),
                # api_error is §2's vocabulary for "the run died on the way out";
                # it covers a failed database write too, which from the caller's
                # side is the same thing: the run did not land.
                "terminated": "api_error",
                "error": detail[:2000],
                "artifact_path": artifact_path,
            })
        except Exception:                        # noqa: BLE001
            pass                                 # nothing left to record it with
        return {"run_id": str(run_id), "status": "failed", "error": detail,
                "traceback": traceback.format_exc(limit=3), "artifact_path": artifact_path}
