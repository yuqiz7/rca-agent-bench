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

**2026-08-27 自等待探针（Case C）**：`prom_wal_restart_probe.sh` 等到 head 跨度
**9607 s（2 h 40 min 07 s）** 时自动抢锁重启，`ready_sec=3`（上限 300），峰值
**327.4 MiB = 15.9% of 2048 MiB**，`oom_killed_before/after` 均 false，
`restart_count` 0 → 0，**verdict=PASS**。证据
`artifacts/resource_audit/prom_mem_2026-08-27_walrestart.summary.txt`
与同名 `.csv`。

**状态：关（2026-08-27 ET）**

2026-08-26 定的两条关闭条件**同时满足**：

| 条件 | 证据 | 结果 |
| --- | --- | --- |
| (a) 开机重放不 OOM | **Case A**：WAL 跨度约 **1.5 h**，峰值 **407.6 MiB = 19.9%**，`OOMKilled=false`，`RestartCount` 0 → 0，stop_reason=stable | 满足 |
| (b) head 跨度 ≥ 2h40m 时重启，峰值 ≤ 上限 60% | **Case C**：head 跨度 **9607 s**，`ready_sec=3`，峰值 **327.4 MiB = 15.9%**，无 OOM，PASS | 满足 |

**结论：2 GiB 限额下最坏情况 < 1/6。** 两个 case 里峰值更高的是开机那次
（407.6 MiB / 19.9%），比 60% 的告警线还差三倍多的余量；跨度达标的那次反而更低
（327.4 MiB / 15.9%）—— 说明峰值主要由**冷启动时的整机争抢**决定，不由 WAL 长度
线性决定，决策 013 的样本率 ×4 没有把重放推向限额。决策 008 记录的自锁循环
（200M 限额下重放 7 s 即被 OOM 杀、WAL 永不 checkpoint）在 2 GiB 限额下不成立。

**遗留**：探针脚本与 `prom_mem_watch.sh` 保留在仓库里，日后若再改导出间隔或
retention，直接重跑一次即可复判，不需要重新设计观测方式。

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

**状态：open（2026-08-28 ET 更新）—— 检测器侧已由 `settle_s=300` 绕开，底层问题仍未解**

**2026-08-28 重跑结果**：`misconfig-cart-75` 设 `settle_s=300`（harvest 从 t_end+150 s
推到 t_end+300 s）后，探针门四项全过、检测器出 **2 条告警**（此前 0 条），
[O-P2-17](#o-p2-174-张过门的卡没有任何-agent-可见告警) 因此关闭。
**但这只证明「等得够久就看得到」，没有回答「抬 settle 还是补阈值」的裁决** ——
实测挂起时长 p50 150 s / max 265 s，300 s 是压着 max 过的，不是有余量的选择。
补回 `10%` / `25%` / `50%` 三张卡仍需要先把裁决做掉，本条**保持 open**。

**（以下为 2026-08-27 的记录，保留备查）**

**状态（旧）：open（2026-08-27 ET 更新）** —— **配方口径已由决策 021 定死**：`cartFailure` 只出 `75%` / `90%` / `100%` 三张，`10%` / `25%` / `50%` 不入 76 卡配方。本条继续 open 的是底层问题本身 —— harvest 的 settle=150 s 少算长挂起报错 span，以及「抬 settle 还是补阈值」的裁决。**该问题解决后可补回 3 张卡**，是 76 → 80 的四张缺口中唯一有明确来源的三张（决策 021 trade-off）。

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

**状态：关（2026-08-27 ET，判据已落码并实测复验）**

**落码（2026-08-27 ET）**：`run_batch.py` 对 targeting 型开关改用**独立判据**，
不再是「把 `ratio` 换个数」：

> 被 targeting 的那个业务 id 上，注入窗内该 id 的自有 server span 中
> **报错占比 ≥ 0.5 且报错绝对数 ≥ 5**，且基线窗内该 id 报错数为 0。

理由是两类开关的失败**形状**不同：概率型把失败随机散布在全部调用上，绝对数正比于
生效比例；targeting 型让某一个实体 100% 失败、其余 0%，此时方法级整体报错率
**等于该实体的流量份额**，用绝对数判等于在判压测器的抽样频率 —— 那个频率由
`LOAD_GENERATOR_VUS` 与 k6 脚本决定，随时会漂（即本条待议 (b) 指出的问题）。
改判命中支自身的报错占比后与份额完全无关，(b) 不再成立，(a) 的预跑也不需要了。
判据原文写进 [fault_schema.md](fault_schema.md) §6。

分组数据由 `three_signals.py` 新增的
`traces.self_edges.server_by_business_id[<tag>][<value>]` 提供，标签名形如
`demo.<entity>.id`（正则匹配，新实体自动纳入）。判据同时记录未命中的其余 id 的
报错数，「只打中一个」在证据里一眼可见。

**（以下为重开时的记录，保留备查）**

2026-08-26 裁定：targeting 型开关的阈值以**实测生效比例 r** 计算，不取 `ratio=1.0`
（`productCatalogFailure`：r = 7.6%、阈值 14、实测 27 通过）。
apply 语义见 [fault_schema.md](fault_schema.md) §5「targeting 型开关的 apply 语义」。

**但 `run_batch.py` 的 `parse_ratio()` 至今仍对 `on`/`off` 型变体返回 `1.0`** ——
那条裁决**从未落到代码里**。2026-08-27 用 `2ZYFJ3GM2N` 做 O-P2-14 的原语验证时撞到：

| 项 | 值 |
| --- | --- |
| `effective_ratio`（代码算出的） | **1.0**（应为约 0.11） |
| `GetProduct` 注入窗调用数 | 212 |
| 阈值 `N = max(2, ⌈0.5 × 1.0 × 212⌉)` | **106** |
| 实测报错 span | **24**（占 11.3%，与该商品份额 11.4% 吻合） |
| `symptom` 判定 | **false** |

注入完全正常（基线 0 报错、`injected` 探针 true、报错精确落在被 targeting 的商品上、
撤除后残留干净），**只是阈值算错了**。按裁决的 r ≈ 0.113 重算：
`N = max(2, ⌈0.5 × 0.113 × 212⌉)` = **12**，实测 24 ≥ 12 通过，且 24 ≥ 2×12 = 24
恰好也满足入卡的「≥ 2 倍阈值」。

**后果（要紧）**
配方里 **10 张 `misconfig-pc-*` 卡**在 `parse_ratio()` 修好之前**全部会被判成
`symptom=false`**，在批次模式下记 `production.probe.verdict=failed` 且不打包。
首批 16 卡里的 `misconfig-pc-OLJCESPC7Z`（第 6 张）就是其中之一 ——
它是首批唯一受此影响的卡，不会触发「连续 3 张失败停批」。

**待议（需用户裁决）**
r 从哪来，三条路都还在：
(a) **预跑测 r** —— 每个 `product_id` 跑一次基线窗统计份额，写进卡片 `params`
    供 runner 读；准，但每张卡多一次预跑；
(b) **从本周期自己的基线窗现算 r** —— `three_signals` 已经有靶子自身的 server span
    分组数据，不需要额外预跑，但要按标签（`demo.product.id`）分组，
    而那正是 [O-P2-16](#o-p2-16证据包丢-span-标签且-jaeger-只有约-30-分钟回溯窗)
    指出的、当前采集里丢掉的东西；
(c) **对 targeting 型开关改判据** —— 不比绝对报错数，改比「被 targeting 的那一支
    报错率 ≈ 100%、其余支 0%」，与份额无关，因此不受压测器配置漂移影响。
    这条最稳，但要新写一条 §5 判据。

**在裁决之前**，`misconfig-pc-*` 的卡按现状会失败；这是**已知的、有解释的失败**，
不是系统异常。

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

**状态：关（2026-08-27 ET，原语已修 + 保留 3 张已落配方）**

**裁决**：按份额分布保留 **3 张** —— `2ZYFJ3GM2N`（份额最高 11.4%）、
`66VCHSJNUP`（最低 9.1%）、`OLJCESPC7Z`（9.3%，已有实测且在首批），
其余 **7 张出库**。总卡数 **76 → 69**，misconfig **21 → 14**。
已落 `recipe.csv`、[recipe.md](recipe.md) v1.1 与
[decisions.md](decisions.md) 决策 021「修订 2026-08-27」；
7 个 yaml 由 `generate.py` 自动删除（生成器负责清理，不手删）。

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
**已修（2026-08-27 ET）**

`set_flag.sh` 增加 `--context-value <v>`：

- `apply` 时把 targeting 条件里的比较值改写为指定值（形状必须是
  `{"==": [{"var": "<key>"}, <value>]}`，不符即报错退出、不猜），同时改命中分支的变体；
- `probe` 用同一个值做求值上下文；值记进 `state/<svc>.flag` 的 `context=` 字段，
  `revert` / `probe` 可以不重复传；
- `revert` 照旧从备份整体 `cp` 回滚，条件值与变体一起回原样；
- **不给该参数时行为与改前完全一致**（缺省值仍是出厂写死的 `OLJCESPC7Z`）。

`generate.py` 生成的 `misconfig-pc-*` 卡片 `params` 本就带 `product_id`；
`run_batch.py` 的 `card_to_cycle()` 现在把它透传为 `--context-value`。

**实测验证（2026-08-27 ET，调试档 30/60/30）**

用**非** `OLJCESPC7Z` 的 `2ZYFJ3GM2N` 跑一次注入：

| 项 | 值 |
| --- | --- |
| 注入期间 targeting 条件 | `{"if": [{"==": [{"var": "product_id"}, "2ZYFJ3GM2N"]}, "on", "off"]}` |
| `injected` 探针 | **true** |
| `GetProduct` 调用数（注入窗，靶子自身 server span） | **212** |
| 其中报错 span | **24** |
| 实测报错占比 | **11.3%** |
| `2ZYFJ3GM2N` 的基线份额（见下表） | **11.4%** |
| `residue_clean` | true（条件值与变体均已恢复为 `OLJCESPC7Z` / `off`，flagd 工作区干净） |

**11.3% 对 11.4%** 就是判据：报错落在 `2ZYFJ3GM2N` 这一支上，而不是出厂写死的
`OLJCESPC7Z`（份额 9.3%）。原语已能按卡片参数注入任意一个 `product_id`。

**`product_id` 份额分布（2026-08-27T18:28:09Z–18:55:09Z，27 min，n = 4154）**

| # | product_id | GetProduct 调用数 | 份额 |
| ---: | --- | ---: | ---: |
| 1 | `2ZYFJ3GM2N` | 475 | 11.4% |
| 2 | `9SIQT8TOJO` | 435 | 10.5% |
| 3 | `6E92ZMYYFZ` | 431 | 10.4% |
| 4 | `HQTGWGPNH4` | 419 | 10.1% |
| 5 | `0PUK6V6EV0` | 418 | 10.1% |
| 6 | `LS4PSXUNUM` | 409 | 9.8% |
| 7 | `1YMWWN1N4O` | 404 | 9.7% |
| 8 | `L9ECAV7KIM` | 396 | 9.5% |
| 9 | `OLJCESPC7Z` | 387 | 9.3% |
| 10 | `66VCHSJNUP` | 380 | 9.1% |

**份额近乎均匀** —— 极差只有 **2.3 个百分点**（11.4% − 9.1%），10 个商品各占约
1/10。这是压测器 `user_browse_product` 均匀抽样的直接结果，不是巧合。

**保留卡数待裁**：均匀分布意味着这 10 张卡在**轴 B 上彼此几乎不可分**
（服务级失败比例都是 9–11%），difficulty 三轴给出的分数也只在 B=1 / B=2 的
边界上被份额差切成两组 —— 而那条边界（10%）恰好落在分布正中间，属于人为切分。
按分布保留 **3–4 张**（取份额最高、中位、最低各一，可再加一张）足以覆盖该开关的
全部行为，其余 **6–7 张出库**。**具体保留几张、留哪几个 `product_id`，需用户裁决。**
本条未改 `recipe.md`、未动任何卡片 yaml —— 裁决落地前 10 张全部保留在配方里。

**数据来源的一处偏差（未绕过）**：本表**不是**按要求从
`evidence/crash-cart-01/traces.json` 的基线段统计的，原因有二，都是硬约束：

1. **`traces.json` 不保留 span 标签。** 打包器按 fault_schema §9 只留
   `traceID` / `spanID` / `parentSpanID` / `service` / `operation` / `start` /
   `duration` / `status` 八个字段，`demo.product.id` 不在其中，从该文件根本算不出
   按 `product_id` 的分布。
2. **源窗口已被 Jaeger 淘汰。** Jaeger 用内存存储、`MEMORY_MAX_TRACES=25000`，
   实测可回溯窗口约 **30–35 分钟**：`crash-cart-01` 的基线段（18:05:25–18:06:26，
   已是 50 分钟前）现在查回来是 **0 条 trace**，而 30 分钟内的窗口正常返回。

因此改用**同口径的新鲜窗口**重测，样本量（4154 次调用 / 27 min）也比原窗口
（61 秒基线段，约 200 次调用）有意义得多。**这两点本身是需要跟进的问题，见 O-P2-16。**

---

## O-P2-15　流量消失规则对低流量服务假阳

**状态：关（2026-08-27 ET，规则已修订并实测复验）**

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

**裁决与修订（2026-08-27 ET）**：取上列 (c) 与 (b) 的组合 —— 规则 2 现在要求
零跨度之外**同时**满足两条：

- **(a) 注入期平均速率 ≤ 基线速率 × 0.1** —— 与决策 016 的 `blackhole` symptom 同口径
  （caller span < 基线 10%），要求速率真的塌了，而不是「恰好这两格没数」；
- **(b) 零跨度内预期调用数 = 基线速率 × 零跨度秒数 ≥ 5** —— 把静默算不算数交给
  该服务**自己的流量水平**判，而不是拿固定采样点数卡所有服务。

`deviation` 同时由「基线速率 ÷ 注入期速率」改为**预期缺失调用数**（= 基线速率 ×
零跨度秒数）。流量真归零时前者是除零，实测算出六位数比值，在排序里压过其余全部规则，
而那个数字不说明丢了多少流量。规则 1 / 3 / 4 的 `deviation` 保持倍数不变，
每条告警带 `deviation_unit` 标明自己的单位。

**复验（2026-08-27 ET）**

1. **干净窗口负对照**：`evidence/_clean/observe-20260827T185059Z/`
   （2026-08-27T18:39:00Z–18:46:30Z，其间无任何注入）。
   **修订后 0 条告警**（`no_alert: true`）。同一份 metrics 用旧规则复算，
   `checkout` / `email` / `payment` 三条照旧会触发 —— 三者零跨度均为 2 个采样点，
   但注入期速率 0.05 /s **高于**基线 0.0167 /s（条件 (a) 直接否掉），
   且零跨度内预期调用数仅 **0.50 < 5**（条件 (b) 也否掉）。两条守卫各自独立生效。

| 服务 | 基线速率 | 观测期速率 | 零跨度采样点 | 预期缺失调用 | 旧规则 | 新规则 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `checkout` | 0.0167 /s | 0.0500 /s | 2 | 0.50 | **触发** | 不触发 |
| `email` | 0.0167 /s | 0.0500 /s | 2 | 0.50 | **触发** | 不触发 |
| `payment` | 0.0167 /s | 0.0500 /s | 2 | 0.50 | **触发** | 不触发 |
| `ad` | 0.2833 /s | 0.2083 /s | 1 | 4.25 | 不触发 | 不触发 |
| `quote` | 0.1000 /s | 0.1167 /s | 1 | 1.50 | 不触发 | 不触发 |
| `shipping` | 0.1167 /s | 0.1667 /s | 1 | 1.75 | 不触发 | 不触发 |

2. **真阳不丢**：`crash-cart-01` 由 **11 条降为 9 条**，掉的两条正是边界误报 ——
   `ad`（注入期 0.0667 /s vs 基线 0.1967 /s，未达 10% 线）与
   `checkout` 的 `traffic_zero`（0.0417 vs 0.0656，同样未达）。
   靶子 `cart` 与其真实级联（`shipping` / `quote` / `payment` / `email`，
   四者注入期速率均为**精确 0.0**）一条不少，`cart` 仍以预期缺失 **99.84** 次调用
   排在 `traffic_zero` 组首位。

---

## O-P2-16　证据包丢 span 标签，且 Jaeger 只有约 30 分钟回溯窗

**状态：关（2026-08-27 ET，取 (a)：标签白名单 + 把 30 min 约束写进规格）**

**裁决与落地**：取待议 (a)，不改 Jaeger 存储。
`pack.py` 的 `traces.json` 每个 span 增加 `tags` 字段，按白名单保留 ——
业务 id 走正则 `^demo\..+\.id$`（实测 `demo.product.id` / `demo.order.id` /
`demo.shipping.tracking.id`），报错消息取 `error`、`error.type`、`otel.status_code`、
`otel.status_description`、`grpc.error_message`、`grpc.error_name`，
其余标签仍丢弃。键名全部取自 2026-08-27 跨全部服务 25 分钟的实测采样。
**状态码族有意不收**（挂在数以万计健康 span 上、不含消息，且每个 span 已有 `status`）。
白名单同时写进 `traces.json` 的 `tag_whitelist` 字段，包自带说明。

**(b) 改 badger 落盘没有采纳**：那要重启整栈、并推翻决策 001 之外的一处环境事实，
而 (a) 已经让包自洽 —— 包自洽之后「还能不能回头补」就不再是必须回答的问题。
代价是 **30 min 的回溯窗成为硬约束**，已作为规格写进
[fault_schema.md](fault_schema.md) §9「已知约束：打包必须在卡结束后 30 min 内完成」：
打包必须紧跟周期（runner 就是这么做的），重打包超过 30 min 只能重跑那张卡。

**已知缺口（不阻塞，记录在案）**：`exception.message` / `exception.type` /
`exception.stacktrace` 是 **span 的 log 字段**而非标签，`traces.json` 不保留
span logs 因此取不到。gRPC 路径的错误消息由 `otel.status_description` 与
`grpc.error_message` 覆盖；HTTP 路径的异常堆栈目前只能从 `logs.jsonl` 里找。

**（以下为开条时的记录，保留备查）**

**内容**
两件事叠在一起，使**证据包一旦打完，任何没进包的 span 属性就永久拿不回来**：

1. **`traces.json` 只留 8 个字段**（fault_schema §9）：`traceID` / `spanID` /
   `parentSpanID` / `service` / `operation` / `start` / `duration` / `status`。
   span 标签一律丢弃 —— `demo.product.id`、`rpc.method`、`http.route`、
   `otel.status_description` 等全部不在包里。
2. **Jaeger 是内存存储**，`MEMORY_MAX_TRACES=25000`。实测回溯窗口约
   **30–35 分钟**：50 分钟前的窗口查回来 0 条 trace，30 分钟内的窗口正常。

**为什么要紧**
- 决策 021 第七节要求「量产后按证据包**实测重算**轴 B 与轴 C」。轴 B 的定义是
  「注入窗内目标自有 server span 的报错数 ÷ 总数」，**按方法分组**；分组键
  （`rpc.method` / `http.route`）恰好是被丢掉的标签。用 `operation` 勉强能代，
  但 `misconfig` 的方法级判据（决策 018）与 targeting 型开关的份额分析都需要
  更细的标签。
- 事后快照模式（决策 020）的全部前提是「证据包里没有的东西，agent 再聪明也拿不到」。
  现在这句话对**生产侧自己**也成立了：包里没有的，30 分钟后连出题人也拿不到。

**待议（需用户裁决）**
(a) 给 `traces.json` 增加一个 `tags` 字段（可白名单若干键，避免体积爆炸 ——
    当前单卡 traces.json 已 7.6 MB）；
(b) 或改 Jaeger 存储为 badger 落盘，把回溯窗口拉到天级，代价是磁盘与一次栈重启；
(c) 或两者都做 —— (a) 保证包自洽，(b) 保证还能回头补。
在裁决之前，**打包必须紧跟周期**（runner 现在就是这么做的，harvest 之后立刻打包），
而**已打完的包不可能再补标签**。

---

## O-P2-17　4 张过门的卡没有任何 agent 可见告警

**状态：closed（2026-08-28 ET，重跑批 `rerun1_20260828T185549Z`）**

**了结**：`misconfig-cart-75` 用 `settle_s=300` 重跑，`no_alert: true → false`，
**2 条 `method_latency_jump`**。4 张全部了结（1 张出库、3 张救回），本条关闭。

同批另有 3 张此前 `no_alert: true` 的卡随 R1 的 `inject_s=300` 新窗一起翻盘 ——
它们不在本条原始的 4 张名单里（那 4 张是首批的），但成因同族，一并记在这里：

| card_id | 修法 | 旧 | 新 | 规则构成 |
| --- | --- | ---: | ---: | --- |
| `misconfig-cart-75` | `settle_s=300` | 0 | **2** | `method_latency_jump` ×2 |
| `misconfig-payment-75` | `inject_s=300` | 0 | **5** | `method_error_rate_jump` ×3、`error_rate_jump` ×2 |
| `misconfig-payment-50` | `inject_s=300` | 0 | **5** | `method_error_rate_jump` ×3、`error_rate_jump` ×2 |
| `crash-email-01` | `inject_s=300` | 0 | **1** | `traffic_zero` ×1 |

**在库的 `no_alert` 卡现在是 0 张。** 拉长窗口（而不是放松阈值）是这四张的共同解 ——
低流量靶子在 120 s 窗里样本量根本不够，不是检测器判错。

**（以下为 2026-08-28 上午的记录，保留备查）**

**状态（旧）：部分关 —— 4 张里 3 张已了结，剩 `misconfig-cart-75` 待重跑验证**

**2026-08-28 结果（决策 023 规则 7 落码 + 全库离线重检）**

| card_id | 处置 | 依据 |
| --- | --- | --- |
| `latency-checkout-800` | **关** —— 规则 7 拿下 | `frontend/POST /api/checkout` p95 **87.5 → 990.0 ms（+902.5）**，比例臂 11.31× |
| `misconfig-pc-OLJCESPC7Z` | 已于 2026-08-27 由规则 6 关掉 | 见下表 |
| `misconfig-ad-on` | **出库** | 阈值结构上不可达（规则 5 的 N = 调用数 25%，而 adFailure 只让 10% 失败），成因记入 findings；决策 023 第 7 条 |
| `misconfig-cart-75` | **待重跑** | 成因是 O-P2-9 在检测器侧重演；本轮给 cartFailure 三张设 `settle_s=300`，随重跑批验证 |

规则 7 的比例臂另加了 **100 ms 绝对地板**，理由见决策 023「放弃了什么」——
不加地板时它在方法级小基线上造 16 条 Δ<100 ms 的噪声告警，
并把 `crash-email-01` / `misconfig-payment-50` / `misconfig-cart-75` 三张
从诚实的 `no_alert` 翻成「有告警但指错服务」。

**（以下为 2026-08-27 的记录，保留备查）**

**增补（2026-08-27 ET，落 [fault_schema.md](fault_schema.md) §10）**

1. **规则 3 加绝对档**：p95 ≥ 2 × 基线 **或** Δp95 ≥ **500 ms**，
   `deviation` 取倍数与 Δ/500 的较大者；
2. **新增规则 5 方法级错误率**：按 `(service, operation)` 判，阈值形式同规则 1
   （`queries.py` 新增 `calls_total_by_operation` / `errors_total_by_operation`）；
3. **新增规则 6 实体级集中**：读 `traces.json` 按 `demo.<entity>.id` 分组，
   某 id 报错 ≥ 5 且占该 id 全部 span ≥ 50%，`deviation` = 报错数。

**复验**

- **干净窗口负对照两份**：`observe-20260827T185059Z`（无标签，验规则 3/5）与
  `observe-20260827T214612Z`（**带标签**，6 条规则全覆盖，1612/29718 span 带标签）
  —— **均 0 条告警**。
- **12 张已打包卡重跑**（先用 `pack.py --refresh-metrics` 补齐新查询；Prometheus
  留存以天计所以补得到，Jaeger 只有 30 min 回溯所以 `traces.json` 不可重取，
  规则 6 只对 O-P2-16 之后打的包有效）：

| card_id | 旧 | 新 | 新增的第一条 |
| --- | ---: | ---: | --- |
| `blackhole-cart-01` | 6 | 6 | — |
| `crash-cart-01` | 7 | **8** | `elevated error rate on frontend/GET /api/cart` |
| `latency-cart-800` | 4 | 4 | — |
| `latency-checkout-800` | 0 | **0** | — |
| `latency-email-800` | 1 | 1 | — |
| `memleak-email-10000x` | 2 | 2 | — |
| `memleak-email-1000x` | 1 | 1 | — |
| `misconfig-ad-on` | 0 | **0** | — |
| `misconfig-cart-75` | 0 | **0** | — |
| `misconfig-checkout-on` | 1 | **3** | `elevated error rate on frontend/POST /api/checkout` |
| `misconfig-payment-100` | 3 | **6** | `elevated error rate on frontend/POST /api/checkout` |
| `misconfig-pc-OLJCESPC7Z` | 0 | **2** | `elevated error rate on frontend/GET /api/recommendations` |

`misconfig-pc-OLJCESPC7Z` 由规则 6 拿下：
`errors concentrated on demo.product.id=OLJCESPC7Z (product-catalog)`，
**32 / 32 = 100%**，正是 targeting 的指纹。

**仍为 `no_alert` 的 3 张 —— 待出库**

| card_id | 成因（实测） |
| --- | --- |
| `latency-checkout-800` | `checkout` 自身 p95 基线 **48.0 ms** → 注入期 **48.0 ms**，一点没动。延迟注在**出口**（按 sport 过滤），落在调用方边上；而调用方 `frontend` 的**服务级** p95 把上百个快操作一起平均掉了。需要**方法级 / 边级**的延迟规则，规则 5 是错误率不是延迟，管不了 |
| `misconfig-ad-on` | **阈值结构上不可达**：规则 5 的 `N = 0.25 × 基线速率 × 120`，即**该方法调用数的 25%**；而 `adFailure` 只让 **10%** 的 `GetAds` 失败。25% > 10%，无论流量多大都够不着。实测 `GetAds` 注入期 17 次调用、4 次报错、N = 7 |
| `misconfig-cart-75` | **[O-P2-9](open_items.md) 在检测器侧重演**：报错的 `EmptyCart` span 挂起 **p50 150 s / max 265 s**，在证据窗关闭前根本没结束，spanmetrics 因此只看到 **1 次调用 / 0 报错**；而入库门读的 Jaeger 是 harvest 时刻查的，看到 **3 次 / 2 报错**。两侧对同一个窗口给出不同的数 |

三张按裁决**待出库**（本轮不改 recipe）。三条成因各不相同，**不能用一条新规则一起解决**：
第一条要新的延迟规则，第二条要重新想低比例故障的阈值形式，第三条要先解决 O-P2-9。

**（以下为开条时的记录，保留备查）**

**内容**
首批跑完后，**12 张过门的卡里有 4 张 `agent_visible_symptom.no_alert = true`** ——
证据包五件齐、探针门三项全过，但检测器一条告警都没出：

| card_id | class | 靶子 | 探针门 | alerts |
| --- | --- | --- | --- | ---: |
| `latency-checkout-800` | latency | `checkout` | 通过 | **0** |
| `misconfig-ad-on` | misconfig | `ad` | 通过 | **0** |
| `misconfig-cart-75` | misconfig | `cart` | 通过 | **0** |
| `misconfig-pc-OLJCESPC7Z` | misconfig | `product-catalog` | 通过（新判据） | **0** |

这样的卡**对评测不可用**：agent 拿到的 `task.json` 只有那句固定 trigger
（"Monitoring detected an anomaly in the system."）和一个空的 alerts 列表，
没有任何可推理的起点。

**为什么**
生产侧判据（fault_schema §5，按靶子自身或调用方 span）与 agent 侧检测器
（§10，按服务级 Prometheus 指标）**看的是两套东西**，在「小比例失败」上系统性分叉：

- `misconfig-pc-OLJCESPC7Z` 重跑实测：命中支 **32 / 32 = 100%** 报错（判据强通过），
  但 `product-catalog` 服务级基线速率约 3.4 /s，规则 1 的
  `N = max(5, ⌈0.25 × 3.4 × 120⌉)` = **102**，32 < 102，不告警。
- `misconfig-ad-on` 的实际生效比例只有 0.1、`misconfig-cart-75` 只作用于
  占 cart 调用 5.7% 的 `EmptyCart` —— 同样是「靶子自己明显不对，服务级看不出来」。
- `latency-checkout-800` 是另一种成因：延迟注在 `checkout` 的出口，
  规则 3 比的是**服务自身 p95**，而 800 ms 相对 `checkout` 的基线 p95 未到 2 倍。

**这不是检测器的 bug** —— 规则 1/3 按裁决实现且在 crash / blackhole 类上工作正常
（`crash-cart-01` 7 条、`blackhole-cart-01` 6 条）。是**评测集设计的缺口**：
决策 020 把 agent 的输入定成证据包，而「什么算可见症状」这条线目前只由 §10 四条规则
划定，没有和 §5 的入库判据对齐过。

**待议（需用户裁决）**
(a) **`no_alert` 的卡不入库** —— 最干净，代价是 misconfig 类会大面积出局
    （21 张里已知至少 3 张形态如此）；
(b) **给检测器加「方法级 / 实体级」规则** —— 用证据包新增的 span 标签
    （[O-P2-16](open_items.md) 已落）按 `operation` 与 `demo.*.id` 分组算报错率，
    与 §5 判据同口径。这条最对症，但等于给 §10 加第五条规则；
(c) **`no_alert` 的卡照样入库**，把「没有告警也要从三信号里找出问题」当作
    更难的一档 —— 与决策 021 的难度轴 C（症状误导）同向，但那时 trigger
    必须改写，否则 agent 无从下手；
(d) 拉长注入窗以抬高绝对报错数 —— 推翻决策 012，且对
    `latency-checkout-800` 那类比值判据无效。

**在裁决之前**：这 4 张卡的证据包与 task.json 已落库，`no_alert: true` 如实记录，
**没有为了让它们"有告警"而调低阈值**。

---

## O-P2-18　超低流量靶子与长连接靶子在 120 s 窗内无可用判据

**状态：关（2026-08-28 ET，决策 023 落码）**

两类各自有了出路，都不是调阈值：

- **(1) 超低流量靶子** → **R1**：实算确认 `checkout` / `email` / `payment` 五窗速率
  **0.0433–0.0633 /s**（均值 0.0567），横跨 0.05 线；名单内 22 张卡设
  `inject_s=300`，期望调用数由约 6.8 抬到约 17，两条判据地板都有了余量。
  本条采的是原文出路 **(a) 拉长注入窗**，代价实算约 **+1.1 h**（22 张各 +180 s），
  未采 (b)「payment 不作 crash/blackhole 靶子」—— 靶子多样性是难度轴 A 的一部分。
- **(2) `valkey-cart` 不"哑"** → **R2**：symptom 增**台阶臂**
  （调用边 during p50 ≥ 1000 ms 或 ≥ 100 × 基线 p50）。用首批实测回放
  `step_arm_pass = True`（5702 ≥ 1000）。即原文出路 **(a) 改判耗时分位数**，
  但只对无 SDK 靶子生效，不动其余靶子的判据 —— 决策 016 当年「与 latency 同形」
  的顾虑靠这个作用域限制解掉。
  同时新增的**边静默臂**覆盖了 `astronomy-db` 的相反形态，两者成对记入
  [fingerprints.md](fingerprints.md)。

**验证**：两条臂对两个已记录形态各自命中设计目标的那一条，回放实测
`astronomy-db` 走边静默臂通过、台阶臂不通过；`valkey-cart` 反之。
**实跑验证随重跑批**（`blackhole-valkey-cart-01` / `crash-astronomy-db-01` 已排入）。

**（以下为开条时的记录，保留备查）**

决策 022 用「Prometheus 300 s 回看做分母」+「靶子侧档」修掉了**分母为零**那一类，
但还剩两类结构性问题，**都不是换阈值能解决的**。

**(1) `payment` 流量太低，两档都够不着**

`payment` 被调实测 **10 次 / 300 s = 0.033 /s ≈ 2 /min**。于是：

- **靶子侧档不适用**：期望调用数 = `0.033 × 120` = **4.0 < 5**（决策 022 的样本量守卫）；
- **调用方边档极窄**：`crash` 需要调用方报错 span > `N = max(5, ⌈0.25×0.033×120⌉)` = **5**，
  而 120 s 内该靶子总共只有约 4 次调用 —— **最多 4 条报错，永远达不到 > 5**。

**2026-08-27 重跑实测**（决策 022 落码后）：

| 卡 | 基线速率(300s) | 注入期速率 | 靶子侧档 | 调用方边档 | verdict |
| --- | ---: | ---: | --- | --- | --- |
| `crash-payment-01` | 0.0333 /s（10 次 / 300 s） | 0.0 /s | **不适用**（期望 4.0 < 5） | 报错 span 0，需 > 5 | **失败** |
| `blackhole-payment-01` | 0.0533 /s（16 次 / 300 s） | 0.0167 /s | 适用但不过：上限 0.00533 /s | 调用方 span 基线 **0** | **失败** |

`blackhole-payment-01` 的靶子侧档这次**适用**了（期望 6.4 ≥ 5），但仍不过 ——
注入期实际服务了 **2 次**调用，而 10% 线换算成绝对数是 **0.64 次**。
计数器最小粒度是 1 次调用，**要过这条线只能一次调用都不剩**。
低流量下「≤ 基线 10%」和「== 0」是同一句话，判据没有余地。

`crash-payment-01` / `blackhole-payment-01` 因此**结构上不可能通过**。
两条出路，都要裁决：(a) 拉长注入窗（推翻决策 012，69 卡机器时间成倍涨）；
(b) `payment` 不作 `crash` / `blackhole` 靶子（`misconfig` 仍可用 ——
`paymentFailure` 走的是靶子自身 server span，不受调用方边稀释）。

**已解决的一类（记录在此，供对照）**：`crash-frontend-01` 深度 0、无可配对的调用方
span，首批 `symptom` / `recovered` 双失败。决策 022 的靶子侧档 + `recovered` 的同源补丁
之后重跑**四项全过**：基线 **8.407 /s** → 注入期 **0.55 /s**（上限 0.841）、
恢复期 **5.967 /s**（要求 ≥ 4.203），证据包五件齐、**10 条告警**
（首条 `traffic dropped to zero on frontend`，deviation 845.25）。
说明「分母为零」那一类确实被修掉了，剩下的两类是另外的问题。

**(2) `valkey-cart` 的 blackhole 不"哑"**

实测见 [fingerprints.md](fingerprints.md)「长连接靶子的 blackhole 指纹与「哑」不同」：
全拦之下耗时 0.49 ms → **5702 ms**（约 11 600 倍），但**零报错**，
且前 74 s 调用照常成功完成，整窗 span 数只掉到基线的 **36%**，远高于 10% 线；
真正的静默只占注入窗的后 38%。

- 调用方边档：span 数不够低 → 不通过；
- 调用方报错档：`cart` 报错 span **0** → 不通过；
- 靶子侧档：无 SDK、无 spanmetrics 系列 → 不适用。

三档全灭。**2026-08-27 决策 022 落码后重跑复现**：基线 caller span **114** →
注入期 **57**（阈值 11.4），报错 **0**，注入期 p50 **5571 ms**；
靶子侧档记 `applicable: false`（`no series in window`）。结论与首批一致。

可选：(a) `blackhole` 对长连接靶子改判**耗时分位数**
（决策 016 曾因「与 `latency` 同形」放弃，但那是对短连接靶子说的；
`valkey-cart` 基线 0.49 ms、注入 5.7 s，量级差 4 个数量级，与 800 ms 的
`latency` 档并不混淆）；(b) 判据只看注入窗**后半段**的速率（静默确实出现，只是晚）；
(c) `valkey-cart` 不作 `blackhole` 靶子。**需裁决。**

---

## O-P2-19　`paymentFailure` 低比例档在 120 s 窗内可能拿不到样本

**状态：关（2026-08-28 ET，决策 023 R1 落码）**

`payment` 进 R1 低流量名单，其**全部 10 张卡**（含 `misconfig-payment-10/25/50/75/90/100`）
设 `inject_s=300`。批次 2 实测印证了本条的担心：`misconfig-payment-75` 注入窗
`Charge` 只有 **4 次调用**、`misconfig-payment-50` **10 次**，样本量本身就在判据地板附近，
两张虽过门但检测器全空。300 s 窗把期望调用数抬到约 17。
**实跑验证随重跑批**（`misconfig-payment-75` / `misconfig-payment-50` 已排入）。

**（以下为开条时的记录，保留备查）**

**背景**
决策 021 原写「验证卡不过门则 6 张整组出库」，2026-08-27 已改写为：
**验证卡过门只解锁该组进入量产排期，组内每张卡仍各自过探针门与告警检测，
过不了的卡单独出库、如实记。**

**为什么改**
验证卡 `misconfig-payment-100` 确实过门，但样本极小 —— `Charge` 在 120 s 注入窗内
只被调 **7 次**，2 报错阈值 4、实测 7，裕度只有 3 条。7 个样本能证明
「这个开关能注进去」，证明不了低比例档也拿得到足够样本。

**风险量化**
`payment` 被调实测 **0.033 /s ≈ 2 /min**（300 s 回看，决策 022），
`Charge` 是其唯一被调方法，120 s 注入窗期望调用数约 **4**。
按 misconfig 判据 `N = max(2, ⌈0.5 × ratio × calls⌉)`：

| variant | 期望报错数（4 次调用） | 阈值 N | 预期 |
| --- | ---: | ---: | --- |
| `100%` | 4.0 | 2 | 实测已过（7 次调用 / 7 报错） |
| `90%` | 3.8 | 2 | 大概率过 |
| `75%` | 3.0 | 2 | 大概率过 |
| `50%` | 2.0 | 2 | **贴阈值，一次抖动就掉** |
| `25%` | 1.0 | 2 | **预期不过** |
| `10%` | 0.4 | 2 | **预期不过** |

`misconfig-payment-10` 还是配方里 5 张难卡之一（轴 B=2、总分 4）。

**待议**
(a) 低比例档随第二批实跑，过不了的按新条款单独出库 —— 本轮取这条，第二批已排入
    `50%` / `75%` / `90%` 三张；
(b) 拉长 `misconfig` 类的注入窗（推翻决策 012）；
(c) 低比例档改用**多周期累计**判定 —— 与决策 004 的「2 轮复现」口径不同，需新判据。

---

## O-P2-20　crash 类靶子 server span 消失时，调用方 CLIENT span 的报错不被规则 1–7 覆盖

**状态：open（2026-08-28 ET，低优先级 —— 现由 300 s 窗 + 规则 2 兜住，此处只留档）**

**内容**

检测器规则 1 / 5（错误率）与规则 3 / 7（p95）的 PromQL 一律限定
`span_kind="SPAN_KIND_SERVER"`：

```
sum by (service_name, span_name) (traces_span_metrics_calls_total{span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"})
```

`crash` 类把靶子容器整个 kill 之后，**靶子自己的 server span 不再产生** ——
不是变成报错，是不存在。而故障的报错**落在调用方的 CLIENT span 上**
（调用方发出请求、拿到 connection refused / UNAVAILABLE）。
这类 span 的 `span_kind` 是 `SPAN_KIND_CLIENT`，**七条规则一条也看不到它**。

**实测（批次 2 `crash-email-01`）**

| 观测点 | 值 |
| --- | ---: |
| 探针（Jaeger，调用方边）注入窗报错 span | **11** |
| 规则 1/5 读的 `email\|POST /send_order_confirmation` 注入窗报错计数 | **0** |
| 规则 1/5 读的 `checkout\|oteldemo.CheckoutService/PlaceOrder` 注入窗报错计数 | **0** |

该卡因此过了探针门却 `no_alert`。同一窗内规则 2（流量归零）也够不着：
它要求 `基线速率 × inject_s ≥ ZERO_MIN_EXPECTED(5)`，而 `email` 基线 0.04 /s ×120 s
= **4.8 < 5**，静默不构成统计意义。

**为什么现在只留档、不落码**

决策 023 的 R1 把 `email` 的 `inject_s` 提到 **300 s**，期望调用数由 4.8 抬到约 **17**，
**规则 2 的样本量守卫就过得去了** —— 靶子被 kill 之后它自己的 server 计数器停止推进，
规则 2 正是为这件事写的。也就是说这个缺口在 `crash` 类上**被 300 s 窗顺带兜住**，
`crash-email-01` 已排入重跑批验证这一点。

**因此本条留的是规则层面的缺口本身**，不是某张卡：
> 只要一类故障的症状**只**出现在调用方 CLIENT span 上，且靶子自身速率不足以触发规则 2，
> 现有七条规则就是盲的。

**如果要落码**，形状大概是「规则 8：调用方边报错率」，读
`span_kind="SPAN_KIND_CLIENT"` 并按 `(service_name, peer)` 分组。
**没有立刻做**，两个原因：(a) 目前没有一张卡因它而不可用（300 s 窗兜住了）；
(b) spanmetrics 的 client 侧序列没有可靠的 peer 维度，
按 `span_name` 分组会把不同下游混在一起，需要先确认 collector 侧能否补出 peer 标签
—— 那是独立的一块工作，不该塞进本轮。

**触发条件**：若重跑批之后 `crash-email-01` 或同类卡在 300 s 窗下**仍** `no_alert`，
本条升级为待落码，并按决策 023 第 7 条先行出库。

---

## O-P2-21　recover 窗没跟上 R1 的窗口加长，低流量靶子的 recovered 门是掷硬币

**状态：已落码待验（2026-08-28 ET，决策 027）** —— 走待议 (b)：按靶子实测基线速率算出
`recover_s` 并经 `cycle_override` 下发，**复用 R1 的同一机制**，不另起覆盖路径。
计算脚本 `scripts/scenarios/recover_window.py`（可重跑复现），公式
`recover_s ≥ 30 + 5 / 速率`，取五窗**最小**速率、向上取整到 10、上限 300。
落到 `checkout`/`email`/`payment` = **150 s**、`quote` = **90 s**、`shipping` = **70 s**，
共 34 张卡。第四批验证。

**残留风险（必须记）**：门槛取「期望 ≥ 5」并没有把假失败消掉，只是压住了。
recovered 要求实测速率 ≥ 基线 × 0.5，即需要 λ/2 次调用落窗，λ=5 时
**仍有 12.5% 的概率在服务已恢复时判失败**。实算：

| MIN_EXPECTED | 假失败概率 |
| ---: | ---: |
| 5 | 12.5% |
| 10 | 6.7% |
| 15 | 1.8% |

**`ad` 恰好卡在线上**：五窗最小速率 0.1733 → 默认窗期望 **5.20 ≥ 5**，
按规则**不加窗**，假失败概率 **10.9%** —— `blackhole-ad-01` 第三批正是这么失败的
（期望 5.2 实收 3）。本轮按既定规则不为它破例；若第四批它再失败，
应把 `MIN_EXPECTED` 抬到 10（一行改动，代价是低流量卡每张多约 2 分钟墙钟）。

**（以下为开条时的记录，保留备查）**

**状态（旧）：open（2026-08-28 ET 开条，重跑批 `rerun1_20260828T185549Z`）**

**内容**
决策 023 的 R1 把**注入窗**对低流量靶子从 120 s 拉到 300 s，理由是样本量不够。
**recover 窗没有跟着改** —— `recover_s` 仍是 60 s，实际比较窗是
`[t_revert+30s, t_end]` 只有 **30 s**（决策 022）。于是同一张卡的注入侧有足够样本、
恢复侧没有。

**实测**（本批 2 张卡因此判失败，两张都是 `arm=target_side`）：

| card_id | 300 s 基线速率 | 恢复窗 | 期望调用数 | 实测 | 门槛 | 结果 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `crash-payment-01` | 0.053333/s | 30 s | **1.6** | 0 | ≥ 0.026666/s（即 ≥1 次） | 失败 |
| `blackhole-payment-01` | 0.083333/s | 30 s | **2.5** | 0 | ≥ 0.041667/s（即 ≥1 次） | 失败 |

门槛「速率 ≥ 300 s 基线 × 0.5」在 30 s 窗里等价于「必须至少落到 1 次调用」。
基线速率 0.0533/s 时，30 s 窗内一次都没有的概率是 **e^(-1.6) ≈ 20%** ——
**这不是恢复信号，是泊松噪声**。两张卡的残留探针都干净
（`probe_after_revert injected=false; container status=running`），
服务实际上已经恢复。

**为什么要紧**
symptom 侧**已经有**这个守卫：`baseline_rate × inject_s >= 5`，样本不够就判
`"why": "baseline rate too low for the silence to mean anything"` 不适用。
**recovered 侧没有同形的守卫**，是两侧不对称。这个不对称会在第三批上原样重演 ——
第三批按优先级④要收 R1 名单靶子的卡，它们全是低流量靶子。

**待议**（三条路，本轮不动码，留给第三批之后一次性改）
(a) 给 recovered 加与 symptom 同形的样本量守卫：期望调用数 < 5 时该臂不适用，
    退回到「残留探针干净 + symptom 不再为真」两项；
(b) 或把 `recover_s` 对 R1 名单靶子同步拉长到 300 s，代价是每张卡多 4 分钟墙钟；
(c) 或两者都做 —— (a) 保证判据自洽，(b) 保证真有信号可读。

**相关**：决策 022（recovered 改按速率比）、决策 023 R1、[F-2](#f-2recover-窗对低流量靶子过短两张-payment-卡判失败2026-08-28)。

---

## O-P2-22　`valkey-cart` 撤除 `drop_inbound` 后重连滞后超过 recover 窗

**状态：已落码待验（2026-08-28 ET，决策 027）** —— 与 O-P2-21 用**同一修法**（加长 recover 窗），
但**理由不同**：valkey-cart 默认窗期望 39.1 次调用，样本量从来不是问题，
问题是 cart 的连接池在 30 s 内没重建完。因此它的 `recover_s=150` 是一个
**待测量的下限**，不是算出来的需求 —— 我们只知道重连 >30 s，不知道具体多久。
第四批 `blackhole-valkey-cart-01` 重跑给出第二个数据点；若 150 s 仍不够，
下一步应直接量重连时长而不是继续加窗猜。

**（以下为开条时的记录，保留备查）**

**状态（旧）：open（2026-08-28 ET 开条，重跑批 `rerun1_20260828T185549Z`）**

**内容**
`blackhole-valkey-cart-01` 的 symptom 通过（台阶臂，见下），但 **recovered 判失败，
且失败原因是「symptom 在恢复窗里仍为真」** —— 不是速率不够，是判据认为故障还在。

**实测**：`cart → valkey-cart` 边基线 **75 span / 60 s = 1.25/s**；
撤除后的 30 s 恢复窗里 **0 span**，期望 37.5 次。边静默臂因此在恢复窗里**再次点火**
（`silence_arm_pass: true`），`symptom_still_true: true`。

残留探针干净（`iptables INPUT DROP rules=0`），iptables 规则确实已删。
所以这大概率**不是测量假象，是真的重连滞后** —— `cart` 到 valkey 的连接在
DROP 期间被打断，撤除后需要重建连接池，30 s 不够。

**与 [O-P2-21](#o-p2-21recover-窗没跟上-r1-的窗口加长低流量靶子的-recovered-门是掷硬币) 的区别**：
O-P2-21 是样本太少读不出信号（期望 1.6 次），本条是样本足够多、信号真的是零
（期望 37.5 次实测 0 次）。**两条不能用同一个修法** —— 给本条加样本量守卫没用，
它需要的是更长的恢复等待，或者一个「连接重建期」的宽限。

**待议**：`blackhole-valkey-cart-01` 是否需要专属的 `recover_s`（量一下重连实际要多久，
目前只有这一轮数据、只知道 >30 s），还是把长连接靶子整类另做判据。
`crash-valkey-cart-01`（配方里还没量产）大概率同病。

**相关**：[O-P2-18](#o-p2-18超低流量靶子与长连接靶子在-120-s-窗内无可用判据)（长连接靶子那半条）、
[F-4](#f-4valkey-cart-撤除后重连滞后30-s-恢复窗读到-0-span2026-08-28)。

---

# findings 底稿：出库卡与失败模式

决策 020 把 findings（含失败模式归类）列为交付物，但 `docs/findings.md` 尚未开写。
在它开写之前，**出库卡的成因记在这里**（决策 023 第 7 条要求）。
每条的形式：出库的卡 / 一句话成因 / 实测数字 / 这条说明了什么。
`docs/findings.md` 建立后整节迁走。

## F-1　10% 随机故障低于 120 s 窗的检测线（`misconfig-ad-on`，2026-08-28 出库）

**一句话**：`adFailure` 只让 **10%** 的 `GetAds` 失败，而检测器规则 1/5 的阈值
`N = max(5, ⌈0.25 × 基线速率 × inject_s⌉)` 的相对项是**该方法调用数的 25%** ——
25% > 10%，**无论流量多大都够不着**，这不是阈值调不对，是阈值形式与故障形式不匹配。

**实测**（首批 `misconfig-ad-on`，注入窗 120 s）：

| 量 | 值 |
| --- | ---: |
| `GetAds` 注入窗调用数 | 17 |
| `GetAds` 注入窗报错数 | 4 |
| 规则 5 门槛 `N` | 7 |
| 实际报错占比 | 4/17 = 23.5%（含基线抖动，标称 10%） |
| 告警数（规则 1–7 全部） | **0** |

**处置**：探针门通过、证据包五件齐，但两轮检测（2026-08-27 六规则、2026-08-28 七规则）
均为 `no_alert`，按决策 023 第 7 条**出库**。卡从 `recipe.csv` 移除（69 → 68），
`scenarios/misconfig-ad-on.yaml` 随生成器删除；
**证据包 `evidence/misconfig-ad-on/` 保留**，作为本条 finding 的原始素材。

**这条说明了什么**：**低比例随机故障需要的是与「比例」对齐的判据形式，不是更低的阈值。**
把 `N` 的相对项从 25% 降到 10% 以下能救这张卡，但会同时放松所有卡的规则 1/5，
把正常抖动判成告警 —— 干净窗负对照会立刻炸。真正的形状应当是
「注入窗报错占比 **显著高于**基线窗报错占比」的统计检验（比例差异检验），
而不是拿一个绝对/相对混合阈值去卡。**这是评测集设计的缺口，不是检测器的 bug** ——
同一批规则在 crash / blackhole / 高比例 misconfig 上工作正常。

**相关**：[O-P2-17](#o-p2-174-张过门的卡没有任何-agent-可见告警)、决策 023 第 7 条。
另两张同期 `no_alert` 的卡走的是别的路：`latency-checkout-800` 由规则 7 救回，
`misconfig-cart-75` 的成因是 [O-P2-9](#o-p2-9cartfailure-的报错-span-存活时间超过-harvest-的-settle-窗)
在检测器侧重演、本轮给 `settle_s=300` 重跑验证。

---

## F-2　recover 窗对低流量靶子过短，两张 payment 卡判失败（2026-08-28）

**一句话**：决策 023 的 R1 只加长了**注入窗**，**恢复窗仍是 30 s** ——
在 0.05/s 量级的基线上，30 s 窗的期望调用数只有 1.6 次，
「速率 ≥ 基线 × 0.5」实际等价于「必须掷出至少 1 次调用」，**判的是运气不是恢复**。

**实测**（`rerun1_20260828T185549Z` 周期 3 与周期 9，两张都走 `arm=target_side`）：

| 量 | `crash-payment-01` | `blackhole-payment-01` |
| --- | ---: | ---: |
| 300 s 基线速率 | 0.053333/s | 0.083333/s |
| 恢复窗长 | 30 s | 30 s |
| 恢复窗期望调用数 | **1.6** | **2.5** |
| 恢复窗实测 span | 0 | 0 |
| 门槛速率 | 0.026666/s | 0.041667/s |
| 一次都没有的概率 | e^(-1.6) ≈ **20%** | e^(-2.5) ≈ **8%** |
| 残留探针 | 干净 | 干净 |
| 结果 | recovered **失败** | recovered **失败** |

两张卡走 `target_side` 臂而不是 `caller_edge`，是因为 60 s 的恢复基线窗里
`frontend → payment` 边**本身就是 0 span**（`baseline_spans: 0`），
caller 臂无从比较，代码退到目标自身速率 —— 而目标自身也是低流量，同一个坑。

**这条说明了什么**：**样本量守卫必须两侧对称。** symptom 侧早就有
`baseline_rate × inject_s >= 5` 这个守卫，样本不够就明确判"不适用"
（实测 detail 里那句 `baseline rate too low for the silence to mean anything`）；
recovered 侧一直没有同形的守卫。**修 symptom 时没有回头看 recovered，
是这次 R1 落地不完整的地方** —— 不是新 bug，是老 bug 的另一半没被找到。

**处置**：两张卡**不出库**（成因是判据缺陷不是卡本身不可做），
按 [O-P2-21](#o-p2-21recover-窗没跟上-r1-的窗口加长低流量靶子的-recovered-门是掷硬币) 待议三条路裁决后重跑。
第三批**不改判据**（第三批已启动，冻结 runner），预计会在 R1 名单靶子上原样重演。

---

## F-3　latency 判据的 `errors not up` 合取项与 800 ms 延迟不相容（2026-08-28）

**一句话**：`latency-frontend-800` 的 **p50 臂过得很漂亮**（右移 802.63 ms，
门槛 640 ms，余量 25%），卡死在同一条规则的**第二个合取项** `errors_not_up` ——
注入期 caller 有 **7 条报错 span**、基线 0 条，于是整条规则判假。

**实测**（`rerun1_20260828T185549Z` 周期 6）：

| 量 | 值 |
| --- | ---: |
| caller p50 基线 | **4.88 ms** |
| caller p50 注入期 | **807.51 ms** |
| 右移 | **802.63 ms** |
| 门槛（`delay × 0.8`） | **640.0 ms** |
| p50 臂 | **通过** |
| caller 注入期报错 span | **7**（基线 0） |
| `errors_not_up` | **失败** |
| 整体 symptom | **失败** |

**顺带证实：决策 023 的 R3 是有效的。** 扩 `PEER_KEYS` 认 Envoy 命名之后，
`frontend` 的 `caller_all_dur.p50` 不再恒为空 —— 基线 4.88 ms、注入期 807.51 ms
两个数都读到了，上游边确实存在且好用。**R3 修的那一半成立，卡是被另一半拦下的。**

**这条说明了什么**：**`errors not up` 这个合取项的前提是「纯延迟不该造成报错」，
而这个前提在 800 ms 上就已经不成立了。** 481 条 span 里 7 条报错（1.4%）——
延迟把一部分请求推过了某个上游超时。这不是注入错了，这正是 800 ms 延迟的真实后果。
判据把"延迟"和"报错"设成互斥，但真实的延迟故障本来就会在尾部产生报错。
`latency-*-3000` 那一档只会更严重 —— **第三批要收 2 张 3000 ms 首档，
大概率会踩同一个坑**，届时能拿到第二个数据点。

`errors_not_up` 当初是为了把 latency 与 crash / blackhole 区分开
（防止把一个"注入延迟结果服务挂了"的周期算成合格的 latency 卡）。
真正的形状应该是**报错率有上界**（比如注入期报错占比 < 5%）而不是**不许增加**。

**处置**：卡**不出库**，改判据前不重跑。第三批不动 runner，先攒 3000 ms 档的数据点。

---

## F-4　`valkey-cart` 撤除后重连滞后，30 s 恢复窗读到 0 span（2026-08-28）

**一句话**：`blackhole-valkey-cart-01` 的 symptom **走的是台阶臂不是边静默臂**，
过得很干脆；recovered 判失败，因为撤除 iptables 之后 30 s 内
`cart → valkey-cart` 边**一条 span 都没有**，边静默臂在恢复窗里又点了一次火。

**实测**（`rerun1_20260828T185549Z` 周期 2）：

注入窗（双臂只需其一）：

| 臂 | 关键数字 | 结果 |
| --- | --- | --- |
| **台阶臂** | 边 p50 **0.58 → 5400.27 ms**；绝对门槛 1000 ms、相对门槛 58 ms（100×基线） | **通过** |
| 边静默臂 | 边速率 **0.441667/s**，天花板 0.125/s（基线 1.25 × 0.1） | 不通过 |

恢复窗：

| 量 | 值 |
| --- | ---: |
| 边基线 | 75 span / 60 s = **1.25/s** |
| 恢复窗（30 s）实测 span | **0** |
| 恢复窗期望调用数 | **37.5** |
| 边静默臂 | **再次点火** → `symptom_still_true: true` |
| 残留探针 | `iptables INPUT DROP rules=0`（干净） |

**这条说明了什么**：**长连接靶子的"恢复"不等于"规则删掉"。** iptables 规则删干净了，
但 `cart` 到 valkey 的 TCP 连接在 DROP 期间被打断，重建连接池要时间，30 s 不够。
期望 37.5 次实测 0 次 —— 这个不是样本量问题（对比 [F-2](#f-2recover-窗对低流量靶子过短两张-payment-卡判失败2026-08-28)
那两张期望才 1.6 次），**信号真的是零，服务真的还没回来**。

**两条 recovered 失败因此是两个不同的病**：F-2 是读不出信号，F-4 是信号真的没有。
给 recovered 加样本量守卫能救 F-2，救不了 F-4；F-4 要的是更长的恢复等待。

**处置**：卡**不出库**，按 [O-P2-22](#o-p2-22valkey-cart-撤除-drop_inbound-后重连滞后超过-recover-窗) 先量重连时长再定 `recover_s`。
`crash-valkey-cart-01`（配方里未量产）大概率同病，第三批**不选它**。

---

## F-5　把长连接靶子的 blackhole 特例写成通例，两张卡被判成 latency（2026-08-28）

**一句话**：v2 prompt 里 crash/blackhole 那段，我把 `valkey-cart` 的
**「超时台阶」**形态写成了 blackhole 的**通例**，而 [fingerprints.md](fingerprints.md)
归档的通例是**「哑」—— 0 span、0 报错、连一条完成的 span 都没有**；
台阶形态只对**有客户端超时的长连接 / 无 SDK 靶子**成立。

**写进 prompt 的原话（v2，已随决策 025 冻结）**：

> A silently dropped packet gives the caller nothing to react to, so the caller waits.
> The signature is a step up in caller-side latency -- p50 rising by orders of
> magnitude, into seconds -- with few or no errors during the fault itself.

**fingerprints.md 实际归档的两种形态**：

| 靶子类型 | 注入窗形态 | 出处 |
| --- | --- | --- |
| 普通 SDK 靶子（`cart` 等，**多数**） | caller span **0 条**、报错 **0 条**、无时延可测 —— TCP 卡在重传退避，调用方**既不成功也不报错，就是挂着** | 「crash vs blackhole 分辨结论」「为什么 blackhole 是"哑"的」 |
| 长连接 / 无 SDK 靶子（`valkey-cart`，**例外**） | p50 **0.49 → 5702 ms**、报错 **0 条**、span 数只掉到 36%；+74.4 s 后才转静默 | 「长连接靶子的 blackhole 指纹与「哑」不同」 |

fingerprints.md 对这个例外写得很清楚：**「`blackhole` 类的「注入期间哑」对有客户端
超时的长连接靶子不成立，它先表现为极端延迟且零报错（与 `latency` 类同形、只差量级）」**
—— 「与 latency 同形」这半句正是危险所在，而我把它当成了 blackhole 的定义。

**后果（实测）**：blackhole 类 top-1 **3/4 → 1/4**。

| card | v1 | v2 |
| --- | --- | --- |
| `blackhole-frontend-01` | frontend/黑洞 ✓ | frontend/**延迟** ✗ |
| `blackhole-cart-01` | cart/黑洞 ✓ | **valkey-cart**/黑洞 ✗ |
| `blackhole-currency-01` | currency/延迟 ✗ | currency/延迟 ✗（v1 就错，未变） |

`blackhole-frontend-01` 是干净的因果：prompt 说 blackhole 的签名是「p50 抬到秒级、
几乎无报错」，这句话同时也是 latency 的签名，模型选了 latency。
`blackhole-cart-01` 更细 —— 模型顺着「台阶 = blackhole」把答案挪到了真正会出台阶的
那个邻居 `valkey-cart`，服务因此错了一格。

**这条说明了什么**：**往 prompt 里灌指纹知识时，「例外」和「通例」必须标清楚，
否则例外会把通例吃掉。** 两段方法论里，写对的那两段（内存曲线、crash fail-fast）
各自把目标类别从 0 抬到 2/4 与 1/2；写错的这一段把一个本来就对的类别从 3/4 打到 1/4。
**知识注入的收益和风险是同一个量级的**，所以素材的准确性不是文档卫生问题，
是准确率问题。

**处置**：**本轮不修。** 决策 025 的一次迭代纪律是在看到结果之前定的
（top-1 变好则定版），v2 的 top-1 确实变好（52.6% → 57.9%），所以 **v2 定版、prompt 冻结**，
本条修正**留给第三版**。修法已经明确、留档在此：

> 通例回到「哑」：callers see neither success nor error, the edge simply goes quiet.
> 台阶形态降级为一句限定的例外：a target that keeps a long-lived connection and whose
> client has its own timeout will first show extreme latency with zero errors before
> going quiet -- do not read that shape as latency.

触发条件同决策 025：**卡集扩大后重测一次 v2 基线，再发第三版**，不单独为这一条改 prompt。

**相关**：决策 025、决策 024 trade-off、[fingerprints.md](fingerprints.md) 的两节 blackhole 指纹。

