# Agent 评测报告 holdout11_agent_20260906

- 模型：`claude-sonnet-5`
- 卡集：11 张（成功执行 11 张）
- 开始：2026-09-06T16:42:48Z　总墙钟：330.7s
- 计价：`https://platform.claude.com/docs/en/about-claude/pricing`（抓取日期 2026-08-28）
- 运行参数：步数上限 20，单卡成本上限 $0.2，工具重试 2，effort=high，缓存=True

## 四指标

| 指标 | 数值 |
| --- | --- |
| top-1 准确率（service + fault_type 全对） | 81.8%（9/11） |
| service-only 准确率（参考列） | 81.8%（9/11） |
| 平均诊断步数 | 5.18 |
| 单卡平均 token 成本 | $0.0681 |
| p95 端到端延迟 | 63.1s |

辅助：单卡平均 token 用量 54986（含缓存读写）；本次合计 $0.7495。

## 逐卡明细

| 卡 | 真值 | 预测 | top-1 | service | 步数 | in | out | 缓存写 | 缓存读 | 成本 $ | 墙钟 s | 终止 | 拦截 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `blackhole-payment-01` | payment / 黑洞 | payment / 黑洞 | ✓ | ✓ | 5 | 10 | 2835 | 12261 | 32947 | 0.0656 | 32.9 | submit | 0/0 |
| `blackhole-shipping-01` | shipping / 黑洞 | shipping / 黑洞 | ✓ | ✓ | 7 | 14 | 5681 | 22948 | 75920 | 0.1294 | 63.1 | submit | 0/0 |
| `crash-payment-01` | payment / 崩溃 | payment / 崩溃 | ✓ | ✓ | 5 | 10 | 2173 | 12100 | 33030 | 0.0586 | 27.0 | submit | 0/0 |
| `crash-shipping-01` | shipping / 崩溃 | shipping / 崩溃 | ✓ | ✓ | 7 | 14 | 2996 | 12945 | 51142 | 0.0726 | 33.2 | submit | 0/0 |
| `latency-ad-800` | ad / 延迟 | frontend / 延迟 | ✗ | ✗ | 6 | 12 | 2662 | 17620 | 48965 | 0.0805 | 29.8 | submit | 0/0 |
| `latency-astronomy-db-3000` | astronomy-db / 延迟 | astronomy-db / 延迟 | ✓ | ✓ | 5 | 10 | 3010 | 16081 | 42875 | 0.0789 | 34.6 | submit | 0/0 |
| `latency-payment-800` | payment / 延迟 | payment / 延迟 | ✓ | ✓ | 3 | 6 | 722 | 9285 | 11693 | 0.0328 | 9.3 | submit | 0/0 |
| `latency-quote-800` | quote / 延迟 | quote / 延迟 | ✓ | ✓ | 3 | 6 | 1469 | 8859 | 12384 | 0.0393 | 16.7 | submit | 0/0 |
| `latency-recommendation-3000` | recommendation / 延迟 | recommendation / 延迟 | ✓ | ✓ | 3 | 6 | 904 | 9256 | 12476 | 0.0347 | 10.7 | submit | 0/0 |
| `blackhole-ad-01` | ad / 黑洞 | frontend / 延迟 | ✗ | ✗ | 7 | 14 | 2276 | 15806 | 64345 | 0.0752 | 26.0 | submit | 0/0 |
| `blackhole-recommendation-01` | recommendation / 黑洞 | recommendation / 黑洞 | ✓ | ✓ | 6 | 12 | 4167 | 12899 | 40003 | 0.0819 | 46.9 | submit | 0/0 |

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
| 正常 submit 终止 | 11 |
| 工具调用总数 | 96 |
| API 请求总数 | 57 |
| token 合计 | 输入 114，输出 28895，缓存写 150060，缓存读 425780 |

## 泄漏自检

每张卡在发出首个 API 请求前均通过 fail-closed 自检：fixed_prompt_and_tools, no_card_id, no_forbidden_keys, user_turn_subset_of_task_json。

agent 输入只含 `trigger` 与 `agent_visible_symptom` 两项；`card_id` 被显式排除——它虽在 `task.json` 白名单内（管线按它寻址证据包），但 `crash-cart-01` 这样的取值字面写着 (service, class) 两半答案。
