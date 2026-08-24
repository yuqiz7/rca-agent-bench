# 故障场景 schema v0.9

**修订记录**

- 2026-08-24：类别 `dep_timeout` 更名 `blackhole`（决策 011）

（版本号仍为 v0.9，v1.0 待 ⑤ 收口。）

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
| `crash`（杀容器） | B 容器不存活 | **报错 span 大量出现**；错误文字为**地址不可达类**（实测 `EHOSTUNREACH`，非"连接被拒"） | 日志归零 |
| `latency`（注入延迟） | B 存活、可达、响应慢，延迟低于调用方超时 | span 变长、错误少 | 正常 |
| `blackhole`（服务端口静默丢包）<br>（原名 `dep_timeout`，2026-08-24 决策 011 更名） | B 存活但服务端口丢包 | **span 完全静默**：既无成功也无报错，调用方卡在 TCP 重传里，无任何超时错误；撤除后积压回放 | 日志归零 |
| `misconfig`（错误配置） | B 配置被改（compose env / flagd 开关） | 收到错误或错误结果 | 自身 span / 日志有错 |
| `mem_leak`（内存泄漏） | B 内存持续增长 | 先变慢后报错 | 内存曲线爬升 → OOM 重启 |

> `crash` 与 `blackhole` 两行为 2026-08-23 实测结果，见 [fingerprints.md](fingerprints.md)。
> 两类的分界是**「吵」与「哑」**：`crash` 期调用方 span 73 条且全部报错，`blackhole`
> 期 0 条。**不可用于分辨的字段**（均已实测证否）：调用方时延（`blackhole` 无完成
> span 可测）、上报心跳（服务已死 120 秒时 `heartbeat_age_s` 仍只有 5.4 s，collector
> 在服务死后继续导出 series）、请求速率（1 分钟 scrape 在 120 秒窗内仅 2 个样本，
> 差分跨注入边界被污染）、日志行数（两类都归零）。
> 其余三类（`latency` / `misconfig` / `mem_leak`）的指纹仍为**未实测的预期**。

### 注入原语映射

| 原语 | class |
| --- | --- |
| `kill_container` | `crash` |
| `delay_outbound_on_service_port` | `latency` |
| `drop_inbound_on_service_port` | `blackhole` |
| `env_override` / `flagd_flag` | `misconfig` |
| flagd 内置泄漏开关 或 `mem_hog` | `mem_leak` |

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
| `latency` / `blackhole` | 目标 netns 内**限定服务端口**的 tc / iptables 规则存在 |
| `misconfig` | env / flag 当前值 = 注入值 |
| `mem_leak` | 容器内存统计持续上升 |

### `symptom` — 针对被注入服务 B 的一条查询

过阈值即通过。**不得使用"系统内任意异常"类查询。**

| class | 初值 |
| --- | --- |
| `crash` | 调用方对 B 的错误 span 数 > N（`cart` 实测建议 N = 20） |
| `blackhole` | 调用方对 B 的 **span 总数**（`caller_spans_total`）**低于基线 10%** —— 该类整链静音、错误 span 恒为 0，用错误数判会永远不通过 |
| `latency` | 调用 B 的 span p95 > 基线 × k |
| `misconfig` | B 自身错误 span / 日志 > N |
| `mem_leak` | B 内存 > 基线 × k |

`N`、`k` 于 ④ 实测后定。

### `recovered`

`revert` 后 `cooldown_s` 内，同一 `symptom` 查询回落至基线，且 `injected` 探针反向通过。

判定窗从 **`t_revert + 30s`** 起算，跳过撤除后的错误尾巴（实测两轮分别在
`t_revert+6.48s` 与 `t_revert+12.42s` 结束）。从 `t_revert` 起算会把尾巴算进来，
逼着把 N 抬高到尾巴之上，白白牺牲 symptom 的灵敏度。

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

## §8 待 ④ 实测确认清单

- [ ] `timing` 三初值（`warmup_s` / `observe_s` / `cooldown_s`）
- [ ] 各类 `symptom` 阈值 `N` / `k`
- [ ] 调用方超时值 — 决定 `latency` 类延迟上限。（`blackhole` 类无此项：实测服务间 gRPC 长连接未设 deadline，250 秒窗内不产生任何超时错误，无 span 耗时可言，见决策 011）
- [x] `crash` 指纹核对（2026-08-23，见 fingerprints.md）
- [x] `blackhole` 指纹核对（2026-08-23，见 fingerprints.md）
- [ ] `latency` / `misconfig` / `mem_leak` 三类指纹逐类核对
- [ ] `mem_leak` 类容器内存指标在 Prometheus 中是否可查
- [ ] flagd 内置故障开关清单（W2 首日收）
