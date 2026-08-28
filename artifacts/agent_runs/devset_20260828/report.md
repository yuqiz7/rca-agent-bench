# Agent 评测报告 devset_20260828

- 模型：`claude-sonnet-5`
- 卡集：19 张（成功执行 19 张）
- 开始：2026-08-28T19:50:19Z　总墙钟：517.0s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 52.6%（10/19） |
| service-only 准确率（参考列） | 84.2%（16/19） |
| 平均诊断步数 | 4.42 |
| 单卡平均 token 成本 | $0.0563 |
| p95 端到端延迟 | 74.0s |

辅助：单卡平均 token 用量 38080（含缓存读写）；本次合计 $1.0698。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-cart-01` | cart / 黑洞 | cart / 黑洞 | ✓ | ✓ | 6 | 12 | 5400 | 17898 | 55351 | 0.1098 | 64.1 | submit | 0/0 |
| `blackhole-currency-01` | currency / 黑洞 | currency / 延迟 | ✗ | ✓ | 6 | 12 | 2725 | 12356 | 32491 | 0.0647 | 31.6 | submit | 0/0 |
| `blackhole-frontend-01` | frontend / 黑洞 | frontend / 黑洞 | ✓ | ✓ | 4 | 8 | 4374 | 12137 | 22692 | 0.0786 | 48.2 | submit | 0/0 |
| `blackhole-quote-01` | quote / 黑洞 | quote / 黑洞 | ✓ | ✓ | 4 | 8 | 1199 | 9757 | 18929 | 0.0402 | 13.5 | submit | 0/0 |
| `crash-cart-01` | cart / 崩溃 | cart / 黑洞 | ✗ | ✓ | 4 | 8 | 2690 | 9746 | 20373 | 0.0554 | 30.1 | submit | 0/0 |
| `crash-currency-01` | currency / 崩溃 | currency / 黑洞 | ✗ | ✓ | 6 | 12 | 3787 | 15079 | 46229 | 0.0848 | 45.2 | submit | 0/0 |
| `crash-frontend-01` | frontend / 崩溃 | frontend / 黑洞 | ✗ | ✓ | 4 | 8 | 1810 | 9977 | 23077 | 0.0477 | 19.5 | submit | 0/0 |
| `crash-product-catalog-01` | product-catalog / 崩溃 | flagd / 延迟 | ✗ | ✗ | 2 | 4 | 1041 | 8815 | 6789 | 0.0338 | 11.7 | submit | 0/0 |
| `latency-cart-800` | cart / 延迟 | cart / 延迟 | ✓ | ✓ | 3 | 6 | 943 | 7506 | 10026 | 0.0302 | 11.5 | submit | 0/0 |
| `latency-checkout-800` | checkout / 延迟 | checkout / 延迟 | ✓ | ✓ | 6 | 12 | 6739 | 18510 | 45510 | 0.1228 | 74.0 | submit | 0/0 |
| `latency-currency-800` | currency / 延迟 | currency / 延迟 | ✓ | ✓ | 3 | 6 | 981 | 7536 | 8445 | 0.0304 | 11.3 | submit | 0/0 |
| `latency-email-800` | email / 延迟 | checkout / 延迟 | ✗ | ✗ | 8 | 16 | 4259 | 14926 | 57640 | 0.0915 | 49.1 | submit | 0/0 |
| `latency-product-catalog-800` | product-catalog / 延迟 | product-catalog / 延迟 | ✓ | ✓ | 4 | 8 | 1439 | 8622 | 17453 | 0.0395 | 15.9 | submit | 0/0 |
| `memleak-email-10000x` | email / 内存泄漏 | email / 延迟 | ✗ | ✓ | 2 | 4 | 345 | 5417 | 3512 | 0.0177 | 4.6 | submit | 0/0 |
| `memleak-email-1000x` | email / 内存泄漏 | email / 延迟 | ✗ | ✓ | 4 | 8 | 1419 | 7746 | 14913 | 0.0366 | 17.1 | submit | 0/0 |
| `misconfig-checkout-on` | checkout / 错配 | payment / 崩溃 | ✗ | ✗ | 7 | 14 | 3239 | 13462 | 42006 | 0.0745 | 34.5 | submit | 1/0 |
| `misconfig-payment-100` | payment / 错配 | payment / 错配 | ✓ | ✓ | 3 | 6 | 796 | 8056 | 10319 | 0.0302 | 8.5 | submit | 0/0 |
| `misconfig-payment-90` | payment / 错配 | payment / 错配 | ✓ | ✓ | 3 | 6 | 651 | 7499 | 8926 | 0.0271 | 7.7 | submit | 0/0 |
| `misconfig-pc-OLJCESPC7Z` | product-catalog / 错配 | product-catalog / 错配 | ✓ | ✓ | 5 | 10 | 1548 | 13667 | 24579 | 0.0546 | 18.4 | submit | 0/0 |

（拦截列为 `参数校验拦截数 / 工具重试数`。）

## 四道保险与用量合计

| 项 | 值 |
| --- | --- |
| 参数校验拦截 | 1 |
| 工具重试 | 0 |
| 步数熔断触发 | 0 |
| 成本熔断触发 | 0 |
| 未提交（提示后仍未调 submit） | 0 |
| API 错误终止 | 0 |
| 正常 submit 终止 | 19 |
| 工具调用总数 | 138 |
| API 请求总数 | 84 |
| token 合计 | 输入 168，输出 45385，缓存写 208712，缓存读 469260 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
