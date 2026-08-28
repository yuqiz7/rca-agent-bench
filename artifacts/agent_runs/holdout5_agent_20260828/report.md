# Agent 评测报告 holdout5_agent_20260828

- 模型：`claude-sonnet-5`
- 卡集：5 张（成功执行 5 张）
- 开始：2026-08-28T22:48:53Z　总墙钟：181.6s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 60.0%（3/5） |
| service-only 准确率（参考列） | 100.0%（5/5） |
| 平均诊断步数 | 5.20 |
| 单卡平均 token 成本 | $0.0712 |
| p95 端到端延迟 | 54.4s |

辅助：单卡平均 token 用量 53210（含缓存读写）；本次合计 $0.3560。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `crash-ad-01` | ad / 崩溃 | ad / 崩溃 | ✓ | ✓ | 6 | 12 | 4740 | 12329 | 35822 | 0.0854 | 54.4 | submit | 0/0 |
| `blackhole-astronomy-db-01` | astronomy-db / 黑洞 | astronomy-db / 延迟 | ✗ | ✓ | 5 | 10 | 3965 | 17381 | 48727 | 0.0929 | 45.4 | submit | 0/0 |
| `crash-quote-01` | quote / 崩溃 | quote / 崩溃 | ✓ | ✓ | 8 | 16 | 4259 | 15714 | 70105 | 0.0959 | 47.9 | submit | 0/0 |
| `blackhole-email-01` | email / 黑洞 | email / 黑洞 | ✓ | ✓ | 4 | 8 | 1860 | 11411 | 21624 | 0.0515 | 21.0 | submit | 0/0 |
| `crash-recommendation-01` | recommendation / 崩溃 | recommendation / 黑洞 | ✗ | ✓ | 3 | 6 | 1197 | 6503 | 10361 | 0.0303 | 12.8 | submit | 0/0 |

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
| 正常 submit 终止 | 5 |
| 工具调用总数 | 45 |
| API 请求总数 | 26 |
| token 合计 | 输入 52，输出 16021，缓存写 63338，缓存读 186639 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
