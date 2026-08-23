# 全栈资源限额审计

采集于 2026-08-23，testbed = opentelemetry-demo 3.0.0（分支 `p2-baseline`）。
规则与背景见 [decisions.md](decisions.md) 008。

## 抬限规则

满足任一条即命中：**用量占比 > 50%**、**曾 OOM（退出码 137）**、**限额 ≤ 64M**。
命中后抬到 **≥ 当前用量 4 倍**，向上取整到 `{256M, 512M, 768M, 1G, 2G}` 最近一档。
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

合计限额 15.3G / 实际用量 4.4G，宿主机 62G。

## 命中清单

共 **16** 个：`astronomy-db`、`cart`、`checkout`、`currency`、`email`、`flagd`、`flagd-ui`、`frontend`、`load-generator`、`opensearch`、`otel-collector`、`payment`、`product-catalog`、`quote`、`shipping`、`valkey-cart`。

## 两处需要注意

**1. `opensearch` 规则冲突。** 用量 925.6M，4 倍 = 3702M，超出 `{…, 2G}` 档顶，
无档可取。封顶为 2G（仍是旧限 1G 的两倍），未擅自新增 4G 档。抬限后用量涨到 
1283.1M / 62.6% — 它是唯一一个抬限后仍逼近阈值的命中服务，JVM 堆由 
`OPENSEARCH_JAVA_OPTS` 决定，容器限额只给空间、不改堆上限。日志量随场景轮次累积，
建库期间需要复查。

**2. 抬限后仍 > 50% 的三个**：`ad`(65.0%)、`grafana`(74.1%)、`opensearch`(62.6%)。
前两个是 002 已单独覆盖过的，稳态如此、无重启，暂不再抬；`opensearch` 见上。

## mem_leak 类的例外

本审计抬的是**基线**限额。`mem_leak` 类做卡时按卡临时压低靶子限额，
靠的是卡内的临时覆盖，与这里的基线无关，抬基线不影响该类可做性。
