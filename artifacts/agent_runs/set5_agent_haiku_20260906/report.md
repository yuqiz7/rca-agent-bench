# Agent 评测报告 set5_agent_haiku_20260906

- 模型：`claude-haiku-4-5`
- 卡集：5 张（成功执行 5 张）
- 开始：2026-09-06T20:21:12Z　总墙钟：258.8s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 60.0%（3/5） |
| service-only 准确率（参考列） | 80.0%（4/5） |
| 平均诊断步数 | 13.60 |
| 单卡平均 token 成本 | $0.0733 |
| p95 端到端延迟 | 71.2s |

辅助：单卡平均 token 用量 229601（含缓存读写）；本次合计 $0.3666。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `crash-ad-01` | ad / 崩溃 | ad / 崩溃 | ✓ | ✓ | 18 | 4029 | 5336 | 24378 | 222309 | 0.0834 | 68.6 | submit | 3/0 |
| `blackhole-astronomy-db-01` | astronomy-db / 黑洞 | 未提交 | ✗ | ✗ | 20 | 118 | 6053 | 43565 | 503871 | 0.1352 | 71.2 | max_steps | 1/0 |
| `crash-quote-01` | quote / 崩溃 | quote / 崩溃 | ✓ | ✓ | 13 | 79 | 4203 | 17017 | 135790 | 0.0559 | 49.3 | submit | 2/0 |
| `blackhole-email-01` | email / 黑洞 | email / 黑洞 | ✓ | ✓ | 9 | 53 | 3065 | 19470 | 79543 | 0.0477 | 37.3 | submit | 0/0 |
| `crash-recommendation-01` | recommendation / 崩溃 | recommendation / 黑洞 | ✗ | ✓ | 8 | 3976 | 2620 | 17387 | 55144 | 0.0443 | 32.3 | submit | 1/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 7 |
| 工具重试 | 0 |
| 步数熔断触发 | 1 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 4 |
| 工具调用总数 | 107 |
| API 请求总数 | 68 |
| token 合计 | 输入 8255，输出 21277，缓存写 121817，缓存读 996657 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
