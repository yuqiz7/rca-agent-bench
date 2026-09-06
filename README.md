# rca-agent-bench

A benchmark for root-cause-analysis agents: faults are injected into a running
OpenTelemetry Demo, each incident is frozen into a read-only evidence pack whose
ground truth is the injection itself, and every arm is scored on the same cards by
the same grader.

**Ground truth is the injection, not a label.** A card records the service and the
fault class that a script applied and reverted, so `(service, fault_class)` is
decided before any arm sees the data, and grading is a string comparison instead of
a judgement.

**Evaluation is offline.** An arm reads a directory of files, never a live backend.
The same card scored today and in six months goes through the same bytes, and the
grader runs without network access.

**The point is the comparison, not the score.** A rules arm with no model, a single
model turn with no tools, and a tool-calling agent answer the same
<!-- GEN:eval_set_size -->43<!-- /GEN --> cards, which is what makes "the loop is
worth its cost" a measurement. The rules arm is also the one place where tuning on
the cards can be quantified: on cards it was never tuned on its top-1 moves by
<!-- GEN:rules_overfit_delta -->-16.7<!-- /GEN --> points.

---

## Results

**Holdout set (<!-- GEN:holdout_set_size -->16<!-- /GEN --> cards, no arm tuned on them)**

| arm | top-1 | service-only | steps | cost/card | p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| rules, no model | <!-- GEN:holdout16.rules.top1 -->50.0% (8/16)<!-- /GEN --> | <!-- GEN:holdout16.rules.service -->62.5% (10/16)<!-- /GEN --> | <!-- GEN:holdout16.rules.steps -->1.00<!-- /GEN --> | <!-- GEN:holdout16.rules.cost -->$0.0000<!-- /GEN --> | <!-- GEN:holdout16.rules.p95 -->0.5s<!-- /GEN --> |
| single-shot LLM | <!-- GEN:holdout16.single_shot.top1 -->56.2% (9/16)<!-- /GEN --> | <!-- GEN:holdout16.single_shot.service -->81.2% (13/16)<!-- /GEN --> | <!-- GEN:holdout16.single_shot.steps -->1.00<!-- /GEN --> | <!-- GEN:holdout16.single_shot.cost -->$0.0238<!-- /GEN --> | <!-- GEN:holdout16.single_shot.p95 -->44.5s<!-- /GEN --> |
| agent | <!-- GEN:holdout16.agent.top1 -->75.0% (12/16)<!-- /GEN --> | <!-- GEN:holdout16.agent.service -->87.5% (14/16)<!-- /GEN --> | <!-- GEN:holdout16.agent.steps -->5.19<!-- /GEN --> | <!-- GEN:holdout16.agent.cost -->$0.0691<!-- /GEN --> | <!-- GEN:holdout16.agent.p95 -->63.1s<!-- /GEN --> |

**Evaluation set (<!-- GEN:eval_set_size -->43<!-- /GEN --> cards, includes the
<!-- GEN:devset_size -->19<!-- /GEN --> dev cards the prompt was written against)**

| arm | top-1 | service-only | steps | cost/card | p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| rules, no model | <!-- GEN:all43.rules.top1 -->62.8% (27/43)<!-- /GEN --> | <!-- GEN:all43.rules.service -->72.1% (31/43)<!-- /GEN --> | <!-- GEN:all43.rules.steps -->1.00<!-- /GEN --> | <!-- GEN:all43.rules.cost -->$0.0000<!-- /GEN --> | <!-- GEN:all43.rules.p95 -->0.4s<!-- /GEN --> |
| single-shot LLM | <!-- GEN:all43.single_shot.top1 -->46.5% (20/43)<!-- /GEN --> | <!-- GEN:all43.single_shot.service -->72.1% (31/43)<!-- /GEN --> | <!-- GEN:all43.single_shot.steps -->1.00<!-- /GEN --> | <!-- GEN:all43.single_shot.cost -->$0.0240<!-- /GEN --> | <!-- GEN:all43.single_shot.p95 -->38.9s<!-- /GEN --> |
| agent | <!-- GEN:all43.agent.top1 -->65.1% (28/43)<!-- /GEN --> | <!-- GEN:all43.agent.service -->83.7% (36/43)<!-- /GEN --> | <!-- GEN:all43.agent.steps -->5.40<!-- /GEN --> | <!-- GEN:all43.agent.cost -->$0.0726<!-- /GEN --> | <!-- GEN:all43.agent.p95 -->63.8s<!-- /GEN --> |

<!-- haiku arms: added by cross-model step -->

Both tables are small samples: one card is worth
<!-- GEN:holdout_card_points -->6.2<!-- /GEN --> points in the first table and
<!-- GEN:eval_card_points -->2.3<!-- /GEN --> points in the second, so read the
gaps as an ordering and not as a measurement.

The evaluation set is <!-- GEN:eval_set_size -->43<!-- /GEN --> cards while
<!-- GEN:instock_cards -->45<!-- /GEN --> cards are in stock: the set was frozen to
a card list before the last batch landed, and a frozen list is what keeps two runs
comparable. Steps and p95 are 1 and sub-second for the rules arm by construction --
it cannot investigate. p95 is wall clock, including local reads of the pack.

The rules arm was tuned on 27 cards. Restricted to the classes the holdout
contains, it scores <!-- GEN:rules_overfit_seen -->12/18<!-- /GEN --> on cards it
was tuned on and <!-- GEN:rules_overfit_held -->8/16<!-- /GEN --> on cards it has
never seen, a change of <!-- GEN:rules_overfit_delta -->-16.7<!-- /GEN --> points.
Almost all of it is one class: blackhole goes
<!-- GEN:rules_overfit_blackhole -->3/6 to 0/6<!-- /GEN -->.

---

## How it works

```
  testbed
     |
     v
  fault primitives
     |
     v
  scenario cards
     |
     v
  probe verdicts
     |
     v
  evidence packs
     |
     v
  agent / baselines
     |
     v
  harness
```

- **testbed** -- the OpenTelemetry Demo on one host under Docker Compose,
  <!-- GEN:containers -->25<!-- /GEN --> containers, with Prometheus, Jaeger and
  OpenSearch as the three signal backends.
- **fault primitives** -- <!-- GEN:primitive_scripts -->4<!-- /GEN --> shell scripts
  with one `apply` / `revert` / `probe` interface, covering
  <!-- GEN:fault_classes -->5<!-- /GEN --> fault classes.
- **scenario cards** -- <!-- GEN:recipe_cards -->68<!-- /GEN --> rows of a recipe
  file generated into `scenarios/*.yaml`, each carrying target, primitive,
  parameters, cycle timing and the ground truth.
- **probe verdicts** -- after every cycle the runner decides
  <!-- GEN:probe_gates -->3<!-- /GEN --> things from the three backends (the
  injection took, the symptom appeared, the service recovered) plus a residue check;
  a card enters the set only if all of them pass.
- **evidence packs** -- the fault window frozen into `evidence/<card>/`:
  <!-- GEN:evidence_files -->8<!-- /GEN --> files per card, of which
  <!-- GEN:evidence_hashed -->5<!-- /GEN --> carry a size and sha256 in the manifest
  so a pack can be recomputed.
- **agent / baselines** -- each arm reads the pack, never the live system, and
  submits one `(service, fault_class)` pair from a fixed answer space.
- **harness** -- one grader for every arm, reporting top-1, service-only, average
  steps, cost per card, p95 wall clock and total cost.

---

## Testbed and faults

The demo runs <!-- GEN:containers -->25<!-- /GEN --> containers;
<!-- GEN:injectable_targets -->16<!-- /GEN --> of them are injection targets, the
rest being the observability backends, the collector, the load generator and the
control plane, which cannot be faulted without cutting off the evidence.

<!-- GEN:primitive_scripts -->4<!-- /GEN --> primitive scripts cover
<!-- GEN:fault_classes -->5<!-- /GEN --> fault classes: container kill, inbound
packet drop inside the target's network namespace, outbound delay filtered by
service port, and a flag write that serves both misconfiguration and memory-leak
cards. Injection is filtered down to the target's traffic rather than cutting a
container off the network.

Each cycle is judged by <!-- GEN:probe_gates -->3<!-- /GEN --> automatic probes plus
a residue check, all on the runner side; the arms never see the probe output.
Rate and latency comparisons use a
<!-- GEN:baseline_window_s -->300<!-- /GEN --> second baseline window before
injection. <!-- GEN:recover_window_cards -->34<!-- /GEN --> cards carry a longer
recovery window, solved offline from the target's measured call rate so that the
window can hold enough calls to decide recovery at all. Targets without an SDK on
the caller side are judged by a separate arm that reads the caller's view.

The detector that turns a pack into alerts has
<!-- GEN:alert_rules -->7<!-- /GEN --> rules, including a
<!-- GEN:p95_floor_ms -->100<!-- /GEN --> ms absolute floor on latency jumps so a
ratio over a tiny baseline cannot fire on its own.

---

## Agent

A hand-written function-calling loop against the Anthropic SDK: no agent framework,
the loop is a `while` over `messages.create`. The model is
`<!-- GEN:agent_model -->claude-sonnet-5<!-- /GEN -->`.

<!-- GEN:agent_tools -->6<!-- /GEN --> read-only tools over the pack (log search,
metric query, trace query, config diff, topology, alerts) plus `submit` as a tool,
so the answer space is enforced by a JSON schema rather than parsed out of prose.

<!-- GEN:agent_guards -->5<!-- /GEN --> guards, each recorded per run: argument
validation, in-tool retry, a format nudge when a turn calls no tool, a step breaker
at <!-- GEN:max_steps -->20<!-- /GEN --> steps and a cost breaker at
$<!-- GEN:cost_cap_usd -->0.20<!-- /GEN --> per card.

A leak check runs before the first token of every card: it asserts that the system
prompt and tool schemas are card-independent constants and that the user turn is a
projection of the task view, and raises rather than continuing. It is fail-closed
and it is one of the CI gates.

The prompt was iterated once on a <!-- GEN:devset_size -->19<!-- /GEN -->-card dev
set, from <!-- GEN:devset_before -->52.6%<!-- /GEN --> to
<!-- GEN:devset_after -->57.9%<!-- /GEN --> top-1, then frozen before any holdout
run. Service-only accuracy moved the other way in the same edit.

---

## Baselines

**Rules, no model.** Deterministic scoring over the same pack: walks the topology
from the alerting services, splits starved callers from broken callees, and takes
all of its thresholds from constants the harness already used. Zero API calls,
sub-second.

**Single-shot LLM.** One model turn on a fixed digest of the same pack, no tools,
same model and same answer space as the agent. The only variable between it and the
agent is the loop.

---

## Engineering

CI is <!-- GEN:ci_gates -->3<!-- /GEN --> offline gates on every push: pytest
(including the leak assertions), a check that regenerating every scenario card from
the recipe changes nothing, and the detector staying silent on two fault-free
windows. No gate touches the VM or the API.

A `None`-versus-zero audit walked <!-- GEN:audit_paths -->27<!-- /GEN --> verdict
paths and fixed <!-- GEN:audit_fixes -->7<!-- /GEN --> places that read a missing
measurement as a zero, then replayed the
<!-- GEN:audit_replay_cards -->41<!-- /GEN --> cards in stock at that date to
confirm the defect had never passed a card.

Cards are produced by an unattended batch runner: a global serial lock so only one
fault is ever live, `nohup` wrapping, and abort on consecutive failures rather than
on the first one. <!-- GEN:batch_count -->7<!-- /GEN --> recorded batches account
for <!-- GEN:batch_hours -->8.9<!-- /GEN --> hours of machine time. Every model call
is priced per card: <!-- GEN:api_spend -->$9.07<!-- /GEN --> over
<!-- GEN:api_runs -->237<!-- /GEN --> metered single-card runs.

Repository scale: <!-- GEN:commits -->~68<!-- /GEN --> commits,
<!-- GEN:code_loc -->~8.3k<!-- /GEN --> lines of Python and shell,
<!-- GEN:docs_loc -->~6.8k<!-- /GEN --> lines of design docs.

---

## Findings

Each entry in `docs/open_items.md` is an item that cost a batch or changed a
verdict; `docs/findings.md` holds the analysis.

- **F-1** a 10% failure rate sits below the detection line of a 120 s window.
- **F-2** the recovery window was too short for low-traffic targets; two cards
  failed on sample size, not on recovery.
- **F-3** the latency verdict required errors to stay flat, which an 800 ms delay
  does not satisfy.
- **F-4** a target's client reconnects after revert more slowly than the recovery
  window allows.
- **F-5** a blackhole special case for long-lived connections was written as the
  general rule, and two cards were graded as latency.
- **F-6** one service's gRPC egress resolves its peer to a container IP, leaving
  four edges permanently empty in the probe.
- **F-7** on a holdout set the rules baseline drops and the agent holds, which is
  what "cards it has seen" is worth.
- **F-8** a target's call edge stops being instrumented after its connection is
  broken, taking its cards out of the set.

Agent failure modes, four of them, in `docs/findings.md` §1: crash and blackhole
collapse into the same shape at harvest time; there is no procedure for memory
leaks; on a low-traffic target the caller takes the blame; and an edge that was
never enumerated is treated as an edge already ruled out.

---

## Limitations

- The verdicts are audited, not independently validated. Every path where a verdict
  could read a missing measurement as a zero has been checked, but there is no
  labelled control set that says the verdicts themselves are right.
- The holdout set contains no misconfiguration and no memory-leak cards, which are
  the two classes the rules arm is best at. The overfitting result covers blackhole,
  crash and latency only.
- There is no human baseline. Nothing in this repository records how fast or how
  accurately a person diagnoses these cards.
- Every number here comes from one model family.
- Tier coverage is partial: the in-stock set is
  <!-- GEN:instock_by_class -->blackhole 12 / crash 12 / latency 12 / mem_leak 2 / misconfig 7<!-- /GEN -->,
  so the harder latency tier and most misconfiguration variants are thin or absent.
- <!-- GEN:valkey_blocked -->2<!-- /GEN --> cards against the cache target cannot be
  run at all: breaking its connection removes the instrumentation the verdict needs
  (open item O-P2-23).

---

## Reproduce

```bash
bash scripts/maintenance/wakeup.sh                                    # start the testbed, gate on health
python scripts/runner/run_batch.py --scenarios scenarios/crash-cart-01.yaml   # inject one card, probe, pack evidence
python scripts/harness/run_eval.py --cards crash-cart-01              # run the agent on that pack and grade it
```

`python scripts/tools/readme_check.py --check` verifies every number on this page
against the repository; `--write` refreshes them.

Docs: `docs/recipe.md` (the card recipe and difficulty axes), `docs/fault_schema.md`
(primitives, targets, verdicts), `docs/fingerprints.md` (measured signal shapes per
fault class), `docs/decisions.md` (what was chosen, why, and what was given up),
`docs/open_items.md` (open questions and findings), `docs/evidence_audit.md` (every
claim traced to a path), `docs/probe_audit.md` (the `None`-versus-zero audit).

---

## Repository layout

```
scripts/    primitives, batch runner, probes, evidence packer, detector, agent, baselines, harness
scenarios/  one yaml per card: target, primitive, params, cycle timing, ground truth, probe verdict
evidence/   one frozen pack per card, the only thing an arm may read
artifacts/  batch logs and per-run eval results with per-card cost
docs/       design docs, decisions, findings, audits
tests/      offline tests: leak assertions, clean-window negative control
testbed/    compose overrides for the demo
tools/      fixture maintenance
```
