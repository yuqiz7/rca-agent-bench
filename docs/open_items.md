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

**runner 现状**：`run_batch.py` **未留批次边界钩子**，清理动作暂无处挂载，见 O-P2-7。

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

W2 建库前需要给 runner 加一个批次级 pre/post hook（或由外层调度脚本承担），
清理动作只能落在批次之间，不得落在周期之间（原因见 [O-P2-1](#o-p2-1w2-harness-需在批次间清理-opensearch-日志索引)）。
