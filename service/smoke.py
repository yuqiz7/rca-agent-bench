#!/usr/bin/env python3
"""smoke.py -- HTTP -> queue -> worker -> grading -> Postgres -> query, end to end.

A LOCAL GATE, NOT A CI ONE (design §6): it needs docker, the compose stack and a
database, and CI's first hard rule is that gates run on a checkout alone.

WHY THE DEFAULT ARM IS `rules`
------------------------------
What this proves is that the CHAIN is intact -- submit, enqueue, claim, execute,
grade, double-write, read back. It is not about model accuracy. The rules arm
costs $0.00 and is deterministic, so this can be run as often as anyone likes,
and a failure is unambiguously the plumbing rather than a model having a bad day.
`--arm agent` runs the identical chain for about $0.07 and exists for the one
time you want to see the real thing; it is never run automatically.

THE CROSS-CHECK IS ARM-DEPENDENT, ON PURPOSE
--------------------------------------------
For `rules` the same card is re-run through keyword_heuristic.run() in this
process and both the answer and the grade must match the service's, which is the
strongest available statement of §4's "the service's numbers are the CLI's". For
`agent` that would mean paying twice for a non-deterministic arm, so the check is
narrower: the stored grade must equal a fresh run_eval.grade() over the answer the
service returned. Weaker, and said so rather than dressed up.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(REPO / "docker-compose.service.yml")]
BASE_URL = os.environ.get("RCA_SMOKE_URL", "http://127.0.0.1:8000")

for sub in ("agent", "harness", "scenarios", "baselines"):
    sys.path.insert(0, str(REPO / "scripts" / sub))


def http(path, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, method=method, data=data,
                                 headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def wait_healthy(deadline_s=120):
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            status, body = http("/healthz", timeout=5)
            if status == 200 and body.get("db") == "ok":
                return body
        except Exception:                                   # noqa: BLE001
            pass
        time.sleep(1)
    raise SystemExit("FAIL: /healthz never became ready")


def dev_card():
    import run_agent
    return run_agent.load_config()["dev_set"][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="rules", choices=["rules", "single_shot", "agent"])
    ap.add_argument("--card")
    ap.add_argument("--no-up", action="store_true", help="assume the stack is already running")
    a = ap.parse_args()

    t0 = time.time()
    card = a.card or dev_card()
    # One smoke row per (arm, card, day). A rerun on the same day comes back
    # through the idempotent-replay path instead of piling up rows -- which the
    # output names, so a green run never hides which path it actually took.
    key = f"smoke-{a.arm}-{card}-{time.strftime('%Y%m%d', time.gmtime())}"
    print(f"smoke: arm={a.arm} card={card} key={key}")

    if not a.no_up:
        subprocess.run(COMPOSE + ["up", "-d", "--build"], check=True,
                       stdout=subprocess.DEVNULL)
    health = wait_healthy()
    print(f"  /healthz            ok  budget_remaining_usd={health['budget_remaining_usd']}")

    status, ref = http("/runs", "POST", {"card_id": card, "arm": a.arm, "idempotency_key": key})
    if status != 202:
        raise SystemExit(f"FAIL: POST /runs -> {status} {json.dumps(ref)}")
    run_id, replayed = ref["run_id"], ref["status"] != "queued"
    print(f"  POST /runs          202 run_id={run_id}"
          + (f"  (idempotent replay, already {ref['status']})" if replayed else ""))

    deadline = time.time() + 600
    run = None
    while time.time() < deadline:
        status, run = http(f"/runs/{run_id}")
        if status != 200:
            raise SystemExit(f"FAIL: GET /runs/{run_id} -> {status}")
        if run["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    if run["status"] != "succeeded":
        raise SystemExit(f"FAIL: run ended {run['status']} terminated={run['terminated']} "
                         f"error={run['error']}")
    print(f"  GET /runs/{{id}}      succeeded terminated={run['terminated']} "
          f"steps={run['steps']} cost=${run['cost_usd']:.4f} wall={run['wall_s']}s")

    import run_eval
    gt = run_eval.ground_truth(card)                        # process memory only
    fresh = run_eval.grade(run["answer"], gt)
    ok = (bool(fresh["top1_ok"]) == run["grade"]["top1_ok"]
          and bool(fresh["service_ok"]) == run["grade"]["service_ok"])
    print(f"  grade regraded      service={run['grade']} recomputed={fresh} match={ok}")

    if a.arm == "rules":
        import keyword_heuristic
        cli = keyword_heuristic.run(card)
        cli_grade = run_eval.grade(cli["answer"], gt)
        same_answer = cli["answer"] == run["answer"]
        same_grade = (bool(cli_grade["top1_ok"]) == run["grade"]["top1_ok"]
                      and bool(cli_grade["service_ok"]) == run["grade"]["service_ok"])
        print(f"  CLI keyword_heuristic answer={cli['answer']} grade={cli_grade}")
        print(f"  service == CLI      answer={same_answer} grade={same_grade}")
        ok = ok and same_answer and same_grade

    # The trace must exist too: a run with no rows under it would satisfy every
    # check above and still mean the double write only half happened.
    status, trace = http(f"/runs/{run_id}/trace")
    ok = ok and status == 200 and len(trace["steps"]) == run["steps"]
    print(f"  GET /runs/{{id}}/trace {status} steps={len(trace['steps'])} (run reports {run['steps']})")

    elapsed = time.time() - t0
    print(f"\n{'PASS' if ok else 'FAIL'}  arm={a.arm} card={card} in {elapsed:.1f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
