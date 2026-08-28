# Agent 评测报告 devset_v2_extra8_20260828

- 模型：`claude-sonnet-5`
- 卡集：8 张（成功执行 8 张）
- 开始：2026-08-28T21:53:14Z　总墙钟：347.5s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 62.5%（5/8） |
| service-only 准确率（参考列） | 87.5%（7/8） |
| 平均诊断步数 | 6.50 |
| 单卡平均 token 成本 | $0.0911 |
| p95 端到端延迟 | 90.7s |

辅助：单卡平均 token 用量 79825（含缓存读写）；本次合计 $0.7291。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-checkout-01` | checkout / 黑洞 | checkout / 黑洞 | ✓ | ✓ | 5 | 10 | 2482 | 18612 | 32932 | 0.0780 | 27.7 | submit | 0/0 |
| `blackhole-product-catalog-01` | product-catalog / 黑洞 | product-catalog / 崩溃 | ✗ | ✓ | 8 | 16 | 5446 | 21251 | 98257 | 0.1273 | 59.8 | submit | 0/0 |
| `crash-astronomy-db-01` | astronomy-db / 崩溃 | astronomy-db / 崩溃 | ✓ | ✓ | 8 | 16 | 4527 | 21595 | 104615 | 0.1202 | 49.0 | submit | 2/0 |
| `crash-checkout-01` | checkout / 崩溃 | checkout / 黑洞 | ✗ | ✓ | 7 | 14 | 5525 | 14287 | 48160 | 0.1006 | 63.4 | submit | 0/0 |
| `crash-email-01` | email / 崩溃 | email / 崩溃 | ✓ | ✓ | 6 | 12 | 3268 | 13824 | 39240 | 0.0751 | 36.5 | submit | 0/0 |
| `misconfig-cart-75` | cart / 错配 | valkey-cart / 黑洞 | ✗ | ✗ | 10 | 20 | 7799 | 22951 | 118076 | 0.1590 | 90.7 | submit | 0/0 |
| `misconfig-payment-50` | payment / 错配 | payment / 错配 | ✓ | ✓ | 4 | 8 | 888 | 9323 | 18068 | 0.0358 | 10.1 | submit | 0/0 |
| `misconfig-payment-75` | payment / 错配 | payment / 错配 | ✓ | ✓ | 4 | 8 | 823 | 8483 | 18066 | 0.0331 | 9.9 | submit | 0/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 2 |
| 工具重试 | 0 |
| 步数熔断触发 | 0 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 8 |
| 工具调用总数 | 84 |
| API 请求总数 | 52 |
| token 合计 | 输入 104，输出 30758，缓存写 130326，缓存读 477414 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
