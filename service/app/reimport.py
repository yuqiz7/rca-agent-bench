#!/usr/bin/env python3
"""reimport.py -- backfill artifacts/agent_runs/ into Postgres (§3, POST /admin/reimport).

WHAT IS ACTUALLY ON DISK, WHICH IS NOT WHAT §3 SAYS
---------------------------------------------------
The design writes the artefact path as `artifacts/agent_runs/<run-id>/*.json` and
imagines one run per file. run_agent.write_result() does produce that shape --
`<run-id>/<card_id>.json`, one card per file -- but the two baseline arms do not:
scripts/baselines/keyword_heuristic.py and single_shot_llm.py each write ONE file
holding a LIST of per-card rows. So a "run" in service terms is a row, not a file,
and one file can hold 27 of them. Everything below follows the disk.

Three artefact shapes, discriminated by CONTENT rather than by filename, so a
renamed or relocated file still imports as the right arm:

  agent        dict with "transcript"           run_agent.py
  single_shot  row with "replies"               single_shot_llm.py  (baseline2.json)
  rules        row with "scores" + "rationale"  keyword_heuristic.py (baseline1.json)

The skip rule for non-run JSON is taken verbatim from
scripts/baselines/compare_arms.py:load_runs -- skip summary.json, require a dict
with an "answer" -- because that function is what every published arm table was
computed from. Using a different rule here would mean the service could import a
file the reports never counted.

IDEMPOTENCE
-----------
run_id is uuid5 over (artifact_path, card_id), so re-running produces the SAME
uuid rather than a new one, and 002_reimport_dedup.sql makes the database refuse
a second row for that pair. Two independent mechanisms because the dedup query
and the insert are not atomic with respect to a second caller, and this endpoint
is the one place where a duplicate would silently double every number in
/summary.

GROUND TRUTH IS READ AND DISCARDED
----------------------------------
Grading needs the answer. run_eval.ground_truth() reads it from the yaml into
process memory, run_eval.grade() consumes it, and nothing between here and the
database carries it: the only thing written is two booleans in `grades`. Note that
summary.json ALSO contains a `ground_truth` block per card -- when a stored grade
is taken from there, only the `grade` key is read out of the row.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import harness, repo

# Stable namespace for reimport run_ids. Fixed literal on purpose: the uuids have
# to be reproducible across processes, machines and rebuilds, which a generated
# namespace would not be.
NAMESPACE = uuid.UUID("6f0a1d2c-3b4e-5a6f-8c9d-0e1f2a3b4c5d")

# Terminal reasons that mean the run did its job. Measured against every artefact
# in the repo (416 rows): agent ends `submit` or `max_steps`, single-shot ends
# `answered`, rules ends `submit`, and no artefact carries a non-null `error`.
# `max_steps` is the one failure mode present -- 9 runs, all of them with a null
# answer -- and it is exactly what §2's `terminated` vocabulary calls a breaker.
PRODUCTIVE_TERMINATED = {"submit", "answered"}

_BATCH_DATE = re.compile(r"_(\d{8})$")


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _batch_created_at(path):
    """A timestamp for arms whose rows carry none.

    The two baseline writers record wall_s but no start time, so the only date the
    repo itself attaches to those rows is the one in the batch directory name
    (`merged_all43_20260906`). Midnight UTC of that day is deliberately coarse --
    it is honest about being a directory name rather than a measurement, and it is
    stable across checkouts in a way that a file mtime is not.
    """
    m = _BATCH_DATE.search(path.parent.name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _agent_children(row):
    steps, model_calls, tool_calls = [], [], []
    previous_cumulative = 0.0
    for entry in row.get("transcript") or []:
        step_no = entry["step"]
        # duration_ms has no source: run_agent records wall_s for the whole run and
        # per-step timing only in traces.jsonl, which exists for exactly one batch.
        # Left NULL rather than divided out of the total -- an invented per-step
        # duration would be indistinguishable from a measured one.
        steps.append({"step_no": step_no, "duration_ms": None})
        usage = entry.get("usage") or {}
        cumulative = entry.get("cumulative_cost_usd")
        step_cost = None
        if cumulative is not None:
            step_cost = round(cumulative - previous_cumulative, 6)
            previous_cumulative = cumulative
        model_calls.append({
            "step_no": step_no,
            "model": row["model"],
            "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"),
            "cache_write_tokens": usage.get("cache_write"),
            "cache_read_tokens": usage.get("cache_read"),
            "step_cost_usd": step_cost,
            "stop_reason": entry.get("stop_reason"),
            "latency_s": None,          # same reason as duration_ms
        })
        for call in entry.get("tool_calls") or []:
            result = call.get("result")
            tool_calls.append({
                "step_no": step_no,
                "tool": call["name"],
                # The arguments themselves are not stored: they are agent-authored
                # text, and the column is named for a digest. The digest is enough
                # to answer "did it make this same call twice".
                "args_digest": _digest(call.get("input")),
                # submit() returns {"accepted": ...} and no byte count, so this is
                # NULL for exactly the submit calls.
                "result_bytes": result.get("bytes") if isinstance(result, dict) else None,
                "ok": call.get("ok"),
                # Per-call flags do not exist in the artefact -- run_agent keeps
                # only the run-level totals, which are preserved in runs.counters
                # as validation_rejects / tool_retries. False here means "not
                # recorded per call", and the aggregate is the honest number.
                "validation_reject": False,
                "retried": False,
                "error": call.get("error"),
            })
    return steps, model_calls, tool_calls


def _single_shot_children(row):
    usage = row.get("usage") or {}
    return (
        [{"step_no": 1, "duration_ms": None}],
        # One row for the whole arm: the artefact reports api_calls as a count and
        # usage as a single total, so splitting it into api_calls rows would mean
        # inventing a per-call division. The count survives in runs.counters.
        [{"step_no": 1, "model": row["model"],
          "input_tokens": usage.get("input"), "output_tokens": usage.get("output"),
          "cache_write_tokens": usage.get("cache_write"),
          "cache_read_tokens": usage.get("cache_read"),
          "step_cost_usd": row.get("cost_usd"), "stop_reason": None, "latency_s": None}],
        [],
    )


def classify(row, is_list_member: bool) -> str:
    if not is_list_member and "transcript" in row:
        return "agent"
    if "replies" in row:
        return "single_shot"
    if "scores" in row and "rationale" in row:
        return "rules"
    raise ValueError("artefact matches no known arm shape "
                     f"(keys: {sorted(row)[:12]})")


def build(row, arm, artifact_path, path, stored_grade=None):
    """One artefact row -> (run, grade, steps, model_calls, tool_calls)."""
    card_id = row["card_id"]
    run_id = uuid.uuid5(NAMESPACE, f"{artifact_path}#{card_id}")
    answer = row.get("answer") or None
    terminated = row.get("terminated")
    created_at = _parse_ts(row.get("started_at")) or _batch_created_at(path)
    wall_s = row.get("wall_s")
    finished_at = created_at + timedelta(seconds=float(wall_s)) if wall_s is not None else None

    if arm == "agent":
        steps, model_calls, tool_calls = _agent_children(row)
        counters = row.get("counters")
        model = row["model"]
    elif arm == "single_shot":
        steps, model_calls, tool_calls = _single_shot_children(row)
        counters = {"api_calls": row.get("api_calls") or 0,
                    "parse_failures": row.get("parse_failures") or 0}
        model = row["model"]
    else:                                   # rules
        steps, model_calls, tool_calls = [{"step_no": 1, "duration_ms": None}], [], []
        counters = None
        # The rules arm calls no model, and runs.model is NOT NULL. readme_check's
        # own label for this arm is "rules, no model"; "none" is that, written so a
        # `SELECT DISTINCT model` reads as a statement rather than as a gap.
        model = "none"

    run = {
        "run_id": run_id,
        "card_id": card_id,
        "arm": arm,
        "model": model,
        "status": "succeeded" if (not row.get("error") and terminated in PRODUCTIVE_TERMINATED)
                  else "failed",
        "created_at": created_at,
        # The artefacts record one timestamp per run, so submission time and start
        # time collapse onto it. Backfilled history has no queue to have waited in.
        "started_at": created_at,
        "finished_at": finished_at,
        "answer_service": (answer or {}).get("service"),
        "answer_fault": (answer or {}).get("fault_type"),
        "terminated": terminated,
        "steps": row.get("steps"),
        "cost_usd": row.get("cost_usd"),
        "wall_s": wall_s,
        "counters": counters,
        "error": row.get("error"),
        "artifact_path": artifact_path,
        "run_config": row.get("run_config"),
    }
    grade = stored_grade or harness.grade(answer, harness.ground_truth(card_id))
    grade = {"top1_ok": bool(grade["top1_ok"]), "service_ok": bool(grade["service_ok"])}
    return run, grade, steps, model_calls, tool_calls


def _stored_grades(directory):
    """{card_id: grade} from a batch's summary.json, if it has one.

    Only the `grade` key is taken. summary.json also carries `ground_truth` per
    card; that block is left in the dict and never travels further.
    """
    summary = directory / "summary.json"
    if not summary.is_file():
        return {}
    try:
        rows = (json.loads(summary.read_text(encoding="utf-8")) or {}).get("rows") or []
    except (json.JSONDecodeError, OSError):
        return {}
    return {r["card_id"]: r["grade"] for r in rows
            if isinstance(r, dict) and r.get("card_id") and r.get("grade")}


def _iter_rows(runs_root):
    """(row, arm, artifact_path, path, stored_grade) for every run on disk."""
    for path in sorted(runs_root.glob("*/*.json")):
        if path.name == "summary.json":         # compare_arms.load_runs skips it
            continue
        artifact_path = str(path.relative_to(harness.REPO))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            yield None, None, artifact_path, path, None, f"unreadable: {exc}"
            continue
        grades = _stored_grades(path.parent)
        members = payload if isinstance(payload, list) else [payload]
        for row in members:
            if not isinstance(row, dict) or "answer" not in row:
                continue                        # summary.json shape, report.md, anything else
            try:
                arm = classify(row, isinstance(payload, list))
            except ValueError as exc:
                yield None, None, artifact_path, path, None, str(exc)
                continue
            stored = grades.get(row.get("card_id")) if arm == "agent" else None
            yield row, arm, artifact_path, path, stored, None


def reimport(conn, runs_root=None) -> dict:
    runs_root = runs_root or harness.RUNS_ROOT
    with conn.cursor() as cur:
        seen = repo.artifact_paths(cur)

    scanned = imported = skipped = 0
    failed = []
    for row, arm, artifact_path, path, stored, error in _iter_rows(runs_root):
        if error:
            scanned += 1
            failed.append({"artifact_path": artifact_path, "card_id": None, "error": error})
            continue
        scanned += 1
        card_id = row.get("card_id")
        if (artifact_path, card_id) in seen:
            skipped += 1
            continue
        try:
            run, grade, steps, model_calls, tool_calls = build(row, arm, artifact_path, path, stored)
            repo.insert_run_bundle(conn, run, grade=grade, steps=steps,
                                   model_calls=model_calls, tool_calls=tool_calls)
        except Exception as exc:                # noqa: BLE001 -- one bad artefact must not stop the batch
            failed.append({"artifact_path": artifact_path, "card_id": card_id, "error": str(exc)})
            continue
        seen.add((artifact_path, card_id))
        imported += 1
    return {"scanned": scanned, "imported": imported, "skipped": skipped, "failed": failed}
