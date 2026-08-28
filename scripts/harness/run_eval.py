#!/usr/bin/env python3
"""run_eval.py -- score the agent over a set of cards and write the report.

Grading lives here, not in the agent, because this is the only half of the
pipeline allowed to read scenarios/: run_agent.py cannot see a card's answer even
by accident, which is what makes the accuracy number worth anything.

Four metrics, per decision 006: a diagnosis is correct only when both halves of
(service, fault_type) match. service-only accuracy rides along as a reference
column -- it separates "found the wrong service" from "found the right service and
misread the mechanism", and those two failures need different fixes.
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "scripts", "agent"))
sys.path.insert(0, os.path.join(REPO, "scripts", "scenarios"))

import cards                                   # noqa: E402
import run_agent                               # noqa: E402

CLASS_LABEL = {"crash": "崩溃", "latency": "延迟", "blackhole": "黑洞",
               "misconfig": "错配", "mem_leak": "内存泄漏"}


def ground_truth(card_id):
    card = cards.load(card_id)
    gt = card["ground_truth"]
    return {"service": gt["service"], "fault_type": gt["class"],
            "aliases": [a.lower() for a in (gt.get("aliases") or [])]}


def grade(answer, gt):
    if not answer:
        return {"service_ok": False, "top1_ok": False}
    svc = (answer.get("service") or "").lower()
    svc_ok = svc == gt["service"].lower() or svc in gt["aliases"]
    ft_ok = answer.get("fault_type") == gt["fault_type"]
    return {"service_ok": svc_ok, "top1_ok": bool(svc_ok and ft_ok)}


def p95(values):
    if not values:
        return None
    s = sorted(values)
    # Nearest-rank on a handful of cards: interpolation would invent a latency
    # between two runs that never happened.
    return s[min(len(s) - 1, int(-(-95 * len(s) // 100)) - 1)]


def _pct(n, d):
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def build_report(run_id, model, rows, prices, config, started_at, wall_s):
    n = len(rows)
    ok_rows = [r for r in rows if r.get("result")]
    top1 = sum(r["grade"]["top1_ok"] for r in ok_rows)
    svc1 = sum(r["grade"]["service_ok"] for r in ok_rows)
    steps = [r["result"]["steps"] for r in ok_rows]
    costs = [r["result"]["cost_usd"] for r in ok_rows]
    walls = [r["result"]["wall_s"] for r in ok_rows]
    toks = [sum(r["result"]["usage"].values()) for r in ok_rows]

    tot = {k: sum(r["result"]["usage"][k] for r in ok_rows)
           for k in ("input", "output", "cache_write", "cache_read")}
    ctr = {k: sum(r["result"]["counters"][k] for r in ok_rows)
           for k in ("api_calls", "tool_calls", "validation_rejects",
                     "tool_retries", "nudges")}
    term = {}
    for r in ok_rows:
        term[r["result"]["terminated"]] = term.get(r["result"]["terminated"], 0) + 1

    L = []
    a = L.append
    a(f"# Agent 评测报告 {run_id}")
    a("")
    a(f"- 模型：`{model}`")
    a(f"- 卡集：{n} 张（成功执行 {len(ok_rows)} 张）")
    a(f"- 开始：{started_at}　总墙钟：{wall_s:.1f}s")
    a(f"- 计价：`{prices['source_url']}`（抓取日期 {prices['fetched_at']}）")
    a(f"- 运行参数：步数上限 {config['run']['max_steps']}，单卡成本上限 "
      f"${config['run']['max_usd_per_card']}，工具重试 {config['run']['tool_retries']}，"
      f"effort={config['run']['effort']}，缓存={config['run']['prompt_caching']}")
    a("")
    a("## 四指标")
    a("")
    a("| 指标 | 数值 |")
    a("| --- | --- |")
    a(f"| top-1 准确率（service + fault_type 全对） | {_pct(top1, len(ok_rows))}"
      f"（{top1}/{len(ok_rows)}） |")
    a(f"| service-only 准确率（参考列） | {_pct(svc1, len(ok_rows))}（{svc1}/{len(ok_rows)}） |")
    a(f"| 平均诊断步数 | {statistics.mean(steps):.2f} |" if steps else "| 平均诊断步数 | n/a |")
    a(f"| 单卡平均 token 成本 | ${statistics.mean(costs):.4f} |" if costs
      else "| 单卡平均 token 成本 | n/a |")
    a(f"| p95 端到端延迟 | {p95(walls):.1f}s |" if walls else "| p95 端到端延迟 | n/a |")
    a("")
    a(f"辅助：单卡平均 token 用量 {statistics.mean(toks):.0f}（含缓存读写）；"
      f"本次合计 ${sum(costs):.4f}。")
    a("")
    a("## 逐卡明细")
    a("")
    a("| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        gt = r["ground_truth"]
        gts = f"{gt['service']} / {CLASS_LABEL.get(gt['fault_type'], gt['fault_type'])}"
        if not r.get("result"):
            a(f"| `{r['card_id']}` | {gts} | — | ✗ | ✗ | — | — | — | — | — | — | — | "
              f"执行失败 | {r.get('error', '')} |")
            continue
        res, g = r["result"], r["grade"]
        ans = res["answer"]
        pred = (f"{ans['service']} / {CLASS_LABEL.get(ans['fault_type'], ans['fault_type'])}"
                if ans else "未提交")
        u, c = res["usage"], res["counters"]
        a(f"| `{r['card_id']}` | {gts} | {pred} | {'✓' if g['top1_ok'] else '✗'} | "
          f"{'✓' if g['service_ok'] else '✗'} | {res['steps']} | {u['input']} | "
          f"{u['output']} | {u['cache_write']} | {u['cache_read']} | "
          f"{res['cost_usd']:.4f} | {res['wall_s']:.1f} | {res['terminated']} | "
          f"{c['validation_rejects']}/{c['tool_retries']} |")
    a("")
    a("（拦截列为 `参数校验拦截数 / 工具重试数`。）")
    a("")
    a("## 四道保险与用量合计")
    a("")
    a("| 项 | 值 |")
    a("| --- | --- |")
    a(f"| 参数校验拦截 | {ctr['validation_rejects']} |")
    a(f"| 工具重试 | {ctr['tool_retries']} |")
    a(f"| 步数熔断触发 | {term.get('max_steps', 0)} |")
    a(f"| 成本熔断触发 | {term.get('cost_cap', 0)} |")
    a(f"| 未提交（提示后仍未调 submit） | {term.get('no_submit', 0)} |")
    a(f"| API 错误终止 | {term.get('api_error', 0)} |")
    a(f"| 正常 submit 终止 | {term.get('submit', 0)} |")
    a(f"| 工具调用总数 | {ctr['tool_calls']} |")
    a(f"| API 请求总数 | {ctr['api_calls']} |")
    a(f"| token 合计 | 输入 {tot['input']}，输出 {tot['output']}，"
      f"缓存写 {tot['cache_write']}，缓存读 {tot['cache_read']} |")
    a("")
    a("## 泄漏自检")
    a("")
    checks = sorted({c for r in ok_rows for c in r["result"]["leak_check"]})
    a(f"每张卡在发出首个 API 请求前均通过 fail-closed 自检：{', '.join(checks) or 'n/a'}。")
    a("")
    a("agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——"
      "它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值"
      "字面写着 (service, class) 两半答案。")
    a("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", help="comma-separated card ids; default = config dev_set")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model")
    ap.add_argument("--run-id")
    ap.add_argument("--runs-root", default=run_agent.RUNS_ROOT)
    a = ap.parse_args()

    config = run_agent.load_config()
    prices = run_agent.load_prices()
    model = a.model or config["models"]["primary"]
    card_ids = ([c.strip() for c in a.cards.split(",") if c.strip()]
                if a.cards else list(config["dev_set"]))
    if a.limit:
        card_ids = card_ids[:a.limit]
    run_id = a.run_id or run_agent.new_run_id("eval")
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.time()

    rows = []
    for i, cid in enumerate(card_ids, 1):
        gt = ground_truth(cid)
        row = {"card_id": cid, "ground_truth": gt}
        try:
            res = run_agent.run_card(cid, model=model, config=config, prices=prices)
        except Exception as exc:                              # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[{i}/{len(card_ids)}] {cid}: FAILED {row['error']}", flush=True)
        else:
            run_agent.write_result(res, run_id, a.runs_root)
            row["result"] = res
            row["grade"] = grade(res["answer"], gt)
            print(f"[{i}/{len(card_ids)}] {cid}: {res['terminated']} "
                  f"answer={res['answer']} gt={gt['service']}/{gt['fault_type']} "
                  f"top1={row['grade']['top1_ok']} steps={res['steps']} "
                  f"${res['cost_usd']:.4f} {res['wall_s']:.1f}s", flush=True)
        rows.append(row)

    wall = time.time() - t0
    report = build_report(run_id, model, rows, prices, config, started_at, wall)
    d = os.path.join(a.runs_root, run_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "report.md")
    with open(path, "w") as f:
        f.write(report)
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump({"run_id": run_id, "model": model, "cards": card_ids,
                   "started_at": started_at, "wall_s": round(wall, 3),
                   "rows": [{k: v for k, v in r.items() if k != "result"} for r in rows]},
                  f, indent=1, ensure_ascii=False)
    print(os.path.relpath(path, REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
