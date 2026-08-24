# 注入周期与批次运行规范（v1，2026-08-24）

## 1 适用范围

本文规定一次故障注入周期的时间结构、两档周期的用途边界、多周期批次的运行纪律与产物落盘。适用于 ④ 起的全部注入原语与 W2 场景库量产。

阈值与探针定义以 [fault_schema.md](fault_schema.md) §5 / §6 为准，本文只引用不另定；**若本文与 §5 / §6 不一致，以 §5 / §6 为准**。

---

## 2 周期结构

三段：**稳定期（pre）→ 注入期（inject）→ 恢复期（post）**。

四个时间锚点：

| 锚点 | 含义 | 来源 |
| --- | --- | --- |
| `t0` | 周期开始（稳定期起点） | runner 记录 |
| `t_inject` | 原语 `apply` 时刻 | 原语脚本 `apply` 的**标准输出**打印 `t_inject=<ISO8601>` |
| `t_revert` | 原语 `revert` 时刻 | 原语脚本 `revert` 的**标准输出**打印 `t_revert=<ISO8601>` |
| `t_end` | 周期结束（恢复期终点） | runner 记录 |

> 命名说明：本规范早期草稿用 `t_apply` 指代注入时刻。仓库内两个原语脚本
> （`kill_container.sh`、`drop_inbound.sh`）实际打印的字段名是 **`t_inject`**，
> 本文统一采用脚本的字段名，`t_apply` 不再使用。

### 三个探针

与 [fault_schema.md](fault_schema.md) §5 一一对应：

| 探针 | 何时判定 | 判据 |
| --- | --- | --- |
| `injected` | `t_inject` 之后 | 原语脚本 `probe <service>` 打印 `injected=true`。`crash` 判容器状态 ≠ `running`；`blackhole` 判目标 netns 内限定服务端口的 iptables 规则存在。 |
| `symptom` | 注入期内（`[t_inject, t_end_of_inject]`） | `crash`：调用方对 B 的错误 span 数 **> N**（`cart` 实测建议 N = 20）。`blackhole`：调用方对 B 的 `caller_spans_total` **低于基线 10%**。 |
| `recovered` | 判定窗自 **`t_revert + 30s`** 起，至 `t_end` | 同一 `symptom` 查询回落至基线，且 `injected` 探针反向通过（打印 `injected=false`）。 |

`symptom` 的阈值形式注意两点，均以 §5 原文为准：`crash` 是**严格大于** N（`> 20`，非 `≥ 20`）；N = 20 是 **`cart` 单靶子实测标定值**，不是全靶子通用常数，其余靶子需各自标定（见 [fingerprints.md](fingerprints.md) 末节）。

### 观测点：注入窗双快照（决策 016）

`three_signals` 的查询时刻与窗口时刻**分开**：

| 快照 | 查询时刻 | 用途 |
| --- | --- | --- |
| `immediate` | `t_revert` 即刻 | `blackhole` 的 symptom —— 静音只在这一刻成立 |
| `harvest` | `t_end + settle`（默认 150s） | `crash` / `latency` 的 symptom；基线窗与恢复窗也只在此查 |

span 只在**结束时**导出，注入窗内拨出的调用多在窗口结束后才结束。单一观测点
必然误判一类：`t_revert` 即刻查把 `crash` 判成哑（实测 0 条），`t_end+settle`
查把 `blackhole` 判成吵（实测 61 条）。

settle = 150s 的来源：Linux `tcp_syn_retries=6` → 127s 建连重试预算，加约 20s
导出落库余量；实测报错 span p95 131.9s、max 134.9s。

`in_flight_at_revert` = harvest 条数 − immediate 条数，即撤除时仍在飞行中的调用数。

### 基线的归属

稳定期内测得的数值即**该周期的基线**。`symptom` 与 `recovered` 都相对**本周期基线**判定，不跨周期借用。

---

## 3 周期双档

| 档位 | pre / inject / post（秒） | 窗口总时长 | **含 settle 的周期墙钟** | 用途 | 产物去向 |
| --- | --- | --- | --- | --- | --- |
| **调试档** | 30 / 60 / 30 | 2 分钟 | **270s**（120 + 150） | 只验脚本与查询通路：原语 `apply` / `revert` 是否生效、三信号查询是否返回、端口与后端是否可达 | 只留在 `scripts/out/`，标 `tier=debug`；**不进** `fingerprints.md`，**不进**准入门 |
| **入库档** | 60 / 120 / 60 | 4 分钟 | **390s**（240 + 150） | 指纹表与准入门的**唯一**数据源 | 进 `fingerprints.md` 与场景准入 |

墙钟含决策 016 的 settle（默认 150s）。80 卡单轮约 8.7 小时。

### 入库档取值依据

- **注入期 120s**：与 ④ 指纹同窗可比、为 `symptom` 阈值提供足够 span 样本；指标导出间隔已降为 **15s**（决策 013），窗内约 8 个采样点。
- **恢复期 60s**：覆盖 ④ 实测的撤除尾巴（12s 内 12 条报错 span，末条在 `t_revert + 12.42s`）与 `recovered` 判定窗起点 `t_revert + 30s`，留出 30 秒判定长度。

### 调试档无法评估 recovered

`recovered` 判定窗自 `t_revert + 30s` 起算，而调试档 post 只有 30s，即判定窗起点恰好落在 `t_end` 上，**窗长为 0**。因此调试档在结构上就产出不了 `recovered` 结论 —— 这是调试档不进准入门的技术原因，不只是数据量的问题。

### 硬规则

- 任何写入 `fingerprints.md` 的数字**必须来自入库档**。
- 调试档跑通后，同一原语**必须再跑一次入库档**才算有指纹。

### 每次运行必须记录

| 项 | 取值方式 |
| --- | --- |
| `tier` | `debug` / `admit` |
| 原语 | 脚本文件名，如 `kill_container.sh` / `drop_inbound.sh` |
| 靶子服务 | 传给原语与 `three_signals.py` 的 `<service>` |
| testbed 版本 | OTel Demo `3.0.0`，分支 `p2-baseline`，commit（本文成文时为 `155c80aa`） |
| 本仓库 commit | `git rev-parse --short HEAD` |
| 三个锚点时刻 | `t0`、`t_inject`、`t_revert`、`t_end`（前者由 runner 记，中间两个取原语输出） |

---

## 4 准入门

**单次运行的通过条件**：一次**入库档**运行中 `injected`、`symptom`、`recovered` 三探针全部通过。任一失败，该运行作废并记原因，**不进指纹表**。

**进入 W2 场景库的条件**由 [fault_schema.md](fault_schema.md) §6 规定，比单次运行严格，共四条：

1. 唯一性 —— 一次注入、一个 `(service, class)`；
2. 双层验证 + 恢复**连续 2 轮全过**（即上述单次条件需连续满足两次）；
3. canary 扫描零命中；
4. 自动计算 `symptom_locus ∈ {self, neighbor, remote}`。

即：单次三探针全过是**必要不充分**条件。第 2 条意味着入库档每卡至少跑 2 次（4 分钟 × 2）。第 3、4 两条目前**尚无实现**（`scripts/` 下无 canary 扫描与 locus 计算脚本），**待建**。

---

## 5 批次运行纪律（多周期无人值守连跑）

- **后台连跑**：多个周期合并为单个后台脚本串行连跑（`nohup` 或 `tmux`，不依赖前台终端）。用户在运行期间默认走开，只看批次结束后的汇总。
- **串行不并行**：同一 testbed 上同一时刻**只允许一个原语处于 apply 状态**。并行注入会互相污染指纹。
- **周期间衔接**：下一周期的 `t0` 不早于上一周期的 `t_end`。若上一周期 `recovered` 未通过，脚本**继续**跑下一周期，但在汇总中**标红**该周期。
  - **【建议，待入库档实测校准】** 连续 2 个周期 `recovered` 失败即中止批次，避免带病连跑。该阈值尚无实测依据，见 §8 待确认项。
- **失败即停**：`injected` 探针失败（注入未生效）**立即 revert 并中止批次**，不带着无效注入往下跑。
- **每周期结束必须 revert 并核对无残留**，核对通过才进入下一周期：
  - 容器状态：`docker inspect <service> --format '{{.State.Status}}'` 应为 `running`；
  - iptables：目标 netns 内 `iptables -S INPUT` 不应含注入规则，等价于原语 `probe` 打印 `injected=false`；
  - `scripts/state/` 下不应残留该服务的状态文件（`<service>.restart` 由 `kill_container.sh` 写、`<service>.dport` 由 `drop_inbound.sh` 写，均在 `revert` 时删除）；
  - tc qdisc：`latency` 原语尚未实现，其残留核对方式**待确认**。
- **批次之间清理日志索引**：见 [open_items.md](open_items.md) O-P2-1。清理动作**只允许落在批次之间**，不得落在周期之间 —— 周期内删索引会抹掉 `logs.log_lines`，而它是确认注入生效的信号之一。
- **与 Claude Code 并行**：批次运行期间**允许**并行编辑 `docs/` 与 `scenarios/`；**禁止**并行改动 testbed compose、`scripts/primitives/`、`scripts/probes/`、`scripts/state/` 与后端配置 —— 这些改动会使运行中的批次结果失效。

---

## 6 产物落盘

### 现状（脚本已实现的）

`scripts/probes/three_signals.py <service> <window_start_iso> <window_end_iso>` 每次调用**平铺**写一个文件：

```
scripts/out/<采集时刻>_<service>_<窗口起点>.json
```

- `<采集时刻>` 为 UTC `%Y%m%dT%H%M%SZ`；`<窗口起点>` 为窗口起点 ISO 串去掉 `-` 与 `:`。
- 文件内容为 `{"summary": <汇总>, "raw": <每次查询的原始请求与原始返回>}`。
- `summary` 顶层字段：`service`、`window`、`collected_at`、`traces`、`metrics`、`logs`、`raw_dump`。

`scripts/state/` 存原语的运行时状态，`apply` 写、`revert` 删：`<service>.restart`（`kill_container.sh` 保存的原 restart 策略）、`<service>.dport`（`drop_inbound.sh` 保存的被堵端口）。

`scripts/out/` 与 `scripts/state/` 均已在 `.gitignore` 中排除（各保留一个 `.gitkeep`）—— `out/` 单轮即数百 MB，是生成物不进版本库。

### 批次层级（已实现）

runner 建目录并把每次 `three_signals` 查询的 raw 转储写进周期目录：

```
scripts/out/<batch-id>/
├── summary.json / summary.md            批次汇总
└── <NN>_<primitive>_<service>/
    ├── window_baseline.json             三个窗口的 summary
    ├── window_during_immediate.json     注入窗快照①（t_revert 即刻）
    ├── window_during_harvest.json       注入窗快照②（t_end+settle）
    ├── window_after.json
    ├── anchors.json                     四锚点 + settle_s + t_harvest + commit
    ├── probes.json                      三探针判定 + 双快照 + in_flight_at_revert
    ├── evidence.json                    仅 kill_container（ARP 邻居缓存等）
    └── <ts>_<svc>_<win>[_immediate|_harvest].json    各次查询的 raw 转储
```

实现方式选了「runner 传目录」而非「runner 事后搬运」：`three_signals.py` 加了
`--out-dir` 与 `--name-suffix` 两个参数，前者定目录、后者区分同窗两次快照，
文件名格式本身未变。

### 配置来源

| 文件 | 内容 |
| --- | --- |
| `scripts/backends.env` | 三后端 base URL，端口已在 compose 覆盖里钉死，为常量：`JAEGER_BASE=http://localhost:16686/jaeger/ui`、`PROM_BASE=http://localhost:9090`、`OPENSEARCH_BASE=http://localhost:9200` |
| `scripts/service_ports.env` | 16 个靶子的服务端口登记表，由 compose 合并配置生成。其中 `FLAGD_PORT` 与 `FRONTEND_PROXY_PORT` 值为 `None`（两服务各发布两个端口，未人工判定），`drop_inbound.sh` 遇到会直接报错退出而不猜。 |

---

## 7 批次脚本（runner）

**已实现。**

| 项 | 内容 |
| --- | --- |
| 路径 | `scripts/runner/run_batch.py`（runner 本体）、`scripts/runner/run_batch.sh`（`nohup` 包装） |
| CLI | `run_batch.py --cycles <文件> [--batch-id ID] [--settle-s 150] [--abort-after-recovered-failures 2] [--out-root DIR]` |
| 清单格式 | 每行 `primitive service tier [param]`，`#` 开头为注释。`primitive ∈ kill_container\|drop_inbound\|delay_outbound`，`tier ∈ debug\|full`，`param` 仅 `delay_outbound` 用（默认 800）。现成清单：`cycles_debug_cart.txt`、`cycles_full_cart.txt` |
| 落盘 | `<out-root>/<batch-id>/<NN>_<primitive>_<service>/` 内：`window_baseline.json`、`window_during_immediate.json`、`window_during_harvest.json`、`window_after.json`、`anchors.json`（四锚点 + `settle_s` + `t_harvest` + testbed/本仓库 commit）、`probes.json`（三探针判定与依据数字 + 双快照 + `in_flight_at_revert`）、`evidence.json`（仅 `kill_container`）。批次级：`summary.json` + `summary.md` |
| 启动 | `./scripts/runner/run_batch.sh <cycles-file> [batch-id]` → 打印 `batch_id` / `pid` / `log`；看进度 `tail -f scripts/out/<batch-id>.log` |
| 串行保证 | `scripts/state/runner.lock` 存在即拒绝启动；`state/` 有任何原语 state 文件也拒绝启动 |
| 失败规则 | `injected` 失败 → 立即 revert、写汇总、中止批次；`recovered` 失败 → 标红继续，连续 2 次中止；原语命令非零退出 → revert 后中止 |

## 8 待确认项

| 项 | 现状 | 需要什么才能定 |
| --- | --- | --- |
| 连续 N 个周期 `recovered` 失败即中止批次 | 实现为 N = 2（`--abort-after-recovered-failures`） | 仍待多批次实测校准；已知一次假失败成因是窗长换算，已由决策 016 的速率比较修掉 |
| `latency` 原语的残留核对方式（tc qdisc） | 原语未实现 | ④ `latency` 原语落地后补写 |
| canary 扫描（§6 第 3 条） | 无实现 | 需实现禁词表扫三后端的脚本 |
| `symptom_locus` 自动计算（§6 第 4 条） | 无实现 | 需实现依赖图最短距离计算 |
| ~~批次层级目录的实现方式~~ | **已定** | 选了 `--out-dir` + `--name-suffix`，见 §6 |
| 跨批次指纹对照 | 未设计 | 指纹只保证周期内可比；跨批次需另做双基线（W2 议题，见决策 012） |
| `crash` 之外各靶子的 `symptom` 阈值 N | 仅 `cart` 标定 N = 20 | 各靶子分别跑入库档 |
| `FLAGD_PORT` / `FRONTEND_PROXY_PORT` | 登记表中为 `None` | 人工判定两服务的服务端口（各发布两个端口） |
