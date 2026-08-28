# 故障场景 schema v1.1

**修订记录**

- 2026-08-24：类别 `dep_timeout` 更名 `blackhole`（决策 011）
- 2026-08-24：**v1.0 定稿** —— 三类（`crash` / `blackhole` / `latency`）定义、三探针判据、
  双观测点冻结（决策 017）；`misconfig` / `mem_leak` 原语待建（W2 首项）；
  `cart` 以外靶子阈值待标定
- 2026-08-24：**v1.1** —— `misconfig` / `mem_leak` 判据定稿（决策 018 第二部分）：
  `misconfig` 判目标**自身** server span 的报错（不是调用方 span），`mem_leak` 判容器
  内存增长；`mem_leak` 可用靶子**仅 `email`**（`recommendationCacheFailure` 120s 窗
  不可判定，见 O-P2-8）

（v1.1 五类齐备。`misconfig` 的可用 flag 与 `mem_leak` 的靶子限制见 [flag_catalog.md](flag_catalog.md)。）

## §1 定位

一个场景 = 一道有唯一标准答案的题；schema = 出题卡的固定格式。80 张卡同格式，验证脚本与判分脚本各写一遍即可覆盖全集。

**根因唯一**：每卡只注入一次，只对应一个 `(service, class)`。级联症状（下游服务报错）是预期现象，不是额外根因。

---

## §2 卡片结构（`scenarios/S###.yaml`，三区）

### 答案区 `private`（agent 不可见）

| 字段 | 说明 |
| --- | --- |
| `target.service` | ∈ `target_enum`（见下） |
| `target.class` | ∈ 五类（见 §3） |
| `injection.primitive` | 注入原语名 |
| `injection.params` | 原语参数 |
| `injection.apply` | 施加命令 |
| `injection.revert` | 撤除命令 |
| `ground_truth.service` | 判分用标准答案（服务） |
| `ground_truth.class` | 判分用标准答案（类别） |
| `ground_truth.aliases[]` | 服务名归一化别名表 |
| `ground_truth.note` | 出题说明，失败分析用 |

### 考生可见区 `agent_view`

| 字段 | 说明 |
| --- | --- |
| `trigger` | 固定为 `Monitoring detected an anomaly in the system.` — 全场景一字不差，不含服务名 |
| `window` | `[t_inject, t_inject + observe_s]` |

这是 agent 的**全部输入**。

### 自检区 `verification` + `admission`

由验证脚本填写，见 §5 / §6。

### `timing`

| 字段 | 初值 |
| --- | --- |
| `warmup_s` | 60 |
| `observe_s` | 120（`mem_leak` 类可覆盖为更长） |
| `cooldown_s` | 60 |

三值为初值，④ 实测指纹后定。

### `target_enum`

由 `docker compose -f compose.yaml -f compose.observability.yaml -f compose.override.yaml config --services` 全集（25 项）剔除纯观测 / 流量发生 / 控制面 / 文档用途的服务后得到，共 **16 项**：

```
ad, astronomy-db, cart, checkout, currency, email, flagd, frontend,
frontend-proxy, image-provider, payment, product-catalog, quote,
recommendation, shipping, valkey-cart
```

剔除的 9 项与理由：

| 服务 | 剔除理由 |
| --- | --- |
| `jaeger` / `prometheus` / `opensearch` / `grafana` | 观测后端，agent 工具面本身 |
| `otel-collector` | 遥测管线，注入即切断证据来源 |
| `load-generator` | 流量发生器 |
| `opamp-server` | collector 配置控制面（定义于 `compose.observability.yaml`） |
| `flagd-ui` / `telemetry-docs` | 辅助 UI / 文档站，仅被 `frontend-proxy` 在启动期 `depends_on`，不在购物请求路径上 |

> 注：`flagd` 保留在 enum 内（被 8 个服务运行时依赖），但它同时是 `misconfig` 类的注入通道，二者重叠需在 ④ 实测时确认是否会造成出题歧义。

---

## §3 五类定义

被注入服务记为 **B**，B 即根因。定义即判分依据；指纹列为**待 ④ 实测确认的预期**，非既定事实。

| class | 定义 | 调用方侧预期 | B 自身预期 |
| --- | --- | --- | --- |
| `crash`（杀容器）<br>**已定稿（015/016）** | B 容器不存活 | **报错 span 大量出现**；错误文字为**地址不可达类**（实测 `EHOSTUNREACH`，非"连接被拒"） | 日志归零 |
| `latency`（注入延迟）<br>**已定稿（015/016）** | B 存活、可达、响应慢，延迟低于调用方超时 | span 变长、错误少 | 正常 |
| `blackhole`（服务端口静默丢包）<br>**已定稿（015/016）**（原名 `dep_timeout`，决策 011 更名） | B 存活但服务端口丢包 | **span 完全静默**：既无成功也无报错，调用方卡在 TCP 重传里，无任何超时错误；撤除后积压回放 | 日志归零 |
| `misconfig`（错误配置）<br>**已定稿（018 第二部分）** | B 配置被改（compose env / flagd 开关） | 收到错误或错误结果 | 自身 span / 日志有错 |
| `mem_leak`（内存泄漏）<br>**已定稿（018 第二部分）**<br>靶子仅 `email` | B 内存持续增长 | 先变慢后报错 | 内存曲线爬升 → OOM 重启 |

> `crash` 与 `blackhole` 两行为 2026-08-23 实测结果，见 [fingerprints.md](fingerprints.md)。
> **观测点前提（决策 016）**：吵 / 哑在 **`immediate`**（`t_revert` 即刻）观测点判，
> 即 ④ 手工测量的时刻。`blackhole` 是**注入期间哑、撤除后回放** —— 在
> `harvest` 观测点看，它的注入窗会被积压回放填满，反而比基线还吵。
>
> 两类的分界是**「吵」与「哑」**：`crash` 期调用方 span 73 条且全部报错，`blackhole`
> 期 0 条。**不可用于分辨的字段**（均已实测证否）：调用方时延（`blackhole` 无完成
> span 可测）、上报心跳（服务已死 120 秒时 `heartbeat_age_s` 仍只有 5.4 s，collector
> 在服务死后继续导出 series）、请求速率（60s 指标粒度在 120 秒窗内仅 2 个样本，该粒度来自
> SDK 导出间隔、经 OTLP 推送，Prometheus 无 scrape；2026-08-24 已降为 15s，见决策 013，
> 差分跨注入边界被污染）、日志行数（两类都归零）。
> `crash` / `blackhole` / `latency` 三行均已由 2026-08-24 入库档实测定稿
> （`full3_cart_205013`，三类九项探针全过，见 fingerprints.md「指纹对照表 v1.0」）；
> `misconfig` / `mem_leak` 两行仍为**未实测的预期**，原语待建。

### 注入原语映射

| 原语 | class |
| --- | --- |
| `kill_container` | `crash` |
| `delay_outbound_on_service_port` | `latency` |
| `drop_inbound_on_service_port` | `blackhole` |
| `set_flag`（flagd 通道，决策 018） | `misconfig` |
| `set_flag`（`emailMemoryLeak` / `recommendationCacheFailure`，决策 018） | `mem_leak` |

### 注入作用面（硬规则）

`latency` 与 `blackhole` 的规则**只作用于 B 的服务端口**：

- `latency` 只延迟**从 B 服务端口发出的响应包**；
- `blackhole` 只丢弃**进入 B 服务端口的请求包**；
- **不得作用于整块网卡。**

否则 B 自身的对外调用与遥测上报会被一并拖慢 / 切断，制造"B 的依赖慢"或"B 崩了"的假象，污染标准答案。

`env_override` 伴随容器重建，属真实变更的伴生现象，不视为泄漏。

---

## §4 agent 工具面与泄漏隔离（四道墙）

**① 注入器运行于宿主机**，不是 compose 内的容器，不进 OTel 管线。

**② 场景元数据零暴露**：`id` / `class` / `title` / `primitive` 名不得出现在环境任何位置 — 无容器 label、无 env、无日志行。

**③ agent 工具面仅五项**，无 shell、无 docker 命令：

| 工具 | 后端 |
| --- | --- |
| `logs_search` | OpenSearch |
| `metrics_query` | Prometheus |
| `trace_query` | Jaeger |
| `topology` | 静态依赖图，从健康期 trace 提取一次后固定 |
| `config_diff` | compose env + flagd 开关 vs 基线快照 |

所有查询受时间栅栏限制：`[window_start − warmup_s, window_end]`。

**④ 入库前 canary 扫描**：以禁词表扫三个后端在窗口内的数据，零命中才入库。

禁词表：场景 id 模式 `S\d{3}`、`chaos`、`netem`、`tc qdisc`、`iptables`、`stress`、`inject`、`scenario`。

> 禁词表**只针对注入器与元数据痕迹**。系统自身产生的证据无论多直白都不算泄漏 — 题目简单 ≠ 题目漏答案。

---

## §5 验证（双层 + 恢复）

三项探针缺一不入库。三者合起来构成"注入前正常 → 注入后异常 → 撤除后正常"的因果证明。

### `injected` — 宿主机机械探针

| class | 探针 |
| --- | --- |
| `crash` | `docker inspect` 状态 ≠ `running` |
| `latency` | 目标 netns 内 `netem` 存在，**且** u32 过滤器的 sport 等于该服务端口（`probe` 把端口编码成 hex 比对，如 `7070` → `1b9e0000/ffff0000`）。netem 在但端口不符判 false。 |
| `blackhole` | 目标 netns 内**限定服务端口**的 iptables 规则存在 |
| `misconfig` | env / flag 当前值 = 注入值 |
| `mem_leak` | 容器内存统计持续上升 |

### `symptom` — 针对被注入服务 B 的一条查询

过阈值即通过。**不得使用"系统内任意异常"类查询。**

**观测点（决策 016）**：注入窗查询两次 —— `immediate` = `t_revert` 即刻，
`harvest` = `t_end + settle`（默认 150s）。基线窗与恢复窗只在 `harvest` 查。
span 只在结束时导出，两类故障的可靠信号出现在不同时刻，单一观测点必然误判其一。

| class | 观测点 | 初值 |
| --- | --- | --- |
| `crash` | `harvest` | 调用方对 B 的错误 span 数 **严格 > N**，`N = max(5, ceil(0.25 × baseline_rate_per_s × inject_s))`（决策 018；`cart` 算得 N = 27，实测报错 90 通过）。固定 20 只对高流量靶子成立，`email`/`payment`/`checkout` 被调仅 4.4/min，120s 窗内约 9 次调用永远达不到。报错要等 127s 建连预算耗尽才集中出现，immediate 会看到 0 条 |
| `blackhole` | `immediate` | 调用方对 B 的 **span 总数**（`caller_spans_total`）**低于基线 × 0.10**（且要求基线 > 0，代码 `b > 0 and d < b*0.10`）—— 该类整链静音、错误 span 恒为 0，用错误数判会永远不通过。静音是机制性的且**只在 immediate 成立**：撤除后积压请求同一秒回放，harvest 会看到比基线还多的 span |
| `latency` | `harvest` | 调用方 → B 的 **`caller_all_dur.p50_ms`** 相对基线**右移 ≥ `delay_ms` × 0.8**，**且报错 span 数不增**（该类只产生「慢」不产生「错」）。`cart` 800ms 入库档实测右移 p50 +800.31ms、报错 0。**判据只看调用方边，没有靶子侧臂** —— `delay_outbound` 只延迟从服务端口发出的响应包（§3 作用面 / 决策 007），netem 排队在应用写完响应之后，靶子自己的 server span 量不到；四张已通过的 latency 卡实测靶子自身 p95 位移 +0.3 / +0.0 / +0.0 / +0.0 ms，而调用方 p95 位移 1922~4727 ms（决策 023）。 |
| `misconfig` | `harvest` | **B 自身** server span 在受影响方法上的报错数 ≥ `max(2, ⌈0.5 × ratio × 该方法调用数⌉)`，**且基线窗 B 自身报错为 0**。`ratio` 由 variant 名解析，另乘代码里的固定概率（`adFailure` 即使 `on` 也只有 0.1）。受影响方法见 [flag_catalog.md](flag_catalog.md)。**症状不在调用方 span 上** —— 实测 `cartFailure=50%` 时调用方 109 条 span 零报错 |
| `mem_leak` | `harvest` | 注入窗 `growth_mib ≥ max(10, 0.15 × first_mib)` **且** `last_mib ≥ first_mib + 阈值`（指标 `container_memory_usage_total_bytes`） |

`N`、`k` 于 ④ 实测后定。

### `recovered`

`revert` 后 `cooldown_s` 内，同一 `symptom` 查询回落至基线，且 `injected` 探针反向通过。

**按每秒速率比较，不比原始条数**（决策 016）：基线窗是 `pre` 秒（入库档 60s），
恢复窗是 `[t_revert+30s, t_end]` 只有 30s，直接比条数等于拿 60 秒的量和 30 秒的量
对撞，恢复正常也会判失败（实测 crash 18 vs 53、blackhole 32 vs 66 两次假失败）。
判据：恢复窗 span 速率 ≥ 基线速率 × 50%，且该类 symptom 在恢复窗判假
（代码 `judge_recovered`：`(not sym_still) and after_rate >= base_rate * 0.50`）。
恢复窗只有一份快照，不适用 symptom 的 immediate / harvest 分流。

`latency` 的 `recovered` 判据是**右移消失**（耗时分布回到基线量级），而非错误数回落 —— 该类全程无错误。

`misconfig` 的 `recovered` 判据是**恢复窗 B 自身 server 报错为 0**；
`mem_leak` 的是**恢复窗增长率（MiB/min）≤ 基线窗增长率 + 1**。

判定窗从 **`t_revert + 30s`** 起算，跳过撤除后的错误尾巴（实测两轮分别在
`t_revert+6.48s` 与 `t_revert+12.42s` 结束）。从 `t_revert` 起算会把尾巴算进来，
逼着把 N 抬高到尾巴之上，白白牺牲 symptom 的灵敏度。

### targeting 型开关的 apply 语义（决策 018 附注，2026-08-27 ET 补）

`demo.flagd.json` 里部分开关带 `targeting` 规则，而 flagd 中 **targeting 的优先级高于
`defaultVariant`**。对这类开关：

- `set_flag` 的 `apply` **不改 `defaultVariant`**，改的是 **`"if"` 命中分支的变体**
  （`off` → `on`），规则条件保持不动；`revert` 照旧从备份整体恢复；
- `probe` 查 OFREP 时**必须带上评估上下文**（`productCatalogFailure` 用
  `{"product_id":"OLJCESPC7Z"}`），否则查到的是**未命中分支**的默认值，
  会把已生效的注入误判成未生效；
- **生效比例 `r` 由目标服务实际请求中命中分支的占比决定**，不是 1.0。
  阈值必须以**实测 r** 计算 —— `productCatalogFailure` 实测 r = 7.6%，
  按 `ratio=1.0` 算会得出 `N=178` 而把正常注入判成失败。

**`in_flight_at_revert`**（决策 016 新增指纹字段）= `harvest` 条数 − `immediate` 条数，
即注入期拨出、撤除后才结束的调用数。`blackhole` 该值等于被卡住的全部请求
（实测 59），`crash` 与 `latency` 接近 0（实测 1 与 3）。

每卡记录 `runs[]`，每轮含 `{ts, injected, symptom, recovered}`。

---

## §6 准入门（四条全过才进 80 题库）

1. **唯一性** — 一次注入、一个 `(service, class)`。
2. **双层验证 + 恢复连续 2 轮全过。**
3. **canary 扫描零命中。**
4. **自动计算 `symptom_locus` ∈ `{self, neighbor, remote}`** — 注入后首个出现错误 / 延迟异常的 span 所属服务，到根因服务在依赖图上的最短距离 `0` / `1` / `≥2`。入库记录，评测报告按此分层报准确率。

候选池按 **100 收 80**。

### `crash` / `blackhole` 的 symptom 判据（2026-08-27 ET 改写，决策 022）

**判据原文**：以下两档**任一成立**即通过。

| 档 | `crash` | `blackhole` |
| --- | --- | --- |
| **调用方边档**（原判据） | 调用方报错 span **>** `N = max(5, ⌈0.25 × 基线速率 × inject_s⌉)` | 调用方 span 数 **<** 基线 × **0.1** |
| **靶子侧档**（新增，两类共用一条） | 靶子**自身作为 server** 的 spanmetrics 请求速率，注入期 **≤ 基线 × 0.1**，**且**基线速率 × `inject_s` **≥ 5** | 同左 |

**基线速率的来源**：Prometheus spanmetrics，`t_inject` 之前 **300 s**，计数器差分。
**不是** runner 那 60 s 静默窗 —— 60 s 对被调约 2 /min 的靶子期望样本数只有 2，
实测多次为 **0**，判据分母为零则卡结构上不可能通过（决策 022）。
取不到系列时回退到 60 s 窗，回退这件事记进 `probes.json` 的 `baseline_rate_source`。

**无 SDK 的靶子**（`valkey-cart` / `astronomy-db`）不产生 server span、没有
spanmetrics 系列，**靶子侧档对它们记「不适用」**，与「不通过」在 `probes.json` 里
分开记 —— 两者混同就又是一个静默的零分母。

**无 SDK 靶子专用的两档（2026-08-28 新增，决策 023 / O-P2-18）**：这两个靶子的
调用方边档也不成立（客户端把故障吞成了别的形状），故再加两档，与上面两档同为或关系：

| 档 | 判据 | 覆盖的形态 |
| --- | --- | --- |
| **台阶档** | 调用边 during `p50_ms` **≥ 1000 ms**，**或** ≥ **100 ×** 基线 p50 | `valkey-cart` blackhole：0.49 → **5702 ms**（约 11 600×）但**零报错**、span 数只掉到 36% |
| **边静默档** | 调用边 during 速率 **≤ 基线 × 0.1**，**且** 基线速率 × `inject_s` **≥ 5** | `astronomy-db` crash：fail-fast 2 条报错后 **119.5 s 静默**（期望约 314 条实收 2 条），p50 **不升反降** |

两条形态成对记在 [fingerprints.md](fingerprints.md)。门槛取值：1000 ms 高于全部靶子
实测的正常边耗时（最大 p50 40.96 ms），低于两个已知台阶；100× 是给低基线边
（`valkey-cart` 0.49 ms）留的相对口子。**这两档只对无 SDK 靶子生效**，
其余 11 个靶子的判据一个字没改。

**为什么要两档**：`frontend` 深度为 0，唯一上游 `frontend-proxy` 不产生能与它配对的
caller span，「从调用方侧看 B 是否变哑」这个问法对它根本不成立；而低流量靶子的
调用方边在 120 s 窗内可能一条 span 都没有。两种情形都只能看靶子自己。

### targeting 型开关的 symptom 判据（2026-08-27 ET，O-P2-13）

**判据原文**：被 targeting 的那个业务 id 上，**注入窗内该 id 的自有 server span 中
报错占比 ≥ 0.5，且报错绝对数 ≥ 5，且基线窗内该 id 报错数为 0。**

不再用概率型的 `N = max(2, ⌈0.5 × ratio × 调用数⌉)`。理由是两类开关的失败形状
根本不同：

- **概率型**（`cartFailure` / `paymentFailure` / `adFailure`）把失败**随机散布在
  全部调用上**，绝对报错数正比于生效比例，按 N 判成立；
- **targeting 型**（`productCatalogFailure`）让**某一个业务实体**的调用 100% 失败、
  其余实体 0% 失败。此时方法级的整体报错率**等于该实体的流量份额**，
  用绝对数判就等于在判**压测器请求该实体的频率** —— 而那个频率由
  `LOAD_GENERATOR_VUS` 与 k6 脚本决定，随时会漂。

**实测依据**：2026-08-27 首批第 16 张 `misconfig-pc-OLJCESPC7Z`，注入完全正常
（`injected=true`、残留干净），`GetProduct` 报错 **35 / 312 = 11.2%**，
恰好等于该商品的流量份额；按 `ratio=1.0` 算出的阈值是 **156**，
把一次正常注入判成失败。改判命中支自身的报错占比后与份额无关。

分组数据来自 `three_signals.py` 的
`traces.self_edges.server_by_business_id[<tag>][<value>]`，标签名形如
`demo.<entity>.id`。判据同时记录**未命中的其余 id 的报错数**，
「只打中一个」这件事在证据里一眼可见。

---

## §7 判分

- agent 输出结构化 JSON `{service, class, evidence[]}`。
- `service` 经 `aliases` 归一化后与 `ground_truth` **精确匹配**；`evidence` 不参与判分，留作失败分析。
- **主指标 top-1** = `(service, class)` 双匹配。
- **副指标** = 仅 `service` 匹配（定位准确率），分开报。
- 不设部分分；不用 LLM 判分。

同一 harness 另记：诊断步数、token 成本、端到端延迟（p95 在评测集级别汇总）。

三基线（关键词启发式 / 无工具单轮 LLM / 换小模型）与 agent 同规则。

---

## §8 实测核对清单

- [x] `timing` 三初值 —— 60/120/60 定稿（决策 012），含 settle 的周期墙钟 390s（决策 016）
- [x] 三类 `symptom` 阈值 —— `crash` N=20、`blackhole` 基线×0.10、`latency` delay×0.8（仅 `cart` 标定，其余靶子 W2 逐靶标定）
- [x] `misconfig` / `mem_leak` 的判据与阈值（决策 018 第二部分）
- [ ] 调用方超时值 — 决定 `latency` 类延迟上限。（`blackhole` 类无此项：实测服务间 gRPC 长连接未设 deadline，250 秒窗内不产生任何超时错误，无 span 耗时可言，见决策 011）
- [x] `crash` 指纹核对（2026-08-24 入库档 `full3_cart_205013`，见 fingerprints.md）
- [x] `blackhole` 指纹核对（2026-08-24 入库档 `full3_cart_205013`，见 fingerprints.md）
- [x] `latency` 指纹核对（2026-08-24 **入库档** `full3_cart_205013`，见 fingerprints.md）
- [x] `misconfig` / `mem_leak` 指纹核对（2026-08-24 入库档 `judge_222727`）
- [x] `mem_leak` 类容器内存指标 —— `container_memory_usage_total_bytes`（docker_stats receiver，标签 `container_name`，实测 10s 一个点）
- [x] flagd 内置故障开关清单 —— 15 个，逐个定位到服务代码判断处（决策 018）

---

## §9 证据包规格（决策 020 / 021）

**编号说明**：本节原定编号为 §6，但 §6「准入门」/ §7「判分」/ §8「实测核对清单」
早已被决策 015 / 016 / 018 与 `run_batch.py` 的注释按号引用，重排会让那些引用全部失准。
故本节与下节顺延为 **§9 / §10**，内容不变。

事后快照模式（决策 020）下，agent 与全部基线**只读证据包**，不查询实时系统。
每张卡一个目录 `evidence/<card_id>/`，由 `scripts/evidence/pack.py` 生成。

### 窗口

打包窗口 = `[t_start − 60 s, t_end + 150 s]`。

- **前 60 s**：给 `rate()[1m]` 留出回看量，同时把基线窗完整包住。
- **后 150 s**：即决策 016 的 settle。span 只在结束时导出，注入期拨出、
  `t_end` 之后才结束的调用必须落在包内，否则重演决策 016 修掉的那个错误。

`manifest.json` 另记三个子窗口，供检测器与人对齐：
`baseline = [t_start, t_inject]`、`inject = [t_inject, t_revert]`、
`recover = [t_revert, t_end]`。

### 五件

| 文件 | 定义 |
| --- | --- |
| `logs.jsonl` | 打包窗内**全部服务**的日志，每行一个 JSON 对象：`ts`（`observedTimestamp`）、`service`（`resource.service.name`）、`body`、`severity`（`severity.text`）、`trace_id`、`span_id`。来源 OpenSearch，索引模式 `otel-logs-*`，按 `observedTimestamp` + `_id` 的 `search_after` 翻页（不用 scroll：无服务端游标状态可漏） |
| `metrics.json` | 固定 PromQL 集合（`scripts/evidence/queries.py`，**所有卡共用**，与卡片的靶子/类别无关）的 `query_range` 结果，步长 **15 s**：每服务请求率 / 错误率 / p95 延迟（均取自 spanmetrics），每服务 `calls_total` / `errors_total` 原始计数器，每容器内存（`container_memory_usage_total_bytes`）与 CPU（`container_cpu_utilization_ratio`）。原始计数器与 `rate()` 同时收：前者是检测器的输入（计数器差分，决策 013 的口径），后者是给人看的仪表盘视角 |
| `traces.json` | 按服务逐个查 Jaeger `/api/traces`（`start` / `end` 为打包窗，`limit` = 5000），合并去重后保留每个 span 的 `traceID` / `spanID` / `parentSpanID` / `service` / `operation` / `start` / `duration` / `status` / **`tags`（白名单，见下）**。是否触顶记在 `limit_hit` 与 `services_hitting_limit`，**不静默截断** |
| `config_diff.txt` | flagd 配置文件（`src/flagd/demo.flagd.json`）与 compose 环境变量（`docker compose <三文件> config` 的 environment 段，`service=KEY=VALUE` 排序）相对基线快照的 unified diff。基线快照存 `evidence/_baseline/`，不存在时由本步从当前干净状态生成一次 |
| `topology.json` | 服务调用拓扑，**全局只生成一次**存 `evidence/_shared/topology.json`；各卡目录下的 `topology.json` 是**引用 + sha256**，不重复存图 |

`manifest.json`（第六个文件，索引）：打包时刻、窗口起止与前后补长、三个子窗口、
基线快照生成时刻、五件各自的 `name` / `path` / `bytes` / `sha256`、各步统计。

### `traces.json` 的 span 标签白名单（2026-08-27 ET，O-P2-16）

原先每个 span 的标签**全部丢弃**，于是「哪个 `product_id` 失败了」这类问题从包里
根本答不出来 —— 而 Jaeger 是内存存储，答不出就是永远答不出（见下条约束）。
现在按白名单保留，键名取自 2026-08-27 跨全部服务 25 分钟实测采样，不是推测：

| 类 | 规则 / 键名 | 实测出现量 |
| --- | --- | --- |
| **业务 id** | 正则 `^demo\..+\.id$` | `demo.product.id`（7332 span）、`demo.order.id`（1818）、`demo.shipping.tracking.id`（606） |
| **报错 / 异常消息** | `error`、`error.type`、`otel.status_code`、`otel.status_description`、`grpc.error_message`、`grpc.error_name` | 报错 span 上 `error` / `otel.status_code` 各 53，`otel.status_description` 16，`grpc.error_*` 各 7 |

**其余标签仍然丢弃。** 用正则而不是清单管业务 id，是为了新的 `demo.*.id` 自动纳入。
**状态码族**（`http.status_code`、`rpc.grpc.status_code`、`http.response.status_code` 等）
**有意不收**：它们挂在数以万计的健康 span 上、且不含消息，而每个 span 自己已经有
`status` 字段。白名单本身写在 `traces.json` 的 `tag_whitelist` 字段里，包自带说明。

**已知缺口**：`exception.message` / `exception.type` / `exception.stacktrace`
不是 span 标签，而是 **span 的 log 字段**，`traces.json` 不保留 span logs，
因此这三个取不到。gRPC 路径的错误消息由 `otel.status_description` 与
`grpc.error_message` 覆盖，HTTP 路径的异常堆栈目前**只能从 `logs.jsonl` 里找**。

### 已知约束：打包必须在卡结束后 30 min 内完成

Jaeger 用**内存存储**，`MEMORY_MAX_TRACES=25000`。在当前流量下实测可回溯窗口约
**30–35 分钟**：50 分钟前的窗口查回来是 **0 条 trace**，30 分钟内的窗口正常返回。

因此：

- **打包必须紧跟周期**（`run_batch.py` 就是这么做的：harvest 之后立刻打包）；
- **重打包同样受这 30 min 限制** —— 超过就再也拿不回来，只能重跑那张卡；
- **包里没有的东西，30 分钟后连出题人也拿不到。** 决策 020 说「证据包里没有的，
  agent 再聪明也拿不到」，这句话对生产侧自己同样成立。

### 拓扑的来源

spanmetrics 只有 `(service_name, span_kind, span_name)`，**没有调用方/被调方的配对标签**，
所以一条边是一次 join：A 有名为 X 的 CLIENT span、B 有归一化后同名的 SERVER span
（归一化 = 去掉 `GET ` / `POST ` 等动词前缀与前导 `/`）。这能解出 gRPC 与具名 HTTP 路由。
解不出两类，两类都由 Jaeger client span 的 peer 标签补（决策 018 对这两个靶子已在用同一手法）：

- **无 SDK 的靶子**（`valkey-cart` / `astronomy-db`）根本没有 server 侧 spanmetrics；
- **泛化的 HTTP client span**（span 名就叫 `POST`）归一化后是空串。

每条边记 `source ∈ {spanmetrics, jaeger_peer}`；spanmetrics 解不出的 client span 名
逐条列在 `spanmetrics_unresolved_client_calls`，**列出来而不是丢掉**。

---

## §10 告警检测器规则（决策 021；规则 5/6 见决策 021 修订，规则 7 见决策 023）

`scripts/evidence/detect.py`。**只读 `evidence/<card_id>/metrics.json`**，
不 import 卡片模块、文件里没有任何 `scenarios/` 路径 —— 检测器要是能看见 ground truth，
告警迟早会开始迎合答案，那条告警就不再是证据（§4 第一道墙）。
回写卡片 `agent_visible_symptom` 的是 `scripts/scenarios/write_symptom.py`，方向单向。

七条规则对**每个**服务（内存规则对每个容器，规则 5/7 对每个 `(service, operation)`）扫：

| # | 规则 | 判据 | 告警文本 |
| --- | --- | --- | --- |
| 1 | 错误率跳升 | 注入期错误数 ≥ `N = max(5, ceil(0.25 × 基线请求速率 × inject_s))` **且** ≥ 2 × 基线错误数（按窗长折算到注入窗） | `elevated error rate on <service>` |
| 2 | 流量消失（**2026-08-27 修订，依据 [O-P2-15](open_items.md)**） | 基线请求率 > 0 **且** 注入期连续 ≥ 2 个采样点为 0 **且** (a) 注入期平均速率 ≤ 基线速率 × 0.1 **且** (b) 零跨度内预期调用数 = 基线速率 × 零跨度秒数 ≥ 5 | `traffic dropped to zero on <service>` |
| 3 | 延迟跳升（**2026-08-27 修订，依据 [O-P2-17](open_items.md)**） | 注入期 p95 ≥ 2 × 基线 p95 **或** Δp95 ≥ **500 ms**（二者满足其一；两侧各取窗内样本的中位数） | `elevated p95 latency on <service>` |
| 4 | 内存越线 | 容器内存相对基线 **+30 MiB** **且**注入期单调上升（采样抖动容差 0.5 MiB） | `memory rising on <container>` |
| 5 | **方法级错误率**（2026-08-27 新增，依据 O-P2-17） | 对每个 `(service, operation)`（spanmetrics 的 `span_name` 维度）：注入期错误数 ≥ `N = max(5, ceil(0.25 × 该方法基线速率 × inject_s))` **且** ≥ 2 × 基线错误数（折算到注入窗） | `elevated error rate on <service>/<operation>` |
| 6 | **实体级集中**（2026-08-27 新增，依据 O-P2-17） | 读 `traces.json`，按白名单里 `demo.<entity>.id` 类标签分组：某 id 值的报错 span **≥ 5** **且**占该 id 全部 span **≥ 50%** | `errors concentrated on <tag>=<value> (<service>)` |
| 7 | **方法级延迟**（2026-08-28 新增，依据 O-P2-17 / 决策 023） | 对每个 `(service, operation)`：注入期 p95 **≥ 2 × 基线 p95 且 Δp95 ≥ 100 ms**，**或** Δp95 **≥ 500 ms**；两窗**各需 ≥ 5 次调用** | `elevated p95 latency on <service>/<operation>` |

- 规则 1 的 `N` 沿用决策 018 的相对阈值形式：固定值只对高流量靶子成立，
  `email` / `payment` / `checkout` 被调约 4.4/min，120 s 窗内总共才约 9 次调用。
- 规则 1 / 2 的计数一律走**原始计数器的正增量之和**，不走 `rate()`：
  计数器归零（容器重启）不会变成负数或巨值，且流量骤停在计数器上是"不再前进"，
  比 `rate()[1m]` 把一次硬停摊平到整整一分钟更利落。
- **规则 3 的绝对档（2026-08-27 修订，依据 O-P2-17）**：只看倍数看不见「给一个本来就慢的
  服务再加一段固定延迟」。`latency-checkout-800` 过了入库门却一条告警都没有 ——
  `checkout` 自身 p95 基线 48.0 ms、注入期 48.0 ms，**一点没动**（延迟注在出口，
  按 sport 过滤，落在调用方边上而不是它自己的 server span 上）。
  500 ms 的取法：高于本 testbed 上任何服务的采样噪声，又低于最小的注入延迟档（800 ms）。
  `deviation` 取**倍数**与 **Δ/500** 的较大者 —— 两者都是「相对各自阈值的倍数」，
  单位一致，排序里可比。
- **规则 5（方法级错误率，2026-08-27 新增，依据 O-P2-17）**：只作用于一个方法的开关
  在服务级计数器里会被稀释掉 —— `adFailure` 只让 1/10 的 `GetAds` 失败，
  `cartFailure` 只作用于占 `cart` 调用 5.7% 的 `EmptyCart`。阈值形式与规则 1 相同，
  只是速率与错误数都取自 `(service, operation)` 维度
  （`queries.py` 的 `calls_total_by_operation` / `errors_total_by_operation`）。
- **规则 6（实体级集中，2026-08-27 新增，依据 O-P2-17）**：targeting 型故障
  **不会移动任何速率** —— 它让一个实体全错、其余全对，服务级错误数只等于该实体的
  流量份额。`misconfig-pc-OLJCESPC7Z` 实测 32 条报错对上规则 1 的阈值 102，
  差得远；按 `demo.product.id` 分组后是 **32 / 32 = 100%**，一眼可见。
  这是唯一一条读 `traces.json` 而不是 `metrics.json` 的规则，输入是 §9 的标签白名单。
  `deviation` 是**报错 span 数**（计数，不是倍数）。
  `traces.json` 缺失或不带标签时返回空 —— **缺输入不算告警**，
  O-P2-16 之前打的包没有标签，不能因此变成假阳。
- **规则 2 的两条附加条件（2026-08-27 修订，依据 O-P2-15）**：原规则只看「连续 ≥ 2 个
  采样点为 0」，在 15 s 步长下对被调约 3 /min 的服务是常态而非故障 —— 干净窗口实测
  `payment` / `checkout` / `email` 三个全部误报，且它们注入期的速率与基线几乎没变。
  条件 (a) 的 0.1 与决策 016 的 `blackhole` symptom 同口径（caller span < 基线 10%），
  要求速率**真的塌了**；条件 (b) 把「静默算不算数」按**该服务自己的流量水平**判，
  而不是拿一个固定采样点数去卡所有服务 —— 每分钟 3 次调用的服务，30 秒没动静
  本来就说明不了什么。修订后同一干净窗口 **0 条告警**，crash 卡的真实告警一条不少。
- **规则 7（方法级延迟，2026-08-28 新增，依据 O-P2-17 / 决策 023）**：规则 3 之于规则 7，
  正如规则 1 之于规则 5 —— 落在一个方法上的延迟会被服务级直方图平均掉。
  `latency-checkout-800` 连规则 3 的绝对档都没救回来（`checkout` 自身 p95 一动没动，
  而调用方 `frontend` 的服务级 p95 把上百个快操作一起平均了）；按方法分组后
  `frontend/POST /api/checkout` **87.5 → 990.0 ms（+902.5）**，一眼可见。
  输入是 `queries.py` 的 `p95_latency_ms_by_operation`。
  **比例臂额外要求 Δ ≥ 100 ms**，这是规则 3 没有的地板：服务级 p95 基线是聚合值、
  本来就大，而方法级基线常在 6–36 ms，2 倍不过是十几毫秒的常态抖动。
  不加地板时实测产出 46 条告警、其中 **16 条 Δ < 100 ms**，并把三张卡从诚实的
  `no_alert` 翻成「有告警但指错服务」（`crash-email-01` 的唯一告警是
  `frontend/GET /api/cart 6.0 → 16.0 ms`，而真因是 email 容器被 kill）。
  加地板后 46 → **30** 条。规则 1 / 2 / 5 本来就是「相对条件 + 绝对地板」双条件，
  规则 7 与它们同形，规则 3 才是例外。
  **两窗各需 ≥ 5 次调用**：近乎空闲的序列上 `histogram_quantile(0.95)` 只是最慢那个
  桶边界，不是延迟。规则 3 靠「每个服务都有流量」隐式免疫，方法级必须显式设地板。
  **该查询在 2026-08-28 之前打的包里不存在** —— 缺输入时规则 7 静默产出 0 条，
  不报错，旧包因此仍可重检（补齐用 `pack.py --refresh-metrics`）。
- 输出 `alerts` 列表，每条含 `rule` / `service` / `baseline` 值 / `observed` 值 / `window`，
  **按 `deviation` 降序**。空列表记 `no_alert: true` ——
  是明确的"呼机没响"，不是缺字段。
- **`deviation` 的单位按规则不同**：规则 1 / 3 / 4 / 5 是**倍数**（相对基线或相对阈值）；
  **规则 2 是「预期缺失调用数」= 基线速率 × 零跨度秒数**，**规则 6 是报错 span 数**，
  两者都是计数而不是倍数。
  改这一条是因为流量真的归零时，「基线速率 ÷ 0」会算出六位数的比值，
  在排序里压过其余所有规则，而那个数字并不说明丢了多少流量。
  每条告警的 `deviation_unit` 字段写明自己的单位。
