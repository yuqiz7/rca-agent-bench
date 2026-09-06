# Agent 评测报告 traceverify_20260906

- 模型：`claude-sonnet-5`
- 卡集：2 张（成功执行 2 张）
- 开始：2026-09-06T21:26:22Z　总墙钟：31.7s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 50.0%（1/2） |
| service-only 准确率（参考列） | 100.0%（2/2） |
| 平均诊断步数 | 3.50 |
| 单卡平均 token 成本 | $0.0421 |
| p95 端到端延迟 | 16.3s |

辅助：单卡平均 token 用量 27476（含缓存读写）；本次合计 $0.0843。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `crash-currency-01` | currency / 崩溃 | currency / 黑洞 | ✗ | ✓ | 3 | 6 | 1415 | 9723 | 13657 | 0.0412 | 16.3 | submit | 0/0 |
| `latency-currency-800` | currency / 延迟 | currency / 延迟 | ✓ | ✓ | 4 | 8 | 1343 | 10379 | 18420 | 0.0431 | 15.4 | submit | 0/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 0 |
| 工具重试 | 0 |
| 步数熔断触发 | 0 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 2 |
| 工具调用总数 | 12 |
| API 请求总数 | 7 |
| token 合计 | 输入 14，输出 2758，缓存写 20102，缓存读 32077 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
