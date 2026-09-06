#!/usr/bin/env python3
"""compare_arms.py -- one metrics table across N arms on one card set.

compare_report.py renders exactly three arms (rules / single-shot / agent) and is
what produced every report under artifacts/agent_runs/. It is left alone: those
reports are cited by docs and must keep rendering byte-identically. This script
is the N-arm case, added for the cross-configuration comparison in 决策 034.

An arm is (label, kind, source):
  kind "rows"  -- a baseline json: a list of per-card result dicts
  kind "runs"  -- one or more run ids under artifacts/agent_runs/, each a
                  directory of per-card result dicts (summary.json is skipped)

Scoring goes through run_eval.grade and run_eval.ground_truth, the same judge the
three-arm reports use, so numbers from both are directly comparable.

The last column is dollars per correct card (total spend / top-1 hits). It is the
one number that orders arms by what they cost to be right rather than by what they
cost to run, and it is undefined -- printed as n/a -- for an arm that got nothing
right.

Usage:
  compare_arms.py --cards-file scripts/baselines/cardset_all43.json \
      --arm "基线① 规则:rows:artifacts/agent_runs/merged_all43_20260906/baseline1.json" \
      --arm "agent-sonnet(调优):runs:devset_v2_20260828,devset_v2_extra8_20260828" \
      --out artifacts/agent_runs/<dir>/report.md [--title "..."]
"""
import argparse
import glob
import json
import os
import statistics
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "scripts", "harness"))
sys.path.insert(0, os.path.join(REPO, "scripts", "agent"))
sys.path.insert(0, os.path.join(REPO, "scripts", "scenarios"))
import run_eval                                     # noqa: E402

RUNS_ROOT = os.path.join(REPO, "artifacts", "agent_runs")


def load_rows(path):
    return {r["card_id"]: r for r in json.load(open(path))}


def load_runs(run_ids):
    out = {}
    for rid in run_ids:
        for f in sorted(glob.glob(os.path.join(RUNS_ROOT, rid, "*.json"))):
            if os.path.basename(f) == "summary.json":
                continue
            d = json.load(open(f))
            if isinstance(d, dict) and "answer" in d:
                out[d["card_id"]] = d
    return out


def parse_arm(spec):
    label, kind, src = spec.split(":", 2)
    if kind == "rows":
        return label, load_rows(src)
    if kind == "runs":
        return label, load_runs([s.strip() for s in src.split(",") if s.strip()])
    raise SystemExit(f"unknown arm kind {kind!r} (expected rows|runs)")


def metrics(rows, cards, gts):
    got = [rows[c] for c in cards if c in rows]
    missing = [c for c in cards if c not in rows]
    n = len(cards)
    grades = [run_eval.grade(rows[c].get("answer"), gts[c]) for c in cards if c in rows]
    top1 = sum(g["top1_ok"] for g in grades)
    svc = sum(g["service_ok"] for g in grades)
    total = sum(r.get("cost_usd") or 0.0 for r in got)
    return {
        "n": n, "missing": missing, "top1": top1, "svc": svc,
        "top1_pct": 100.0 * top1 / n, "svc_pct": 100.0 * svc / n,
        "steps": statistics.mean([r.get("steps") or 0 for r in got]) if got else 0.0,
        "cost": statistics.mean([r.get("cost_usd") or 0.0 for r in got]) if got else 0.0,
        "total": total,
        "p95": run_eval.p95([r.get("wall_s") or 0.0 for r in got]) or 0.0,
        # Dollars per correct card. Undefined at zero hits -- do not print 0.
        "per_hit": (total / top1) if top1 else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards-file", required=True)
    ap.add_argument("--arm", action="append", required=True,
                    help="<label>:<rows|runs>:<path or comma-separated run ids>")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    cards = json.load(open(a.cards_file))
    gts = {c: run_eval.ground_truth(c) for c in cards}
    arms = [parse_arm(s) for s in a.arm]
    m = {label: metrics(rows, cards, gts) for label, rows in arms}

    L = []
    A = L.append
    A(f"# {a.title or os.path.basename(a.cards_file)}")
    A("")
    A(f"- 卡集：**{len(cards)} 张**，冻结于 `{os.path.relpath(a.cards_file, REPO)}`")
    A("- 所有臂用同一判分器 `run_eval.grade` 与同一答案空间，因此可直接比较。")
    A("- 无工具的臂步数按 1 计。「每命中成本」= 该臂总花费 ÷ top-1 命中卡数。")
    A("")
    A("| 臂 | top-1 | service-only | 均步数 | 单卡成本 | p95 延迟 | 总花费 | 每命中成本 |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for label, _ in arms:
        x = m[label]
        ph = "n/a" if x["per_hit"] is None else f"${x['per_hit']:.4f}"
        A(f"| {label} | **{x['top1_pct']:.1f}%**（{x['top1']}/{x['n']}） | "
          f"{x['svc_pct']:.1f}%（{x['svc']}/{x['n']}） | {x['steps']:.2f} | "
          f"${x['cost']:.4f} | {x['p95']:.1f}s | ${x['total']:.4f} | {ph} |")
    A("")
    for label, _ in arms:
        if m[label]["missing"]:
            A(f"> **{label} 缺卡 {len(m[label]['missing'])} 张**："
              f"{', '.join('`%s`' % c for c in m[label]['missing'])} —— "
              f"缺的卡按未命中计入分母。")
            A("")
    with open(a.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
