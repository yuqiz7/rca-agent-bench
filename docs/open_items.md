# 待办事项（Open Items）

编号 `O-P2-<n>`。每条写清依据，不写没有依据的猜测。

---

## O-P2-1　W2 harness 需在批次间清理 OpenSearch 日志索引

**内容**
W2 的建库 harness 必须在场景批次之间清理 OpenSearch 的 `otel-logs-*` 索引，
否则日志库会随 80 卡的运行持续涨满。

**依据**
`opensearch` 是全栈资源审计里唯一一个抬限后仍逼近阈值的服务：1G → 2G 之后用量
仍从 925.6M 涨到 1283.1M（**62.6%**）并继续上涨，最终不得不后补 4G 档。
见 [resource_audit.md](resource_audit.md)。

日志量随场景轮次**线性累积**，而 80 卡 × 每卡至少 2 轮验证（决策 004）意味着
至少 160 轮注入观察窗的日志。抬限额只是推迟撞墙，不解决增长本身。

**runner 现状（2026-08-24 更新）**：`run_batch.py` 已加 `--pre-batch-hook`，
清理动作有了挂载点，见 O-P2-7。

**索引现状（2026-08-24 实测）**：仅 2 个索引、共 223 MB
（`otel-logs-2026-08-23` 44 236 docs / 22.3 MB，`otel-logs-2026-08-24` 374 230 docs
/ 200.6 MB）。距 `opensearch` 的 4G 限额尚远，**本轮未删任何索引**。

**索引现状（2026-08-26 实测，`obs2card_233337` 的 pre-batch 报告）**：**4 个索引、
共约 275 MB**：

| 索引 | 文档数 | 大小 |
| --- | ---: | ---: |
| `otel-logs-2026-08-23` | 44 236 | 22.3 MB |
| `otel-logs-2026-08-24` | 467 119 | 213.6 MB |
| `otel-logs-2026-08-25` | 2 766 | 1.8 MB |
| `otel-logs-2026-08-26` | 78 062 | 37.5 MB |

**日增估算**：两日之间总量由 223 MB 增至约 275 MB，**约 +52 MB/两日 ≈ 26 MB/日**，
但日增极不均匀 —— 取决于当天跑了多少批次（08-24 跑满批次 213.6 MB，08-25 几乎没跑
1.8 MB）。按"跑满批次的一天约 200 MB"估，`opensearch` 的 4G 限额可支撑约 20 个满负荷
批次日。80 卡量产需要定期清理，本条**保持开放**。

**注意**
清理动作必须落在**批次之间**，不能落在单卡的观察窗内 —— 窗口内删索引会把
`logs.log_lines` 这个信号直接抹掉，而它是确认注入生效的信号之一（`crash` 与
`blackhole` 两类实测日志**都归零**）。

（本段原写作"§3 区分 `crash`（日志归零）与 `blackhole`（日志继续）的判据之一"，
与决策 010 的实测结论矛盾 —— 日志行数在两类上都归零，不能用于分辨类别，
只能用于确认注入生效。2026-08-24 随 011 更名一并订正。）

---

## O-P2-2　指标采集粒度 60s→15s

**状态：关闭**

建于知识库侧 2026-08-23，仓库 2026-08-24 补录并关闭；执行与实测见决策 013。

---

## O-P2-3　Prometheus 15s 后重启 WAL 重放压力复测

**内容**
下次起机时记录 `prometheus` 重启期间的 MEM 峰值，判定 2G 限额是否仍够。

**依据**
决策 013 把 11 个自研服务的指标导出间隔从 60s 降到 15s，样本率 ×4。改后 10 分钟实测
WAL 由 **148.0M → 183.4M**、TSDB 由 **351.1M → 406.5M**、MEM 由 **319.6MiB → 332.4MiB**。

稳态增长不是风险所在 —— 决策 008 记录的那次死锁正是**重启时 WAL 重放**撑爆 200M cgroup
上限、容器活约 7 秒被 OOM 杀掉、WAL 因此永不 checkpoint 的自锁循环。样本率 ×4 意味着
同样时长的 WAL 重放要多吃约 4 倍内存，而本次未经历重启，**重放峰值尚无实测**。

**怎么测**
下次 `--force-recreate prometheus` 或整机重启时，在启动窗口内持续采样
`docker stats --no-stream prometheus`，记录 MEM 峰值与 `WAL replay completed` 的
`total_replay_duration`（008 那次修复后为 6.42s）。
工具已就绪：`scripts/maintenance/prom_mem_watch.sh`（支持 `status=absent` 轮询，
可先起监视器再启容器，从而采到启动第一秒）。

**2026-08-26 第一次复测：保持开放 —— 未压到真正的 WAL 重放**

实测（Case B，`docker compose … restart prometheus`）：峰值 **88.0 MiB = 4.3% of 2048 MiB**
@ +144.4 s，稳态 85.3 MiB，`OOMKilled=false`，`RestartCount` 0 → 0，stop_reason=stable。
证据见 [resource_audit.md](resource_audit.md)「2026-08-26 — Prometheus restart WAL replay peak」
与 `artifacts/resource_audit/prom_mem_2026-08-26.csv`。

按规则 `peak_pct ≤ 60%` 本应关闭，**但不关闭**：容器重启前只运行了 3.5 分钟，
WAL 仅 3.6M，`total_replay_duration` **118.97 ms** —— 这不构成对 2G 限额的压力测试。
真正有意义的重放（昨日 WAL）发生在 VM 自动开机的 22:33:54，早于监视器启动 3.5 分钟，
峰值未被采到（CSV 前两行 167.8/168.1 MiB 只是那次启动的尾部）。

**新的关闭条件（2026-08-26）**：两条同时满足才关闭 —— (a) 开机重放不 OOM
（已满足：手动重启前的采样对开机实例显示 `restart_count=0`、`oom_killed=false`）；
(b) 在 head 跨度 ≥ 2h40m 时做一次重启，峰值 ≤ 上限的 60%（尚未实测）。
60–85% / >85% / OOM 三个分档不变；复测时 `restarted_during_watch` 只从手动重启
之后起算。复测窗口与理由见 [resource_audit.md](resource_audit.md) 的 Addendum。

**下次怎么补**：**先**启动监视器（它会以 `status=absent` 轮询等待），**再**重启容器，
从新实例存在的第一秒开始采样。

---

## O-P2-4　collector receiver 采集间隔是否统一到 15s

**内容**
决策 013 的 `OTEL_METRIC_EXPORT_INTERVAL` 只作用于自研服务的 SDK。由 collector receiver
采集的指标不受该变量控制，2026-08-24 实测各不相同：

| receiver | 实测间隔 | 配置来源 |
| --- | ---: | --- |
| `docker_stats` | **10.0s** | `otelcol-config.yml:32` 未设 `collection_interval`，走 receiver 默认 |
| `postgresql` | **10.0s** | `otelcol-config.yml:40` 未设 `collection_interval`，走 receiver 默认 |
| `http_check` | **60.0s** | `otelcol-config.yml:26` 未设 `collection_interval`，走 receiver 默认 |
| `host_metrics` | **60.0s** | `otelcol-config.yml:76` 未设 `collection_interval`，走 receiver 默认 |

另有两个**自研服务未生效**，真实周期仍为 60s：`currency`（C++ 代码不读该 env）、
`frontend`（JS `sdk-metrics` 2.9.0 把 `exportIntervalMillis=60000` 写死且不查 env）。
两者的具体代码位置见决策 013。

**待裁决**
是否需要把这些统一到 15s，待 W2 按靶子清单裁决 —— 取决于哪些靶子的哪些指标真的会被
agent 的 `metrics_query` 工具用于时间定位。`currency` 与 `frontend` 若要生效，只能改
应用代码（显式传入间隔），而那会破坏 3.0.0 pin 的复现锚点（见决策 001、011）。

---

## O-P2-5　crash 错误形态两日不一致，ARP 邻居缓存假设待验证

**内容**
同一原语（`kill_container`）、同一靶子（`cart`），四轮测到三种错误形态：

| 轮次 | 错误文字 | `<100ms` 占比 | p50 |
| --- | --- | ---: | ---: |
| 8/23 ④ 手工 | `EHOSTUNREACH` | 45.2% | 5 399 ms |
| 8/24 `full_cart_194613` | **`ETIMEDOUT`** | **0%** | 65 786 ms |
| 8/24 `full2_cart_201241` | `EHOSTUNREACH` | 50.0% | 1 529 ms |
| 8/24 `full3_cart_205013` | `EHOSTUNREACH` | 70.0% | 0.51 ms |

**假设（待验证）**
差别在于调用方 ARP 邻居缓存中 `cart` 旧 IP 是否已过期：缓存有效则 SYN 发往已失效的
MAC，直到 `tcp_syn_retries=6` 的 127s 建连预算耗尽才报 `ETIMEDOUT`；缓存转
`FAILED` 则秒级 `EHOSTUNREACH`。

**证据与缺口**
两轮 `EHOSTUNREACH` 的 `evidence.json` 显示邻居缓存在注入后 40–80s 内
`REACHABLE → INCOMPLETE → FAILED`，转 `FAILED` 越快、快速失败占比越高（70.0% vs
50.0%），方向一致。但 **`ETIMEDOUT` 那一轮没有 evidence 对照**（钩子是之后才加的），
假设未验证。`target_ip_after` 与 before 相同，IP 变更已排除。

详见 [fingerprints.md](fingerprints.md)「crash 错误形态 8/23 vs 8/24」。

**怎么验**
连跑多轮 `crash` 周期并全程采 `evidence.json`，看是否能复现 `ETIMEDOUT` 轮次并对上
邻居缓存状态。若假设成立，`crash` 的指纹需按邻居缓存状态分两种形态记录，
决策 010 的「吵」也要相应细化。

---

## O-P2-6　agent 评测观测点：实时（immediate）还是事后（harvest）

**内容**
决策 016 让 runner 在两个时刻各取一次注入窗快照。**agent 评测时拿到哪一个视角，
决定它看到的 `blackhole` 是什么形态**：

- `immediate`（`t_revert` 即刻）：整链静音，0 条 span —— 「哑」，靠信号缺失诊断；
- `harvest`（`t_end+settle`）：59 条 span、无报错、p50 68.6 秒 —— 「慢到离谱」，
  形态反而接近 `latency`。

同一张卡在两个视角下的难度与正确解法都不同。

**待裁决**
W3 harness 设计前必裁：agent 的 `trace_query` 工具查到的是哪个时刻的数据快照、
证据快照取在何时。两种都保留在落盘里，选择权还在。

---

## O-P2-7　runner 的批次边界钩子（承 O-P2-1）

**内容**
`run_batch.py` **目前没有**批次边界钩子 —— 批次开始/结束时不执行任何外部命令，
因此 O-P2-1 要求的「批次之间清理 OpenSearch 日志索引」尚无处挂载。

**2026-08-24 已实现（部分关闭）**：`run_batch.py` 新增 `--pre-batch-hook <脚本>`
（默认空），批次开始前执行一次，输出落 `<batch>/pre_batch_hook.log`，钩子非零退出
即中止批次。配套脚本 `scripts/maintenance/log_index_report.sh`：默认**只报告不删**，
`--delete-older-than-days N` 才删，且排除当前写入索引（`otel-logs-<今天>`）。

本轮以报告模式作为 `judge_222727` 批次的前钩子实跑，输出见该批次目录。

**仍开放的部分**：只有 pre hook，**没有 post hook**；清理策略（保留几天、多大触发）
未定，取决于 O-P2-1 的索引增长观测。

---

## O-P2-8　`recommendationCacheFailure` 在 120s 窗内不可判定

**内容**
`recommendationCacheFailure=on` 的内存增长实测 **+0.3 MiB / 120s = 0.1 MiB/min**
（47.4 → 47.7 MiB），远低于 `mem_leak` 判据的 `max(10, 0.15 × first_mib)` ≈ 10 MiB。
该 flag **不入库**，`mem_leak` 类当前**只有 `email` 一个可用靶子**。

**为什么慢**
泄漏点在 `src/recommendation/recommendation_server.py:86-87`（`cached_ids` 每次
cache miss 追加自身 1/4，几何增长），但两个因素同时压制：cache miss 只有 50% 概率
触发（`:80` `random.random() < 0.5`），且 `recommendation` 被调仅 **25.9 /min**。
120 秒窗内只有约 26 次机会、其中约 13 次触发增长，攒不出可观增量。

**待议**
若 W3 需要第二个 `mem_leak` 靶子，须另议窗长 —— 几何增长意味着拉长窗口的收益是
超线性的，但会推翻决策 012 的 60/120/60 并让 80 卡机器时间成倍上升。另一条路是
换更高流量的靶子，但该 flag 的靶子锁死在 `recommendation`，只能改应用代码，
而那会破坏 3.0.0 pin（决策 001）。

---

## O-P2-9　cartFailure 的报错 span 存活时间超过 harvest 的 settle 窗

**状态：open（2026-08-26）**

**内容**
`cartFailure` 的报错 span 挂起时间远超 harvest 快照的等待时长。在
`rerun_cart_225459` 中，报错的 `EmptyCart` span 实测 p50 **65.0 s**、
**max 262.1 s**，而 harvest 快照取于 `t_end + settle`，settle = **150 s**（决策 016）。
在该时刻仍在飞行中的报错 span 不会出现在 harvest 窗口里，因此
**harvest 可能少算该 flag 的 `misconfig` 报错 span**。

**为什么要紧**
`misconfig` 的 symptom（决策 018 第二部分）判在 harvest 快照上。少算会把卡推向
假的 symptom 失败 —— 与决策 016 为 `crash` 修掉的是同一形状的错误，只是成因是
span 时长而不是观测点。

**做卡的临时规则**
`cartFailure` **只用 75% 及以上的变体**。`EmptyCart` 被调约 3.9 /min，120 s 窗内
约 4–8 次调用；比例更低时，即便不考虑少算，预期报错数也就贴着阈值，两者叠加
使卡不可靠（实测：首跑 0/6 失败、重跑 2/4 通过 —— 两次都在概率范围内，
见决策 018 第二部分）。

**待议**
是给 `misconfig` 周期单独抬高 `settle`，还是接受少算并在阈值上补偿。抬 settle 会
让每个周期都多花墙钟（决策 016 的 trade-off）；另一条路则需要在更多轮次上测出
挂起时长的分布 —— 目前只有两轮。

---

## O-P2-10　paymentUnreachable 开启后 checkout 行为未改变

**状态：open（2026-08-26）**

**内容**
`set_flag checkout paymentUnreachable=on` 在入库档观察批（`obs2card_233337`）里
**注入生效但系统行为不变**：`probe` 返回 `injected=true`（flagd 的 OFREP 确实返回 `on`），
而注入期 120 s 内 `checkout → payment` 8 条调用**全部成功**、`checkout` 自有
`PlaceOrder` 8 条 **0 报错**、`payment` 自有 8 条 **0 报错**、`badAddress` 在 checkout
全量日志里出现 **0** 次。

**代码预期**
`src/checkout/main.go:567-571`：`chargeCard` 在 flag 为真时把 payment 客户端换成指向
`badAddress:50051` 的连接，`Charge` 应因名字解析失败返回错误，`PlaceOrder` 随之失败。
实测完全没有发生。

**假设（未验证）**
checkout 用的是 flagd Go provider（`src/checkout/main.go:30`，指向 `FLAGD_HOST=flagd`
/ `FLAGD_PORT=8013`），该 provider 带求值缓存。若缓存失效依赖的推送通道没工作、
或 TTL 长于 120 s 的注入窗，checkout 就会在整个窗口里继续用旧值。
**这只是假设** —— 没有直接证据，需要单独验证。

**怎么验**
拉长注入窗看 checkout 是否最终切换；或在注入期间直接观测 checkout 与 flagd 之间的
连接（checkout 在 Jaeger 依赖图里**没有** `checkout → flagd` 边，说明它不是每请求
RPC 求值）；或对照另一个同样由 checkout 读取的 flag（`kafkaQueueProblems`）看是否同样不生效。

**影响**
`paymentUnreachable` 目前**不入卡**。它原本是唯一一个症状落在**调用方 client span**
上的 misconfig 候选（区别于其余四个落在目标自有 span 上），这条"症状误导"路线暂时没有可用素材。

---

## O-P2-11　productCatalogFailure 的 targeting 规则使其无法开启

**状态：open（2026-08-26）**

**内容**
`demo.flagd.json` 里该 flag 的 targeting 规则**两个分支都返回 `off`**：

```json
"targeting": { "if": [ { "==": [ { "var": "product_id" }, "OLJCESPC7Z" ] }, "off", "off" ] }
```

flagd 中 targeting 优先于 `defaultVariant`，所以 `set_flag.sh` 改 `defaultVariant`
**不会改变求值结果**。OFREP 实测（带 `product_id=OLJCESPC7Z` 与空上下文各一次）均返回
`{"value":false,"variant":"off","reason":"TARGETING_MATCH"}`。`apply` 正确失败并回滚。

**影响**
`productCatalogFailure` **不入卡**。可惜的是它本该是个好靶子：`GetProduct` 被调
**155.9 /min**，是全栈流量最高的方法之一，报错数远超任何阈值，且"只对一个特定商品失败"
这种局部性对 agent 是有意思的难度。

**待议**
启用它必须改 `demo.flagd.json` 的 targeting 规则本身（把第一个分支改成 `"on"`），
而该文件是测试床上游文件、是决策 001 的复现锚点。可选：
(a) 接受改动并记入决策，把改动纳入 `testbed/` 归档；
(b) 走 `set_flag.sh` 之外的第二条通道（改 targeting 而非 defaultVariant），
    但那让原语的语义从"换变体"扩展到"改规则"；
(c) 放弃该 flag。**需用户裁决**。
