# flagd 故障开关档案

`set_flag` 原语（决策 018）可用的开关。代码位置与调用量均为 2026-08-24 实测，
调用量取自 Jaeger 近 10 分钟的 server span。

判据见 [fault_schema.md](fault_schema.md) §5。**「预期报错数」一列是按代码里的
实际生效比例推算的，不是实测** —— 注明「预期」。

---

## misconfig 五项

### `adFailure` → `ad`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/ad/src/main/java/oteldemo/AdService.java:238` |
| 受影响方法 | `GetAds`（该服务唯一被调方法） |
| 失败方式 | 抛 `StatusRuntimeException(Status.UNAVAILABLE)` |
| 症状落点 | **目标 server span**（`ad` 自身 `GetAds` 报错） |
| 调用量 | 116 / 10min = **11.6 /min** |
| variant | `off` / `on` |
| **实际生效比例** | **0.1** —— 代码是 `flag && random.nextInt(10) == 0`，**开关为 `on` 也只有 1/10 的请求失败** |

| variant | 120s 窗预期报错数 | 阈值 `max(2, ⌈0.5×ratio×calls⌉)` | 预期是否达标 |
| --- | ---: | ---: | --- |
| `on` | 11.6/min × 2min × 0.1 ≈ **2.3** | `max(2, ⌈0.5×0.1×23.2⌉)` = **2** | **预期勉强达标**（2.3 ≥ 2，裕度极小） |

### `cartFailure` → `cart`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/cart/src/services/CartService.cs:82`（在 `EmptyCart` 方法体内，`:74-90`） |
| 受影响方法 | **仅 `EmptyCart`** —— `GetCart`、`AddItem` 完全不受影响 |
| 失败方式 | 走 `_badCartStore`，抛 `FailedPrecondition: Can't access cart storage` |
| 症状落点 | **目标 server span**；调用方 span **不报错**（实测 109 条调用方 span、0 报错） |
| 调用量 | `EmptyCart` 39 / 10min = **3.9 /min**（同期 `GetCart` 46.8 /min、`AddItem` 16.3 /min） |
| variant | `off` / `10%` / `25%` / `50%` / `75%` / `90%` / `100%` |
| **实际生效比例** | = variant 名的百分比，**但只对 `EmptyCart` 生效** |

| variant | 120s 窗预期报错数 | 阈值 | 预期是否达标 |
| --- | ---: | ---: | --- |
| `10%` | 7.8 × 0.10 = **0.8** | `max(2, ⌈0.39⌉)` = 2 | 不达标 |
| `25%` | 7.8 × 0.25 = **2.0** | `max(2, ⌈0.98⌉)` = 2 | 边界 |
| `50%` | 7.8 × 0.50 = **3.9** | `max(2, ⌈1.95⌉)` = 2 | 达标 |
| `75%` | 7.8 × 0.75 = **5.9** | `max(2, ⌈2.93⌉)` = 3 | 达标 |
| `90%` | 7.8 × 0.90 = **7.0** | `max(2, ⌈3.51⌉)` = 4 | 达标 |
| `100%` | 7.8 × 1.00 = **7.8** | `max(2, ⌈3.9⌉)` = 4 | 达标 |

> **出题要求**：variant 名里的百分比是**该方法的失败率**，不是该服务的。
> 实测 `50%` 时 `cart` 整体报错率只有 **1.6%**（123 条里 2 条），因为 `EmptyCart`
> 只占全部调用的 5.7%。`ground_truth.note` 必须写清受影响的方法。
>
> **Use variants at 75% or above only** — errored `EmptyCart` spans hang up to
> 262 s, past the 150 s harvest settle, so harvest can undercount them.
> See [open_items.md](open_items.md) O-P2-9.

### `paymentFailure` → `payment`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/payment/charge.js:39-47` |
| 受影响方法 | `Charge`（该服务唯一被调方法） |
| 失败方式 | `throw new Error('Payment request failed. Invalid token. demo.user_context.loyalty_level=gold')` |
| 症状落点 | **目标 server span** |
| 调用量 | 39 / 10min = **3.9 /min**（低流量靶子） |
| variant | `off` / `10%` / `25%` / `50%` / `75%` / `90%`(=0.95) / `100%` |
| 实际生效比例 | = variant 值（`Math.random() < numberVariant`）。注意 `90%` 的值是 **0.95** |

| variant | 120s 窗预期报错数 | 阈值 | 预期是否达标 |
| --- | ---: | ---: | --- |
| `50%` | 7.8 × 0.50 = **3.9** | 2 | 达标 |
| `100%` | 7.8 × 1.00 = **7.8** | 4 | 达标 |
| `10%` | 7.8 × 0.10 = **0.8** | 2 | 不达标 |

### `productCatalogFailure` → `product-catalog`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/product-catalog/main.go:373`（`GetProduct` 内），谓词 `checkProductFailure` 在 `:419-421` |
| 受影响方法 | **仅 `GetProduct`**，且**只对特定 `product_id`** —— flag 求值带 `product_id` 上下文 |
| 失败方式 | 返回 gRPC 错误码 |
| 症状落点 | **目标 server span** |
| 调用量 | `GetProduct` 1559 / 10min = **155.9 /min**（全栈最高之一） |
| variant | `off` / `on` |
| 实际生效比例 | **恒为 0** —— 见下方实测 |

**2026-08-26 首次观察批：无法开启** —— targeting 规则两分支都是 `off`，而 flagd 中
targeting 优先于 `defaultVariant`。**2026-08-26 已由 `set_flag.sh` 支持**：对带
`targeting` 的开关，`apply` 改的是**命中分支的变体**（`"if"` 的第一个分支 `off` → `on`），
规则条件不动，`defaultVariant` 不改；`probe` 按开关附带评估上下文查 OFREP
（`productCatalogFailure` 用 `{"product_id":"OLJCESPC7Z"}`，取自
`product-catalog/main.go:420` 传入的 `product_id`）。

**2026-08-26 重跑（`obs2card_001542`，入库档，observe-only）：注入生效。**

| 项 | 基线（60s） | 注入期（120s，harvest） | 恢复窗（30s） |
| --- | --- | --- | --- |
| `GetProduct` | 158 条 / **0 报错** | **355 条 / 27 报错** | 75 条 / **0 报错** |
| `product-catalog` 自有 server span | 196 / 0 | 433 / **27** | 94 / 0 |
| `frontend → product-catalog` | 162 / 0 | 355 / **27**（调用方同步报错） | 80 / 0 |
| `in_flight_at_revert` | — | 0 | — |

**实际生效比例 r = 27 / 355 = 7.6%** —— 即请求 `OLJCESPC7Z` 这一个商品的调用占
`GetProduct` 全部调用的比例。

**判定：入卡（2026-08-27 ET 裁定）。**

| 项 | 值 |
| --- | --- |
| ground truth | **(product-catalog, misconfig)**，`flag=productCatalogFailure` |
| 类型 | **targeting 型**（apply 改命中分支变体，见 [fault_schema.md](fault_schema.md) §5） |
| 生效比例 r | **27 / 355 = 7.6%**（8/26 单窗实测，**待多窗校准**） |
| 阈值 | 按 r 计：`max(2, ⌈0.5×0.076×355⌉)` = **14**，8/26 实测 **27 ≥ 14 通过** |
| 难度 | **高**（按「部分失败比例」轴：只有 7.6% 的 `GetProduct` 失败，其余全绿） |

阈值不能用 `ratio=1.0` 算 —— 那会得出 `N=178`，把一次完全正常的注入判成失败。
targeting 型开关的 `ratio` 必须取**实测生效比例**。

### `paymentUnreachable` → `checkout`（注意靶子是 checkout 不是 payment）

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/checkout/main.go:567-571` |
| 受影响方法 | `PlaceOrder` → 内部 `chargeCard` |
| 失败方式 | **跳过真实调用**，把 payment 客户端换成指向 `badAddress:50051` 的连接 |
| 症状落点 | **目标 client span**（`checkout → badAddress`），**不是** `checkout` 的 server span 直接报错，也**不是** `payment` 报错 —— `payment` 全程正常，一条请求都收不到 |
| 调用量 | `PlaceOrder` 39 / 10min = **3.9 /min** |
| variant | `off` / `on` |
| 实际生效比例 | 1.0（无随机） |

> **这一项的症状落点与其余四项不同**，`self_edges.server_by_method` 上可能看不到，
> 要看 `self_edges.client_by_peer`。

**2026-08-26 首次观察批（`obs2card_233337`）：注入未生效** —— 根因是开机竞态
（checkout 早于 flagd 监听器 5.9 秒启动），已由决策 019 修掉，留档见 O-P2-10。

**2026-08-26 重跑（`obs2card_001542`，入库档 60/120/60，observe-only）：注入生效。**

| 项 | 基线（60s） | 注入期（120s，harvest） | 恢复窗（30s） |
| --- | --- | --- | --- |
| `checkout` 自有 `PlaceOrder` | 2 条 / **0 报错** | **11 条 / 11 报错** | 3 条 / 0 报错 |
| `frontend → checkout` | 2 条 / 0 报错 | **11 条 / 11 报错** | 3 条 / 0 报错 |
| `checkout → payment` 边 | 2 条 / 0 报错 | **该边整个消失** | 3 条 / 0 报错 |
| `payment` 自有 server span | — | **0 条 / 0 报错**（一条请求都没收到） | — |
| `payment` 容器 | — | `running`、`RestartCount=0` | — |
| `in_flight_at_revert` | — | **0** | — |

错误原文：
`failed to charge card: could not charge the card: rpc error: code = Unavailable desc = dns: A record lookup error: lookup badAddress on 127.0.0.11:53: server misbehaving`

基线速率 2/60s = 0.0333/s → `N = max(5, ⌈0.25×0.0333×120⌉)` = **5**。

**判定：入卡（2026-08-27 ET 裁定）。**

| 项 | 值 |
| --- | --- |
| ground truth | **(checkout, misconfig)**，`flag=paymentUnreachable` |
| 难度 | **高** |
| 快照 | `harvest`（`in_flight_at_revert = 0`，两快照数字完全相同） |
| 判据 | §5 标准 misconfig：自有 server span 报错 ≥ `max(2, ⌈0.5×1.0×11⌉)` = **6**，实测 **11 ≥ 6 通过** |

**形态备注**：症状落在 `checkout` 自有 server span（8/26 实测 11/11 报错），
`checkout → payment` 边整条消失（gRPC 名字解析在建连前失败，不产生 client span），
`payment` 侧健康且零流量；「客户端侧变体」假设已证伪，按标准 misconfig 判据通过
（11 ≥ 阈值 6）。指纹见 [fingerprints.md](fingerprints.md)「下游边消失形态」。

### `emailMemoryLeak` → `email`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/email/email_server.rb:67`、`:71`（撑大邮件体）、`:83-84`（倍率 ≥1 时**跳过** `Mail::TestMailer.deliveries.clear`） |
| variant | `off` / `1x` / `10x` / `100x` / `1000x` / `10000x` |
| **实测增长** | `10000x`：**+171.5 MiB / 120s = 85.8 MiB/min**（58.6 → 230.1 MiB），限额 512M 未触碰，`RestartCount` 0，`OOMKilled=false` |

### `recommendationCacheFailure` → `recommendation`

| 项 | 内容 |
| --- | --- |
| 判断分支 | `src/recommendation/recommendation_server.py:78`；泄漏点 `:86-87`（`cached_ids` 每次 cache miss 追加自身 1/4，几何增长），`:39` 定义、全文件无删除 |
| 类别 | **`mem_leak`**（上游 flag 描述写的是 "Fail recommendation service cache"，但代码注释是 `# Feature flag scenario - Cache Leak`；判据以代码为准） |
| variant | `off` / `on` |
| **实测增长** | **+0.3 MiB / 120s = 0.1 MiB/min**（47.4 → 47.7 MiB） |
| **结论** | **120s 窗不可判定、不入库。** cache miss 只有 50% 概率触发（`:80` `random.random() < 0.5`），且 `recommendation` 被调仅 25.9 /min，窗内攒不出可观增长。见 [open_items.md](open_items.md) O-P2-8 |

---

## 其余六项（本轮未建档）

`adHighCpu`、`adManualGc`、`failedReadinessProbe`、`imageSlowLoad`、
`intlShippingSlowdown`、`kafkaQueueProblems` —— 症状形态与前五项不同
（CPU / GC / 就绪探针 / 前端渲染 / 延迟 / 队列），需各自实测后再归类，W2 处理。
`loadGeneratorTraffic` / `loadGeneratorVUs` 是压测器控制项，不是故障开关。
