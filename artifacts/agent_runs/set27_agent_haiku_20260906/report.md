# Agent 评测报告 set27_agent_haiku_20260906

- 模型：`claude-haiku-4-5`
- 卡集：27 张（成功执行 27 张）
- 开始：2026-09-06T19:56:55Z　总墙钟：1412.4s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 40.7%（11/27） |
| service-only 准确率（参考列） | 59.3%（16/27） |
| 平均诊断步数 | 12.70 |
| 单卡平均 token 成本 | $0.0734 |
| p95 端到端延迟 | 99.2s |

辅助：单卡平均 token 用量 220531（含缓存读写）；本次合计 $1.9813。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-cart-01` | cart / 黑洞 | cart / 黑洞 | ✓ | ✓ | 16 | 102 | 7981 | 42739 | 404178 | 0.1338 | 99.2 | submit | 1/0 |
| `blackhole-checkout-01` | checkout / 黑洞 | checkout / 黑洞 | ✓ | ✓ | 17 | 95 | 6143 | 32137 | 256074 | 0.0966 | 78.6 | submit | 0/0 |
| `blackhole-currency-01` | currency / 黑洞 | currency / 崩溃 | ✗ | ✓ | 13 | 3430 | 4698 | 23772 | 126474 | 0.0693 | 56.3 | submit | 0/0 |
| `blackhole-frontend-01` | frontend / 黑洞 | 未提交 | ✗ | ✗ | 20 | 106 | 6415 | 45051 | 510611 | 0.1396 | 80.4 | max_steps | 0/0 |
| `blackhole-product-catalog-01` | product-catalog / 黑洞 | flagd / 延迟 | ✗ | ✗ | 6 | 36 | 2188 | 22356 | 80915 | 0.0470 | 26.6 | submit | 0/0 |
| `blackhole-quote-01` | quote / 黑洞 | quote / 黑洞 | ✓ | ✓ | 11 | 61 | 2607 | 13317 | 96932 | 0.0394 | 32.2 | submit | 1/0 |
| `crash-astronomy-db-01` | astronomy-db / 崩溃 | 未提交 | ✗ | ✗ | 20 | 114 | 5361 | 30139 | 353491 | 0.0999 | 64.3 | max_steps | 0/0 |
| `crash-cart-01` | cart / 崩溃 | cart / 黑洞 | ✗ | ✓ | 8 | 50 | 3046 | 18604 | 82276 | 0.0468 | 37.4 | submit | 0/0 |
| `crash-checkout-01` | checkout / 崩溃 | checkout / 崩溃 | ✓ | ✓ | 5 | 29 | 1589 | 9757 | 27603 | 0.0229 | 20.6 | submit | 0/0 |
| `crash-currency-01` | currency / 崩溃 | currency / 黑洞 | ✗ | ✓ | 12 | 72 | 4134 | 24157 | 168376 | 0.0678 | 49.5 | submit | 1/0 |
| `crash-email-01` | email / 崩溃 | email / 崩溃 | ✓ | ✓ | 7 | 3423 | 2934 | 15105 | 47077 | 0.0417 | 33.0 | submit | 1/0 |
| `crash-frontend-01` | frontend / 崩溃 | frontend / 黑洞 | ✗ | ✓ | 6 | 38 | 2142 | 15391 | 55123 | 0.0355 | 25.7 | submit | 0/0 |
| `crash-product-catalog-01` | product-catalog / 崩溃 | flagd / 延迟 | ✗ | ✗ | 12 | 70 | 4066 | 24363 | 143041 | 0.0652 | 48.3 | submit | 0/0 |
| `latency-cart-800` | cart / 延迟 | 未提交 | ✗ | ✗ | 20 | 4181 | 4442 | 26197 | 288543 | 0.0880 | 57.2 | max_steps | 1/0 |
| `latency-checkout-800` | checkout / 延迟 | flagd / 延迟 | ✗ | ✗ | 17 | 3196 | 6811 | 39718 | 288366 | 0.1157 | 83.3 | submit | 0/0 |
| `latency-currency-800` | currency / 延迟 | currency / 延迟 | ✓ | ✓ | 14 | 3441 | 5325 | 30185 | 201043 | 0.0879 | 47.8 | submit | 2/0 |
| `latency-email-800` | email / 延迟 | 未提交 | ✗ | ✗ | 20 | 3473 | 7482 | 33302 | 341230 | 0.1166 | 84.0 | max_steps | 1/0 |
| `latency-product-catalog-800` | product-catalog / 延迟 | recommendation / 延迟 | ✗ | ✗ | 7 | 39 | 1981 | 17889 | 65290 | 0.0388 | 25.3 | submit | 0/0 |
| `memleak-email-10000x` | email / 内存泄漏 | email / 内存泄漏 | ✓ | ✓ | 6 | 3632 | 1827 | 11478 | 34217 | 0.0305 | 20.5 | submit | 1/0 |
| `memleak-email-1000x` | email / 内存泄漏 | email / 内存泄漏 | ✓ | ✓ | 7 | 3388 | 2011 | 9710 | 36357 | 0.0292 | 22.6 | submit | 1/0 |
| `misconfig-cart-75` | cart / 错配 | valkey-cart / 延迟 | ✗ | ✗ | 16 | 3819 | 5543 | 27594 | 252219 | 0.0912 | 65.5 | submit | 2/0 |
| `misconfig-checkout-on` | checkout / 错配 | payment / 黑洞 | ✗ | ✗ | 16 | 3918 | 5326 | 29429 | 179655 | 0.0853 | 62.7 | submit | 1/0 |
| `misconfig-payment-100` | payment / 错配 | payment / 错配 | ✓ | ✓ | 9 | 51 | 2433 | 14938 | 80719 | 0.0390 | 29.5 | submit | 0/0 |
| `misconfig-payment-50` | payment / 错配 | payment / 错配 | ✓ | ✓ | 11 | 57 | 2470 | 13675 | 98300 | 0.0393 | 33.3 | submit | 0/0 |
| `misconfig-payment-75` | payment / 错配 | 未提交 | ✗ | ✗ | 20 | 114 | 8054 | 34805 | 371618 | 0.1211 | 100.2 | max_steps | 0/0 |
| `misconfig-payment-90` | payment / 错配 | payment / 错配 | ✓ | ✓ | 9 | 55 | 3003 | 17506 | 89655 | 0.0459 | 34.3 | submit | 0/0 |
| `misconfig-pc-OLJCESPC7Z` | product-catalog / 错配 | product-catalog / 延迟 | ✗ | ✓ | 18 | 3723 | 8061 | 46818 | 446041 | 0.1472 | 93.0 | submit | 2/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 15 |
| 工具重试 | 0 |
| 步数熔断触发 | 5 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 22 |
| 工具调用总数 | 580 |
| API 请求总数 | 343 |
| token 合计 | 输入 40713，输出 118073，缓存写 670132，缓存读 5125424 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
