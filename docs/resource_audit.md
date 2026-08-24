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
