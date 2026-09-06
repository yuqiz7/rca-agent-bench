#!/usr/bin/env python3
"""readme_check.py -- keep every number in README.md tied to a repo fact.

README carries numbers that a reader will check against the repo, so none of them
is typed by hand. Each one sits between markers:

    <!-- GEN:key -->rendered text<!-- /GEN -->

`--check` recomputes every key and compares it with what README says, exit 1 on
any mismatch. `--write` rewrites the marker bodies in place. This is the light
version of the make_report.py --check precedent: same contract, no report.

Values are read from **HEAD, not the working tree** (`git show HEAD:<path>`).
A batch runner writing scenarios/ or an eval writing artifacts/ moves those
numbers minute by minute while it runs; README describes the committed repo, and
CI checks out exactly that, so HEAD is the only source that makes --check mean
the same thing on both sides. Untracked run directories are invisible here on
purpose.

Two keys drift with every commit (`commits`, `code_loc`); they carry a tolerance
and are rendered with a leading ~. `commits` is skipped in a shallow clone,
where the count is an artifact of fetch depth.

KEY SOURCES -- each line is the same locator docs/evidence_audit.md cites.

  containers            scripts/maintenance/wakeup.sh, the `[ "$RUNNING" = N ]` gate     (A1)
  injectable_targets    docs/fault_schema.md, the `target_enum` code block               (A1)
  primitive_scripts     count of scripts/primitives/*.sh                                 (A2)
  fault_classes         distinct `class` in scripts/scenarios/recipe.csv                 (A2)
  probe_gates           production.probe keys injected/symptom/recovered in scenarios/   (A4)
  baseline_window_s     scripts/runner/run_batch.py BASELINE_LOOKBACK_S                  (A4)
  recover_window_cards  recipe.csv rows whose cycle_override sets recover_s              (A4)
  evidence_files        tracked files in evidence/<card>/ plus the gitignored generated ones (A5)
  evidence_hashed       scripts/evidence/pack.py FIVE (the sha256-manifested subset)     (A5)
  alert_rules           len(RULES) in scripts/evidence/detect.py                         (A6)
  p95_floor_ms          scripts/evidence/detect.py METHOD_P95_MIN_DELTA_MS               (A6)
  recipe_cards          rows in scripts/scenarios/recipe.csv                             (A3)
  instock_cards         scenarios/*.yaml with production.probe.verdict == passed         (A3)
  instock_by_class      same set, broken down by `class`                                 (A3)
  valkey_blocked        recipe rows targeting valkey-cart that are not in stock          (O-P2-23)
  eval_set_size         len(scripts/baselines/cardset_all43.json)                        (C4)
  holdout_set_size      len(scripts/baselines/cardset_holdout16.json)                    (C3)
  eval_card_points      100 / eval_set_size -- one card's weight in the table        (C3, C4)
  holdout_card_points   100 / holdout_set_size                                       (C3, C4)
  agent_model           scripts/agent/config.yaml models.primary                         (B1)
  agent_tools           tool_schemas() names in scripts/agent/evidence_tools.py, minus submit (B1)
  agent_guards          run_agent.py: 3 counters + 2 terminated breakers                 (B2)
  max_steps             scripts/agent/config.yaml run.max_steps                          (B2)
  cost_cap_usd          scripts/agent/config.yaml run.max_usd_per_card                   (B2)
  devset_size           card count in artifacts/agent_runs/devset_20260828/summary.json  (B4)
  devset_before/after   top-1 in devset_20260828 / devset_v2_20260828 report.md          (B4)
  holdout16.<arm>.*     artifacts/agent_runs/merged_holdout16_20260906/report.md table   (C3, C5)
  all43.<arm>.*         artifacts/agent_runs/merged_all43_20260906/report.md table       (C4, C5)
  rules_overfit_*       baseline1.json of baselines_20260828 (class-aligned) vs
                        merged_holdout16_20260906, regraded with run_eval.grade's rule   (C6)
  ci_gates              `gate N:` steps in .github/workflows/ci.yml                      (D1)
  audit_paths/fixes/replay  docs/probe_audit.md, the 结论 statistics line                (D2)
  batch_hours/count     first-to-last timestamp of each artifacts/batches/*.log          (D4)
  api_spend/api_runs    cost_usd of every per-card json under artifacts/agent_runs/,
                        plus each row of baseline1/baseline2.json, merged_* excluded     (E1)
  commits               git rev-list --count HEAD (tolerance 5; skipped when shallow)    (E2)
  code_loc              wc -l of scripts/*.py|sh, tests/*.py, tools/*.py at HEAD         (E2)
  docs_loc              wc -l of docs/*.md at HEAD                                       (E2)
"""
import argparse
import collections
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
README = os.path.join(REPO, "README.md")

MARKER = re.compile(r"<!-- GEN:([A-Za-z0-9_.]+) -->(.*?)<!-- /GEN -->", re.S)


# ---------------------------------------------------------------- git access

def git(*args):
    out = subprocess.run(["git", "-C", REPO] + list(args),
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def read(path):
    """File content at HEAD. Never the working tree -- see the module docstring."""
    return git("show", f"HEAD:{path}")


def ls(prefix):
    return [p for p in git("ls-tree", "-r", "--name-only", "HEAD", prefix).split("\n") if p]


def is_shallow():
    try:
        return git("rev-parse", "--is-shallow-repository").strip() == "true"
    except RuntimeError:
        return False


def yaml_load(text):
    import yaml
    return yaml.safe_load(text)


# ------------------------------------------------------------ small readers

def _const(path, name, cast=float):
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", read(path), re.M)
    if not m:
        raise RuntimeError(f"{name} not found in {path}")
    return cast(m.group(1))


def recipe_rows():
    return list(csv.DictReader(io.StringIO(read("scripts/scenarios/recipe.csv"))))


_SCEN = None


def scenarios():
    """Every card at HEAD, parsed once."""
    global _SCEN
    if _SCEN is None:
        _SCEN = {}
        for p in ls("scenarios/"):
            if p.endswith(".yaml"):
                _SCEN[os.path.basename(p)[:-5]] = yaml_load(read(p))
    return _SCEN


def instock():
    return {cid: c for cid, c in scenarios().items()
            if ((c.get("production") or {}).get("probe") or {}).get("verdict") == "passed"}


def config():
    return yaml_load(read("scripts/agent/config.yaml"))


# ------------------------------------------------------- eval report parsing

ARM_KEYS = [("关键词", "rules"), ("单轮", "single_shot"), ("agent", "agent")]


def arm_key(name):
    """Map a report's arm label to a stable key. A haiku arm gets a haiku_ prefix
    so a cross-model report widens the key set instead of colliding with it."""
    base = None
    for needle, key in ARM_KEYS:
        if needle in name:
            base = key
    if base is None:
        return None
    return ("haiku_" + base) if "haiku" in name.lower() else base


def report_table(run_dir):
    """The 六列 arm table of a comparison report: {arm_key: {metric: text}}."""
    text = read(f"artifacts/agent_runs/{run_dir}/report.md")
    body = text.split("## 四指标对照", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in body.strip().split("\n"):
        if not line.startswith("|") or "---" in line or "top-1" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        key = arm_key(cells[0])
        if key is None:
            continue
        clean = [c.replace("**", "").replace("（", " (").replace("）", ")") for c in cells]
        out[key] = {"top1": clean[1], "service": clean[2], "steps": clean[3],
                    "cost": clean[4], "p95": clean[5]}
    return out


def report_top1(run_dir):
    """top-1 percentage from a single-arm agent report (the 四指标 block)."""
    text = read(f"artifacts/agent_runs/{run_dir}/report.md")
    m = re.search(r"top-1[^|]*\|\s*([0-9.]+)%", text)
    return float(m.group(1))


# ------------------------------------------------------------ rules overfit

def grade_rows(rows):
    """(top1_ok, class) per row. Mirrors run_eval.grade (run_eval.py:40); it is
    re-implemented rather than imported because run_eval pulls in run_agent, and
    CI deliberately has no anthropic SDK installed."""
    cards = scenarios()
    out = []
    for r in rows:
        gt = cards[r["card_id"]]["ground_truth"]
        ans = r.get("answer") or {}
        svc = (ans.get("service") or "").lower()
        aliases = [a.lower() for a in (gt.get("aliases") or [])]
        ok = (svc == gt["service"].lower() or svc in aliases) and \
             ans.get("fault_type") == gt["class"]
        out.append((bool(ok), cards[r["card_id"]]["class"]))
    return out


def baseline1(run_dir):
    return json.loads(read(f"artifacts/agent_runs/{run_dir}/baseline1.json"))


def overfit():
    """The rules arm on cards it was tuned on vs cards it has never seen, with the
    27-card set restricted to the classes the holdout actually contains -- the
    unaligned delta would count 'the holdout has no misconfig' as overfitting."""
    held = grade_rows(baseline1("merged_holdout16_20260906"))
    classes = {c for _, c in held}
    seen = [g for g in grade_rows(baseline1("baselines_20260828")) if g[1] in classes]
    s_ok, s_n = sum(1 for ok, _ in seen if ok), len(seen)
    h_ok, h_n = sum(1 for ok, _ in held if ok), len(held)
    bh_s = [ok for ok, c in seen if c == "blackhole"]
    bh_h = [ok for ok, c in held if c == "blackhole"]
    return {"seen": (s_ok, s_n), "held": (h_ok, h_n),
            "delta": 100.0 * h_ok / h_n - 100.0 * s_ok / s_n,
            "bh_seen": (sum(bh_s), len(bh_s)), "bh_held": (sum(bh_h), len(bh_h))}


# ----------------------------------------------------------------- spend, time

def batch_logs():
    import datetime
    total, count = 0.0, 0
    for p in ls("artifacts/batches/"):
        if not p.endswith(".log"):
            continue
        stamps = [re.match(r"\[([0-9T:\-]+Z)\]", line) for line in read(p).split("\n")]
        stamps = [datetime.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                  for m in stamps if m]
        if len(stamps) < 2:
            continue
        total += (stamps[-1] - stamps[0]).total_seconds()
        count += 1
    return total / 3600.0, count


def api_spend():
    """Every metered single-card run: one json per agent card, one row per card in
    each baseline arm. merged_* directories re-copy rows already counted."""
    spend, runs = 0.0, 0
    for p in ls("artifacts/agent_runs/"):
        parts = p.split("/")
        if not p.endswith(".json") or parts[2].startswith("merged_"):
            continue
        doc = json.loads(read(p))
        if isinstance(doc, dict) and "card_id" in doc and "cost_usd" in doc:
            spend += doc["cost_usd"]
            runs += 1
        elif isinstance(doc, list) and os.path.basename(p).startswith("baseline"):
            for row in doc:
                spend += row.get("cost_usd") or 0.0
                runs += 1
    return spend, runs


def loc(patterns):
    total = 0
    for pat in patterns:
        for p in git("ls-files", "--", pat).split("\n"):
            if p:
                total += len(read(p).split("\n")) - 1
    return total


# --------------------------------------------------------------------- keys

def compute():
    v = {}
    rows = recipe_rows()
    cards = scenarios()
    stock = instock()
    cfg = config()

    # testbed and injection
    v["containers"] = re.search(r'"\$RUNNING"\s*=\s*"(\d+)"',
                                read("scripts/maintenance/wakeup.sh")).group(1)
    enum = read("docs/fault_schema.md").split("### `target_enum`", 1)[1].split("```")[1]
    v["injectable_targets"] = str(len([t for t in re.split(r"[,\s]+", enum) if t]))
    v["primitive_scripts"] = str(len([p for p in ls("scripts/primitives/") if p.endswith(".sh")]))
    v["fault_classes"] = str(len({r["class"] for r in rows}))
    probe = (next(iter(stock.values()))["production"]["probe"])
    v["probe_gates"] = str(len([k for k in ("injected", "symptom", "recovered") if k in probe]))
    v["baseline_window_s"] = str(int(_const("scripts/runner/run_batch.py", "BASELINE_LOOKBACK_S")))
    v["recover_window_cards"] = str(len([r for r in rows if "recover_s" in (r["cycle_override"] or "")]))
    pack_files = [p for p in ls("evidence/crash-ad-01/")]
    ignored = re.findall(r"^evidence/\*\*/(\S+)$", read(".gitignore"), re.M)
    v["evidence_files"] = str(len(pack_files) + len(ignored))
    v["evidence_hashed"] = str(len(re.search(r"^FIVE = \[(.*?)\]", read("scripts/evidence/pack.py"),
                                             re.M | re.S).group(1).split(",")))
    rules = read("scripts/evidence/detect.py").split("RULES = {", 1)[1].split("\n}", 1)[0]
    v["alert_rules"] = str(len(re.findall(r'^\s*"[a-z0-9_]+":', rules, re.M)))
    v["p95_floor_ms"] = str(int(_const("scripts/evidence/detect.py", "METHOD_P95_MIN_DELTA_MS")))

    # cards
    v["recipe_cards"] = str(len(rows))
    v["instock_cards"] = str(len(stock))
    by = collections.Counter(c["class"] for c in stock.values())
    v["instock_by_class"] = " / ".join(f"{k} {by[k]}" for k in sorted(by))
    v["valkey_blocked"] = str(len([r for r in rows
                                   if r["target"] == "valkey-cart" and r["card_id"] not in stock]))
    v["eval_set_size"] = str(len(json.loads(read("scripts/baselines/cardset_all43.json"))))
    v["holdout_set_size"] = str(len(json.loads(read("scripts/baselines/cardset_holdout16.json"))))
    # What one card is worth in each table -- the honest unit of both result sets.
    v["eval_card_points"] = "%.1f" % (100.0 / int(v["eval_set_size"]))
    v["holdout_card_points"] = "%.1f" % (100.0 / int(v["holdout_set_size"]))

    # agent
    v["agent_model"] = cfg["models"]["primary"]
    names = re.findall(r'"name": "([a-z_]+)"', read("scripts/agent/evidence_tools.py"))
    v["agent_tools"] = str(len([n for n in dict.fromkeys(names) if n != "submit"]))
    agent_src = read("scripts/agent/run_agent.py")
    # Three guards keep a counter, two circuit breakers land in `terminated`.
    guards = [k for k in ("validation_rejects", "tool_retries", "nudges") if k in agent_src]
    guards += [k for k in ("max_steps", "cost_cap")
               if re.search(rf'terminated"?\]?\s*[=:]\s*"{k}"', agent_src)]
    v["agent_guards"] = str(len(guards))
    v["max_steps"] = str(cfg["run"]["max_steps"])
    v["cost_cap_usd"] = f"{cfg['run']['max_usd_per_card']:.2f}"
    v["devset_size"] = str(len(json.loads(
        read("artifacts/agent_runs/devset_20260828/summary.json"))["rows"]))
    v["devset_before"] = f"{report_top1('devset_20260828'):.1f}%"
    v["devset_after"] = f"{report_top1('devset_v2_20260828'):.1f}%"

    # results
    for prefix, run_dir in (("holdout16", "merged_holdout16_20260906"),
                            ("all43", "merged_all43_20260906")):
        for arm, metrics in report_table(run_dir).items():
            for metric, text in metrics.items():
                v[f"{prefix}.{arm}.{metric}"] = text

    o = overfit()
    v["rules_overfit_delta"] = f"{o['delta']:.1f}"
    v["rules_overfit_seen"] = "{}/{}".format(*o["seen"])
    v["rules_overfit_held"] = "{}/{}".format(*o["held"])
    v["rules_overfit_blackhole"] = "{}/{} to {}/{}".format(*o["bh_seen"], *o["bh_held"])

    # engineering
    v["ci_gates"] = str(len(re.findall(r'name: "gate \d', read(".github/workflows/ci.yml"))))
    stats = re.search(r"\*\*统计\*\*：\*\*(\d+) 条\*\*路径中 \*\*正确 (\d+)、可接受 (\d+)、错误 (\d+)",
                      read("docs/probe_audit.md"))
    v["audit_paths"], v["audit_fixes"] = stats.group(1), stats.group(4)
    v["audit_replay_cards"] = re.search(r"(\d+) 张在库卡的四个窗口快照",
                                        read("docs/probe_audit.md")).group(1)
    hours, batches = batch_logs()
    v["batch_hours"] = f"{hours:.1f}"
    v["batch_count"] = str(batches)
    spend, runs = api_spend()
    v["api_spend"] = f"${spend:.2f}"
    v["api_runs"] = str(runs)

    v["commits"] = "~" + git("rev-list", "--count", "HEAD").strip()
    v["code_loc"] = "~%.1fk" % (loc(["scripts/*.py", "scripts/*.sh",
                                     "tests/*.py", "tools/*.py"]) / 1000.0)
    v["docs_loc"] = "~%.1fk" % (loc(["docs/*.md"]) / 1000.0)
    return v


# Keys whose value moves on every commit. --check compares the numbers inside
# them with slack instead of exactly, so an ordinary commit does not turn CI red.
TOLERANCE = {"commits": 5.0, "code_loc": 0.3, "docs_loc": 0.3}
SHALLOW_SKIP = {"commits"}


def _num(text):
    m = re.search(r"-?[0-9]+(?:\.[0-9]+)?", text)
    return float(m.group(0)) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare, exit 1 on mismatch")
    ap.add_argument("--write", action="store_true", help="rewrite marker bodies in place")
    ap.add_argument("--list", action="store_true", help="print every computed key")
    a = ap.parse_args()

    values = compute()
    if a.list:
        for k in sorted(values):
            print(f"{k}\t{values[k]}")
        return 0

    text = open(README, encoding="utf-8").read()
    shallow = is_shallow()

    if a.write:
        missing = []

        def sub(m):
            key = m.group(1)
            if key not in values:
                missing.append(key)
                return m.group(0)
            return f"<!-- GEN:{key} -->{values[key]}<!-- /GEN -->"

        out = MARKER.sub(sub, text)
        if missing:
            print("unknown keys in README: " + ", ".join(sorted(set(missing))), file=sys.stderr)
            return 1
        if out != text:
            open(README, "w", encoding="utf-8").write(out)
            print("README.md updated")
        else:
            print("README.md already current")
        return 0

    bad, seen = [], set()
    for m in MARKER.finditer(text):
        key, got = m.group(1), m.group(2)
        seen.add(key)
        if key not in values:
            bad.append(f"  {key}: no such key")
            continue
        want = values[key]
        if got == want:
            continue
        if key in SHALLOW_SKIP and shallow:
            continue
        tol = TOLERANCE.get(key)
        if tol is not None:
            g, w = _num(got), _num(want)
            if g is not None and w is not None and abs(g - w) <= tol:
                continue
        bad.append(f"  {key}: README has {got!r}, repo says {want!r}")
    if not seen:
        bad.append("  no GEN markers found in README.md")
    if bad:
        print("readme_check: README.md is out of date with the repo", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print("run: python scripts/tools/readme_check.py --write", file=sys.stderr)
        return 1
    print(f"readme_check: {len(seen)} markers match the repo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
