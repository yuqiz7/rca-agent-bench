# Agent 评测报告 set11_agent_haiku_20260906

- 模型：`claude-haiku-4-5`
- 卡集：11 张（成功执行 11 张）
- 开始：2026-09-06T20:27:04Z　总墙钟：669.3s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 54.5%（6/11） |
| service-only 准确率（参考列） | 54.5%（6/11） |
| 平均诊断步数 | 15.00 |
| 单卡平均 token 成本 | $0.0868 |
| p95 端到端延迟 | 86.5s |

辅助：单卡平均 token 用量 277865（含缓存读写）；本次合计 $0.9546。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-payment-01` | payment / 黑洞 | payment / 黑洞 | ✓ | ✓ | 9 | 53 | 2465 | 13288 | 71432 | 0.0361 | 30.6 | submit | 0/0 |
| `blackhole-shipping-01` | shipping / 黑洞 | quote / 黑洞 | ✗ | ✗ | 20 | 128 | 7738 | 42523 | 508893 | 0.1429 | 86.5 | submit | 1/0 |
| `crash-payment-01` | payment / 崩溃 | payment / 崩溃 | ✓ | ✓ | 17 | 101 | 5170 | 30754 | 221008 | 0.0865 | 63.3 | submit | 0/0 |
| `crash-shipping-01` | shipping / 崩溃 | shipping / 崩溃 | ✓ | ✓ | 10 | 60 | 3317 | 13068 | 76066 | 0.0406 | 38.9 | submit | 0/0 |
| `latency-ad-800` | ad / 延迟 | 未提交 | ✗ | ✗ | 20 | 3517 | 6592 | 32555 | 354720 | 0.1126 | 80.9 | max_steps | 1/0 |
| `latency-astronomy-db-3000` | astronomy-db / 延迟 | astronomy-db / 延迟 | ✓ | ✓ | 11 | 65 | 3386 | 20575 | 152570 | 0.0580 | 41.7 | submit | 3/0 |
| `latency-payment-800` | payment / 延迟 | payment / 延迟 | ✓ | ✓ | 17 | 3751 | 5777 | 25233 | 225807 | 0.0868 | 71.2 | submit | 1/0 |
| `latency-quote-800` | quote / 延迟 | shipping / 延迟 | ✗ | ✗ | 7 | 39 | 2332 | 14512 | 54160 | 0.0353 | 30.3 | submit | 0/0 |
| `latency-recommendation-3000` | recommendation / 延迟 | 未提交 | ✗ | ✗ | 20 | 120 | 7198 | 40001 | 396793 | 0.1258 | 84.2 | max_steps | 3/0 |
| `blackhole-ad-01` | ad / 黑洞 | 未提交 | ✗ | ✗ | 20 | 3537 | 6073 | 41673 | 381843 | 0.1242 | 70.8 | max_steps | 1/0 |
| `blackhole-recommendation-01` | recommendation / 黑洞 | recommendation / 黑洞 | ✓ | ✓ | 14 | 3997 | 5898 | 39735 | 227988 | 0.1060 | 70.4 | submit | 1/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 11 |
| 工具重试 | 0 |
| 步数熔断触发 | 3 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 8 |
| 工具调用总数 | 275 |
| API 请求总数 | 165 |
| token 合计 | 输入 15368，输出 55946，缓存写 313917，缓存读 2671280 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
