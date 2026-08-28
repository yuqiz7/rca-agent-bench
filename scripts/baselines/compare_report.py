#!/usr/bin/env python3
"""compare_report.py -- the three-arm comparison over one card set.

Same cards, same grader, same answer space for all three arms, so the numbers sit
on one axis. What differs is only how much machinery each arm is allowed:

  baseline 1  rules over the pack, no model at all
  baseline 2  one model turn over a fixed digest, no tools
  agent       the v2 prompt with six tools and up to 20 steps

The agent's rows are read from whichever eval runs already covered these cards --
re-running them would spend money to reproduce numbers that are already on disk,
and the packs are frozen so a re-run measures the same thing.

Steps are 1 for both baselines by construction: neither can investigate, so the
column is only meaningful for the agent, and reporting it as 1 keeps that visible
instead of leaving a blank that reads as "unknown".
"""
import argparse
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "scripts", "harness"))
sys.path.insert(0, os.path.join(REPO, "scripts", "scenarios"))
sys.path.insert(0, HERE)

from run_eval import CLASS_LABEL, grade, ground_truth, p95  # noqa: E402


def load_agent_rows(run_ids, cards):
    """card -> result dict, taking the first run that has each card."""
    out = {}
    for rid in run_ids:
        d = os.path.join(REPO, "artifacts", "agent_runs", rid)
        for c in cards:
            if c in out:
                continue
            path = os.path.join(d, f"{c}.json")
            if os.path.exists(path):
                with open(path) as f:
                    out[c] = json.load(f)
    return out


def metrics(rows, cards):
    """rows: card -> {answer, steps, cost_usd, wall_s}."""
    got = [rows[c] for c in cards if c in rows]
    ok = svc = 0
    for c in cards:
        r = rows.get(c)
        g = grade(r.get("answer") if r else None, ground_truth(c))
        ok += g["top1_ok"]
        svc += g["service_ok"]
    n = len(cards)
    return {
        "n": n, "top1": ok, "svc": svc,
        "top1_pct": 100.0 * ok / n, "svc_pct": 100.0 * svc / n,
        "steps": statistics.mean([r.get("steps") or 0 for r in got]) if got else 0,
        "cost": statistics.mean([r.get("cost_usd") or 0.0 for r in got]) if got else 0,
        "total_cost": sum(r.get("cost_usd") or 0.0 for r in got),
        "p95": p95([r.get("wall_s") or 0.0 for r in got]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards-file", default=os.path.join(HERE, "cardset_27.json"))
    ap.add_argument("--baseline1", required=True)
    ap.add_argument("--baseline2", required=True)
    ap.add_argument("--agent-runs", required=True, help="comma-separated run ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="baselines")
    a = ap.parse_args()

    with open(a.cards_file) as f:
        cards = json.load(f)
    b1 = {r["card_id"]: r for r in json.load(open(a.baseline1))}
    b2 = {r["card_id"]: r for r in json.load(open(a.baseline2))}
    ag = load_agent_rows(a.agent_runs.split(","), cards)

    arms = [("基线① 关键词启发式", b1), ("基线② 单轮 LLM", b2), ("主 agent（v2 prompt）", ag)]
    m = {name: metrics(rows, cards) for name, rows in arms}

    L = []
    A = L.append
    A(f"# 三方基线对照报告 {a.label}")
    A("")
    A(f"- 卡集：**{len(cards)} 张在库卡**，冻结于 `{os.path.relpath(a.cards_file, REPO)}`")
    A("- 三方用同一卡集、同一判分器（`run_eval.grade`）、同一答案空间，因此可直接比较。")
    A("- 基线①②的步数按 1 计：两者都没有调查能力，单轮出答案。")
    A("")
    A("## 四指标对照")
    A("")
    A("| 臂 | top-1 | service-only | 平均步数 | 单卡成本 | p95 延迟 | 合计成本 |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name, _ in arms:
        x = m[name]
        A(f"| {name} | **{x['top1_pct']:.1f}%**（{x['top1']}/{x['n']}） | "
          f"{x['svc_pct']:.1f}%（{x['svc']}/{x['n']}） | {x['steps']:.2f} | "
          f"${x['cost']:.4f} | {x['p95']:.1f}s | ${x['total_cost']:.4f} |")
    A("")

    A("## 逐卡三方预测")
    A("")
    A("| 卡 | 真值 | 基线① | 基线② | 主 agent |")
    A("| --- | --- | --- | --- | --- |")

    def cell(rows, c):
        r = rows.get(c)
        ans = r.get("answer") if r else None
        if not ans:
            return f"—（{(r or {}).get('terminated', '缺')}）"
        g = grade(ans, ground_truth(c))
        mark = "✓" if g["top1_ok"] else ("△" if g["service_ok"] else "✗")
        return f"{ans['service']}/{CLASS_LABEL.get(ans['fault_type'], ans['fault_type'])} {mark}"

    for c in cards:
        gt = ground_truth(c)
        A(f"| `{c}` | {gt['service']}/{CLASS_LABEL.get(gt['fault_type'], gt['fault_type'])} | "
          + " | ".join(cell(rows, c) for _, rows in arms) + " |")
    A("")
    A("（✓ = 双匹配；△ = 服务对、类别错；✗ = 服务错。）")
    A("")

    A("## 按真值类别拆开的 top-1")
    A("")
    types = []
    for c in cards:
        t = ground_truth(c)["fault_type"]
        if t not in types:
            types.append(t)
    A("| 类别 | 卡数 | " + " | ".join(n for n, _ in arms) + " |")
    A("| --- | ---: | " + " | ".join("---:" for _ in arms) + " |")
    for t in types:
        sub = [c for c in cards if ground_truth(c)["fault_type"] == t]
        cells = []
        for _, rows in arms:
            k = sum(grade((rows.get(c) or {}).get("answer"), ground_truth(c))["top1_ok"] for c in sub)
            cells.append(f"{k}/{len(sub)}")
        A(f"| {CLASS_LABEL.get(t, t)} | {len(sub)} | " + " | ".join(cells) + " |")
    A("")

    b1m, b2m, agm = m[arms[0][0]], m[arms[1][0]], m[arms[2][0]]
    A("## 读法")
    A("")
    A(f"**1. 规则基线赢了 agent 的 top-1（{b1m['top1_pct']:.1f}% vs {agm['top1_pct']:.1f}%），"
      f"但输了 service-only（{b1m['svc_pct']:.1f}% vs {agm['svc_pct']:.1f}%）。**")
    A("两个数字指向同一件事：**工具循环买到的是「找对服务」，没买到「说对机制」**。")
    A("agent 多花 5.5 步、单卡贵约 "
      f"{(agm['cost'] / b2m['cost']) if b2m['cost'] else 0:.1f} 倍于单轮 LLM，"
      "换来的定位优势只有几个百分点，而类别判断反被确定性阈值压过。")
    A("")
    A(f"**2. 单轮 LLM 只有 {b2m['top1_pct']:.1f}%，明显低于另外两臂。**")
    A(f"同一模型、同一答案空间，去掉循环就掉 {agm['top1_pct'] - b2m['top1_pct']:.1f} 个点 —— "
      "**循环本身是有价值的**，问题不在「能不能查」，在「查完怎么判」。")
    A("")
    A("**3. 规则赢在哪：延迟 5/5、内存泄漏 2/2。**")
    A("这两类的判据是**可以写成数值阈值**的（p50 位移、内存曲线单调抬升），"
      "规则一次算准；两个 LLM 臂则在同样的数据上把内存泄漏读成延迟。"
      "反过来 agent 赢在需要**追查**的卡（无 SDK 数据库靶子、入口服务），"
      "那些卡规则的图走不到正确的深度。")
    A("")
    A("**4. 三臂在错配上完全打平（各 5/7），错的还是同两张。**")
    A("说明这两张的难点不在推理能力，在证据面 —— 一张的开关让服务不再调用下游，"
      "现象与下游挂掉同形；另一张的报错 span 挂起超过窗口。")
    A("")
    A("### 一个必须写明的方法论警告")
    A("")
    A("**基线①的规则是在这 27 张卡上反复调出来的，agent 的 v2 prompt 不是。**")
    A("开发基线①时，作者按整体准确率迭代了若干轮（每轮看的是聚合数字与失败模式，"
      "不是逐卡答案，也没有任何 card_id 特判），而 v2 prompt 在看到本卡集结果之前就已冻结。"
      "**因此 70.4% 对基线①是乐观的，两个数字不是完全对等的比较。**")
    A("规则里的阈值全部取自 harness 既有常数（静默天花板 0.1、mem_leak 地板 "
      "max(10 MiB, 0.15×起始)、规则 7 的 2× 与 100 ms、决策 023 的 1000 ms 台阶），"
      "没有一个是为凑这批卡新调的 —— 但**规则的结构**（走图的方向、starved/broken 的分法）"
      "确实是对着这批卡的失败模式设计的。")
    A("要拿到诚实的对照，基线①必须在**它没见过的卡**上复测 —— "
      "第三批产出的卡正是现成的留出集，下一轮该在那上面重跑三臂。")
    A("")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:40]))
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
