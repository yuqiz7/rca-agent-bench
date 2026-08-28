# 场景库配方（v1.1，2026-08-27 ET）

> **v1.1，见 [决策 021](decisions.md) 及其「修订 2026-08-27」。**
> 本文的配方表由 `scripts/scenarios/recipe.csv` 生成（`scripts/scenarios/generate.py`），
> csv 是唯一真源；改配方改 csv，不要手改本文第四节的表。
> 配方里删掉的卡由生成器负责删除对应 yaml，**不要手删** `scenarios/`。

**v1.0 → v1.1 差异摘要**：`productCatalogFailure` 由 **10 张减到 3 张**
（`2ZYFJ3GM2N` / `66VCHSJNUP` / `OLJCESPC7Z`），总卡数 **76 → 69**，misconfig **21 → 14**。

**修订理由**：[O-P2-14](open_items.md) 的份额实测（2026-08-27，27 min，n = 4154）显示
10 个 `product_id` 的 `GetProduct` 份额**近乎均匀** —— 极差只有 **2.3 个百分点**
（11.4% ~ 9.1%），是压测器 `user_browse_product` 均匀抽样的直接结果。
份额均匀意味着这 10 张卡**在轴 B 上彼此不可分**：服务级失败比例都是 9–11%，
三轴给出的分数只在 B=1 / B=2 那条 10% 的边界上被切成两组，而那条边界恰好落在
分布正中间，属于人为切分而不是真实差异。**10 张里有 7 张是重复卡**，
对 agent 是同一道题，却照样各吃 6.5 min 机器时间。
保留的 3 张取份额**最高**（`2ZYFJ3GM2N` 11.4%）、**最低**（`66VCHSJNUP` 9.1%）、
**已有实测且在首批**（`OLJCESPC7Z` 9.3%）各一张。

`recipe.csv` 同时新增 **`batch_order` 列**：首批 16 张按本文第五节顺序编号 1–16，
其余留空；`run_batch.py --batch N` 按该列排序，不再走文件名字母序。

**v0.2 → v1.0 差异摘要**：配方经决策 021 定稿 —— 总卡数 **76**（crash 13／blackhole 13／
latency 26／misconfig 21／mem_leak 3，v1.1 已改为 69）；难度三轴与档位规则写入决策；`paymentFailure` 6 张
按裁决入卡、全部标未验证，首批带一张 `misconfig-payment-100` 做验证（不过门则整组出库）；
首批 16 卡中 `crash-quote-01` 由 `misconfig-payment-100` 替换；配方表改由 csv 生成，
第四节表格为生成物。

**v0.1 → v0.2 差异摘要**：落实六条裁决 —— 新增「靶子须在 trace 路径上」准入规则并移除
`image-provider`；补裁 `adFailure` / `cartFailure` / `paymentFailure` 三个 misconfig 开关入卡
（misconfig 根因对由 2 增至 5）；多变体开关按变体逐张出卡（`paymentFailure` 6 张、
`productCatalogFailure` 按 10 个 `product_id` 各 1 张、`cartFailure` 3 张）；难档目标由 ≥15 改为 ≥8
并新增「难度终判规则」；首批 16 卡改为由 runner 准入门承担参数验证；`latency` 高档轴 C 暂记 0。
总卡数由 65 增至 **76**，难卡由 5 增至 **14**。

### 一、靶子盘点（裁决后）

**裁决 2026-08-27｜靶子准入新增规则**：靶子必须**在购物请求的 trace 路径上**
（自身有 SDK，或有带 SDK 的调用方边）。

**据此移除**

| 移除靶子 | 原属类 | 理由 |
| --- | --- | --- |
| `image-provider` | crash / blackhole / latency | `frontend-proxy` 直转的静态图片服务，**无 SDK、不在 trace 路径上**；出卡后证据包中 **trace / 指标 / 配置 diff 三件为空**，agent 无从推理，卡本身不成立 |

基础设施组件的排除理由沿用 v0.1（`otel-collector` / `prometheus` / `jaeger` / `grafana` /
`opensearch` / `flagd` / `load-generator` / `frontend-proxy` / `flagd-ui` / `telemetry-docs` /
`opamp-server`），不再重复。

#### crash / blackhole / latency 共用靶子集合 —— **13 个**

拓扑来源：仓库内无拓扑导出文件；本表由 Jaeger `/api/dependencies`（2h 回看，2026-08-27 现采）
加 compose 依赖推出。`astronomy-db` / `valkey-cart` 不产生 server span，其边由调用方 client span
的 peer 补入（决策 018）。

| 靶子 | 调用深度 | 调用方 |
| --- | ---: | --- |
| `ad` | 1 | `frontend` |
| `astronomy-db` | 2 | `product-catalog` |
| `cart` | 1 | `checkout`, `frontend` |
| `checkout` | 1 | `frontend` |
| `currency` | 1 | `checkout`, `frontend` |
| `email` | 2 | `checkout` |
| `frontend` | 0 | `frontend-proxy` |
| `payment` | 2 | `checkout` |
| `product-catalog` | 1 | `checkout`, `frontend`, `recommendation` |
| `quote` | 2 | `shipping` |
| `recommendation` | 1 | `frontend` |
| `shipping` | 1 | `checkout`, `frontend` |
| `valkey-cart` | 2 | `cart` |

`latency` 两档：**800 ms**（低档，已入库档实测）／**3000 ms**（高档，待验证）。

#### misconfig 靶子 —— **5 个开关**（裁决 2026-08-27 补裁 3 个入卡）

| 开关 | ground truth | 形态 | 实测比例 | 入卡依据 |
| --- | --- | --- | ---: | --- |
| `adFailure` | **(ad, misconfig)** | 方法级（`GetAds`，唯一被调方法） | **0.1** | judge_222727 实测 5/23 |
| `cartFailure` | **(cart, misconfig)** | 方法级（仅 `EmptyCart`，占 cart 调用 5.7%） | 变体值 × 5.7% | rerun_cart_225459 实测 2/4 通过 |
| `paymentFailure` | **(payment, misconfig)** | 方法级（`Charge`，唯一被调方法） | = 变体值 | **无入库档实测，见拦路问题 1** |
| `paymentUnreachable` | **(checkout, misconfig)** | 下游边消失 | 1.0（全量） | obs2card_001542 实测 11/11 |
| `productCatalogFailure` | **(product-catalog, misconfig)** | targeting 型 | 按 product_id 占比 | obs2card_001542 实测 r=7.6% |

**复查结论**：`flag_catalog.md` 中其余开关（`adHighCpu`、`adManualGc`、`failedReadinessProbe`、
`imageSlowLoad`、`intlShippingSlowdown`、`kafkaQueueProblems`）**均无任何实测记录**，按裁决
「没有实测记录的开关一律不入」**不入卡**。`recommendationCacheFailure` 按 O-P2-8 排除
（0.1 MiB/min，120 s 窗不可判定）。

#### mem_leak —— 1 个开关

`emailMemoryLeak` → **(email, mem_leak)**，深度 2。实测：`10000x` +171.5 MiB/120s、
`1000x` +35.5 MiB/120s。出 3 张卡（含 `100x` 待验证）；`1x`/`10x` 量级过低未出卡。

### 二、唯一根因对（裁决后重算）

| 类 | 唯一根因对 | 卡数 |
| --- | ---: | ---: |
| `crash` | **13** | 13 |
| `blackhole` | **13** | 13 |
| `latency` | **13** | 26 |
| `misconfig` | **5** | 21 |
| `mem_leak` | **1** | 3 |
| **合计** | **45** | **76** |

### 三、难度评分（合并展示）

| 根因对模式 | 深度 | A | B | C | 总分 | 档位 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `(ad, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(astronomy-db, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(cart, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(checkout, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(currency, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(email, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(frontend, crash)` | 0 | 0 | 0 | 0 | 0 | **易** |
| `(payment, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(product-catalog, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(quote, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(recommendation, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(shipping, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(valkey-cart, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(ad, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(astronomy-db, blackhole)` | 2 | 2 | 0 | 1 | 3 | **中** |
| `(cart, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(checkout, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(currency, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(email, blackhole)` | 2 | 2 | 0 | 1 | 3 | **中** |
| `(frontend, blackhole)` | 0 | 0 | 0 | 1 | 1 | **易** |
| `(payment, blackhole)` | 2 | 2 | 0 | 1 | 3 | **中** |
| `(product-catalog, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(quote, blackhole)` | 2 | 2 | 0 | 1 | 3 | **中** |
| `(recommendation, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(shipping, blackhole)` | 1 | 1 | 0 | 1 | 2 | **中** |
| `(valkey-cart, blackhole)` | 2 | 2 | 0 | 1 | 3 | **中** |
| `(ad, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(astronomy-db, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(cart, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(checkout, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(currency, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(email, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(frontend, latency)` | 0 | 0 | 0 | 0 | 0 | **易** |
| `(payment, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(product-catalog, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(quote, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(recommendation, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(shipping, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(valkey-cart, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(ad, misconfig)` B=2 | 1 | 1 | 2 | 0 | 3 | **中** |
| `(cart, misconfig)` B=2 | 1 | 1 | 2 | 0 | 3 | **中** |
| `(payment, misconfig)` B=2 | 2 | 2 | 2 | 0 | 4 | **难** |
| `(payment, misconfig)` B=1 | 2 | 2 | 1 | 0 | 3 | **中** |
| `(payment, misconfig)` B=0 | 2 | 2 | 0 | 0 | 2 | **中** |
| `(checkout, misconfig)` B=0 | 1 | 1 | 0 | 2 | 3 | **难** |
| `(product-catalog, misconfig)` B=1 | 1 | 1 | 1 | 0 | 2 | **中** |
| `(product-catalog, misconfig)` B=2 | 1 | 1 | 2 | 0 | 3 | **中** |
| `(email, mem_leak)` | 2 | 2 | 2 | 1 | 5 | **难** |

### 四、配方表（全部卡，69 张）

下表由 `scripts/scenarios/generate.py` 从 `scripts/scenarios/recipe.csv` 生成，
**不要手改**。`params` 列即卡片 yaml 的 `params` 字段（`key=value` 展开）。
档位由三轴按决策 021 算出：总分 0–1 易、2–3 中、≥4 难，轴 C=2 直接升一档。

<!-- BEGIN GENERATED: recipe-table (scripts/scenarios/generate.py) -->

| card_id | class | target | primitive | params | A | B | C | total | 档位 | param_validated | batch | 周期覆盖 | note |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- | --- |
| `crash-ad-01` | crash | `ad` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 3 | — |  |
| `crash-astronomy-db-01` | crash | `astronomy-db` | `kill_container` | — | 2 | 0 | 0 | 2 | 中 | no | 2 | — |  |
| `crash-cart-01` | crash | `cart` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | yes | 1 | — |  |
| `crash-checkout-01` | crash | `checkout` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 2 | `inject_s=300` |  |
| `crash-currency-01` | crash | `currency` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 2 | — |  |
| `crash-email-01` | crash | `email` | `kill_container` | — | 2 | 0 | 0 | 2 | 中 | no | 2 | `inject_s=300` |  |
| `crash-frontend-01` | crash | `frontend` | `kill_container` | — | 0 | 0 | 0 | 0 | 易 | no | 1 | — |  |
| `crash-payment-01` | crash | `payment` | `kill_container` | — | 2 | 0 | 0 | 2 | 中 | no | 1 | `inject_s=300` |  |
| `crash-product-catalog-01` | crash | `product-catalog` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 2 | — |  |
| `crash-quote-01` | crash | `quote` | `kill_container` | — | 2 | 0 | 0 | 2 | 中 | no | 3 | — |  |
| `crash-recommendation-01` | crash | `recommendation` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 3 | — |  |
| `crash-shipping-01` | crash | `shipping` | `kill_container` | — | 1 | 0 | 0 | 1 | 易 | no | 3 | — |  |
| `crash-valkey-cart-01` | crash | `valkey-cart` | `kill_container` | — | 2 | 0 | 0 | 2 | 中 | no | — | — |  |
| `blackhole-ad-01` | blackhole | `ad` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 3 | — |  |
| `blackhole-astronomy-db-01` | blackhole | `astronomy-db` | `drop_inbound` | — | 2 | 0 | 1 | 3 | 中 | no | 3 | — |  |
| `blackhole-cart-01` | blackhole | `cart` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | yes | 1 | — |  |
| `blackhole-checkout-01` | blackhole | `checkout` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 2 | `inject_s=300` |  |
| `blackhole-currency-01` | blackhole | `currency` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 2 | — |  |
| `blackhole-email-01` | blackhole | `email` | `drop_inbound` | — | 2 | 0 | 1 | 3 | 中 | no | 3 | `inject_s=300` |  |
| `blackhole-frontend-01` | blackhole | `frontend` | `drop_inbound` | — | 0 | 0 | 1 | 1 | 易 | no | 2 | — |  |
| `blackhole-payment-01` | blackhole | `payment` | `drop_inbound` | — | 2 | 0 | 1 | 3 | 中 | no | 1 | `inject_s=300` |  |
| `blackhole-product-catalog-01` | blackhole | `product-catalog` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 2 | — |  |
| `blackhole-quote-01` | blackhole | `quote` | `drop_inbound` | — | 2 | 0 | 1 | 3 | 中 | no | 2 | — |  |
| `blackhole-recommendation-01` | blackhole | `recommendation` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 3 | — |  |
| `blackhole-shipping-01` | blackhole | `shipping` | `drop_inbound` | — | 1 | 0 | 1 | 2 | 中 | no | 3 | — |  |
| `blackhole-valkey-cart-01` | blackhole | `valkey-cart` | `drop_inbound` | — | 2 | 0 | 1 | 3 | 中 | no | 1 | — |  |
| `latency-ad-800` | latency | `ad` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | 3 | — | 低档，入库档实测值 |
| `latency-ad-3000` | latency | `ad` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-astronomy-db-800` | latency | `astronomy-db` | `delay_outbound` | delay_ms=800 | 2 | 0 | 0 | 2 | 中 | no | — | — | 低档，入库档实测值 |
| `latency-astronomy-db-3000` | latency | `astronomy-db` | `delay_outbound` | delay_ms=3000 | 2 | 0 | 0 | 2 | 中 | no | 3 | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-cart-800` | latency | `cart` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | yes | 1 | — | 低档，入库档实测值 |
| `latency-cart-3000` | latency | `cart` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-checkout-800` | latency | `checkout` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | 1 | `inject_s=300` | 低档，入库档实测值 |
| `latency-checkout-3000` | latency | `checkout` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | — | `inject_s=300` | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-currency-800` | latency | `currency` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | 2 | — | 低档，入库档实测值 |
| `latency-currency-3000` | latency | `currency` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-email-800` | latency | `email` | `delay_outbound` | delay_ms=800 | 2 | 0 | 0 | 2 | 中 | no | 1 | `inject_s=300` | 低档，入库档实测值 |
| `latency-email-3000` | latency | `email` | `delay_outbound` | delay_ms=3000 | 2 | 0 | 0 | 2 | 中 | no | — | `inject_s=300` | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-frontend-800` | latency | `frontend` | `delay_outbound` | delay_ms=800 | 0 | 0 | 0 | 0 | 易 | no | 2 | — | 低档，入库档实测值 |
| `latency-frontend-3000` | latency | `frontend` | `delay_outbound` | delay_ms=3000 | 0 | 0 | 0 | 0 | 易 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-payment-800` | latency | `payment` | `delay_outbound` | delay_ms=800 | 2 | 0 | 0 | 2 | 中 | no | 3 | `inject_s=300` | 低档，入库档实测值 |
| `latency-payment-3000` | latency | `payment` | `delay_outbound` | delay_ms=3000 | 2 | 0 | 0 | 2 | 中 | no | — | `inject_s=300` | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-product-catalog-800` | latency | `product-catalog` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | 2 | — | 低档，入库档实测值 |
| `latency-product-catalog-3000` | latency | `product-catalog` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-quote-800` | latency | `quote` | `delay_outbound` | delay_ms=800 | 2 | 0 | 0 | 2 | 中 | no | 3 | — | 低档，入库档实测值 |
| `latency-quote-3000` | latency | `quote` | `delay_outbound` | delay_ms=3000 | 2 | 0 | 0 | 2 | 中 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-recommendation-800` | latency | `recommendation` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | — | — | 低档，入库档实测值 |
| `latency-recommendation-3000` | latency | `recommendation` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | 3 | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-shipping-800` | latency | `shipping` | `delay_outbound` | delay_ms=800 | 1 | 0 | 0 | 1 | 易 | no | — | — | 低档，入库档实测值 |
| `latency-shipping-3000` | latency | `shipping` | `delay_outbound` | delay_ms=3000 | 1 | 0 | 0 | 1 | 易 | no | 3 | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `latency-valkey-cart-800` | latency | `valkey-cart` | `delay_outbound` | delay_ms=800 | 2 | 0 | 0 | 2 | 中 | no | 3 | — | 低档，入库档实测值 |
| `latency-valkey-cart-3000` | latency | `valkey-cart` | `delay_outbound` | delay_ms=3000 | 2 | 0 | 0 | 2 | 中 | no | — | — | 高档；轴 C 暂记 0，待实测重判（决策 011：gRPC 无 deadline） |
| `misconfig-cart-75` | misconfig | `cart` | `set_flag` | flag=cartFailure variant=75% | 1 | 2 | 0 | 3 | 中 | no | 1 | `settle_s=300` | 方法级：仅 EmptyCart（占 cart 调用 5.7%），服务级有效比例 ≈4.3%；10%/25%/50% 三档按 O-P2-9 排除 |
| `misconfig-cart-90` | misconfig | `cart` | `set_flag` | flag=cartFailure variant=90% | 1 | 2 | 0 | 3 | 中 | no | — | `settle_s=300` | 方法级：仅 EmptyCart（占 cart 调用 5.7%），服务级有效比例 ≈5.1%；10%/25%/50% 三档按 O-P2-9 排除 |
| `misconfig-cart-100` | misconfig | `cart` | `set_flag` | flag=cartFailure variant=100% | 1 | 2 | 0 | 3 | 中 | no | — | `settle_s=300` | 方法级：仅 EmptyCart（占 cart 调用 5.7%），服务级有效比例 ≈5.7%；10%/25%/50% 三档按 O-P2-9 排除 |
| `misconfig-payment-10` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=10% | 2 | 2 | 0 | 4 | 难 | no | — | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=10%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-payment-25` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=25% | 2 | 1 | 0 | 3 | 中 | no | — | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=25%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-payment-50` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=50% | 2 | 0 | 0 | 2 | 中 | no | 2 | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=50%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-payment-75` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=75% | 2 | 0 | 0 | 2 | 中 | no | 2 | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=75%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-payment-90` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=90% | 2 | 0 | 0 | 2 | 中 | no | 2 | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=90%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-payment-100` | misconfig | `payment` | `set_flag` | flag=paymentFailure variant=100% | 2 | 0 | 0 | 2 | 中 | no | 1 | `inject_s=300` | Charge 为 payment 唯一被调方法，服务级比例=100%；该开关无入库档实测，按决策 021 标未验证 |
| `misconfig-checkout-on` | misconfig | `checkout` | `set_flag` | flag=paymentUnreachable variant=on | 1 | 0 | 2 | 3 | 难 | yes | 1 | `inject_s=300` | 下游边消失形态；obs2card_001542 实测 PlaceOrder 11/11 报错 |
| `misconfig-pc-2ZYFJ3GM2N` | misconfig | `product-catalog` | `set_flag` | flag=productCatalogFailure product_id=2ZYFJ3GM2N variant=on | 1 | 1 | 0 | 2 | 中 | no | — | — | targeting 变体；该 product 在基线 GetProduct 中占比 12.1%（20 min 实测 371/3072） |
| `misconfig-pc-66VCHSJNUP` | misconfig | `product-catalog` | `set_flag` | flag=productCatalogFailure product_id=66VCHSJNUP variant=on | 1 | 2 | 0 | 3 | 中 | no | — | — | targeting 变体；该 product 在基线 GetProduct 中占比 9.9%（20 min 实测 304/3072） |
| `misconfig-pc-OLJCESPC7Z` | misconfig | `product-catalog` | `set_flag` | flag=productCatalogFailure product_id=OLJCESPC7Z variant=on | 1 | 2 | 0 | 3 | 中 | yes | 1 | — | targeting 变体；该 product 在基线 GetProduct 中占比 9.8%（20 min 实测 302/3072）；obs2card_001542 实测 r=7.6% |
| `memleak-email-10000x` | mem_leak | `email` | `set_flag` | flag=emailMemoryLeak variant=10000x | 2 | 2 | 1 | 5 | 难 | yes | 1 | `inject_s=300` | judge_222727 实测 +171.5 MiB/120s |
| `memleak-email-1000x` | mem_leak | `email` | `set_flag` | flag=emailMemoryLeak variant=1000x | 2 | 2 | 1 | 5 | 难 | yes | 1 | `inject_s=300` | judge_222727 实测 +35.5 MiB/120s |
| `memleak-email-100x` | mem_leak | `email` | `set_flag` | flag=emailMemoryLeak variant=100x | 2 | 2 | 1 | 5 | 难 | no | — | `inject_s=300` | 更低倍率，检验判据下限；待验证 |

<!-- END GENERATED: recipe-table -->

### 五、首批 16 卡（决策 021 定稿）

规则：五类都有、三档都有、**包含全部 8 张 `param_validated=yes`**，
其余 8 张从未实测靶子中选，优先覆盖不同深度与不同类。
首批由 runner 三探针准入门承担参数验证；**过不了门的卡记 `param_failed`、换参数重排，不进卡库**。

**v0.2 → v1.0 变更**：第 11 位由 `crash-quote-01` 换成 `misconfig-payment-100`。
`paymentFailure` 整组 6 张无入库档实测（见第八节拦路问题 1），决策 021 裁定
「入库、标未验证、首批带一张做验证」—— 这张就是那张验证卡；**不过门则 6 张整组出库**。
换掉 `crash-quote-01` 而不是加一张，是为了不动首批 16 的规模。

| # | card_id | class | depth | difficulty | param_validated |
| ---: | --- | --- | ---: | --- | --- |
| 1 | `crash-cart-01` | crash | 1 | 易 | yes |
| 2 | `blackhole-cart-01` | blackhole | 1 | 中 | yes |
| 3 | `latency-cart-800` | latency | 1 | 易 | yes |
| 4 | `misconfig-ad-on` | misconfig | 1 | 中 | yes |
| 5 | `misconfig-checkout-on` | misconfig | 1 | 难 | yes |
| 6 | `misconfig-pc-OLJCESPC7Z` | misconfig | 1 | 中 | yes |
| 7 | `memleak-email-10000x` | mem_leak | 2 | 难 | yes |
| 8 | `memleak-email-1000x` | mem_leak | 2 | 难 | yes |
| 9 | `crash-frontend-01` | crash | 0 | 易 | no |
| 10 | `crash-payment-01` | crash | 2 | 中 | no |
| 11 | `misconfig-payment-100` | misconfig | 2 | 中 | no |
| 12 | `blackhole-payment-01` | blackhole | 2 | 中 | no |
| 13 | `blackhole-valkey-cart-01` | blackhole | 2 | 中 | no |
| 14 | `latency-checkout-800` | latency | 1 | 易 | no |
| 15 | `latency-email-800` | latency | 2 | 中 | no |
| 16 | `misconfig-cart-75` | misconfig | 1 | 中 | no |

首批清单在 `scripts/scenarios/recipe.csv` 的 `batch` 列（值为 `1`），
卡片 yaml 里对应 `batch: 1`；本表与 csv 不一致时以 csv 为准。

### 六、统计

**总卡数 69**（v1.0 为 76，`productCatalogFailure` 减 7 张）。

| 类 | 卡数 |
| --- | ---: |
| `crash` | 13 |
| `blackhole` | 13 |
| `latency` | 26 |
| `misconfig` | **14** |
| `mem_leak` | 3 |
| **合计** | **69** |

**多变体开关清单**

| 开关 | 出卡变体数 | 变体 |
| --- | ---: | --- |
| `adFailure` | 1 | `on` |
| `cartFailure` | 3 | `75%` / `90%` / `100%`（`10%`/`25%`/`50%` 按 O-P2-9 排除） |
| `paymentFailure` | 6 | `10%` / `25%` / `50%` / `75%` / `90%` / `100%` |
| `paymentUnreachable` | 1 | `on` |
| `productCatalogFailure` | **3** | `2ZYFJ3GM2N`（份额最高 11.4%）／`66VCHSJNUP`（最低 9.1%）／`OLJCESPC7Z`（9.3%，已有实测、在首批）—— v1.1 由 10 减至 3，见头部修订理由 |
| `emailMemoryLeak` | 3 | `10000x` / `1000x` / `100x`（`1x`/`10x` 未出卡，量级过低） |

**难度直方图**

| 档位 | 卡数 | 目标 | 差 |
| --- | ---: | ---: | ---: |
| 易 | 25 | — | — |
| 中 | **39** | — | — |
| 难 | 5 | ≥8 | -3 |

**难卡 card_id 清单（5 张）**：`misconfig-payment-10`、`misconfig-checkout-on`、`memleak-email-10000x`、`memleak-email-1000x`、`memleak-email-100x`

**`param_validated=no`：68 张**；`yes`：8 张
（`crash-cart-01`、`blackhole-cart-01`、`latency-cart-800`、`misconfig-ad-on`、
`misconfig-checkout-on`、`misconfig-pc-OLJCESPC7Z`、`memleak-email-10000x`、`memleak-email-1000x`）

---

### 七、难度终判规则（决策 021）

**本表的分数是预估，不是终值。** 量产后每张卡必须按证据包重算：

- **轴 B** 改用证据包中**实测的失败请求占比**（注入窗内目标自有 server span 的报错数 ÷ 总数），
  不再用代码推算或变体名面值；
- **轴 C** 按证据包中**调用方是否实际出现报错 span** 重判 —— 例如 `latency` 高档若实测
  不产生调用方报错，轴 C 保持 0；若产生则记 1；
- **难度档以实测为准**，与本表不一致时以实测覆盖。

**生成器与 harness 必须支持从证据包重算分数**，重算结果写回卡片的 `difficulty_measured`（生成器不会覆盖该字段，见 `generate.py` 的 `CARRIED`）。

---

### 八、拦路问题（未绕过）

**1. `paymentFailure` 的入卡理由不成立 —— 它没有任何入库档实测。**
裁决 2 的理由写的是「三者均有入库档实测」，但实际核查：`adFailure` 有 1 个周期
（`judge_222727`）、`cartFailure` 有 4 个周期（`judge_222727` + `rerun_cart_225459`）、
**`paymentFailure` 有 0 个周期**。`flag_catalog.md` 里它只有一张「**预期**报错数」推算表，
没有任何实测行。

而同一条裁决又写明「没有实测记录的开关一律不入」—— 两句自相矛盾。
**决策 021 的处理**：入卡（6 张变体卡），全部标 `param_validated=false`，
首批带一张 `misconfig-payment-100` 做验证，**不过门则 6 张整组出库**。
折中而非解决 —— 这 6 张在那一轮验证跑完之前不能算数。

**2. 轴 B 的「失败比例」按服务级而非方法级口径解释。**
裁决 3 写「轴 B 按该变体的失败比例打分」，但对方法级开关有两种读法：变体名的面值
（`cartFailure=75%` → 75%）还是服务级有效比例（75% × EmptyCart 占比 5.7% ≈ 4.3%）。
**决策 021 取服务级口径**，因为那才是 agent 在证据包里实际看到的比例 —— 实测
`cartFailure=50%` 时 `cart` 整体报错率只有 1.6%。若改按面值，`cartFailure` 三张卡的
轴 B 由 2 降为 0，难度由「中」降为「易」。量产后按证据包实测重算（第七节），
届时面值与服务级之争自动消失。

**3. 总卡数 76，距 80 差 4 张。**
不为凑数增加参数完全相同的卡。可补的只剩一个方向：`cartFailure` 的 `10%`/`25%`/`50%`
三档（当前按 O-P2-9 排除，**O-P2-9 解决则可补 3 张**）。
`emailMemoryLeak=10x` 已由决策 021 明确放弃 —— 按 `1000x` 实测线性外推增量约 3.5 MiB，
低于 `mem_leak` 判据阈值约 10 MiB，补进来也检测不出。
**总数口径待知识库 D83 修订，用户裁决中**；在那之前 76 是工作数字
（决策 020 的 v1 只要求 ≥40 卡）。

**4. `latency` 高档 3000 ms 的轴 C 暂记 0，是假设不是实测。**
依据决策 011：本系统服务间 gRPC 无 deadline，`blackhole` 下请求可挂 250 s 仍不报错，
因此 3000 ms 很可能同样「慢而不错」。13 张 `latency-*-3000` 的档位全部依赖这一假设，
实测后可能整体上移一档。

**5. `set_flag.sh` 无法为 `productCatalogFailure` 指定 `product_id`（[O-P2-14](open_items.md)）。**
配方里这个开关出 10 张卡，每张锁一个不同的 `product_id`；但原语的 targeting 分支只改
命中分支的变体、不动规则条件，而条件在 `demo.flagd.json` 里写死为 `OLJCESPC7Z`，
`flag_context()` 的求值上下文也写死同一个 ID。**照跑的话 10 张里只有
`misconfig-pc-OLJCESPC7Z` 是真的，其余 9 张实际注入的是同一条规则、等于重复卡。**
量产 `misconfig-pc-*` 之前必须先改原语。

**6. 告警检测器规则 2 对低流量服务假阳（[O-P2-15](open_items.md)）。**
「连续 ≥ 2 个采样点为 0」在 15 s 步长下，对被调约 3 /min 的
`payment` / `checkout` / `email` 是常态而非故障 —— 干净窗口实测三个全部误报，
且它们的注入期请求率与基线几乎没变。规则按裁决**原样实现，未私自加保护条件**，
门槛口径需用户裁决。
