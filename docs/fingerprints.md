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

## 指纹对照表 v1.1（定稿，2026-08-24）

三类数据源 `full3_cart_205013`，两类见下方小节。

三类故障在 `cart` 上的入库档实测。观测点见决策 016：`immediate` = `t_revert` 即刻，
`harvest` = `t_end + settle`（150s）。数字逐格抄自
`scripts/out/full3_cart_205013/*/probes.json` 与 `window_baseline.json`。

| 字段 | `crash` | `blackhole` | `latency`（800ms） |
| --- | --- | --- | --- |
| 基线 spans / err | 53 / **0** | 45 / **0** | 69 / **0** |
| 基线 p50 | 2.51 ms | 2.57 ms | 2.36 ms |
| **immediate** spans / err | **89 / 89** | **0 / 0** | **120 / 0** |
| **immediate** p50 | 0.52 ms | — | 802.67 ms |
| **harvest** spans / err | **90 / 90** | **59 / 0** | **123 / 0** |
| **harvest** p50 | 0.51 ms | **68 627.71 ms** | 802.67 ms |
| **`in_flight_at_revert`** | **1** | **59** | **3** |
| symptom 读快照 | `harvest` | `immediate` | `harvest` |
| `recovered` 基线速率 | 0.8833 /s | 0.75 /s | 1.15 /s |
| `recovered` 恢复速率 | 1.1 /s | 1.0333 /s | 0.9333 /s |
| `recovered` 阈值 | ≥ 0.4417 /s | ≥ 0.375 /s | ≥ 0.575 /s |
| **独占特征** | **有报错**：两个观测点都是 89–90 条报错 span，另两类全程 0 报错 | **撤除后积压回放**：immediate 全哑（0 条），harvest 被 59 条 p50 68.6s 的 span 填满，`in_flight` ≈ 注入期全部请求 | **常数右移无在途**：两个观测点 p50 都是 802.67ms（基线 2.36ms），报错 0，`in_flight` 仅 3 |

### misconfig / mem_leak（决策 018 第二部分，数据源 `judge_222727` + `rerun_cart_225459`）

| 字段 | `misconfig`（`adFailure=on`） | `mem_leak`（`emailMemoryLeak=10000x`） |
| --- | --- | --- |
| 靶子 | `ad` | `email` |
| 症状落点 | **目标自身 server span**（调用方 span 零报错） | 容器内存 |
| 受影响方法 | `GetAds`（调用 23 次），实际 ratio **0.1** | — |
| 阈值 | N = `max(2, ⌈0.5×0.1×23⌉)` = **2** | `max(10, 0.15×80.7)` = **12.11** MiB |
| 实测 | 报错 **5** | 内存 80.7 → **131.8**（max 139.3）MiB，增长 **51.1** |
| 基线 | 自身报错 **0** | 增长率 0.6 MiB/min |
| `recovered` | 恢复窗自身报错 **0** | 恢复窗增长率 **1.4** MiB/min（≤ 0.6+1） |
| **独占特征** | 调用方全绿、**目标自身报错**；`caller_all_dur` 无右移 | 内存单调上涨、span 数与报错均无异常 |

另两组同批数字：`cartFailure=50%` 首跑 `EmptyCart` 0/6 **失败**、重跑 2/4 通过
（该方法仅 3.9 /min，概率型判定天然不稳，见决策 018）；`emailMemoryLeak=1000x`
58.5 → 94.0 MiB、增长 35.5（阈值 10）通过。

**`mem_leak` 类当前只有 `email` 一个可用靶子** —— `recommendationCacheFailure`
实测 0.1 MiB/min，120s 窗不可判定（O-P2-8）。

三类九项探针全过。三个独占特征互不重叠：看报错数分出 `crash`，看 `in_flight_at_revert`
（59 vs 1/3）分出 `blackhole`，看「两快照 p50 相同且为常数偏移」分出 `latency`。

### 两条限定

**1. `crash` 的耗时形态不稳定，不作判据。** 同一原语同一靶子四轮测到三种形态：
p50 分别为 **65 786 ms / 1 529 ms / 0.51 ms**，错误文字两种（**`ETIMEDOUT`** 与
**`EHOSTUNREACH`**）。v1.0 因此以**报错数**而非耗时形态作 `crash` 判据。成因假设
（调用方 ARP 邻居缓存是否过期）未验证，见 [open_items.md](open_items.md) O-P2-5。

**2. 本表阈值只对 `cart` 标定。** `cart` 是高流量靶子（基线约 0.75–1.15 span/s）。
其余 15 个靶子的 `N`、`k` 与 `blackhole` 的基线比例需在 W2 **逐靶标定** ——
低流量靶子在 120 秒注入窗内可能根本达不到 `N = 20`。

---

## 指纹表

> 历史过程记录，定稿见顶部「指纹对照表 v1.0」。

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

## 入库档三类指纹（runner `full3_cart_205013`，settle 150s，2026-08-24）

> 历史过程记录，定稿见顶部「指纹对照表 v1.0」。

批次三周期九项探针全过。观测点见决策 016：`immediate` = `t_revert` 即刻，
`harvest` = `t_end + settle`。`in_flight` = harvest 条数 − immediate 条数。

| 类 | 基线（60s） | **immediate**（t_revert） | **harvest**（t_end+150s） | `in_flight_at_revert` | symptom 读 | 判定 |
| --- | --- | --- | --- | ---: | --- | --- |
| `crash` | 53 spans / 0 err | **89 spans / 89 err** / p50 0.52ms | **90 spans / 90 err** / p50 0.51ms | **1** | `harvest` | 通过（90 > 20） |
| `blackhole` | 45 spans / 0 err | **0 spans / 0 err** | **59 spans / 0 err** / p50 68 627.71ms | **59** | `immediate` | 通过（0 < 4.5） |
| `latency` | 69 spans / 0 err / p50 2.36ms | **120 spans / 0 err** / p50 802.67ms | **123 spans / 0 err** / p50 802.67ms | **3** | `harvest` | 通过（右移 +800.31ms ≥ 640） |

`in_flight_at_revert` 三个数把 016 的机制讲清楚了：`blackhole` 的 **59** 条全部是被
`DROP` 卡住、撤除后才完成的调用（harvest 里 p50 达 68.6 秒 ≈ 注入窗的一半）；
`crash` 与 `latency` 分别只有 1 与 3 条，撤除时几乎没有在飞行中的调用。

`recovered` 三类全过（按速率比较，决策 016）：

| 类 | 基线速率 | 恢复窗速率 | 阈值 |
| --- | ---: | ---: | ---: |
| `crash` | 53/60s = 0.8833/s | 33/30s = **1.1/s** | ≥ 0.4417/s |
| `blackhole` | 45/60s = 0.75/s | 31/30s = **1.0333/s** | ≥ 0.375/s |
| `latency` | 69/60s = 1.15/s | 28/30s = **0.9333/s** | ≥ 0.575/s |

> ④ 手工测量的数字（本文件上方各节）取自 **`t_revert` 即刻**附近，因此
> **≈ `immediate` 观测点**，与本表 immediate 列可比，与 harvest 列不可比。

### latency 下游未误伤（入库档复核）

| 边 | 基线 p95 | 注入期 p95 | 偏移 |
| --- | ---: | ---: | ---: |
| `cart → valkey-cart` | 0.67 ms (n=138) | 0.76 ms (n=218) | **+0.09 ms** |
| `cart → flagd` | 1.65 ms (n=18) | 1.59 ms (n=26) | **−0.06 ms** |

`caller → cart` 本轮 `min 801.76 / p50 802.67 / p95 803.98 ms` —— 整体平移，最小值也抬到 801.76ms。

**flagd 抖动结论更新**：调试档那轮测到 `+2.45ms`，`full2` 测到 `−0.97ms`，本轮 `−0.06ms`
—— **两轮反向偏移，确认为小样本抖动**（基线样本仅 8–18 条、绝对值全在 1–4ms 量级），
不是注入误伤。真正的证据是 `valkey-cart`：三轮样本 105–218 条，偏移始终在 ±0.1ms 内。

### misconfig 变体：下游边消失形态（2026-08-27 ET 定稿）

代表卡：`set_flag checkout paymentUnreachable=on`。三条特征：

1. **目标自有 span 报错** —— `checkout` 的 `PlaceOrder` 8/26 实测 **11/11 报错**，
   错误原文 `... lookup badAddress on 127.0.0.11:53: server misbehaving`；
2. **目标 → 下游的调用边在 trace 中整条消失** —— 不是报错、是**不存在**。
   gRPC 对 `badAddress:50051` 的名字解析在建连之前就失败，不产生任何已完成的
   client span，`badAddress` 也不作为 peer 出现；注入期 `checkout` 的
   `client_by_peer` 里 `payment` 干净消失；
3. **下游服务健康、零流量** —— `payment` 自有 server span **0 条**、容器 `running`、
   `RestartCount=0`。

**与标准 misconfig 形态的区别**：标准形态是「调用方全绿、目标自有 span 报错」；
本形态在此之上**多了一条边消失 + 下游零流量**。agent 看到「checkout 报错、payment
一条请求都没收到」，很容易误判成下游故障（payment 挂了 / 网络不通），
而真因是 checkout 自己的配置被改。**因此难度标高。**

---

### crash 错误形态 8/23 vs 8/24（结论待确认）

同一原语、同一靶子，四轮测到**三种形态**：

| 轮次 | 错误文字 | `<100ms` 占比 | p50 | min | max |
| --- | --- | ---: | ---: | ---: | ---: |
| 8/23 ④ 手工 | `EHOSTUNREACH` | 45.2% | 5 399.08 ms | 0.4 ms | 71 449.3 ms |
| 8/24 `full_cart_194613`（重查） | **`ETIMEDOUT`** | **0%** | 65 786.23 ms | **17 916.7 ms** | 134 909.71 ms |
| 8/24 `full2_cart_201241` | `EHOSTUNREACH` | 50.0% | 1 529.42 ms | 0.35 ms | 71 427.91 ms |
| 8/24 `full3_cart_205013` | `EHOSTUNREACH` | **70.0%** | **0.51 ms** | 0.29 ms | 39 204.03 ms |

`evidence.json`（`full3`，钩子仅 `kill_container` 采集，只记录不判定）：

```json
{"caller":"frontend","target":"cart","target_ip_before":"172.18.0.14",
 "caller_tcp_syn_retries":"6","target_ip_after":"172.18.0.14",
 "neigh":[
  {"ts":"20:51:24Z","state":"REACHABLE","entry":"172.18.0.14 dev eth0 lladdr 9e:ce:3f:17:ab:ff REACHABLE"},
  {"ts":"20:51:54Z","state":"INCOMPLETE","entry":"172.18.0.14 dev eth0 INCOMPLETE"},
  {"ts":"20:52:34Z","state":"FAILED",    "entry":"172.18.0.14 dev eth0 FAILED"}]}
```

`full2` 的同一钩子测到 `REACHABLE → DELAY → INCOMPLETE`（转到 INCOMPLETE 约慢 40 秒）。

**观察**：两轮 `EHOSTUNREACH` 的邻居缓存都在注入后 40–80 秒内失效；`full3` 转 `FAILED`
更快，快速失败占比也更高（70.0% vs 50.0%），方向一致。`target_ip_after` 与 before 相同，
**IP 变更不是形态差异的原因**。

**结论待确认**：`ETIMEDOUT` 那一轮**没有 evidence 对照**（钩子是之后才加的），
假设「调用方 ARP 邻居缓存中 cart 旧 IP 是否已过期决定形态 —— 缓存有效则 SYN 发往
空 MAC 直到 127s 建连预算耗尽（`ETIMEDOUT`），缓存 `FAILED` 则秒级 `EHOSTUNREACH`」
尚未验证。见 [open_items.md](open_items.md) O-P2-5。

---

## latency / cart（调试档首跑记录，数字已由上方入库档表取代）

> 历史过程记录，定稿见顶部「指纹对照表 v1.0」。

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

> 历史过程记录，定稿见顶部「指纹对照表 v1.0」。

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

> 历史过程记录，定稿见顶部「指纹对照表 v1.0」。

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
