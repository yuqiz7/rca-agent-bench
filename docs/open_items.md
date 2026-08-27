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

**索引现状（2026-08-26 实测，`obs2card_001542` 的 pre-batch 报告）**：**5 个索引、
共约 303 MB**：

| 索引 | 文档数 | 大小 |
| --- | ---: | ---: |
| `otel-logs-2026-08-23` | 44 236 | 22.3 MB |
| `otel-logs-2026-08-24` | 467 119 | 213.6 MB |
| `otel-logs-2026-08-25` | 2 766 | 1.8 MB |
| `otel-logs-2026-08-26` | 112 776 | 54.5 MB |
| `otel-logs-2026-08-27` | 20 896 | 11.1 MB |

08-26 一天最终 54.5 MB（跑了两个观察批 + 诊断实验），08-27 开始 15 分钟已 11.1 MB。
按"跑满批次的一天约 200 MB"估，4G 限额仍可支撑约 20 个满负荷批次日。**未删任何索引。**

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

**2026-08-27 开机重放（Case A）**：峰值 **407.6 MiB = 19.9% of 2048 MiB** @ 启动后
27.8 s，稳态 189.0 MiB，`OOMKilled=false`，`RestartCount` 0 → 0，
`restarted_during_watch=false`，stop_reason=stable。证据
`artifacts/resource_audit/prom_mem_2026-08-27_boot.csv`。

**结论：保留。** 今早开机 WAL 跨度约 1.5 h，不满足「head 跨度 ≥2h40m 重启峰值 ≤60%」
条件；已挂自等待探针脚本（`scripts/maintenance/prom_wal_restart_probe.sh`），
结果落 `artifacts/resource_audit/prom_mem_<date>_walrestart.summary.txt`，读到即裁。

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

**状态：closed（2026-08-26 ET，决策 020）** —— 评测采用**事后快照模式**：
量产每张卡时落盘证据包五件，agent 与全部基线只读证据包、不查实时系统。
观测点之争因此收敛为「证据包里放哪个快照」，属证据包设计问题。详见决策 020。


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

**状态：open（配方口径已定，2026-08-27 ET，决策 021）** —— 该 flag 按本条排除，不进 76 卡配方；`mem_leak` 类三张卡全部落在 `email` 上。本条继续 open 的是「W3 若需要第二个 `mem_leak` 靶子怎么办」，与配方无关。

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

**状态：open（2026-08-27 ET 更新）** —— **配方口径已由决策 021 定死**：`cartFailure` 只出 `75%` / `90%` / `100%` 三张，`10%` / `25%` / `50%` 不入 76 卡配方。本条继续 open 的是底层问题本身 —— harvest 的 settle=150 s 少算长挂起报错 span，以及「抬 settle 还是补阈值」的裁决。**该问题解决后可补回 3 张卡**，是 76 → 80 的四张缺口中唯一有明确来源的三张（决策 021 trade-off）。

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

**状态：closed（2026-08-26 ET，决策 019）**

**根因**：VM 开机时容器被 restart policy 同时拉起，`checkout` 比 flagd 的 8013 监听器
早 5.9 秒启动，其非阻塞的 `openfeature.SetProvider` 首次连接失败后既不报错也不写日志，
该进程实例此后一直取 flag 默认值。**修法见决策 019**：收工 `shutdown.sh`、
起床 `wakeup.sh`（等 flagd 应答 OFREP + 无条件重启全部 flag 消费方 + 三项门）。

以下为定位过程留档。

**现象（`obs2card_233337`，入库档 observe-only）**
`probe` 返回 `injected=true`（OFREP 确返回 `on`），但注入期 120 s 内
`checkout → payment` 8 条调用全部成功、`PlaceOrder` 8 条 0 报错、
`payment` 自有 8 条 0 报错、`badAddress` 在 checkout 日志出现 0 次。

### 取证（2026-08-26 只读）

| 项 | 结果 |
| --- | --- |
| 容器启动时刻 | `checkout` **22:33:55.020**、`flagd` **22:33:55.012**（相差 8 ms，实质同时） |
| flagd 进程与监听器就绪 | 进程 **22:34:00.802**，`Flag IResolver listening at [::]:8013` **22:34:00.957** |
| **关键时间差** | checkout 比 flagd 的 8013 监听器**早 5.9 秒**启动 |
| checkout 的 flagd 环境变量 | `FLAGD_HOST=flagd`、`FLAGD_PORT=8013`（存在且正确） |
| checkout 日志 | **全量 0 行** —— 日志走 OTLP 不落 stdout，provider 连接失败不可见 |
| flagd 日志 | 注入/撤除时刻均有 `filepath event: ... WRITE`，flagd 侧工作正常 |
| checkout provider 初始化 | `main.go:197` `flagd.NewProvider()`（无选项）+ `main.go:202` **`openfeature.SetProvider(provider)`** —— **非阻塞版**，不等待连接就绪 |
| 对照 `product-catalog` | `main.go:147/152` **完全相同**的写法（同样非阻塞） |
| 对照 `payment`（Node） | `charge.js:37` 用的是 **`await OpenFeature.setProviderAndWait(...)`** —— 会等 |
| provider 库版本 | `github.com/open-feature/go-sdk-contrib/providers/flagd v0.6.0` |

### 实验（B1 / B2，调试档时长）

| 实验 | 操作 | `PlaceOrder` | `payment` 边 | 结论 |
| --- | --- | ---: | --- | --- |
| **B1-c** | apply `on` → **重启 checkout** → 稳定 60 s → 采 120 s | **10 条 / 5 报错** | 重启后窗口内 `payment` 自有 span **0** | **重启后注入生效** |
| **B1-d** | revert `off`，**不重启**，采 revert 之后的干净窗 | **7 条 / 0 报错** | `payment` 恢复 7 条 0 报错 | **运行时撤除生效，无需重启** |
| **B2** | 不重启直接 apply `on`，等 20 s，采干净窗 | **2 条 / 2 报错** | `payment` peer **消失** | **运行时注入生效，无需重启** |

### 归因

| 假设 | 判定 | 依据 |
| --- | --- | --- |
| **A 开机顺序** —— checkout 先于 flagd 启动，provider 首次连接失败后未恢复 | **支持** | checkout 比 flagd 的 8013 监听器早 5.9 秒启动；checkout 用**非阻塞**的 `SetProvider`，首次连接失败不会阻塞启动也没有可见日志；重启 checkout（此时 flagd 已就绪）后 B1-c / B2 两个方向都立刻生效 |
| **B 运行时不失效** —— provider 缓存未被事件流失效 | **否定** | B1-d 不重启即恢复（7/0）、B2 不重启即生效（2/2）。缓存失效通道在连接健康时工作正常 |
| **C 配置 / 评估错误** —— env 缺失或评估持续报错走默认 false | **否定** | `FLAGD_HOST` / `FLAGD_PORT` 均存在且正确；同一进程重启后评估完全正常 |

**根因**：`checkout` 与 `flagd` 由 compose 的 `depends_on: service_started` 约束，
只保证 flagd **容器**已启动，不保证 flagd **进程的 8013 监听器**已就绪。开机时两者
相差 5.9 秒，checkout 的 flagd provider 在监听器就绪前发起首次连接并失败；因为用的是
非阻塞的 `openfeature.SetProvider`，失败既不阻塞启动、也不产生可见日志，该进程实例
此后一直用默认值 `false`。**这不是 flagd 的问题，也不是 `set_flag.sh` 的问题** ——
两者都工作正常。

### 修法建议（**未实施，待裁决**）

**建议 1（推荐）：收工用 `docker compose stop`，让开机不自动复活，由起床命令按依赖顺序拉起。**
`docker compose up -d` 会按 `depends_on` 顺序启动并给 flagd 留出就绪时间，避免 VM 开机
时 25 个容器几乎同时被 restart policy 拉起。代价是每天必须记得 stop，且忘记 stop 时
问题会静默复现 —— 而它**没有任何可见症状**（checkout 零日志），只会让 flag 类的卡
静默失效。

**建议 2：checkout 类开关的注入序列加一步重启。**
`set_flag.sh apply` 之后、观察窗开始之前重启目标服务。代价是重启本身会制造一段
空窗与一次冷启动尖峰，污染基线；且 `misconfig` 卡的语义会从"改配置"变成"改配置 + 重启"。

**建议 3（仅记录，不推荐）：改 checkout 源码用 `SetProviderAndWait`。**
那是测试床上游文件，破坏决策 001 的复现锚点。

**影响面**：凡是用 `flagd.NewProvider()` + 非阻塞 `SetProvider` 的 Go 服务都可能中招 ——
已确认 `checkout` 与 `product-catalog` 写法相同。做 flag 类卡之前，应对每个靶子先验证
"运行时改值是否生效"。

---

## O-P2-11　productCatalogFailure 的 targeting 规则使其无法开启

**状态：closed（2026-08-26 ET）** —— `set_flag.sh` 已支持 targeting 型开关：`apply` 改
命中分支的变体（`"if"` 第一个分支 `off` → `on`），规则条件与 `defaultVariant` 均不动；
`probe` 按开关附带评估上下文查 OFREP。实测注入生效：`GetProduct` 355 条 / 27 报错
（7.6%），撤除后归零。**未改测试床任何文件** —— `demo.flagd.json` 只在注入窗内被改，
撤除即从备份整体恢复。该卡的入库问题另见 O-P2-13。

以下为原始记录。

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


---

## O-P2-12　paymentUnreachable 的症状落点与「客户端侧变体」假设不符

**状态：关（2026-08-26 ET）** —— 按标准 misconfig 判据入卡，ground truth
`(checkout, misconfig)`、难度高。「客户端侧变体」假设已证伪：症状在目标自有 server span 上，
独特之处是**下游边消失 + 下游零流量**。结论见 [flag_catalog.md](flag_catalog.md)
paymentUnreachable 条目与 [fingerprints.md](fingerprints.md)「下游边消失形态」。

**内容**
决策 019 修掉开机竞态后，`paymentUnreachable` 的注入**确实生效了**
（`obs2card_001542`：`PlaceOrder` 11 条 / **11 报错**，错误原文
`... lookup badAddress on 127.0.0.11:53: server misbehaving`）。但它**不满足**
为它设计的「客户端侧判据」第一条。

**为什么不满足**
判据要求「`checkout → payment` 边的报错数 ≥ N（=5）」，实测该边的报错数是 **0** ——
因为**整条边在注入期不存在**。gRPC 对 `badAddress:50051` 的名字解析在建连之前就失败，
不产生任何已完成的 client span；`badAddress` 也不会作为 peer 出现在
`client_by_peer` 里。注入期 `checkout` 的 peer 只剩
`172.18.0.24`/`172.18.0.4`/`172.18.0.5`/`shipping`，payment 干净消失。

**症状实际在哪**
在 `checkout` **自有 server span** 上（11/11 报错），与其余四个 misconfig flag **落点相同**。
它真正的独特之处是另一回事：**下游边整个消失，而下游服务本身健康且零流量** ——
`payment` 自有 server span **0 条**、容器 `running`、`RestartCount=0`。
这依然是一张有价值的「症状误导」卡（agent 看到 checkout 报错、payment 完全正常），
只是识别特征是「边消失」而不是「client span 报错」。

**按标准判据能过**
§5 现行 misconfig 判据：自有 server span 报错 ≥ `max(2, ⌈0.5×ratio×calls⌉)`
= `max(2, ⌈0.5×1.0×11⌉)` = **6**，实测 **11 ≥ 6 通过**；基线自有报错 0，恢复窗 0。

**待裁决**
(a) 按标准 misconfig 判据入卡，ground truth 记 `(checkout, misconfig, flag=paymentUnreachable)`，
    快照取 `harvest`（`in_flight_at_revert = 0`，两快照数字完全相同）；
(b) 保留「客户端侧变体」这条线，但把判据从「client span 报错数」改成
    「下游边消失 + 下游服务零流量且健康」，并在 runner 里实现该分支；
(c) 放弃该卡。**需用户裁决。**

---

## O-P2-13　misconfig 阈值公式对 targeting 型开关不适用

**状态：关（2026-08-26 ET）** —— targeting 型开关的阈值以**实测生效比例 r** 计算，
不取 `ratio=1.0`。`productCatalogFailure` 入卡：r = 7.6%、阈值 14、实测 27 通过。
apply 语义见 [fault_schema.md](fault_schema.md) §5「targeting 型开关的 apply 语义」。

**内容**
§5 的 misconfig 阈值 `N = max(2, ⌈0.5 × ratio × calls⌉)` 里，`ratio` 对 `on`/`off` 型开关
一律取 **1.0**。这对 `productCatalogFailure` 不成立 —— 它只让**一个特定商品**
（`OLJCESPC7Z`）的 `GetProduct` 失败，实测生效比例 **r = 27/355 = 7.6%**。

**后果**
按 `ratio=1.0` 算得 `N = max(2, ⌈0.5×1.0×355⌉)` = **178**，实测 27 远不及，
卡被判成注入失败 —— 但注入其实完全正常（基线 0 报错、注入期 27 报错、恢复窗 0 报错，
调用方同步报错 27 条）。

**按真实比例重算仍差一条**
`ratio = 0.076` → `N = max(2, ⌈0.5×0.076×355⌉)` = **14**，实测 **27 ≥ 14 通过**，
但入卡还要求 ≥ 2 倍阈值，**27 < 28，差一条报错**。

**待议**
(a) 给 targeting 型开关引入「实测生效比例」作为 `ratio`，需要一次预跑来测 r；
(b) 该 flag 的 r 由压测器请求 `OLJCESPC7Z` 的频率决定，不是常量，
    换 `LOAD_GENERATOR_VUS` 或 k6 脚本就会变，作为阈值输入不稳定；
(c) 拉长 `observe_s` 以增加样本，让 27 这个绝对数上去 —— 但那推翻决策 012。
**需用户裁决。**

---

## O-P2-14　`set_flag.sh` 无法为 `productCatalogFailure` 指定 `product_id`

**状态：open（2026-08-27 ET，量产前必须解决）**

**内容**
决策 021 的配方里 `productCatalogFailure` 出 **10 张卡**，每张锁定一个不同的
`product_id`（targeting 命中分支各一）。当前 `set_flag.sh` **做不到**，两处写死：

1. `apply` 对 targeting 型开关只改**命中分支的变体**（`t['if'][1]='on'`），
   **不动规则条件**；而 `demo.flagd.json` 的条件写死为
   `{"==": [{"var":"product_id"}, "OLJCESPC7Z"]}`。
2. `flag_context()` 里 probe 用的求值上下文同样写死 `{"product_id":"OLJCESPC7Z"}`。

因此 10 张卡里**只有 `misconfig-pc-OLJCESPC7Z` 能按卡片 `params` 真实注入**，
其余 9 张若照跑，实际注入的仍是 `OLJCESPC7Z` 那一条规则 —— 卡片的
`params.product_id` 与系统里真正生效的 targeting 目标**不一致**，
ground truth 虽仍是 `(product-catalog, misconfig)`（服务与类别不变），
但「10 个不同 targeting 变体」这一配方前提不成立，等于 9 张重复卡。

**依据**
`scripts/primitives/set_flag.sh` 的 targeting 分支与 `flag_context()`；
`~/projects/opentelemetry-demo/src/flagd/demo.flagd.json` 的
`productCatalogFailure.targeting` 实读。

**待议**
改法本身不难 —— `set_flag.sh` 多收一个可选的 `product_id`，apply 时把条件里的
目标 ID 一并改写、probe 时用同一个 ID 做上下文（备份/恢复机制不变，仍是整文件
`cp` 回滚）。但这动的是**已在跑的注入原语**，且 `flag_context` 的签名要从
「按 flag 查表」变成「按 flag + 卡片参数」，会牵连 runner 的传参路径。
本轮未改（本轮任务范围是生成器 / 打包器 / 检测器 / runner 集成，且原语脚本
正被后台探针占用）。**量产 `misconfig-pc-*` 之前必须先改，否则那 9 张卡是废卡。**

---

## O-P2-15　流量消失规则对低流量服务假阳

**状态：open（2026-08-27 ET，决策 021 的检测器实测发现）**

**内容**
`detect.py` 规则 2（决策 021 / fault_schema §10）写的是
「基线请求率 > 0 且注入期连续 ≥ 2 个采样点为 0 → `traffic dropped to zero`」。
在**没有任何注入**的干净窗口上实测，该规则对三个低流量服务全部误报：

| 服务 | 基线请求率 | 注入窗请求率 | 连续零采样点 | 窗内采样点 |
| --- | ---: | ---: | ---: | ---: |
| `payment` | 0.0500 /s | 0.0417 /s | 3 | 9 |
| `checkout` | 0.0500 /s | 0.0500 /s | 3 | 9 |
| `email` | 0.0500 /s | 0.0500 /s | 3 | 9 |

**为什么**
采样步长 15 s，而这三个服务被调约 **3 /min = 0.05 /s**，即平均每 20 s 才一次调用。
「连续两个 15 s 采样点没有新调用」是这些服务的**常态**，不是故障。
三例的请求率注入期与基线**几乎没变**（0.05 → 0.042 / 0.05 / 0.05），
规则却照样触发。

**没有绕过**
规则按裁决**原样实现**，未私自加保护条件。`deviation` 字段能把真假两种情形分开 ——
假阳的 `deviation` 是 1.0–1.2（速率基本没变），真的 blackhole 会是几个数量级，
但**规则本身的触发条件里没有用到 `deviation`**，所以假阳照样进 `alerts` 列表、
照样写进卡片的 `agent_visible_symptom`。

**待议（需用户裁决）**
(a) 加最低流量门槛：基线请求率低于某值（如 0.2 /s，即 12 /min）的服务不适用规则 2；
(b) 把「连续零采样点」的门槛按基线速率算，而不是固定 2
    （如 `max(2, ceil(3 / (基线速率 × 步长)))`）；
(c) 规则 2 追加「注入期请求率 ≤ 基线 × 0.1」的与条件 —— 与 `blackhole` 的
    symptom 判据（决策 016：caller span < 基线 10%）同口径；
(d) 接受假阳，理由是 agent 本来就该在证据包里自行分辨。

在裁决之前，`blackhole` / `crash` 类卡的 `agent_visible_symptom` 里会稳定多出
`payment` / `checkout` / `email` 三条无关告警。
