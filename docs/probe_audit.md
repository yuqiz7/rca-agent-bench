# None-vs-zero 探针审计（2026-08-28 ET，只读，未改码）

**为什么做这件事**：「分子取不到」被当成「分子是零」已经出过两次，两次都花了一整轮
才定位：

- **R3 / O-P2-19**（批次 2）：`frontend` 的 `caller_all_dur.p50` 恒为 `null`，
  因为 `PEER_KEYS` 不认 Envoy 的 `upstream_cluster` 命名。当时读成「流量太低」。
- **F-6**（批次 3）：`payment` 的同一个字段恒为 `null`，因为 `checkout` 的 gRPC
  client span 把 peer 写成容器 IP。当时又差点读成「该流量下分位数物理不可判」。

两次是**同一类 bug**，而 R3 的修法是「再加两个命名键」——一次修一个键，
没有回头问「还有多少地方在把取不到当成零」。本表就是那次没做的回头看。

**判定口径**（三档）：

| 语义 | 含义 |
| --- | --- |
| **正确** | 取不到时明确判「不适用」并带出原因，调用方能分辨 |
| **可接受** | 当成 0/失败，但有守卫（样本量、`b>0` 之类）使其**只可能造成假失败、不可能造成假通过**，且有书面理由 |
| **错误** | 当成 0/失败且无守卫，取不到与真实为零不可分辨，可能造成**假通过**或静默假失败 |

**风险等级**：按「错判的后果 × 无声程度」定。

---

## 一、探针采集侧（`scripts/probes/three_signals.py`）

| # | 路径 | 取不到时的现行为 | 语义判定 | 风险 |
| --- | --- | --- | --- | --- |
| 1 | `collect_traces` 整体失败（Jaeger `/api/services` 挂） | 返回 `{"error": ..., "caller_error_spans": None}`，**其余键缺失** | **正确**（采集侧如实记 `error`） | 低 |
| 2 | `collect_metrics` / `collect_memory` / `collect_logs` 失败 | 各自返回带 `"error": err` 的字典，数值键为 `None`、`samples: 0` | **正确** | 低 |
| 3 | `caller_edges` 匹配不上（PEER_KEYS 不认命名／peer 是 IP） | **边不存在 = 边为空**，与「真的没有调用」完全同形，`error` 为 `None` | **错误** —— 这正是 R3 与 F-6 的成因；F-6 已修 IP 反解，但**命名不全的可能性没有被检测机制覆盖** | **高** |
| 4 | `caller_all_dur.p50_ms` 无 span | `None`（`edge_stats` 空列表） | **正确**（采集侧给 `None` 是对的，问题在消费侧） | 低 |

**第 3 行是本表的核心。** 采集侧无法区分「这条边不存在」与「我不认识这条边的写法」，
因为两者的观测都是「零条 span」。**F-6 修的是 IP 一种写法，没有修这个不可分辨性本身。**

---

## 二、症状判据（`run_batch.judge_symptom`）

| # | 路径判据 | None 现行为 | 语义判定 | 风险 |
| --- | --- | --- | --- | --- |
| 5 | **crash 调用方臂** `got = dt_.get("caller_error_spans") or 0` | `None → 0`，`0 > N` 为假 → **判无症状** | **错误** —— 采集失败与「零报错」不可分辨，且**无守卫**。Jaeger 抖一下就是一张失败卡 | **高** |
| 6 | **blackhole 调用方臂** `b = ...or 0`；`caller_ok = (b > 0 and d < thr)` | `b=0` → 臂为假 | **可接受** —— `b > 0` 是守卫，取不到只会假失败不会假通过；但 detail 里报 `caller_arm_pass: False`，读起来像「算过了没过」而非「算不了」 | 中 |
| 7 | **靶子侧臂** `target_side_arm` | 明确 `return None` + `applicable: False` + `why` | **正确** —— 全仓最好的一处，docstring 明写「否则又是一个静默的零分母」 | 低 |
| 8 | **无 SDK 台阶臂** `dp is not None and dp >= 1000` | `dp=None` → 臂为假 | **可接受**（显式 `is not None` 守卫） | 低 |
| 9 | **无 SDK 边静默臂** `b_rate = (b_spans or 0)/base_s` | 边取不到 → `b_rate=0` → `enough=False` → 臂为假 | **可接受** —— `expected >= 5` 是守卫，取不到只会假失败 | 中 |
| 10 | **latency 臂** `bp`/`dp` 任一为 `None` → `shift=None` → 判失败 | **当失败**，detail 里 `shift_ms: null` | **错误** —— 与「位移不够」不可分辨。**这就是 R3 与 F-6 两次踩的那一格**；决策 027 已在 detail 里补 `caller_spans_total`，但**判定本身仍是「失败」而非「不适用」** | **高** |
| 11 | **misconfig 臂** `calls = dm.get("spans") or 0` → `n = max(2, ...)` | 方法名对不上或自身 span 取不到 → `calls=0`、`got=0` → 判无症状 | **错误** —— 无守卫，与 crash 臂同型。方法名来自 `FLAG_METHOD` 硬表，改一次服务接口就静默失效 | **高** |
| 12 | **mem_leak 臂** `first`/`last` 为 `None` → `thr=None` → `ok=False` | 当失败 | **可接受**（显式 `is not None` 守卫，且内存序列缺失属基础设施故障，会同时反映在 `error` 上） | 中 |

---

## 三、恢复判据（`run_batch.judge_recovered`）

| # | 路径判据 | None 现行为 | 语义判定 | 风险 |
| --- | --- | --- | --- | --- |
| 13 | `as_ = after["traces"].get("caller_spans_total") or 0` | `None → 0` → `arate=0` → `span_back` 假 → **判未恢复** | **错误** —— 采集失败＝未恢复，无守卫。一次 Jaeger 抖动就能让一张已恢复的卡判失败 | **高** |
| 14 | `span_back = brate > 0 and ...` | `brate=0`（基线边取不到）→ 假 | **可接受**（`brate > 0` 守卫），但随后退靶子侧臂 | 中 |
| 15 | **靶子侧退路** `tgt_brate` 取不到 | 走 `prom_base`，为 `None` 时该臂不参与 | **正确** | 低 |
| 16 | **misconfig 恢复** `got = ase.get("server_error_spans") or 0`；`return got == 0` | 取不到 → `got=0` → **判已恢复（通过）** | **错误，且是全表唯一会造成假通过的一处** —— 其余错误项都只造成假失败，这一处相反：自身 span 采集失败会被读成「恢复窗零报错」，直接放行 | **高（唯一假通过）** |
| 17 | **mem_leak 恢复** `br`/`ar` 任一 `None` → `ok=False` | 当失败 | **可接受**（显式守卫） | 低 |

---

## 四、检测器（`scripts/evidence/detect.py`）

| # | 路径判据 | None 现行为 | 语义判定 | 风险 |
| --- | --- | --- | --- | --- |
| 18 | `q["calls_total"]["series"]` 等十个查询 | **直接下标**，查询缺失会 `KeyError` 抛错 | **正确**（fail loud 好过 fail silent；且 CI 门 3 断言十个查询齐全） | 低 |
| 19 | 某服务无序列 `calls.get(svc, [])` | 空列表 → `counter_delta([])=0` → `b_rate=0` | **可接受** —— 见下一行的守卫 | 中 |
| 20 | **规则 1/2** `if b_rate > 0` | 基线速率为 0 的服务**整个跳过** | **正确** —— O-P2-15 就是为此加的守卫 | 低 |
| 21 | **规则 2 traffic_zero**：序列整段消失 | **有意当成零**（Prometheus 对死靶子保留 5 分钟陈旧样本，killed 容器通常表现为计数器平坦而非缺口） | **正确** —— 这是**唯一一处有书面理由的 None→0**，代码注释写明「both read the same here on purpose」 | 低 |
| 22 | **规则 3/7** `if bp is None or ip is None or bp <= 0: continue` | 跳过该服务 | **正确** | 低 |
| 23 | **规则 4 内存** `if not bpts or len(ipts) < 2: continue` | 跳过 | **正确** | 低 |
| 24 | **规则 6** `if not os.path.exists(traces_path): return []` | 无 traces 文件 → 无告警 | **可接受** —— 注释明写「a missing input is not an alert」，且 O-P2-16 之前的包本来就无标签 | 中 |
| 25 | 规则 5/7 的 `_by_operation` 序列缺失 | `(q.get(...) or {}).get("series") or {}` → 空表 → 无告警 | **可接受**（只会漏报不会误报） | 中 |

---

## 五、结论

**统计**：25 条路径中 **正确 11、可接受 9、错误 5**。

**五处语义错误（不改码，标记待裁）**：

| # | 位置 | 后果 |
| --- | --- | --- |
| 3 | `caller_edges` 命名不认＝边为空 | 假失败，且**无声**（R3/F-6 的根） |
| 5 | crash 调用方臂 `or 0` | 假失败 |
| 10 | latency 臂 `p50=None` 当失败 | 假失败（**已实际发生两次**） |
| 11 | misconfig 臂 `calls or 0` | 假失败 |
| 13 | recovered `caller_spans_total or 0` | 假失败 |
| 16 | **misconfig recovered `server_error_spans or 0`** | **假通过** |

**复验（只读，直接调判据函数，模拟「trace 采集整体失败」）**：

```
judge_recovered('misconfig', ..., after={'traces': {'error': 'jaeger down',
                                                    'caller_error_spans': None}})
  -> True        # 通过。detail 里写着 after_self_errors: 0
judge_symptom('crash',      ..., during 同上)
  -> False       # 失败
```

**同一场基础设施故障，misconfig 的恢复门放行、crash 的症状门拦下。**
两者都不该给出裁决，现在一个给了通过、一个给了失败。

**两条判断**：

1. **第 16 行是唯一会造成假通过的一处，应优先裁决。** 其余四处的错法都是「取不到 →
   判失败」，代价是重跑一张卡；第 16 行是「取不到 → 判通过」，代价是**一张证据不实的卡进库**，
   而入库之后没有任何环节会再回头查它。

2. **前五处共享同一个修法**：把「取不到」与「真的为零」在**采集侧**就分开 ——
   `collect_traces` 已经有 `error` 字段，缺的是**「这条边查过但一条没有」与
   「这条边根本没被查」的区分**。建议在 summary 里增加
   `caller_edges_queried`（实际匹配上的调用方集合）与 `caller_match_method`
   （名字命中／IP 反解命中），消费侧据此判「不适用」而不是「失败」。
   `target_side_arm` 的 `(None, {"applicable": False, "why": ...})` 三元组已经是
   现成的样板，五处照抄即可。

**本轮不改码的理由**：第四批正在跑，判据冻结（红线）。以上全部标记待裁，
待第四批收口后与 F-2 的 `MIN_EXPECTED` 一并处理。

**相关**：[F-6](open_items.md)、决策 023 R3、决策 027、[O-P2-19](open_items.md)。
