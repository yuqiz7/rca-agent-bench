#!/usr/bin/env python3
"""readme_check.py -- keep every number in README.md tied to a repo fact.

Two mechanisms, split by what GitHub's markdown parser tolerates.

  BLOCK MARKERS. `<!-- GEN:key -->` and `<!-- /GEN -->` each alone on a line, one
  blank line inside each of them, pure markdown between. Anything sharing a line
  with an HTML comment starts an HTML block, and the markdown in that block --
  images, bold, backticks -- renders as literal text. That is what the first
  version of this file did to the badge row, so markers are now block-level only
  and used for four things: the badge row, the hero table, the figure line and
  the results table.

  INLINE VALUES. docs/readme_values.json maps a key to the exact phrase that must
  appear somewhere in README ("45 in stock", "7 detector rules"). --check asserts
  the phrase is there and that a superseded phrase is gone; --write rewrites the
  old phrase into the new one. The prose carries no comments at all.

Everything is read from HEAD, not the working tree (`git show HEAD:<path>`). A
batch runner writing scenarios/ or an eval writing artifacts/ moves those numbers
minute by minute; README describes the committed repo and CI checks out exactly
that, so HEAD is the only source that makes --check mean the same thing on both
sides.

A few keys drift on every commit (repo scale, metered spend). They are rendered
coarsely and carry a numeric tolerance, so an ordinary commit does not turn CI
red; `commits` is skipped entirely in a shallow clone, where the count is an
artifact of fetch depth.

KEY SOURCES -- each line is the locator docs/evidence_audit.md cites.

  containers            scripts/maintenance/wakeup.sh, the `[ "$RUNNING" = N ]` gate     (A1)
  targets               docs/fault_schema.md, the `target_enum` code block               (A1)
  primitives            count of scripts/primitives/*.sh                                 (A2)
  classes               distinct `class` in scripts/scenarios/recipe.csv                 (A2)
  baseline_window       scripts/runner/run_batch.py BASELINE_LOOKBACK_S                  (A4)
  recover_cards         recipe.csv rows whose cycle_override sets recover_s              (A4)
  evidence_files        tracked files in evidence/<card>/ plus the gitignored ones       (A5)
  evidence_hashed       scripts/evidence/pack.py FIVE, the sha256-manifested subset      (A5)
  alert_rules           len(RULES) in scripts/evidence/detect.py                         (A6)
  p95_floor             scripts/evidence/detect.py METHOD_P95_MIN_DELTA_MS               (A6)
  recipe_cards          rows in scripts/scenarios/recipe.csv                             (A3)
  instock               scenarios/*.yaml with production.probe.verdict == passed         (A3)
  instock_by_class      the same set, broken down by `class`                             (A3)
  valkey_blocked        recipe rows targeting valkey-cart that are not in stock     (O-P2-23)
  eval_set              len(scripts/baselines/cardset_all43.json)                        (C4)
  holdout_set           len(scripts/baselines/cardset_holdout16.json)                    (C3)
  card_points           100 / set size -- one card's weight in each table            (C3, C4)
  model                 scripts/agent/config.yaml models.primary                         (B1)
  tools                 tool_schemas() names in evidence_tools.py, minus submit          (B1)
  guards                run_agent.py: 3 counters plus 2 `terminated` breakers            (B2)
  max_steps, cost_cap   scripts/agent/config.yaml run.*                                  (B2)
  devset, devset_move   artifacts/agent_runs/devset_20260828 and devset_v2_20260828      (B4)
  badges, hero_*        fivearm_holdout16_20260906/report.md, the arm table      (C3, C5, C7)
  table_all43           fivearm_all43_20260906/report.md, the arm table          (C4, C5, C7)
  haiku_*, sonnet_spend the same five-arm table: steps ratio, totals, top-1 gap       (C7)
  overfit_*             baseline1.json of baselines_20260828, class-aligned against
                        merged_holdout16_20260906, regraded with run_eval.grade's rule   (C6)
  ci_gates              `gate N:` steps in .github/workflows/ci.yml                      (D1)
  service_endpoints     paths in tests/fixtures/openapi.json, the committed contract      (D5)
  service_budget        RCA_DAILY_BUDGET_USD in docker-compose.service.yml                (D5)
  audit_*               docs/probe_audit.md, the statistics line of the conclusion       (D2)
  batches, machine      first-to-last timestamp of each artifacts/batches/*.log          (D4)
  spend                 cost_usd of every per-card json under artifacts/agent_runs/,
                        plus each row of baseline1/baseline2.json, merged_* excluded     (E1)
  scale                 git rev-list --count HEAD, wc -l of the tracked code at HEAD     (E2)
  f1_*                  the numbers in the F-1 heading of docs/open_items.md              (D3)
  more_findings         `## F-N` headings in docs/open_items.md, minus the 3 listed       (D3)
  failure_modes         `### 1.N` headings in docs/findings.md                            (D3)
  walkthrough_*         one frozen run json plus its card's ground truth
  figure_arms           markdown image line; the png is redrawn by make_figures.py
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
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
README = os.path.join(REPO, "README.md")
VALUES_JSON = os.path.join(REPO, "docs", "readme_values.json")

MARKER = re.compile(r"<!-- GEN:([A-Za-z0-9_.]+) -->(.*?)<!-- /GEN -->", re.S)

# The card the README walks through: on the holdout set, answered in 7 steps, and
# wrong in both baselines. Chosen for the shape of the run, not for its score.
WALKTHROUGH_CARD = "blackhole-shipping-01"
WALKTHROUGH_RUN = "holdout11_agent_20260906"

BADGE_GREY, BADGE_ACCENT = "555", "1f4e5f"

# F items the Findings section spells out; the rest are summed into "N more".
LISTED_FINDINGS = 3
NUMBER_WORD = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
               6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}

# Keys whose value moves without anyone editing code. --check compares the first
# number in them with slack instead of demanding the exact string.
TOLERANCE = {"scale": 8.0, "spend": 2.0}
SHALLOW_SKIP = {"scale"}


# ---------------------------------------------------------------- git access

def git(*args):
    out = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True)
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


# ------------------------------------------------------------- small readers

def _const(path, name, cast=float):
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", read(path), re.M)
    if not m:
        raise RuntimeError(f"{name} not found in {path}")
    return cast(m.group(1))


def recipe_rows():
    return list(csv.DictReader(io.StringIO(read("scripts/scenarios/recipe.csv"))))


_SCEN = None


def scenarios():
    global _SCEN
    if _SCEN is None:
        _SCEN = {}
        for p in ls("scenarios/"):
            if p.endswith(".yaml"):
                card = yaml_load(read(p))
                if isinstance(card, dict) and "card_id" in card:
                    _SCEN[card["card_id"]] = card
    return _SCEN


def instock():
    return {cid: c for cid, c in scenarios().items()
            if ((c.get("production") or {}).get("probe") or {}).get("verdict") == "passed"}


def config():
    return yaml_load(read("scripts/agent/config.yaml"))


# ------------------------------------------------------- eval report parsing

# Arm labels are Chinese in the reports and one config per arm in the README. The
# names follow evidence_audit C7: the cheap arms run stock because haiku 4.5
# rejects the thinking and effort parameters the tuned arms use, so "stock" and
# "tuned" are part of the arm's name, not a footnote.
ARM_LABEL = {"rules": "rules, no model",
             "single_shot_sonnet": "single-shot sonnet",
             "single_shot_haiku": "single-shot haiku (stock)",
             "agent_haiku": "agent-haiku (stock)",
             "agent_sonnet": "agent-sonnet (tuned)"}


def arm_key(name):
    """Report label to stable key."""
    low = name.lower()
    family = "haiku" if "haiku" in low else "sonnet"
    if "规则" in name or "关键词" in name:
        return "rules"
    if "agent" in low:
        return f"agent_{family}"
    if "单轮" in name:
        return f"single_shot_{family}"
    return None


def report_table(run_dir):
    """The arm table of a comparison report: {arm_key: {metric: text}}. Both the
    three-arm and the five-arm reports open their table with `| 臂 |`, and their
    first six columns line up, so one parser covers both."""
    lines = read(f"artifacts/agent_runs/{run_dir}/report.md").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("| 臂 |"))
    out = collections.OrderedDict()
    for line in lines[start + 1:]:
        if not line.startswith("|"):
            break
        if "---" in line:
            continue
        cells = [c.strip().replace("**", "").replace("（", " (").replace("）", ")")
                 for c in line.strip("|").split("|")]
        key = arm_key(cells[0])
        if key is None:
            continue
        row = {"top1": cells[1], "service": cells[2], "steps": cells[3],
               "cost": cells[4], "p95": cells[5]}
        if len(cells) > 6:
            row["total"] = cells[6]
        out[key] = row
    return out


def report_top1(run_dir):
    text = read(f"artifacts/agent_runs/{run_dir}/report.md")
    return float(re.search(r"top-1[^|]*\|\s*([0-9.]+)%", text).group(1))


def pct_of(text):
    return float(re.search(r"([0-9.]+)%", text).group(1))


def usd_of(text):
    return float(re.search(r"\$([0-9.]+)", text).group(1))


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
    """The rules arm on cards it was tuned on against cards it has never seen, the
    27-card set restricted to the classes the holdout contains -- the unaligned
    delta would count "the holdout has no misconfig" as overfitting."""
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


# ---------------------------------------------------------------- spend, time

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
    each baseline arm. merged_* re-copies rows already counted."""
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


# ------------------------------------------------------------------- badges

def shield(label, message, color):
    """One shields.io static badge. `-` and `_` escaped as shields requires."""
    def esc(t):
        return urllib.parse.quote(t.replace("-", "--").replace("_", "__"), safe="")
    return (f"![{label}](https://img.shields.io/badge/"
            f"{esc(label)}-{esc(message)}-{color}?style=flat-square)")


# --------------------------------------------------------------------- keys

def compute():
    """Every value the README may quote: block bodies first, inline phrases after."""
    rows = recipe_rows()
    stock = instock()
    cfg = config()
    hold = report_table("fivearm_holdout16_20260906")
    all43 = report_table("fivearm_all43_20260906")
    o = overfit()
    hours, batches = batch_logs()
    spend, _runs = api_spend()
    commits = int(git("rev-list", "--count", "HEAD").strip())
    code_k = loc(["scripts/*.py", "scripts/*.sh", "tests/*.py", "tools/*.py"]) / 1000.0

    n_recipe = len(rows)
    n_stock = len(stock)
    n_hold = len(json.loads(read("scripts/baselines/cardset_holdout16.json")))
    n_eval = len(json.loads(read("scripts/baselines/cardset_all43.json")))
    agent = hold["agent_sonnet"]
    top1 = f"{pct_of(agent['top1']):.1f}%"
    cost = "${:.3f}".format(usd_of(agent["cost"]))
    p95 = "{:.0f} s".format(float(re.search(r"([0-9.]+)s", agent["p95"]).group(1)))
    gain = pct_of(agent["top1"]) - pct_of(hold["single_shot_sonnet"]["top1"])

    v = {}

    # --- block bodies -------------------------------------------------------
    v["badges"] = " ".join([
        shield("holdout top-1", top1, BADGE_ACCENT),
        shield("cards", f"{n_stock} in stock of {n_recipe}", BADGE_GREY),
        shield("cost", f"{cost} per card", BADGE_GREY),
    ])
    v["hero_table"] = "\n".join([
        f"| **{top1}** | **{gain:+.1f} pts** | **{cost} / {p95}** |",
        "| :--- | :--- | :--- |",
        f"| holdout top-1, agent on {n_hold} unseen cards "
        f"| over the single-shot LLM on the same cards | per diagnosis, wall clock |",
    ])
    v["figure_arms"] = "![Top-1 on the {} unseen holdout cards: {}]({})".format(
        n_hold, ", ".join("{} {}".format(ARM_LABEL[k], m["top1"].split(" ")[0])
                          for k, m in hold.items()),
        "docs/figures/arms_holdout16.png")
    v["figure_pipeline"] = ("![Pipeline: testbed, fault primitives, scenario cards, probe "
                            "verdicts, evidence packs, three arms, harness, findings]"
                            "(docs/figures/pipeline.png)")
    header = ["| arm | top-1 | service-only | steps | cost/card | p95 |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    v["table_all43"] = "\n".join(header + [
        "| {} | {} | {} | {} | {} | {} |".format(
            ARM_LABEL.get(arm, arm), m["top1"], m["service"], m["steps"], m["cost"], m["p95"])
        for arm, m in all43.items()])

    # --- inline phrases -----------------------------------------------------
    by = collections.Counter(c["class"] for c in stock.values())
    enum = read("docs/fault_schema.md").split("### `target_enum`", 1)[1].split("```")[1]
    rules_block = read("scripts/evidence/detect.py").split("RULES = {", 1)[1].split("\n}", 1)[0]
    agent_src = read("scripts/agent/run_agent.py")
    guards = [k for k in ("validation_rejects", "tool_retries", "nudges") if k in agent_src]
    guards += [k for k in ("max_steps", "cost_cap")
               if re.search(rf'terminated"?\]?\s*[=:]\s*"{k}"', agent_src)]
    names = re.findall(r'"name": "([a-z_]+)"', read("scripts/agent/evidence_tools.py"))
    pack_files = ls("evidence/crash-ad-01/")
    ignored = re.findall(r"^evidence/\*\*/(\S+)$", read(".gitignore"), re.M)
    stats = re.search(r"\*\*统计\*\*：\*\*(\d+) 条\*\*路径中 \*\*正确 (\d+)、可接受 (\d+)、错误 (\d+)",
                      read("docs/probe_audit.md"))
    probe = next(iter(stock.values()))["production"]["probe"]

    v["containers"] = "{} containers".format(
        re.search(r'"\$RUNNING"\s*=\s*"(\d+)"', read("scripts/maintenance/wakeup.sh")).group(1))
    v["targets"] = "{} injection targets".format(len([t for t in re.split(r"[,\s]+", enum) if t]))
    v["primitives"] = "{} primitive scripts".format(
        len([p for p in ls("scripts/primitives/") if p.endswith(".sh")]))
    v["classes"] = "{} fault classes".format(len({r["class"] for r in rows}))
    v["probes"] = "{} probes".format(
        len([k for k in ("injected", "symptom", "recovered") if k in probe]))
    v["baseline_window"] = "{:.0f}-second baseline window".format(
        _const("scripts/runner/run_batch.py", "BASELINE_LOOKBACK_S"))
    v["recover_cards"] = "{} cards carry a longer recovery window".format(
        len([r for r in rows if "recover_s" in (r["cycle_override"] or "")]))
    v["evidence_files"] = "{} files per card".format(len(pack_files) + len(ignored))
    v["evidence_hashed"] = "{} of them carry a sha256".format(
        len(re.search(r"^FIVE = \[(.*?)\]", read("scripts/evidence/pack.py"),
                      re.M | re.S).group(1).split(",")))
    v["alert_rules"] = "{} detector rules".format(
        len(re.findall(r'^\s*"[a-z0-9_]+":', rules_block, re.M)))
    v["p95_floor"] = "{:.0f} ms floor".format(
        _const("scripts/evidence/detect.py", "METHOD_P95_MIN_DELTA_MS"))

    v["recipe_cards"] = f"{n_recipe} recipe rows"
    v["instock"] = f"{n_stock} in stock"
    v["instock_by_class"] = " / ".join(f"{k} {by[k]}" for k in sorted(by))
    v["valkey_blocked"] = "{} cache cards".format(
        len([r for r in rows if r["target"] == "valkey-cart" and r["card_id"] not in stock]))
    v["eval_set"] = f"{n_eval} cards"
    v["holdout_set"] = f"{n_hold} unseen cards"
    v["card_points"] = "{:.2f} points".format(100.0 / n_hold)
    v["eval_card_points"] = "{:.2f} points".format(100.0 / n_eval)

    v["model"] = cfg["models"]["primary"]
    v["tools"] = "{} read-only tools".format(len([n for n in dict.fromkeys(names) if n != "submit"]))
    v["guards"] = f"{len(guards)} guards"
    v["max_steps"] = "{} steps".format(cfg["run"]["max_steps"])
    v["cost_cap"] = "${:.2f} per card".format(cfg["run"]["max_usd_per_card"])
    v["devset"] = "{}-card dev set".format(
        len(json.loads(read("artifacts/agent_runs/devset_20260828/summary.json"))["rows"]))
    v["devset_move"] = "{:.1f}% to {:.1f}%".format(
        report_top1("devset_20260828"), report_top1("devset_v2_20260828"))

    # The sentence carries the direction ("falls"), the key carries the size.
    v["overfit_seen"] = "{}/{}".format(*o["seen"])
    v["overfit_held"] = "{}/{}".format(*o["held"])
    v["overfit_delta"] = "{:.1f} points".format(abs(o["delta"]))
    v["overfit_blackhole"] = "{}/{} to {}/{}".format(*o["bh_seen"], *o["bh_held"])

    v["ci_gates"] = "{} offline gates".format(
        len(re.findall(r'name: "gate \d', read(".github/workflows/ci.yml"))))

    # The evaluation service (决策 036). Both come from files the API cannot change
    # without changing: the OpenAPI snapshot is the committed contract and moves
    # only when gate 4 is deliberately re-baselined, and the daily budget is the
    # value the container actually runs with rather than the one the design names.
    v["service_endpoints"] = "{} endpoints".format(
        len(json.loads(read("tests/fixtures/openapi.json"))["paths"]))
    v["service_budget"] = "${:g} daily budget".format(float(re.search(
        r'RCA_DAILY_BUDGET_USD:\s*"([\d.]+)"',
        read("docker-compose.service.yml")).group(1)))
    v["audit_paths"] = f"{stats.group(1)} verdict paths"
    v["audit_fixes"] = f"{stats.group(4)} places"
    v["audit_replay"] = "{} cards".format(
        re.search(r"(\d+) 张在库卡的四个窗口快照", read("docs/probe_audit.md")).group(1))
    v["batches"] = f"{batches} batches"
    v["machine"] = f"{hours:.1f} hours"
    v["spend"] = "${:.0f} in metered API calls".format(spend)
    v["scale"] = "~{:.0f} commits and ~{:.0f}k lines of Python and shell".format(
        round(commits / 10.0) * 10, code_k)

    # Cross-configuration arms (evidence_audit C7): the cheap arm is not cheaper.
    v["haiku_steps_ratio"] = "{:.1f}\u00d7 the steps".format(
        float(all43["agent_haiku"]["steps"]) / float(all43["agent_sonnet"]["steps"]))
    v["haiku_spend"] = "${:.2f}".format(usd_of(all43["agent_haiku"]["total"]))
    v["sonnet_spend"] = "${:.2f}".format(usd_of(all43["agent_sonnet"]["total"]))
    v["haiku_gap"] = "{:.1f} points lower".format(
        pct_of(all43["agent_sonnet"]["top1"]) - pct_of(all43["agent_haiku"]["top1"]))

    # The Findings section lists 3 of the F items by hand; the rest are counted, so
    # the "N more" cannot drift when a finding is added.
    n_more = len(re.findall(r"^## F-\d", read("docs/open_items.md"), re.M)) - LISTED_FINDINGS
    v["more_findings"] = "{} more".format(NUMBER_WORD[n_more])
    v["failure_modes"] = "{} failure modes".format(NUMBER_WORD[
        len(re.findall(r"^### 1\.\d ", read("docs/findings.md"), re.M))].lower())

    f1 = re.search(r"^## F-1.*?(\d+)%.*?(\d+) s ", read("docs/open_items.md"), re.M)
    v["f1_rate"] = f"{f1.group(1)}% failure rate"
    v["f1_window"] = f"{f1.group(2)}-second window"

    run = json.loads(read(f"artifacts/agent_runs/{WALKTHROUGH_RUN}/{WALKTHROUGH_CARD}.json"))
    gt = scenarios()[WALKTHROUGH_CARD]["ground_truth"]
    v["walkthrough_card"] = WALKTHROUGH_CARD
    v["walkthrough_stats"] = "{} steps, {} tool calls, {:.0f} s, ${:.3f}".format(
        run["steps"], sum(len(s["tool_calls"]) for s in run["transcript"]),
        run["wall_s"], run["cost_usd"])
    v["walkthrough_verdict"] = "Submitted {service} / {fault_type}".format(**run["answer"]) + \
        " -- truth {} / {}".format(gt["service"], gt["class"])
    return v


BLOCK_KEYS = ("badges", "hero_table", "figure_arms", "figure_pipeline", "table_all43")


# ------------------------------------------------------------------ commands

def load_stored():
    if not os.path.exists(VALUES_JSON):
        return {}
    with open(VALUES_JSON, encoding="utf-8") as f:
        return {k: val for k, val in json.load(f).items() if not k.startswith("_")}


def save_stored(values):
    doc = {"_note": "generated by scripts/tools/readme_check.py --write; "
                    "each value is a phrase README must contain verbatim"}
    doc.update({k: v for k, v in sorted(values.items()) if k not in BLOCK_KEYS})
    with open(VALUES_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _num(text):
    m = re.search(r"-?[0-9]+(?:\.[0-9]+)?", text)
    return float(m.group(0)) if m else None


def redraw_figures(values):
    """--write also redraws docs/figures/. matplotlib is not a CI dependency, so a
    missing one is a warning: the committed png stays and --check still runs."""
    sys.path.insert(0, HERE)
    try:
        import make_figures
        for path in make_figures.regenerate(values):
            print(f"wrote {os.path.relpath(path, REPO)}")
    except ImportError as exc:                                     # noqa: BLE001
        print(f"figures not redrawn: {exc}", file=sys.stderr)


def check(text, values, stored, shallow):
    bad = []
    seen_markers = set()
    for m in MARKER.finditer(text):
        key, body = m.group(1), m.group(2)
        seen_markers.add(key)
        if key not in values:
            bad.append(f"  {key}: no such key")
        elif body.strip() != values[key].strip():
            bad.append(f"  {key}: block body differs from the repo")
        elif not (body.startswith("\n\n") and body.endswith("\n\n")):
            bad.append(f"  {key}: marker must sit on its own line, blank line inside")
    for key in BLOCK_KEYS:
        if key not in seen_markers:
            bad.append(f"  {key}: block marker missing from README")

    for key, want in sorted(values.items()):
        if key in BLOCK_KEYS:
            continue
        old = stored.get(key)
        if key in SHALLOW_SKIP and shallow:
            continue
        if key in TOLERANCE and old is not None:
            g, w = _num(old), _num(want)
            if g is not None and w is not None and abs(g - w) <= TOLERANCE[key]:
                want = old                      # inside tolerance: the page may lag
            else:
                bad.append(f"  {key}: README has {old!r}, repo says {want!r}")
                continue
        if want not in text:
            bad.append(f"  {key}: {want!r} does not appear in README")
        elif old and old != want and old in text:
            bad.append(f"  {key}: superseded {old!r} is still in README")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="compare, exit 1 on mismatch")
    ap.add_argument("--write", action="store_true", help="rewrite blocks and phrases in place")
    ap.add_argument("--list", action="store_true", help="print every computed value")
    a = ap.parse_args()

    values = compute()
    if a.list:
        for k in sorted(values):
            print(f"{k}\t{values[k]}")
        return 0

    text = open(README, encoding="utf-8").read()
    stored = load_stored()

    if a.write:
        redraw_figures(values)
        missing = []

        def sub(m):
            key = m.group(1)
            if key not in values:
                missing.append(key)
                return m.group(0)
            return f"<!-- GEN:{key} -->\n\n{values[key]}\n\n<!-- /GEN -->"

        out = MARKER.sub(sub, text)
        for key, want in values.items():
            old = stored.get(key)
            if key in BLOCK_KEYS or want == old:
                continue
            if old and old in out:
                out = out.replace(old, want)
            elif want not in out:
                missing.append(key)
        if missing:
            print("no place in README for: " + ", ".join(sorted(set(missing))), file=sys.stderr)
            return 1
        if out != text:
            open(README, "w", encoding="utf-8").write(out)
            print("README.md updated")
        else:
            print("README.md already current")
        save_stored(values)
        return 0

    bad = check(text, values, stored, is_shallow())
    if bad:
        print("readme_check: README.md is out of date with the repo", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print("run: python scripts/tools/readme_check.py --write", file=sys.stderr)
        return 1
    print(f"readme_check: {len(values)} values match the repo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
