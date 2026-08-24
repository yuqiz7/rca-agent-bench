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
| `latency` | `harvest` | 调用方 → B 的 **`caller_all_dur.p50_ms`** 相对基线**右移 ≥ `delay_ms` × 0.8**，**且报错 span 数不增**（该类只产生「慢」不产生「错」）。`cart` 800ms 入库档实测右移 p50 +800.31ms、报错 0。 |
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
