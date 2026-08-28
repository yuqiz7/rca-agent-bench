#!/usr/bin/env python3
"""recover_window.py -- derive recover_s per target so the recovered gate has data.

F-2: decision 023's R1 lengthened the INJECT window for low-traffic targets and
left the recover window at its default. The recovered gate compares a rate
measured over [t_revert + 30 s, t_end], which at recover_s = 60 is a 30 s window;
on a 0.05 /s target that window expects 1.6 calls, so "rate >= baseline x 0.5"
reduces to "at least one call has to land" and the gate reports Poisson noise as
a failed recovery.

The fix is the same shape as R1's: compute the window each target needs and carry
it in recipe.csv's cycle_override, rather than adding a second override path.
Nothing here is hand-typed -- rerun this against the baseline audit and the
numbers reproduce.

  expected calls = rate x (recover_s - RECOVER_SKIP_S) >= MIN_EXPECTED
  => recover_s >= RECOVER_SKIP_S + MIN_EXPECTED / rate

The rate used is the MINIMUM of the five audit windows, not the mean. The point
of the rule is to hold on the target's slowest window; sizing on the mean leaves
the slow ones failing, which is the position R1 already found itself in when it
picked its list on the min rather than the mean.

F-4 rides on the same override. valkey-cart has plenty of traffic -- 43 expected
calls in the default window -- so the count rule gives it nothing, but its
failure was that cart had not rebuilt its connection pool 30 s after the iptables
rule came off, and a longer window is the same remedy for a different reason. Its
value is a MEASUREMENT TARGET, not a derived one: we know the lag is > 30 s and
nothing more, so it gets the same floor as the low-traffic targets and the next
run tells us whether that was enough.

Usage:  recover_window.py            # print the table
        recover_window.py --apply    # write cycle_override into recipe.csv
"""
import argparse
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
AUDIT = os.path.join(REPO, "artifacts", "baseline_audit",
                     "r1_baseline_5x300s_2026-08-28.json")
RECIPE = os.path.join(HERE, "recipe.csv")

RECOVER_SKIP_S = 30      # run_batch.RECOVER_SKIP_S
# The floor the symptom side already guards with, reused here so the two sides of
# the gate agree. It bounds the failure mode but does not remove it: the recovered
# rule wants the observed rate to reach half the baseline, so it needs lambda/2
# calls to land, and at lambda = 5 that still fails 12.5% of the time on a healthy
# service. Measured against this table:
#
#   MIN_EXPECTED   P(false recovered failure)
#              5                       12.5%
#             10                        6.7%
#             15                        1.8%
#
# Raising it is a one-line change and costs wall clock on every low-traffic card
# (a target at 0.0433 /s needs recover_s = 260 to expect 10). Left at 5 for now:
# that is the floor the rest of the harness is written against, and moving it is a
# separate decision from fixing the window being absent altogether.
MIN_EXPECTED = 5.0
RECOVER_S_DEFAULT = 60
RECOVER_S_CAP = 300
ROUND_TO = 10

# Targets whose recovered failure is reconnect lag rather than sample count
# (F-4). Value is a floor to measure against, not a derived requirement.
RECONNECT_LAG_TARGETS = {"valkey-cart": 150}


def required_recover_s(rate):
    if not rate:
        return RECOVER_S_CAP
    need = RECOVER_SKIP_S + MIN_EXPECTED / rate
    need = int(ROUND_TO * -(-need // ROUND_TO))       # round up to ROUND_TO
    return max(RECOVER_S_DEFAULT, min(RECOVER_S_CAP, need))


def table():
    with open(AUDIT) as f:
        audit = json.load(f)
    out = {}
    for tgt, d in sorted(audit["targets"].items()):
        rate = d["min"]
        need = required_recover_s(rate)
        expected_default = rate * (RECOVER_S_DEFAULT - RECOVER_SKIP_S)
        row = {"rate_min": rate, "expected_at_default": round(expected_default, 2),
               "recover_s": need, "reason": "sample count" if need > RECOVER_S_DEFAULT else "-"}
        if tgt in RECONNECT_LAG_TARGETS:
            floor = RECONNECT_LAG_TARGETS[tgt]
            if floor > row["recover_s"]:
                row["recover_s"] = floor
                row["reason"] = "reconnect lag (F-4), measurement target"
        row["expected_at_new"] = round(rate * (row["recover_s"] - RECOVER_SKIP_S), 2)
        out[tgt] = row
    return out


def apply_to_recipe(tbl, path=RECIPE):
    with open(path) as f:
        rows = list(csv.DictReader(f))
        fn = list(rows[0].keys())
    changed = []
    for r in rows:
        need = tbl.get(r["target"], {}).get("recover_s", RECOVER_S_DEFAULT)
        if need <= RECOVER_S_DEFAULT:
            continue
        ov = json.loads(r["cycle_override"]) if r["cycle_override"] else {}
        if ov.get("recover_s") == need:
            continue
        ov["recover_s"] = need
        r["cycle_override"] = json.dumps(ov, separators=(",", ":"))
        changed.append((r["card_id"], need))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fn, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    tbl = table()
    print(f"{'target':18s} {'min rate/s':>10s} {'exp@60':>7s} {'recover_s':>9s} {'exp@new':>8s}  reason")
    for t, r in tbl.items():
        print(f"{t:18s} {r['rate_min']:>10.4f} {r['expected_at_default']:>7.2f} "
              f"{r['recover_s']:>9d} {r['expected_at_new']:>8.2f}  {r['reason']}")
    if a.apply:
        changed = apply_to_recipe(tbl)
        print(f"\nrecipe.csv: {len(changed)} card(s) given recover_s")
        for cid, n in changed:
            print(f"  {cid} -> recover_s={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
