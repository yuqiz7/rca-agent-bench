# Agent 评测报告 devset_v2_20260828

- 模型：`claude-sonnet-5`
- 卡集：19 张（成功执行 19 张）
- 开始：2026-08-28T21:05:28Z　总墙钟：625.1s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 57.9%（11/19） |
| service-only 准确率（参考列） | 78.9%（15/19） |
| 平均诊断步数 | 5.11 |
| 单卡平均 token 成本 | $0.0676 |
| p95 端到端延迟 | 121.8s |

辅助：单卡平均 token 用量 50947（含缓存读写）；本次合计 $1.2851。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-cart-01` | cart / 黑洞 | valkey-cart / 黑洞 | ✗ | ✗ | 9 | 18 | 5430 | 17996 | 102596 | 0.1198 | 63.8 | submit | 1/0 |
| `blackhole-currency-01` | currency / 黑洞 | currency / 延迟 | ✗ | ✓ | 5 | 10 | 1683 | 9295 | 26032 | 0.0453 | 19.0 | submit | 0/0 |
| `blackhole-frontend-01` | frontend / 黑洞 | frontend / 延迟 | ✗ | ✓ | 3 | 6 | 2794 | 12390 | 16644 | 0.0623 | 33.3 | submit | 0/0 |
| `blackhole-quote-01` | quote / 黑洞 | quote / 黑洞 | ✓ | ✓ | 6 | 12 | 2030 | 11597 | 40481 | 0.0574 | 23.0 | submit | 0/0 |
| `crash-cart-01` | cart / 崩溃 | cart / 黑洞 | ✗ | ✓ | 5 | 10 | 2409 | 12600 | 35165 | 0.0626 | 27.1 | submit | 0/0 |
| `crash-currency-01` | currency / 崩溃 | currency / 崩溃 | ✓ | ✓ | 5 | 10 | 4188 | 13938 | 35015 | 0.0837 | 48.1 | submit | 0/0 |
| `crash-frontend-01` | frontend / 崩溃 | frontend / 崩溃 | ✓ | ✓ | 6 | 12 | 4590 | 25156 | 50032 | 0.1188 | 50.5 | submit | 0/0 |
| `crash-product-catalog-01` | product-catalog / 崩溃 | flagd / 黑洞 | ✗ | ✗ | 8 | 16 | 10797 | 22457 | 97748 | 0.1837 | 121.8 | submit | 0/0 |
| `latency-cart-800` | cart / 延迟 | cart / 延迟 | ✓ | ✓ | 4 | 8 | 1381 | 10406 | 20933 | 0.0440 | 16.8 | submit | 0/0 |
| `latency-checkout-800` | checkout / 延迟 | checkout / 延迟 | ✓ | ✓ | 6 | 12 | 2364 | 11957 | 35846 | 0.0607 | 25.7 | submit | 0/0 |
| `latency-currency-800` | currency / 延迟 | currency / 延迟 | ✓ | ✓ | 4 | 8 | 2141 | 10520 | 18420 | 0.0514 | 24.2 | submit | 0/0 |
| `latency-email-800` | email / 延迟 | email / 延迟 | ✓ | ✓ | 5 | 10 | 1837 | 11993 | 28187 | 0.0540 | 20.0 | submit | 0/0 |
| `latency-product-catalog-800` | product-catalog / 延迟 | recommendation / 延迟 | ✗ | ✗ | 3 | 6 | 2861 | 10376 | 13259 | 0.0572 | 31.9 | submit | 0/0 |
| `memleak-email-10000x` | email / 内存泄漏 | email / 延迟 | ✗ | ✓ | 4 | 8 | 2029 | 9207 | 18475 | 0.0470 | 25.5 | submit | 0/0 |
| `memleak-email-1000x` | email / 内存泄漏 | email / 内存泄漏 | ✓ | ✓ | 7 | 14 | 2452 | 12005 | 49182 | 0.0644 | 30.0 | submit | 1/0 |
| `misconfig-checkout-on` | checkout / 错配 | payment / 崩溃 | ✗ | ✗ | 4 | 8 | 1163 | 8001 | 18663 | 0.0354 | 12.6 | submit | 0/0 |
| `misconfig-payment-100` | payment / 错配 | payment / 错配 | ✓ | ✓ | 4 | 8 | 797 | 8442 | 19087 | 0.0329 | 9.9 | submit | 0/0 |
| `misconfig-payment-90` | payment / 错配 | payment / 错配 | ✓ | ✓ | 4 | 8 | 927 | 8685 | 18243 | 0.0346 | 11.4 | submit | 0/0 |
| `misconfig-pc-OLJCESPC7Z` | product-catalog / 错配 | product-catalog / 错配 | ✓ | ✓ | 5 | 10 | 2569 | 15444 | 26885 | 0.0697 | 29.8 | submit | 0/0 |

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
| 正常 submit 终止 | 19 |
| 工具调用总数 | 161 |
| API 请求总数 | 97 |
| token 合计 | 输入 194，输出 54442，缓存写 242465，缓存读 670893 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
