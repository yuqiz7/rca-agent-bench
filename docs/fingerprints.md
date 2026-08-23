# 故障指纹实测表

对照 [fault_schema.md](fault_schema.md) §3 / §5。每类一行，实测填格。

采集环境：opentelemetry-demo 3.0.0（分支 `p2-baseline`，含 [resource_audit.md](resource_audit.md) 的限额覆盖），
宿主机 16 核 / 62G。采集时间 2026-08-23 23:01–23:06 UTC。

---

## crash / cart

原语 `kill_container`，时序用 §2 默认值 `warmup_s=60` / `observe_s=120` / `cooldown_s=60`。

- `t_inject` = 2026-08-23T23:02:52Z
- `t_revert` = 2026-08-23T23:04:57Z

| 字段 | baseline<br>`[t_inj-65s, t_inj-5s]` | during<br>`[t_inj, t_inj+120s]` | after<br>`[t_rev, t_rev+60s]` |
| --- | ---: | ---: | ---: |
| `traces.caller_error_spans` | **0** | **62** | **3** |
| `traces.caller_spans_total` | 68 | 62 | 39 |
| `traces.caller_error_p50_ms` | — | **4 589.8** | 0.42 |
| `traces.caller_error_p95_ms` | — | **70 931.1** | 0.51 |
| `traces.error_messages_top3` | — | `14 UNAVAILABLE: No connection established. Last error: Error: connect EHOSTUNREACH 172.18.0.21:7070.` ×62 | 同左 ×3 |
| `metrics.req_rate_per_s` | **4.6** | **0.0167** | **null**（样本不足） |
| `metrics.samples_in_window` | 2 | 2 | **1** |
| `metrics.target_info_present` | true | **true** | true |
| `logs.log_lines` | 76 | **0** | 44 |

补充字段：

| 项 | 实测 |
| --- | --- |
| 错误出现延迟（`t_inject` → 首条报错 span） | **1.61 s** |
| 恢复延迟（`t_revert` → `caller_error_spans` 回到 0） | **≈ 6.5 s**（末条报错 span 在 `t_revert+6.48s`；10 秒切片采样中 `[+0,+10s]` 有 3 条，`[+10,+20s]` 起全 0） |
| 报错 span 全部归属 | `frontend`（62/62），方法 `GetCart` 40 / `AddItem` 22 |
| restart 策略原值 | `unless-stopped` |
| restart 策略处理方式 | `apply` 时存入 `scripts/state/cart.restart` → `docker update --restart=no` → `docker kill`；`revert` 时 `docker start` → 恢复 `unless-stopped` → 删除 state 文件。不存盘就 kill 会被 Docker 立刻拉起，注入不成立。 |
| 后端访问方式 | Jaeger `http://localhost:32774/jaeger/ui`、Prometheus `http://localhost:9090`、OpenSearch `http://localhost:32801`。均为宿主机已发布端口，未走 `frontend-proxy`（它在 `target_enum` 内，将来被注入会把探针一起打断）。 |

### 报错 span 时延分布（during，62 条）

| 桶 | 条数 | 占比 |
| --- | ---: | ---: |
| < 100 ms | 26 | 41.9% |
| 100 ms – 1 s | 0 | 0% |
| 1 – 10 s | 5 | 8.1% |
| 10 – 60 s | 22 | 35.5% |
| > 60 s | 9 | 14.5% |

`min = 0.4 ms`，`p50 = 11 693 ms`，`max = 71 449 ms`。**双峰**，中间几乎空白。

---

## 与 §3 crash 行预期的对照

§3 原文：`crash`（杀容器）= B 容器不存活；**调用方侧预期**：连接被拒、span 即时报错（毫秒级）；**B 自身预期**：指标 / 日志消失。

| 预期格 | 判定 | 实际现象 |
| --- | --- | --- |
| B 容器不存活 | **符合** | `probe` 返回 `injected=true`，`docker inspect` 状态 `exited`。 |
| 调用方侧：报错 | **符合** | 62 条报错 span，全部来自 `frontend`，baseline 为 0，信噪比干净。 |
| 调用方侧：**连接被拒** | **不符合** | 实际是 `EHOSTUNREACH`（主机不可达），不是 `ECONNREFUSED`（连接被拒）。`docker kill` 把容器的网络端点一并摘掉，IP 直接不可达，而不是有人在端口上回 RST。 |
| 调用方侧：**即时报错（毫秒级）** | **不符合** | 双峰：41.9% 确实 < 100 ms，但 43.6% 落在 10 s 以上，`p50 = 11.7 s`、`p95 = 70.9 s`。Node gRPC 客户端对已知失效的 subchannel 立刻失败，对需要新建连接的请求则挂到连接超时（约 71 s）。 |
| B 自身：日志消失 | **符合** | `log_lines` 76 → **0** → 44，干净利落。 |
| B 自身：**指标消失** | **不符合** | `target_info_present` 在整个 120 秒注入期**始终为 true**。Prometheus 的 series staleness 是 5 分钟、scrape_interval 是 1 分钟，120 秒的观察窗根本不足以让 series 过期。`req_rate_per_s` 也没有归零，而是 4.6 → 0.0167（窗口边界上还留着注入前的计数器样本）。 |

### 三条结论

1. **`crash` 与 `dep_timeout` 无法靠调用方时延区分。** §3 把 `crash` 定为"毫秒级"、`dep_timeout` 定为"耗时 ≈ 超时值"，但实测 `crash` 有一半以上的报错 span 慢到 10 s 以上，正落在 `dep_timeout` 的预期区间。两类的真正分界在**错误文字**（`EHOSTUNREACH` vs 超时）与 **B 自身信号**（`crash` 日志归零、`dep_timeout` 日志继续），不在时延。
2. **`crash` 的"指标消失"在 120 秒窗口内不成立**，只有日志消失。以指标消失作为 `crash` 的判据会在默认时序下必然失败。
3. **指标分辨率不足以支撑默认时序。** `scrape_interval=60s` 对 60 秒窗口只能拿到 1–2 个样本，`after` 窗口实测只拿到 1 个、连速率都算不出来。

---

## symptom 探针阈值 N 的建议值

§5 对 `crash` / `dep_timeout` 的 symptom 定义为「调用方对 B 的错误 span 数或错误日志数 > N」。

**建议 N = 10**（针对 `cart`）。

依据：

- baseline 实测 **0**，无本底噪声。
- during 实测 **62**，N = 10 约为 during 的 **16%**，留出 6 倍余量 —— 足以容纳流量低于 `cart`（4.6 req/s）的靶子，或注入时刻恰好落在流量低谷的轮次。
- 非注入期观测到的最大非零值是恢复尾巴的 **3 条**（`after` 窗口）。N = 10 高于它，`recovered` 判据（symptom 回落至基线）不会被恢复尾巴误判成"仍有症状"。

**这个 N 只对 `cart` 标定。** `cart` 是高流量靶子；`email`、`quote` 这类低流量服务在 120 秒里产生的调用方报错 span 会少一个量级，固定 N = 10 可能永远达不到。逐服务标定 N，或把 N 表达成 baseline 请求数的比例，需要在 §8 的实测轮次里对每个靶子各跑一遍再定 —— 本轮只跑了 `cart`，不外推。
