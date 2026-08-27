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

**2026-08-26 观察批实测：无法开启，不入卡。**

`demo.flagd.json` 里该 flag 的 targeting 规则**两个分支都返回 `off`**：

```json
"targeting": { "if": [ { "==": [ { "var": "product_id" }, "OLJCESPC7Z" ] }, "off", "off" ] }
```

flagd 中 targeting 的优先级高于 `defaultVariant`，因此把 `defaultVariant` 改成 `on`
**不会改变求值结果**。OFREP 实测（带与不带 `product_id` 上下文各一次）均返回
`{"value":false,"variant":"off","reason":"TARGETING_MATCH"}`。

`set_flag.sh apply` 因此正确失败并回滚：
`error: flagd did not pick up 'on' within 30s (got 'off'); rolling back`，
观察批在该周期中止。

**判定：不入卡。** 要启用必须改 `demo.flagd.json` 的 targeting 规则本身，
而那是测试床上游文件（决策 001 的复现锚点），本步只读不改。
见 [open_items.md](open_items.md) O-P2-11。

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

**2026-08-26 观察批实测（`obs2card_233337`，入库档 60/120/60，observe-only）：不入卡。**

| 项 | 实测 |
| --- | --- |
| `set_flag probe` | `injected=true` —— flagd 的 OFREP 确实返回 `on` |
| `checkout → payment`（`client_by_peer=172.18.0.18`） | 注入期 **8 条调用、0 报错**（基线 6 条 / 0 报错） |
| `checkout` 自有 server span（`PlaceOrder`） | 注入期 **8 条、0 报错**（基线 6 条 / 0 报错） |
| `frontend` 自有 server span | 注入期 996 条、**0 报错** |
| `payment` 自有 server span | 注入期 **8 条、0 报错** |
| `payment` 容器 | `Status=running`、`RestartCount=0` |
| `badAddress` 出现次数（checkout 全量日志） | **0** |
| `in_flight_at_revert` | 0 |
| 基线速率 / 阈值 | 6/60s = 0.1/s → `N = max(5, ⌈0.25×0.1×120⌉)` = **5** |

**判定：不入卡。** 判据第一条（client 侧报错数 ≥ N）不满足 —— 实测 **0 < 5**。
另两条反而都满足（`payment` 容器 running、`payment` 自有 span 报错 0），
但那是因为**注入压根没有生效**：checkout 全程正常向真实的 payment 收费，
`badAddress` 从未出现。

**根因已于 2026-08-26 查明**：开机时 `checkout` 比 flagd 的 8013 监听器早 5.9 秒启动，
其非阻塞的 `openfeature.SetProvider` 首次连接失败后该进程实例一直用默认值。
**重启 checkout 后注入立即生效**（`PlaceOrder` 10 条 / 5 报错），运行时改值两个方向
都正常。详见 [open_items.md](open_items.md) O-P2-10（含取证、B1/B2 实验与修法建议）。

---

## mem_leak 两项

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
