# 80 卡场景库配方草案（v0，2026-08-27 ET）

> **草案，未定稿。** 本文只做盘点与配方推演，不新增决策条目、不建生成器、不改脚本。
> 三处硬约束目前无法同时满足，见第六节统计与文末「拦路问题」。

### 一、靶子盘点

调用深度＝从 `frontend` 出发沿调用图到该服务的最短跳数（`frontend`=0）。
拓扑来源：仓库内**无**拓扑导出文件，本表由 Jaeger `/api/dependencies`（2h 回看，2026-08-27 现采）
的跨服务边 + compose 依赖推出；`astronomy-db` / `valkey-cart` 不产生 server span，
其边由调用方 client span 的 peer 补入（决策 018）。

**排除的基础设施组件**

| 组件 | 排除理由 |
| --- | --- |
| `otel-collector` | 观测管线本身；杀掉后三信号全断，注入与症状都无法观测 |
| `prometheus` | 指标后端；agent 工具面之一，杀掉即毁掉证据来源 |
| `jaeger` | trace 后端；同上 |
| `grafana` | 观测 UI；不在请求路径上，杀掉无业务症状 |
| `opensearch` | 日志后端；同上，且是 agent 工具面 |
| `flagd` | flag 求值服务；misconfig / mem_leak 两类的注入通道本身，杀掉会让注入手段与故障混淆（决策 018） |
| `load-generator` | 流量发生器；杀掉等于停掉全部流量，所有信号一起消失，无法区分 |
| `frontend-proxy` | 入口 Envoy；杀掉切断全部外部流量，且 backends.env 之外的 agent 访问路径经它（决策 013 注） |
| `flagd-ui` | 辅助 UI，不在购物请求路径上（决策 001 剔除理由） |
| `telemetry-docs` | 文档站，同上 |
| `opamp-server` | collector 配置控制面，属观测侧 |

#### crash（`kill_container`）—— 14 个靶子

| 靶子 | 调用深度 | 调用方 |
| --- | ---: | --- |
| `ad` | 1 | `frontend` |
| `astronomy-db` | 2 | `product-catalog` |
| `cart` | 1 | `checkout`, `frontend` |
| `checkout` | 1 | `frontend` |
| `currency` | 1 | `checkout`, `frontend` |
| `email` | 2 | `checkout` |
| `frontend` | 0 | `frontend-proxy` |
| `image-provider` | n/a | `frontend-proxy` |
| `payment` | 2 | `checkout` |
| `product-catalog` | 1 | `checkout`, `frontend`, `recommendation` |
| `quote` | 2 | `shipping` |
| `recommendation` | 1 | `frontend` |
| `shipping` | 1 | `checkout`, `frontend` |
| `valkey-cart` | 2 | `cart` |

> `image-provider` 由 `frontend-proxy` 直连，**不在 `frontend` 出发的调用图上**，
> 深度记 `n/a`；轴 A 按「距入口一跳」类比记 1 分，**此处为判断而非实测，需复核**。

#### blackhole（`drop_inbound`）—— 14 个靶子

靶子集合与 crash 相同（均有上游调用方，且 `service_ports.env` 全 16 项已登记无 `None`）。
含两个无 SDK 数据库靶子 `valkey-cart`（调用方 `cart`）与 `astronomy-db`（调用方 `product-catalog`），
按决策 017/018 从**调用方边**判定。调用方同上表。

#### latency（`delay_outbound`）—— 14 个靶子

| 档位 | 值 | 状态 |
| --- | --- | --- |
| 低档 | **800 ms** | **已入库档实测**（`full3_cart_205013`：p50 右移 +800.31 ms、报错 0、下游未误伤） |
| 高档（建议） | **3000 ms** | **待验证** |

> 高档能否让轴 C 得 1 分（触发调用方超时报错）**存疑**：决策 011 实测本系统
> 服务间 gRPC **无 deadline**，`blackhole` 下请求可挂 250 s 而不报错。
> 因此 3000 ms 很可能仍是「慢而不错」，轴 C 仍为 0。**该档位与其轴 C 分值均待实测**。

#### misconfig —— 2 个已入卡开关

| 开关 | ground truth | 形态 | 实测生效比例 |
| --- | --- | --- | ---: |
| `paymentUnreachable` | **(checkout, misconfig)** | 下游边消失 | **1.0**（全量） |
| `productCatalogFailure` | **(product-catalog, misconfig)** | targeting 型 | **7.6%**（27/355，单窗，待多窗校准） |

**未计入**（flag_catalog 中有实测数据但**无入卡裁定**）：
`adFailure`（方法级 `GetAds`，实际比例 0.1）、`cartFailure`（方法级 `EmptyCart`，
受 O-P2-9 限制只能用 ≥75% 变体）、`paymentFailure`（方法级 `Charge`）。
`recommendationCacheFailure` 按 O-P2-8 排除（0.1 MiB/min，120 s 窗不可判定）。

#### mem_leak —— 1 个可用开关

| 开关 | ground truth | 调用深度 | 实测内存增量 |
| --- | --- | ---: | --- |
| `emailMemoryLeak=10000x` | **(email, mem_leak)** | 2 | **+171.5 MiB / 120 s**（58.6 → 230.1 MiB，85.8 MiB/min） |
| `emailMemoryLeak=1000x` | 同上 | 2 | **+35.5 MiB / 120 s**（58.5 → 94.0 MiB） |

`recommendationCacheFailure` 按 O-P2-8 排除。

### 二、唯一根因对

以 `(service, fault_class)` 为单位，不按开关名计 —— `paymentUnreachable` 计入 `(checkout, misconfig)`。

| 类 | 唯一根因对数 | 构成 |
| --- | ---: | --- |
| `crash` | **14** | 14 个应用服务各一 |
| `blackhole` | **14** | 同上 |
| `latency` | **14** | 同上 |
| `misconfig` | **2** | `(checkout, misconfig)`、`(product-catalog, misconfig)` |
| `mem_leak` | **1** | `(email, mem_leak)` |
| **合计** | **45** | |

### 三、难度评分规则

- **轴 A 上浮跳数**：按调用深度，0 → 0 分，1 → 1 分，≥2 → 2 分。
- **轴 B 部分失败比例**：`crash` / `blackhole` / `latency` 为 0 分；
  `misconfig` 方法级（如 `cartFailure` 仅 `EmptyCart`）1 分；
  `misconfig` 生效比例 ≤10%（`adFailure`、`productCatalogFailure`）2 分；
  `misconfig` 全量（`paymentUnreachable`）0 分；`mem_leak` 2 分。
- **轴 C 症状误导**：`crash` 0 分；标准 `misconfig` 0 分；`latency` 低档 0 分、
  高档（触发调用方超时报错）1 分；`blackhole` 1 分；`mem_leak` 1 分；
  下游边消失形态（`paymentUnreachable`）2 分。
- 总分 **0–1 易**、**2–3 中**、**≥4 难**；**附加规则：轴 C 得 2 分的根因对直接升一档**。

#### 各唯一根因对评分

| 根因对 | 深度 | A | B | C | 总分 | 档位 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `(ad, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(astronomy-db, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(cart, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(checkout, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(currency, crash)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(email, crash)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(frontend, crash)` | 0 | 0 | 0 | 0 | 0 | **易** |
| `(image-provider, crash)` | n/a | 1 | 0 | 0 | 1 | **易** |
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
| `(image-provider, blackhole)` | n/a | 1 | 0 | 1 | 2 | **中** |
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
| `(image-provider, latency)` | n/a | 1 | 0 | 0 | 1 | **易** |
| `(payment, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(product-catalog, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(quote, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(recommendation, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(shipping, latency)` | 1 | 1 | 0 | 0 | 1 | **易** |
| `(valkey-cart, latency)` | 2 | 2 | 0 | 0 | 2 | **中** |
| `(checkout, misconfig)` | 1 | 1 | 0 | 2 | 3 | **难** |
| `(product-catalog, misconfig)` | 1 | 1 | 2 | 0 | 3 | **中** |
| `(email, mem_leak)` | 2 | 2 | 2 | 1 | 5 | **难** |

### 四、配方草案

共 **65** 张卡。表列见下。

| card_id | class | target | params | root_cause_pair | depth | A | B | C | total | difficulty | param_validated | note |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| `crash-ad-01` | crash | `ad` | kill_container | `(ad, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-astronomy-db-01` | crash | `astronomy-db` | kill_container | `(astronomy-db, crash)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-cart-01` | crash | `cart` | kill_container | `(cart, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | yes | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-checkout-01` | crash | `checkout` | kill_container | `(checkout, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-currency-01` | crash | `currency` | kill_container | `(currency, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-email-01` | crash | `email` | kill_container | `(email, crash)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-frontend-01` | crash | `frontend` | kill_container | `(frontend, crash)` | 0 | 0 | 0 | 0 | 0 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-image-provider-01` | crash | `image-provider` | kill_container | `(image-provider, crash)` | n/a | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-payment-01` | crash | `payment` | kill_container | `(payment, crash)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-product-catalog-01` | crash | `product-catalog` | kill_container | `(product-catalog, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-quote-01` | crash | `quote` | kill_container | `(quote, crash)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-recommendation-01` | crash | `recommendation` | kill_container | `(recommendation, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-shipping-01` | crash | `shipping` | kill_container | `(shipping, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-valkey-cart-01` | crash | `valkey-cart` | kill_container | `(valkey-cart, crash)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 入库档仅在 cart 实测；其余靶子参数形式相同、值无参数 |
| `crash-checkout-02` | crash | `checkout` | kill_container | `(checkout, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 变体：注入时刻移至 checkout 高流量段（PlaceOrder 峰值） |
| `crash-product-catalog-02` | crash | `product-catalog` | kill_container | `(product-catalog, crash)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 变体：注入时刻移至 GetProduct 峰值段 |
| `blackhole-ad-01` | blackhole | `ad` | drop_inbound | `(ad, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-astronomy-db-01` | blackhole | `astronomy-db` | drop_inbound | `(astronomy-db, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-cart-01` | blackhole | `cart` | drop_inbound | `(cart, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | yes | 入库档仅在 cart 实测 |
| `blackhole-checkout-01` | blackhole | `checkout` | drop_inbound | `(checkout, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-currency-01` | blackhole | `currency` | drop_inbound | `(currency, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-email-01` | blackhole | `email` | drop_inbound | `(email, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-frontend-01` | blackhole | `frontend` | drop_inbound | `(frontend, blackhole)` | 0 | 0 | 0 | 1 | 1 | 易 | no | 入库档仅在 cart 实测 |
| `blackhole-image-provider-01` | blackhole | `image-provider` | drop_inbound | `(image-provider, blackhole)` | n/a | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-payment-01` | blackhole | `payment` | drop_inbound | `(payment, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-product-catalog-01` | blackhole | `product-catalog` | drop_inbound | `(product-catalog, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-quote-01` | blackhole | `quote` | drop_inbound | `(quote, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-recommendation-01` | blackhole | `recommendation` | drop_inbound | `(recommendation, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-shipping-01` | blackhole | `shipping` | drop_inbound | `(shipping, blackhole)` | 1 | 1 | 0 | 1 | 2 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-valkey-cart-01` | blackhole | `valkey-cart` | drop_inbound | `(valkey-cart, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 入库档仅在 cart 实测 |
| `blackhole-valkey-cart-02` | blackhole | `valkey-cart` | drop_inbound | `(valkey-cart, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 变体：注入时刻移至 cart 高流量段（无 server span 靶子，从调用方边判定） |
| `blackhole-astronomy-db-02` | blackhole | `astronomy-db` | drop_inbound | `(astronomy-db, blackhole)` | 2 | 2 | 0 | 1 | 3 | 中 | no | 变体：注入时刻移至 GetProduct 峰值段（同上） |
| `latency-ad-01` | latency | `ad` | delay_outbound 800ms | `(ad, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-astronomy-db-01` | latency | `astronomy-db` | delay_outbound 800ms | `(astronomy-db, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-cart-01` | latency | `cart` | delay_outbound 800ms | `(cart, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | yes | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-checkout-01` | latency | `checkout` | delay_outbound 800ms | `(checkout, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-currency-01` | latency | `currency` | delay_outbound 800ms | `(currency, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-email-01` | latency | `email` | delay_outbound 800ms | `(email, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-frontend-01` | latency | `frontend` | delay_outbound 800ms | `(frontend, latency)` | 0 | 0 | 0 | 0 | 0 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-image-provider-01` | latency | `image-provider` | delay_outbound 800ms | `(image-provider, latency)` | n/a | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-payment-01` | latency | `payment` | delay_outbound 800ms | `(payment, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-product-catalog-01` | latency | `product-catalog` | delay_outbound 800ms | `(product-catalog, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-quote-01` | latency | `quote` | delay_outbound 800ms | `(quote, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-recommendation-01` | latency | `recommendation` | delay_outbound 800ms | `(recommendation, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-shipping-01` | latency | `shipping` | delay_outbound 800ms | `(shipping, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-valkey-cart-01` | latency | `valkey-cart` | delay_outbound 800ms | `(valkey-cart, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 低档 800 ms；入库档仅在 cart 实测 |
| `latency-ad-02` | latency | `ad` | delay_outbound 3000ms | `(ad, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-astronomy-db-02` | latency | `astronomy-db` | delay_outbound 3000ms | `(astronomy-db, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-cart-02` | latency | `cart` | delay_outbound 3000ms | `(cart, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-checkout-02` | latency | `checkout` | delay_outbound 3000ms | `(checkout, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-currency-02` | latency | `currency` | delay_outbound 3000ms | `(currency, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-email-02` | latency | `email` | delay_outbound 3000ms | `(email, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-frontend-02` | latency | `frontend` | delay_outbound 3000ms | `(frontend, latency)` | 0 | 0 | 0 | 0 | 0 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-image-provider-02` | latency | `image-provider` | delay_outbound 3000ms | `(image-provider, latency)` | n/a | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-payment-02` | latency | `payment` | delay_outbound 3000ms | `(payment, latency)` | 2 | 2 | 0 | 0 | 2 | 中 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `latency-product-catalog-02` | latency | `product-catalog` | delay_outbound 3000ms | `(product-catalog, latency)` | 1 | 1 | 0 | 0 | 1 | 易 | no | 高档 3000 ms **待验证**；轴 C 暂记 0——决策 011 实测无 deadline，很可能仍不报错 |
| `misconfig-checkout-01` | misconfig | `checkout` | set_flag paymentUnreachable=on | `(checkout, misconfig)` | 1 | 1 | 0 | 2 | 3 | 难 | yes | 下游边消失形态；8/26 入库档实测 PlaceOrder 11/11 报错 |
| `misconfig-checkout-02` | misconfig | `checkout` | set_flag paymentUnreachable=on @低流量段 | `(checkout, misconfig)` | 1 | 1 | 0 | 2 | 3 | 难 | no | 变体：注入时刻移至 PlaceOrder 低流量段（约 3.9/min），检验样本量下限 |
| `misconfig-product-catalog-01` | misconfig | `product-catalog` | set_flag productCatalogFailure=on (product_id=OLJCESPC7Z) | `(product-catalog, misconfig)` | 1 | 1 | 2 | 0 | 3 | 中 | yes | targeting 型；8/26 实测 r=7.6%（27/355） |
| `misconfig-product-catalog-66VC` | misconfig | `product-catalog` | set_flag productCatalogFailure=on (product_id=66VCHSJNUP) | `(product-catalog, misconfig)` | 1 | 1 | 2 | 0 | 3 | 中 | no | 变体：**不同命中分支**（targeting 条件改判 66VCHSJNUP）；r 待实测 |
| `misconfig-product-catalog-1YMW` | misconfig | `product-catalog` | set_flag productCatalogFailure=on (product_id=1YMWWN1N4O) | `(product-catalog, misconfig)` | 1 | 1 | 2 | 0 | 3 | 中 | no | 变体：**不同命中分支**（targeting 条件改判 1YMWWN1N4O）；r 待实测 |
| `misconfig-product-catalog-L9EC` | misconfig | `product-catalog` | set_flag productCatalogFailure=on (product_id=L9ECAV7KIM) | `(product-catalog, misconfig)` | 1 | 1 | 2 | 0 | 3 | 中 | no | 变体：**不同命中分支**（targeting 条件改判 L9ECAV7KIM）；r 待实测 |
| `memleak-email-01` | mem_leak | `email` | set_flag emailMemoryLeak=10000x | `(email, mem_leak)` | 2 | 2 | 2 | 1 | 5 | 难 | yes | 8/26 入库档实测 +171.5 MiB/120s |
| `memleak-email-02` | mem_leak | `email` | set_flag emailMemoryLeak=1000x | `(email, mem_leak)` | 2 | 2 | 2 | 1 | 5 | 难 | yes | 8/26 入库档实测 +35.5 MiB/120s |
| `memleak-email-03` | mem_leak | `email` | set_flag emailMemoryLeak=100x | `(email, mem_leak)` | 2 | 2 | 2 | 1 | 5 | 难 | no | 变体：更低倍率，检验判据下限（阈值 max(10, 0.15×first)）；**待验证** |

### 五、首批 16 卡

**约束冲突：`param_validated=yes` 的卡只有 7 张**，凑不满 16 张。清单：

- `crash-cart-01`（crash，易）
- `blackhole-cart-01`（blackhole，中）
- `latency-cart-01`（latency，易）
- `misconfig-checkout-01`（misconfig，难）
- `misconfig-product-catalog-01`（misconfig，中）
- `memleak-email-01`（mem_leak，难）
- `memleak-email-02`（mem_leak，难）

原因：入库档只在 `cart`（crash / blackhole / latency 各一）、`checkout`、
`product-catalog`、`email` 上跑过。要凑齐 16 张 `param_validated=yes`，
**必须先补跑约 10 个入库档周期**（每个 390 s，约 65 分钟机器时）。

建议的首批 16 卡（前 6 张已验证，后 10 张需补跑后才算 yes）：

- `crash-cart-01`
- `blackhole-cart-01`
- `latency-cart-01`
- `misconfig-checkout-01`
- `misconfig-product-catalog-01`
- `memleak-email-01`
- `memleak-email-02`
- `crash-checkout-01`
- `crash-payment-01`
- `crash-frontend-01`
- `blackhole-payment-01`
- `blackhole-quote-01`
- `latency-checkout-01`
- `latency-payment-01`
- `latency-recommendation-01`
- `misconfig-product-catalog-66VC`
- `memleak-email-03`

### 六、统计

**每类卡数**

| 类 | 卡数 | 目标 | 差 |
| --- | ---: | ---: | ---: |
| `crash` | 16 | 16 | +0 |
| `blackhole` | 16 | 16 | +0 |
| `latency` | 24 | 24 | +0 |
| `misconfig` | 6 | 20 | -14 |
| `mem_leak` | 3 | 3 | +0 |
| **合计** | **65** | 78–80 | **-13**（对下限） |

**唯一根因对总数：45**（crash 14 / blackhole 14 / latency 14 / misconfig 2 / mem_leak 1）

**难度直方图**

| 档位 | 卡数 | 目标 | 差 |
| --- | ---: | ---: | ---: |
| 易 | 28 | ≥15 | +13 |
| 中 | 32 | ≥15 | +17 |
| 难 | 5 | ≥15 | -10 |

**`param_validated=no` 的卡：58 张**，参数清单：

- `kill_container` × 15
- `drop_inbound` × 15
- `delay_outbound 800ms` × 13
- `delay_outbound 3000ms` × 10
- `set_flag paymentUnreachable=on @低流量段` × 1
- `set_flag productCatalogFailure=on (product_id=66VCHSJNUP)` × 1
- `set_flag productCatalogFailure=on (product_id=1YMWWN1N4O)` × 1
- `set_flag productCatalogFailure=on (product_id=L9ECAV7KIM)` × 1
- `set_flag emailMemoryLeak=100x` × 1

**首批 16 卡分布**：已验证仅 7 张（crash 1 / blackhole 1 / latency 1 / misconfig 2 / mem_leak 2），
难度 {'易': 2, '中': 2, '难': 3}。**三个难度档已齐，但张数与 `param_validated=yes` 约束不可同时满足。**

---

### 七、拦路问题（未绕过，需裁决）

**1. `misconfig` 只有 2 个已入卡开关，比目标 20 卡少 14 张。**
`flag_catalog.md` 中标为「入卡」的只有 `paymentUnreachable` 与 `productCatalogFailure`。
`adFailure`（方法级 `GetAds`，实际比例 0.1）、`cartFailure`（方法级 `EmptyCart`，
受 O-P2-9 限制只能用 ≥75% 变体）、`paymentFailure`（方法级 `Charge`）三个**有入库档
实测数据但没有入卡裁定**，因此未计入盘点。

若把这三个补裁为入卡，`misconfig` 的唯一根因对由 2 增至 **5**
（`+(ad, misconfig)`、`+(cart, misconfig)`、`+(payment, misconfig)`），
misconfig 可扩到约 14–16 张，总数达到 **73–75**，仍略低于 78。**需用户裁决。**

**2. 难度「难」档只有 5 张，比目标 15 张少 10 张。**
原因是结构性的：轴 B 只有 `misconfig`（≤10% 比例）与 `mem_leak` 拿得到 2 分，
轴 C 只有 `paymentUnreachable`（下游边消失）拿得到 2 分。而这三者对应的根因对
一共只有 3 个。`crash` / `blackhole` / `latency` 三类共 42 个根因对，轴 B 恒为 0、
轴 C 最高 1，**总分上限是 3（中档），结构上产不出难卡**。

要补难卡，只能从三个方向选：
(a) 补裁 `adFailure`（轴 B=2）等低比例 misconfig；
(b) 给 `latency` 高档找到真能触发调用方报错的值（轴 C=1）—— 但决策 011 实测无 deadline，
    前景不乐观；
(c) 引入新的轴或新故障类（例如级联注入、多跳组合）。**需用户裁决。**

**3. `param_validated=yes` 的卡只有 7 张，凑不满首批 16。**
入库档实测只覆盖过 `cart`（三类各一）、`checkout`、`product-catalog`、`email`。
要让首批 16 张全部 `param_validated=yes`，须先补跑约 10 个入库档周期
（每个 390 s，合计约 65 分钟机器时）。第五节已给出建议清单并标明哪 10 张待补跑。

**4. `latency` 高档 3000 ms 的轴 C 分值是假设值。**
表中暂记 0 分。若实测证明它能触发调用方超时报错，10 张 `latency-*-02` 的轴 C 应改为 1，
其中 depth≥1 的会从「中」升到「中」（总分 2→3）或维持，**不改变难档缺口**。

**5. `image-provider` 的调用深度记为 `n/a`。**
它由 `frontend-proxy` 直连，不在 `frontend` 出发的调用图上。轴 A 按「距入口一跳」
类比记 1 分 —— **这是判断不是实测**，需复核；若改记 0 分，3 张相关卡的档位会下移一档。
