# 全栈资源限额审计

采集于 2026-08-23，testbed = opentelemetry-demo 3.0.0（分支 `p2-baseline`）。
规则与背景见 [decisions.md](decisions.md) 008。

## 抬限规则

满足任一条即命中：**用量占比 > 50%**、**曾 OOM（退出码 137）**、**限额 ≤ 64M**。
命中后抬到 **≥ 当前用量 4 倍**，向上取整到 `{256M, 512M, 768M, 1G, 2G, 4G}` 最近一档。
**4G 档为后补**，全栈仅 `opensearch` 命中（首轮按 `{…,2G}` 封顶后仍持续上涨）。
已有独立覆盖的 `ad` / `grafana` / `prometheus` 不再动；`checkout` 与 `astronomy-db` 强制纳入。

## 审计表

`lim0/use0/pct0` = 抬限前；`lim1/use1/pct1` = 抬限并稳定 628 秒后。

| 服务 | lim0 | use0 | pct0 | 重启 | OOM过 | 命中原因 | lim1 | use1 | pct1 |
| --- | ---: | ---: | ---: | ---: | :---: | --- | ---: | ---: | ---: |
| `ad` | 768M | 498.6M | 64.9% | 0 | 否 | — | 768M | 499.5M | 65.0% |
| `astronomy-db` | 80M | 55.0M | 68.8% | 1 | 否 | pct>50+forced | 256M | 52.9M | 20.7% |
| `cart` | 160M | 108.0M | 67.5% | 0 | 否 | pct>50 | 512M | 47.9M | 9.4% |
| `checkout` | 20M | 14.8M | 73.8% | 1 | 否 | pct>50+lim<=64M+forced | 256M | 30.9M | 12.1% |
| `currency` | 20M | 8.2M | 41.0% | 0 | 否 | lim<=64M | 256M | 14.6M | 5.7% |
| `email` | 100M | 67.2M | 67.2% | 0 | 否 | pct>50 | 512M | 70.0M | 13.7% |
| `flagd` | 75M | 58.2M | 77.7% | 0 | 否 | pct>50 | 256M | 85.9M | 33.6% |
| `flagd-ui` | 200M | 175.6M | 87.8% | 0 | 否 | pct>50 | 768M | 177.5M | 23.1% |
| `frontend` | 250M | 178.9M | 71.6% | 0 | 否 | pct>50 | 768M | 127.9M | 16.7% |
| `frontend-proxy` | 90M | 37.1M | 41.2% | 0 | 否 | — | 90M | 38.5M | 42.8% |
| `grafana` | 512M | 380.0M | 74.2% | 0 | 否 | — | 512M | 379.6M | 74.1% |
| `image-provider` | 120M | 27.6M | 23.0% | 0 | 否 | — | 120M | 27.9M | 23.2% |
| `jaeger` | 1200M | 356.8M | 29.7% | 0 | 否 | — | 1200M | 397.2M | 33.1% |
| `load-generator` | 512M | 453.2M | 88.5% | 0 | 否 | pct>50 | 2G | 403.3M | 19.7% |
| `opamp-server` | 65M | 21.3M | 32.8% | 0 | 否 | — | 65M | 21.6M | 33.2% |
| `opensearch` | 1G | 925.6M | 90.4% | 1 | 否 | pct>50 | 2G | 1283.1M | 62.6% |
| `otel-collector` | 400M | 200.1M | 50.0% | 0 | 否 | pct>50 | 1G | 212.5M | 20.8% |
| `payment` | 140M | 117.9M | 84.2% | 0 | 否 | pct>50 | 512M | 129.8M | 25.4% |
| `product-catalog` | 20M | 16.0M | 79.9% | 1 | 否 | pct>50+lim<=64M | 256M | 25.2M | 9.8% |
| `prometheus` | 2G | 375.1M | 18.3% | 0 | 否 | — | 2G | 389.0M | 19.0% |
| `quote` | 40M | 20.8M | 52.0% | 0 | 否 | pct>50+lim<=64M | 256M | 19.3M | 7.5% |
| `recommendation` | 500M | 76.4M | 15.3% | 0 | 否 | — | 500M | 75.8M | 15.2% |
| `shipping` | 20M | 9.6M | 48.1% | 0 | 否 | lim<=64M | 256M | 9.7M | 3.8% |
| `telemetry-docs` | 100M | 24.8M | 24.8% | 0 | 否 | — | 100M | 24.2M | 24.2% |
| `valkey-cart` | 20M | 4.9M | 24.7% | 0 | 否 | lim<=64M | 256M | 4.5M | 1.8% |

合计限额 17.3G / 实际用量 4.4G，宿主机 62G（`opensearch` 后补 4G 档后由 15.3G 变为 17.3G）。

## 命中清单

共 **16** 个：`astronomy-db`、`cart`、`checkout`、`currency`、`email`、`flagd`、`flagd-ui`、`frontend`、`load-generator`、`opensearch`、`otel-collector`、`payment`、`product-catalog`、`quote`、`shipping`、`valkey-cart`。

## 两处需要注意

**1. `opensearch` 规则冲突 —— 已用后补的 4G 档解决。** 用量 925.6M，4 倍 = 3702M，
超出当时 `{…, 2G}` 的档顶，首轮封顶为 2G。抬到 2G 后用量继续涨到 1283.1M / 62.6%，
是唯一一个抬限后仍逼近阈值的命中服务。故补 4G 档并抬到 **4G**（2026-08-23 23:21 重建）。
JVM 堆由 `OPENSEARCH_JAVA_OPTS` 决定，容器限额只给空间、不改堆上限；日志量随场景轮次
线性累积，4G 也只是推迟而非解决 —— 真正的对策是批次间清理索引，见
[open_items.md](open_items.md) O-P2-1。

**2. 抬限后仍 > 50% 的两个**：`ad`(65.0%)、`grafana`(74.1%)。两个都是 002 已单独覆盖过的，
稳态如此、无重启，暂不再抬。`opensearch` 抬到 4G 后占比降至约 31%，见上。

## mem_leak 类的例外

本审计抬的是**基线**限额。`mem_leak` 类做卡时按卡临时压低靶子限额，
靠的是卡内的临时覆盖，与这里的基线无关，抬基线不影响该类可做性。

## 端口钉死（2026-08-23 23:21）

基础文件里 `jaeger` / `opensearch` 的 `ports` 只写了容器端口，Docker 每次重建都分配
新的随机宿主机端口（实测 32774 / 32801），`scripts/backends.env` 会随之失效。现钉成
固定映射：**jaeger 16686、opensearch 9200**。

用了 Compose 的 `!override` 标签而非默认合并 —— compose 对 `ports` 是**追加**，
不加标签会同时保留基础文件那条随机端口映射（实测合并结果为
`[(None,16686), ('16686',16686)]`）。

另注：`opensearch` 在覆盖文件里已由审计块定义过，端口与 4G 是**并入**该条目的。
YAML 里重复出现同名 key 时 PyYAML 静默取最后一个，而 compose 的 Go 解析器会直接报
`mapping key "opensearch" already defined` —— 追加式编辑覆盖文件时要留意。

---

## 2026-08-24 导出间隔 15s 后复查

决策 013 把 11 个自研服务的指标导出间隔由 60s 降到 15s（样本率 ×4），并把 spanmetrics
的 `metrics_flush_interval` 一并设为 15s。`prometheus` 限额未动，仍为 2G。

| 指标 | 改前（18:39:11Z） | 改后 10 分钟（19:00:43Z） | 变化 |
| --- | ---: | ---: | ---: |
| MEM | 319.6MiB / 2GiB（15.60%） | 332.4MiB / 2GiB（16.23%） | +12.8MiB |
| CPU | 21.08% | 0.51% | 采样瞬时值，不可比 |
| TSDB `/prometheus` | 351.1M | 406.5M | +55.4M |
| WAL `/prometheus/wal` | 148.0M | 183.4M | +35.4M |

十分钟窗口内稳态内存仅增 12.8MiB，2G 限额有充裕余量。

**重启重放峰值待 [O-P2-3](open_items.md)。** 本次全程未重启 `prometheus`，因此这张表反映的
只是**稳态**代价。决策 008 记录的那次死锁发生在**重启时的 WAL 重放**阶段，样本率 ×4 后
重放要多吃约 4 倍内存，而该峰值尚无实测数据 —— 稳态数字好看不能推断重启安全。

---

## 2026-08-26 — Prometheus restart WAL replay peak（O-P2-3 复测，15s 导出间隔）

**Case B** —— 25 个容器随 VM 自动重启（`Up 2 minutes`），`prometheus` 已在运行。
先过健康门（25/25 running，`cart` RestartCount=0），再启动监视器并执行
`docker compose <三文件> restart prometheus` 作为被测重放事件。

| 项 | 值 |
| --- | --- |
| 限额 | **2048 MiB**（默认 **200M** → 覆盖 **2G**，来源 `testbed/compose.override.yaml:37-41`；默认值在 `opentelemetry-demo/compose.observability.yaml:83`） |
| 重启后峰值 | **88.0 MiB = 4.3% of limit**，出现在 StartedAt **+144.4 s** |
| 稳态 | **85.3 MiB**（末 12 采样均值） |
| WAL 大小 | `/prometheus/wal` **3.6M**（TSDB 总计 404.9M） |
| head series | **unavailable**（宿主机 curl 与容器内 wget 均取不到） |
| OOMKilled | **false** |
| RestartCount | **0 → 0**（`docker compose restart` 不增计数） |
| `restarted_during_watch` | **true —— Case B 的设计使然**，见下方说明 |
| stop_reason | **stable** |
| WAL replay 实测 | `total_replay_duration=` **118.974 ms**（`wal_replay_duration=117.73ms`） |
| CSV / summary | `artifacts/resource_audit/prom_mem_2026-08-26.csv`、`…csv.summary.txt` |
| 监视日志 | `artifacts/resource_audit/prom_mem_watch_2026-08-26.log` |

### 结论：**O-P2-3 保持开放** —— 本次未真正压到 WAL 重放

按第 6 步规则，`peak_pct = 4.3% ≤ 60%` 对应「关闭」。**但不予关闭**，理由是数据没有
回答 O-P2-3 的问题：

1. **重放的 WAL 只有 3.5 分钟的量。** 容器在 22:33:54 随 VM 启动，我在 22:37:25 重启它
   —— 此时 WAL 只积了 3.5 分钟，`total_replay_duration` 仅 **118.97 ms**。而决策 008 记录的
   死锁发生在积累约一天之后。**119 毫秒的重放不构成对 2G 限额的压力测试。**
2. **真正有意义的那次重放被错过了。** 昨日 WAL（15s 导出间隔下累积）的重放发生在
   VM 自动开机的 **22:33:54**，比监视器启动早 3.5 分钟。CSV 前两行的 167.8 / 168.1 MiB
   是那次启动的**尾部**，不是它的峰值 —— 峰值未被采到。
3. `restarted_during_watch=true` **不是崩溃证据**。Case B 要求脚本主动 restart，
   StartedAt 必然改变。同期 `RestartCount` 全程 **0 → 0**、`OOMKilled=false`，
   说明容器没有因超限被杀。第 6 步规则的第一条（`restarted_during_watch=true` →
   BLOCKING）是为「重放中意外崩溃」写的，在 Case B 下会恒真，故不按字面适用；
   此处以 `RestartCount` 与 `OOMKilled` 为准。

**要真正测到峰值**，需要在 WAL 积累一天以上后、在**容器启动的那一刻之前**就开始采样
（监视器支持 `status=absent` 轮询，可先起监视器再 `up -d`），或直接在下次 VM 冷启动前
把监视器挂上。

### 全栈稳定后快照

| 容器 | 内存 | 占限额 | CPU |
| --- | ---: | ---: | ---: |
| `ad` | 410.1 MiB / 768 MiB | 53.39% | 0.14% |
| `astronomy-db` | 78.36 MiB / 256 MiB | 30.61% | 6.44% |
| `cart` | 107.5 MiB / 512 MiB | 20.99% | 0.07% |
| `checkout` | 42.56 MiB / 256 MiB | 16.63% | 0.61% |
| `currency` | 27.39 MiB / 256 MiB | 10.70% | 3.55% |
| `email` | 90.51 MiB / 512 MiB | 17.68% | 0.20% |
| `flagd` | 112 MiB / 256 MiB | 43.75% | 0.19% |
| `flagd-ui` | 175.6 MiB / 768 MiB | 22.86% | 0.05% |
| `frontend` | 188.3 MiB / 768 MiB | 24.52% | 7.34% |
| `frontend-proxy` | 39.58 MiB / 90 MiB | 43.98% | 3.15% |
| `grafana` | 394.7 MiB / 512 MiB | **77.09%** | 1.03% |
| `image-provider` | 27.29 MiB / 120 MiB | 22.74% | 0.23% |
| `jaeger` | 147.4 MiB / 1.172 GiB | 12.29% | 0.80% |
| `load-generator` | 481.5 MiB / 2 GiB | 23.51% | 136.05% |
| `opamp-server` | 20.7 MiB / 65 MiB | 31.84% | 0.00% |
| `opensearch` | 1.325 GiB / 4 GiB | 33.12% | 0.92% |
| `otel-collector` | 321.6 MiB / 1 GiB | 31.40% | 2.79% |
| `payment` | 145 MiB / 512 MiB | 28.32% | 1.03% |
| `product-catalog` | 22.33 MiB / 256 MiB | 8.72% | 0.77% |
| **`prometheus`** | **97.33 MiB / 2 GiB** | **4.75%** | 0.40% |
| `quote` | 36.04 MiB / 256 MiB | 14.08% | 0.01% |
| `recommendation` | 71.89 MiB / 500 MiB | 14.38% | 2.26% |
| `shipping` | 19.5 MiB / 256 MiB | 7.62% | 0.31% |
| `telemetry-docs` | 25.7 MiB / 100 MiB | 25.70% | 0.21% |
| `valkey-cart` | 12.13 MiB / 256 MiB | 4.74% | 0.24% |

宿主机 `free -m`：总 64295 / 已用 5091 / 可用 59203 MiB。未改动任何限额。

### Addendum —— 为什么 88.0 MiB 不是装满态的峰值

**1. 开机重放早就跑完了，而且没给手动重启留下可重放的东西。**
容器带 restart policy，随 VM 一起复活。开机时昨天的 WAL 在无人值守下完成重放，
几分钟内 head block 就被压缩成正式块、WAL 被截断。等到 Case B 的手动重启执行时，
WAL 里只剩开机之后的数据 —— **3.6M，重放耗时 118.97 ms**。所以
**88.0 MiB 是一次近乎空载重放的峰值**，不是装满态的。

**2. 手动重启前的采样能证明什么、不能证明什么。**
watcher 在手动重启**之前**取到的两个采样，对开机实例
（`StartedAt 2026-08-26T22:33:54Z`）显示 `restart_count=0`、`oom_killed=false`。
这是**开机重放没有把容器 OOM 杀掉**的正面证据，也是唯一能拿到的证据：
手动 `docker compose restart` 会把这个可观测的计数器清零，之后取的采样对开机
那次事件什么都说明不了。

**3. O-P2-3 的新关闭条件。**
两条必须同时满足：
  - **(a) 开机重放不 OOM** —— 已满足，见第 2 点；
  - **(b) 在 head 跨度 ≥ 2h40m 时做一次重启，峰值 ≤ 上限的 60%** —— 尚未实测。

其余分档不变：60–85% 保留该项并在首批 16 卡后复查；> 85% 或出现任何 OOM 即为
拦路项，新限额由用户决定。复测时 **`restarted_during_watch` 只从手动重启之后起算** ——
在 Case B 下这个标志按构造必然为 true，说明不了是否崩溃。

**4. 复测窗口。**
取自 `artifacts/resource_audit/prom_mem_2026-08-26.csv` 中第一条 `status=running`
样本的 `started_at`，即开机时的容器启动时间：

| 时刻 | UTC | America/New_York |
| --- | --- | --- |
| 开机容器启动 | `2026-08-26T22:33:54Z` | `2026-08-26 18:33:54 EDT` |
| **+2h40m**（窗口开） | `2026-08-27T01:13:54Z` | `2026-08-26 21:13:54 EDT` |
| **+2h59m**（窗口关） | `2026-08-27T01:32:54Z` | `2026-08-26 21:32:54 EDT` |

**先**启动 `scripts/maintenance/prom_mem_watch.sh` **再**重启容器 —— 它会以
`status=absent` 轮询等待，因此能采到新实例的第一秒。


---

## 2026-08-27 开机重放（Case A）

今早 VM 冷启动时由 `wakeup.sh --watch-prometheus` 在起床命令**之前**挂上监视器，
因此采到了新实例存在的第一秒 —— 这是 2026-08-26 那次 Case B 缺的东西。

| 项 | 值 |
| --- | --- |
| WAL 跨度 | 约 **1.5 h**（前一实例 `StartedAt 2026-08-26T22:37:25Z`，本次 `2026-08-27T16:14:23Z`） |
| 峰值 | **407.6 MiB**，启动后 **27.8 s** |
| 峰值占限额 | **19.9%**（限额 2048 MiB） |
| 稳态 | 189.0 MiB |
| OOMKilled | **false** |
| RestartCount | 0 → 0 |
| `restarted_during_watch` | false |
| stop_reason | stable |
| 证据 | `artifacts/resource_audit/prom_mem_2026-08-27_boot.csv`、`…csv.summary.txt` |

对照 2026-08-26 的 Case B（3.6M WAL、重放 118.97 ms、峰值 88.0 MiB / 4.3%）：
这次 WAL 跨度大得多，峰值也高了 4.6 倍，但仍只用掉限额的 **19.9%**。

**O-P2-3 仍保留** —— 1.5 h 跨度不满足关闭条件 (b) 要求的 **≥ 2h40m**。
自等待探针 `scripts/maintenance/prom_wal_restart_probe.sh` 已挂上，会在 head 跨度
达标时自动抢锁重启一次并落 `prom_mem_<date>_walrestart.summary.txt`。
