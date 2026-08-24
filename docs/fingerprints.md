# 故障指纹实测表

对照 [fault_schema.md](fault_schema.md) §3 / §5。

采集环境：opentelemetry-demo 3.0.0（分支 `p2-baseline`，含 [resource_audit.md](resource_audit.md) 的限额覆盖与端口钉死），
宿主机 16 核 / 62G。两轮均用**同一探针版本**（含 009 后的修订：span 按 `startTime` 过滤、
计数器差分算速率、`heartbeat_age_s` 取 `timestamp(target_info)` 真实样本时刻、分位数线性插值）。

- `crash / cart`：`kill_container`，`t_inject` = 2026-08-23T23:29:56Z，`t_revert` = 23:32:02Z
- `blackhole / cart`：`drop_inbound`（`iptables -I INPUT -p tcp --dport 7070 -j DROP`，只堵服务端口），
  `t_inject` = 23:25:39Z，`t_revert` = 23:27:43Z

时序均为 §2 默认值 60 / 120 / 60。

---

## 指纹表

| 字段 | **crash** baseline | **crash** during | **crash** after | **blackhole** baseline | **blackhole** during | **blackhole** after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `caller_spans_total` | 60 | 73 | 43 | 54 | **0** | 80 |
| `caller_error_spans` | 0 | **73** | 12 | 0 | **0** | 0 |
| 时延 `<100ms` 占比 | — | 45.2% | 100% | — | 无数据 | — |
| 时延 `>10s` 占比 | — | 45.2% | 0% | — | 无数据 | — |
| `caller_error_p50_ms` | — | 5 399.08 | 0.44 | — | 无数据 | — |
| `caller_error_p95_ms` | — | 68 187.17 | 1.78 | — | 无数据 | — |
| `heartbeat_age_s` | 0.4 | **5.4** | 11.4 | 36.5 | **41.5** | 45.5 |
| `heartbeat_samples_in_window` | 3 | 3 | 2 | 4 | 6 | 4 |
| `req_rate_per_s` | 1.2167 | 0.05 | **null**（1 样本） | 2.5667 | **3.4** | 0.1333 |
| `logs.log_lines` | 73 | **0** | 42 | 61 | **0** | **151** |
| 错误出现延迟 | — | **1.83 s** | — | — | **无错误** | — |
| 恢复延迟 | — | — | **12.42 s** | — | — | **无错误可恢复** |

`error_messages_top3` 原文：

- **crash / during**：`14 UNAVAILABLE: No connection established. Last error: Error: connect EHOSTUNREACH 172.18.0.21:7070. Resolution note: ` ×73
- **crash / after**：同上 ×12（撤除后的尾巴）
- **blackhole / 全部三窗**：**空**。没有任何报错 span，因此没有错误文字。

### blackhole 的窗口外复查

`during` 窗内 `caller_spans_total = 0` 一度像是探针漏采，故把窗口拉宽复查
（须避开紧接着的 crash 轮次，其 `t_inject` 在 blackhole 的 `TI+257s`）：

| 窗口 | `caller_spans_total` | `caller_error_spans` |
| --- | ---: | ---: |
| `[TI, TI+120s]`（默认 observe_s） | 0 | 0 |
| `[TI, TI+180s]` | 136 | **0** |
| `[TI, TI+250s]` | 207 | **0** |

拉到 250 秒仍是**零报错**。多出来的 span 全部是撤除之后恢复正常的成功调用。

---

## latency / cart　（调试档，待入库档确认）

原语 `delay_outbound`（`tc netem delay 800ms` 挂容器出口，u32 匹配 `tcp sport=7070`，
只延迟 `cart` 服务端口发出的响应包），时序 **30 / 60 / 30**（调试档，非入库档）。
`t_inject` = 2026-08-24T19:19:24Z，`t_revert` = 19:20:30Z。

> **本行数字来自调试档，不得写入准入门判定。** 按决策 012，指纹表的唯一数据源是入库档
> （60/120/60）；本行仅记录原语首次跑通的功能验证结果，入库档实测后替换。

| 字段 | baseline `[19:18:50Z, 19:19:20Z]` | during `[19:19:24Z, 19:20:24Z]` | 右移 |
| --- | ---: | ---: | ---: |
| `caller→cart` n | 29 | 72 | — |
| `caller→cart` min | 1.41 ms | **801.98 ms** | +800.57 |
| `caller→cart` p50 | 2.43 ms | **802.63 ms** | **+800.20** |
| `caller→cart` p90 | 3.48 ms | **803.63 ms** | **+800.15** |
| `caller→cart` p95 | 3.77 ms | **803.87 ms** | **+800.10** |
| `caller→cart` max | 3.91 ms | 1015.59 ms | +1011.68 |
| `caller_error_spans` | 0 | **0** | 不增 |
| `cart→valkey-cart` p95 | 0.68 ms | 0.72 ms | **+0.04** |
| `cart→flagd` p95 | 1.44 ms | 3.89 ms | +2.45 |

### 四项功能核对

| 项 | 判定 | 数字 |
| --- | --- | --- |
| (a) `caller→cart` 整体右移落在 700–900ms | **通过** | p50 +800.20 / p90 +800.15 / p95 +800.10 ms，三个分位数全部落在区间内；`min` 也从 1.41ms 抬到 801.98ms，说明是**整体平移**而非长尾拖动 |
| (b) 注入期报错 span = 0 | **通过** | baseline 0 → during 0。印证决策 010、011：本系统 gRPC 无 deadline，延迟只产生「慢」不产生「错」 |
| (c) 下游未被误伤 | **通过** | `cart→valkey-cart` p95 **+0.04ms**（n 49→130），完全没动，证明 u32 的 sport 过滤生效 |
| (d) revert 后无残留 | **通过** | `tc qdisc show dev eth0` 仅剩 `qdisc noqueue 0: root refcnt 2` |

关于 (c) 的 `cart→flagd`：p95 +2.45ms 看着像动了，但基线本身只有 1.44ms、样本仅 6 条，
during 也只有 14 条，绝对值全在 1–4ms 量级 —— 这是小样本的抖动，不是 800ms 量级的右移。
真正能证明过滤生效的是 `valkey-cart`（样本 49→130，p95 纹丝不动）。若入库档下 `flagd`
仍有同向偏移，需单独查（`cart` 调 `flagd` 的连接可能复用了不同路径）。

---

## crash vs blackhole 分辨结论

| 字段 | 可分？ | 依据 |
| --- | --- | --- |
| `caller_spans_total` | **可分（最强判据）** | crash 期 73 条（全部报错），blackhole 期 **0 条**。前者"吵"，后者"哑"，形态相反。 |
| `caller_error_spans` | **可分** | 73 vs 0。 |
| `error_messages_top3` | **可分，但不是原假设的方式** | 不是"地址不可达类 vs 超时类"两种文字，而是 **有错误文字（`EHOSTUNREACH` ×73）vs 一条 span 都没有**。blackhole 根本不产生错误文字。 |
| 时延分布 / p50 / p95 | **不可分** | blackhole 期无任何完成的 span，无时延可测；crash 的双峰（`<100ms` 45.2%、`>10s` 45.2%）没有可比对象。 |
| `heartbeat_age_s` | **不可分** | crash 期 cart 已死 120 秒，`heartbeat_age_s` 却只有 **5.4 s** —— collector 在服务死后仍继续导出该 series，Prometheus 照常拿到新样本。两类的取值区间（crash 0.4→11.4、blackhole 36.5→45.5）差异完全来自 60s 指标粒度的相位噪声（±60s），与是否注入无关。该粒度来自 SDK 导出间隔（经 OTLP 推送，Prometheus 无 scrape），2026-08-24 已降为 15s，见决策 013。 |
| `heartbeat_samples_in_window` | **不可分** | 3 vs 6，同属相位噪声量级。 |
| `req_rate_per_s` | **不可分** | crash 0.05、blackhole **3.4**（比自身 baseline 2.5667 还高）。60s 指标粒度在 120 秒窗内只有 2 个样本，差分跨越注入边界，被注入前的计数值污染。该粒度来自 SDK 导出间隔（经 OTLP 推送，Prometheus 无 scrape），2026-08-24 已降为 15s，见决策 013。 |
| `logs.log_lines` | **不可分** | 两类都归零（73→0 / 61→0）。cart 只在处理请求时记日志，请求进不来就都不记。 |
| 撤除后的积压回放 | **可分（次要判据）** | blackhole 的 `after` 窗日志 **151** 条，是自身 baseline（61）的 2.5 倍 —— TCP 重传的请求在规则撤除后一次性涌入。crash 的 `after` 是 42 条，低于 baseline 73（容器刚重启）。 |

### 为什么 blackhole 是"哑"的

`iptables ... -j DROP` 丢包而不回 RST，调用方的 TCP 连接卡在重传退避里
（Linux `tcp_retries2` 默认约 15 次、合计十几分钟）。而 demo 的 frontend 调用 cart
时没有设置足以在 120 秒内触发的 gRPC deadline，所以调用方既不成功也不报错，就是**挂着**。
规则一撤，重传立刻成功，请求补跑完成 —— 全程一条错误 span 都不产生。

也就是说，在这个被测系统里，`blackhole` 的真实表现是**"卡住"而不是"超时"**。
§3 原先预期的"等到超时才报错（耗时 ≈ 超时值）"没有发生。

---

## symptom 探针阈值建议

§5 现定义 `crash` / `blackhole` 的 symptom 为「调用方对 B 的错误 span 数 > N」。

**`crash`：N = 20**（针对 `cart`）。baseline 为 0，during 为 73，N = 20 约为 during 的 27%，
留 3.6 倍余量。取 20 而非上一轮建议的 10，是因为本轮 `after` 窗测到 **12 条**恢复尾巴
（上一轮只有 3 条）——N 若低于 12，`recovered` 判据会把恢复尾巴误判成"仍有症状"。

更干净的做法是把 `recovered` 的判定窗从 `t_revert` 起算改为从 **`t_revert + 30s`** 起算：
实测两轮的错误尾巴分别在 `t_revert+6.48s` 和 `t_revert+12.42s` 结束，30 秒足够跳过，
且不必为了迁就尾巴而抬高 N、牺牲 symptom 的灵敏度。

**`blackhole`：按错误数判的旧定义不可用。** 错误 span 数恒为 0，`0 > N` 永远为假，
按错误数判该类的卡**一张都通不过验证**。该类的 symptom 已改判
`caller_spans_total` **降至 ≈ 0**（baseline 54 → during 0），即「哑」而非「错」。
具体阈值需在其余靶子上各跑一轮再定 —— 本轮只测了 `cart`，不外推。
