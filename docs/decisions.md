# 决策记录（Decision Records）

P2 RCA 项目的关键决策。每条统一四段：**选了什么 / 为什么 / 放弃了什么 / trade-off**。

环境事实基准（核对自 `~/projects/opentelemetry-demo`，2026-08-22）：
tag `3.0.0` = commit `1755859a`；基线 commit `a94fd5c5`（分支 `p2-baseline`，无 upstream，未推送）。

---

## 001 Testbed 选型与版本 pin

**选了什么**
OpenTelemetry Demo 作为 testbed，checkout tag `3.0.0`（按创建时间排序的最新正式版，commit `1755859a`）。
`.env` 中 `DEMO_VERSION` 由 `latest` 同步 pin 为 `3.0.0`，与 checkout 的 tag 对齐。

**为什么**
- 三信号（trace / metric / log）原生完整，无需自行埋点。
- 具备 flagd 与混沌注入双通道，故障可编程触发。
- 上游活跃维护：`origin/main` 在选型当天（2026-08-22 13:06:24 +0000，commit `054282e3`）仍有提交。
- 规模适配 RCA 叙事：`compose.yaml` 定义 20 个服务，叠加可观测后端后共 25 个。

**放弃了什么**
- Sock Shop：作为回退项保留，D1 触发器未触发，已关闭。
- main 分支的 `latest` 镜像。tag `3.0.0` 的 `.env` 出厂值为 `DEMO_VERSION=latest`，即使 checkout 了 tag，`docker compose up` 实际拉取的仍是 main 构建的 `:latest-*` 镜像（`IMAGE_VERSION=3.0.0` 只用于 `cache_from`，不决定运行镜像）。这会破坏复现锚点，故改 pin。

**trade-off**
pin 在 `3.0.0` 意味着放弃 main 的新特性，换取 golden dataset 全程可复现。

**附**
- 上游 tag 命名换过方案（早期 `v` 前缀 → 现无前缀），`git tag --sort=-v:refname` 会把 `v1.2.1` 排在首位，给出误导性结果；须用 `--sort=-creatordate` 才得到正确的最新版 `3.0.0`。
- `3.0.0` 已将可观测后端（Jaeger / Grafana / Prometheus / OpenSearch）从 `compose.yaml` 拆分至 `compose.observability.yaml`。日常起停必须三文件全列，顺序见 002。

---

## 002 资源限额本地适配（ad / grafana）

**选了什么**
新增 `compose.override.yaml` 覆盖两个容器的内存限额：
- `ad`：300M → 768M
- `grafana`：175M → 512M

**为什么**
上游 stock 限额按小型 CI 机标定，在 16 核主机上不成立。

- `ad`：JVM 原生开销（GC 线程、netty event loop、OTel javaagent metaspace）随核数放大，`-Xmx200m` 之外的部分被显著撑大。稳态占用 341.2MiB，超出 300M（314572800 B）上限，`OOMKilled=true` / exit 137 循环重启。因 `frontend` / `frontend-proxy` / `load-generator` 均依赖 `ad` service_healthy，三者连锁卡在 `Created` 状态无法启动。
- `grafana`：启动时在线安装插件（metricsdrilldown、pyroscope、exploretraces）产生内存峰值，OOM 重启 5 次后才稳定；且稳定后贴着 165.9MiB / 175MiB（95%）运行。放开限额后自然稳态为 181MiB — 本就超过旧限 175M（183500800 B），先前的"稳住"属擦顶通过，不可持续。

**放弃了什么**
- 直接修改上游 compose 文件：会污染已 pin 的基线，破坏与 tag 的可比性。
- 降低 JVM 线程数等侵入式调参：改变被观测系统自身行为，对 RCA 场景不可接受。

**trade-off**
override 文件干净、可整体回退，但引入了 `-f` 合并顺序陷阱：

- compose 按 `-f` 出现顺序合并，**后者覆盖前者**。若写成 `-f compose.yaml -f compose.override.yaml -f compose.observability.yaml`，observability 文件里的 175M 会静默盖回 override 的 512M — 不报错、不重建，`docker compose config` 实测得到 `memory: "183500800"`。override 必须排在最后，正确顺序下才得到 `memory: "536870912"`。正确命令已注释在 override 文件头部：

  ```
  docker compose -f compose.yaml -f compose.observability.yaml \
    -f compose.override.yaml up -d
  ```

- `up -d <service>` 不会重建已存在的容器（只回报 `Container grafana Running`），改限额后须 `--force-recreate --no-deps <service>` 定点重建，否则限额不生效。

**附**
`ad` 的 override 之前"看似生效"，仅因 `ad` 只存在于 `compose.yaml`、override 天然排在其后，属侥幸而非顺序正确。

---

## 003 基线 commit 策略

**选了什么**
将 detached HEAD 转为本地分支 `p2-baseline`，把环境适配（`.env` pin + `compose.override.yaml`）以单个 commit 承载，最终 amend 至 `a94fd5c5`。

**为什么**
上游 tag 不可动，本地适配必须可追溯、可整体回滚。amend 前已验证该分支无 upstream（`fatal: no upstream configured for branch 'p2-baseline'`）、未推送任何 remote，改写历史安全。

**放弃了什么**
- fork 上游仓库：重量级，本项目无必要。
- 把适配散落在未提交的工作区：不可追溯，环境重建后即丢失。

**trade-off**
本地分支不随上游演进。未来若升级 tag，需要 rebase 这两处改动（`.env` 一行 + override 文件），成本可接受。

---

## 004 验证双层 + 恢复 + 2 轮复现

**选了什么**
每张场景卡的准入验证由三项探针构成，且须连续 2 轮全过：注入生效探针（`injected`，宿主机机械检查）+ 针对被注入服务 B 的症状探针（`symptom`）+ 恢复探针（`recovered`）。

**为什么**
- 只验注入生效，会收进"注入确实做了、但三信号里看不出任何异常"的盲题。这类题进了库，top-1 分数就失去解释力 — 答错未必是 agent 不行。
- 只验症状，无法排除环境自带故障。本项目已经踩过：`ad` 在 300M 上限下的 OOM 循环（见 002）是环境固有问题，不是注入的结果，只看"有异常"会把它误判为注入成功。
- 恢复探针把三者串成"注入前正常 → 注入后异常 → 撤除后正常"的因果证明，同时保证题与题之间的隔离 — 上一卡的残留不会污染下一卡。

**放弃了什么**
- 单层验证（只验注入或只验症状）。
- 人工目检：80 卡不可持续，且判据不可复现。
- "系统内任意异常"式的症状查询：无法区分注入症状与环境噪声。

**trade-off**
每卡验证约 8 分钟，80 卡约 11 小时机器时。可脚本化无人值守，成本可接受。候选池按 100 收 80，为验证不过的卡留出裕度。

---

## 005 泄漏隔离四道墙

**选了什么**
四道墙叠加：① 注入器运行在被观测系统之外（宿主机，不进 OTel 管线）；② 场景元数据零暴露（无容器 label / env / 日志行）；③ agent 工具面仅五项且无 shell，所有查询受时间栅栏限制；④ 入库前用禁词表 canary 扫描三个后端，零命中才入库。

**为什么**
只要存在泄漏，高分就等于抄答案，整个评测作废 — 而且这种作废是事后无法补救的，必须在建库阶段挡住。

**放弃了什么**
- 靠自觉不扫：不可审计，无法向读者证明。
- 给 agent shell：`docker ps` 会让 `crash` 类直接变成一眼题，且泄漏面不可控、无法枚举。

**trade-off**
工具面窄于真实 SRE 的实际权限，这是有意的取舍。`config_diff` 对 `misconfig` 类近乎直通车，但配置比对本就是真实排障做法，不视为泄漏。

---

## 006 判分粒度 (service, class) 双匹配

**选了什么**
主指标 top-1 = `(service, class)` 双匹配；副指标 = 仅 service 匹配（定位准确率），分开报。agent 输出结构化 JSON，经别名归一化后精确匹配。零 LLM 判分。

**为什么**
- 仅判服务太粗：target_enum 16 项（见 [fault_schema.md](fault_schema.md) §2），乱猜约 6%，且不同 class 对应的处置动作完全不同 — 知道"是 cart 出问题"而不知道是崩了还是慢了，运维价值有限。
- 判到注入参数太细：参数（具体延迟毫秒数、具体被改的 env 值）本就不可观测，要求答出等于考记忆而非考诊断。

**放弃了什么**
- 自由文本输出 + LLM-as-a-judge：判分本身带噪声，且与"用确定性判据评测 agent"的立项逻辑自相矛盾。
- 部分分：会让"蒙对服务、蒙错类别"和"全对"的差距被稀释。

**trade-off**
五类定义必须逐卡唯一且在三信号中可区分，这个约束反过来压在出题上（见 [fault_schema.md](fault_schema.md) §3 指纹表）。结构化输出的格式约束会略微压低无工具基线的表现，但三基线与 agent 同规则，可比性不受影响。

---

## 007 注入作用面限定服务端口

**选了什么**
`latency` 只延迟从 B 服务端口发出的响应包；`dep_timeout` 只丢弃进入 B 服务端口的请求包。规则按端口过滤，不作用于整块网卡。

**为什么**
整网卡注入会同时拖慢或切断 B 自身的对外调用与遥测上报，标准答案会被注入手法本身污染：

- 整网卡加延迟：`cart` 调用 `valkey-cart` 的出向请求也被延迟，trace 上呈现为"cart → valkey 慢"。标准答案是 `cart`，证据却指向 `valkey-cart`。
- 整网卡丢包：`cart` 的遥测上报一并中断，指标与日志直接消失，`dep_timeout` 类退化成 `crash` 类的指纹。

两种情况下，agent 即使推理正确也会被判错 — 错的是题，不是答题者。

**放弃了什么**
- 整网卡 `tc netem`。
- 整网卡丢包规则。

**trade-off**
规则需按端口过滤，实现比整网卡复杂；各服务的服务端口需逐一登记并随 compose 变更维护。

---

## 008 资源覆盖从三项扩展为全栈审计规则

**选了什么**
把 `compose.override.yaml` 里逐个撞坑加出来的三条覆盖（`ad` / `grafana` / `prometheus`），升级为一条对全部 25 个容器执行的审计规则：

> 用量占比 > 50%，或曾 OOM（退出码 137），或限额 ≤ 64M 的服务，一律抬到 ≥ 当前用量 4 倍，并向上取整到 `{256M, 512M, 768M, 1G, 2G, 4G}` 中最近一档。已有覆盖的 `ad` / `grafana` / `prometheus` 不再动；`checkout` 与 `astronomy-db` 强制纳入。

档位表中 **4G 为后补档，全栈仅 `opensearch` 命中**：首轮按 `{…,2G}` 封顶后用量仍涨到 1283M / 62.6% 并随日志累积继续上涨，2026-08-23 23:21 抬到 4G。

规则命中 16 个服务，连同 `prometheus` 200M → 2G 一并写入覆盖文件。完整审计表见 [resource_audit.md](resource_audit.md)。

**为什么**
- `prometheus` 在 200M 下把自己锁死了：TSDB WAL 重放需要的内存超过 cgroup 上限，容器活约 7 秒就被 OOM 杀掉，WAL 因此永远不被 checkpoint，下次启动重放同一份 WAL — 88 次重启，不会自愈。Prometheus 从 cgroup 上限自动推出 `GOMEMLIMIT=188743680`，又以 `GOMAXPROCS=16`（日志原文 `CPU quota undefined`）运行，和 002 里 `ad` 的 16 核放大是同一个机制。
- 更要紧的是：**任何环境自带的 OOM 都会污染场景症状**。004 已经写过单验症状无法排除环境自带故障，而当时举的例子（`ad` 的 OOM 循环）正是这一类。`checkout` 20M、`product-catalog` 20M、`shipping` 20M 这些靶子都在 `target_enum` 里，一旦建库期间自发 OOM，验证脚本会把它当成注入症状，直接产出错卡。逐个撞坑不如一次扫干净。

**放弃了什么**
- 只修 `prometheus`、其余继续逐个撞坑：已经撞了三次（`ad` → `grafana` → `prometheus`），每次都要停下来诊断，成本高于一次性审计。
- 给全部服务加 CPU 上限：治本（16 核放大的根源就是没有 CPU quota），但会改变被测系统的运行行为，直接影响后续 `latency` 类的延迟指纹，不可接受。
- 保留 Prometheus TSDB 历史：抬限额后 WAL 重放 6.42 秒完成，历史实际保住了；但即使保不住也会直接删 — 那段数据里没有任何场景数据，价值为零。

**trade-off**
内存在本机免费（限额合计 15.3G，实际用量 4.4G，宿主机 62G），代价只是覆盖文件变长、与上游 compose 的差异变大，将来升级 tag 时 rebase 成本上升（见 003）。

`opensearch` 上有一处规则冲突：4 倍 = 3702M 超出当时的 2G 档顶。首轮封顶 2G 未擅自加档，随后实测其用量持续上涨，才补了 4G 档并抬上去 —— 加档是被实测推着走的，不是一开始就放宽规则。详见 [resource_audit.md](resource_audit.md)。

**附**
本审计抬的是**基线**限额。`mem_leak` 类做卡时靠卡内临时覆盖压低靶子限额，与基线无关，抬基线不影响该类可做性。

---

## 009 crash 类判据改为「错误文字 + B 自身信号」，时序初值上调待定

**选了什么**
放弃用**调用方时延**区分 `crash` 与 `dep_timeout`，改用**错误文字**（`EHOSTUNREACH` vs 超时）加 **B 自身信号**（`crash` 日志归零、`dep_timeout` 日志继续）。`crash` 的 B 自身判据只保留「日志消失」，删掉「指标消失」。symptom 探针 `N = 10`（仅对 `cart` 标定）。§2 的 `warmup_s=60` / `observe_s=120` 三个初值标为"实测证伪，待重定"，不在本轮改数。

**为什么**
[fingerprints.md](fingerprints.md) 实测出三处与 §3 / §5 预期不符：

- **时延双峰。** §3 预测 `crash` 调用方"即时报错（毫秒级）"，实测 62 条报错 span 里只有 41.9% < 100 ms，43.6% 慢到 10 s 以上，`p50 = 11.7 s`、`p95 = 70.9 s`，正落在 §3 给 `dep_timeout` 的"耗时 ≈ 超时值"区间。Node gRPC 客户端对已失效的 subchannel 立即失败、对需新建连接的请求挂到约 71 s 的连接超时，两种路径同时存在。按原判据，两类不可分 —— 而 006 的 `(service, class)` 双匹配主指标恰恰押在类别可分上。
- **指标不消失。** §3 预测 `crash` 时 B "指标 / 日志消失"，实测日志确实归零（76 → 0），但 `target_info_present` 整个 120 秒注入期**始终为 true**：Prometheus series staleness 是 5 分钟、scrape 间隔 1 分钟，120 秒窗口连过期都不够。以"指标消失"当 `crash` 判据，在默认时序下必然失败。
- **错误被拒 ≠ 连接被拒。** 实际错误是 `EHOSTUNREACH` 而非 `ECONNREFUSED` —— `docker kill` 把容器网络端点一并摘除，IP 不可达，而不是端口回 RST。这反而是好消息：错误文字对两类是干净的分界。

**放弃了什么**
- 用时延区分 `crash` / `dep_timeout`：实测证伪。
- `crash` 的"指标消失"判据：在任何合理观察窗内都不成立，除非把 `observe_s` 拉到 5 分钟以上（Prometheus staleness）。
- 顺手改 `observe_s` 到 300s：会连带改变全部五类的指纹与建库机器时（004 已按每卡约 8 分钟估过 80 卡 11 小时），不能只凭 `crash` 一类的实测就改全局，留到五类都测完一起定。

**trade-off**
判据从"一个时延阈值"变成"错误文字 + 日志信号"两个条件，验证脚本要按类写分支、比单一阈值复杂；换来的是两类真正可分。`N = 10` 只对 `cart`（4.6 req/s）标定，低流量靶子必须逐个重标，这笔工作量记在 §8。

**附**
实现上被迫改了两处探针设计，均已写进 `scripts/probes/three_signals.py` 注释：

1. **span 必须按自身 `startTime` 过滤到窗口内。** Jaeger 返回的是与窗口相交的整条 trace，不过滤会把注入期起始的长 span 算进 `after` 窗口 —— 实测一条 71.3 s 的 `GetCart` span 同时出现在 `during` 和 `after`，把 `after` 的 `p95` 顶到 71 293 ms，看上去像"根本没恢复"。
2. **请求速率改用计数器差分，不用 `rate()`。** `scrape_interval` 是 1 分钟，`rate(...[60s])` 在 60 秒窗口里通常只有 1 个样本，直接返回空向量（首轮 baseline 与 after 两个窗口都拿到 `null`）。改成窗口两端取计数器差分，并把窗口内实际样本数一并输出，让分辨率不足可见而不是变成一个 `null`。即便如此，`after` 窗口仍只拿到 1 个样本、算不出速率 —— 这是 §2 时序初值必须上调的直接证据。

---

## 010 crash / dep_timeout 的分辨依据定为「吵 vs 哑」，心跳与时延均证否

**选了什么**
两类的分辨依据定为**调用方 span 的有无**：`crash` = 报错 span 大量出现且错误文字为地址不可达类（`EHOSTUNREACH`）；`dep_timeout` = **span 完全静默**，既无成功也无报错。时延、上报心跳、请求速率、日志行数四个字段一律**不作分辨依据**。

同时改 §5 的 symptom 定义：`dep_timeout` 不再判「错误 span 数 > N」，改判 `caller_spans_total` 降至 ≈ 0；`recovered` 的判定窗从 `t_revert + 30s` 起算。

**为什么**
009 曾提出用「错误文字 + 上报心跳」分辨两类。本轮把 `dep_timeout` 原语实现出来、两类在同一探针版本下各跑一轮后，这个假设**只对了一半**（见 [fingerprints.md](fingerprints.md)）：

- **时延不可分 —— 但不是因为重叠，是因为一侧根本没有数据。** `dep_timeout` 注入期内调用方对 `cart` 的 span 总数是 **0**，一条完成的 span 都没有，无时延可测。`iptables -j DROP` 丢包不回 RST，调用方卡在 TCP 重传退避里（`tcp_retries2` 默认约十几分钟），而 frontend 调 cart 没有能在 120 秒内触发的 gRPC deadline —— 既不成功也不报错，就是挂着。窗口拉到 250 秒复查仍是零报错。**在这个被测系统里 `dep_timeout` 的真实表现是"卡住"而不是"超时"。**
- **心跳不可分 —— 假设被直接证否。** `crash` 注入期 `cart` 已死 120 秒，`heartbeat_age_s` 却只有 **5.4 s**：collector 在服务死后仍继续导出该 series，Prometheus 照常拿到新样本。两类的取值区间（0.4→11.4 vs 36.5→45.5）差异完全来自 1 分钟 scrape 的相位噪声，与是否注入无关。009 里"改用心跳"这一半是错的。
- **错误文字可分，但不是原设想的方式。** 不是"地址不可达类 vs 超时类"两种文字，而是**有错误文字 vs 一条 span 都没有**。`dep_timeout` 不产生任何错误文字。
- **日志不可分。** 两类都归零（73→0 / 61→0）—— `cart` 只在处理请求时记日志，请求进不来就都不记。009 把"日志归零"当作 `crash` 的判据，实际上 `dep_timeout` 也归零。

**放弃了什么**
- 按时延分辨（009 已放弃，本轮进一步证实一侧无数据）。
- 以「指标序列消失」判 `crash`（008/009 已放弃：Prometheus 5 分钟惯性）。
- **以「上报心跳停止」判 `crash`**（009 提出，本轮证否：collector 在服务死后继续导出 series，心跳不停）。
- **以「日志归零」区分两类**（009 隐含，本轮证否：两类都归零，只能用于确认注入生效，不能用于分类）。
- `dep_timeout` 沿用「错误 span 数 > N」的 symptom 定义：错误数恒为 0，`0 > N` 永远为假，按现定义该类的卡**一张都通不过验证**。

**trade-off**
判据落在「span 有无」这个形态特征上，比一个数值阈值更稳，但也更依赖**流量本身存在**：`caller_spans_total ≈ 0` 既可能是 `dep_timeout`，也可能是压测器停了。建库时必须以 baseline 的 span 数作对照（`cart` 实测 baseline 54–60 条），低流量靶子的"哑"和"本来就没流量"更难区分，需逐靶子标定。

另外，`dep_timeout` 的"哑"是**观察窗内**的结论。若把 `observe_s` 拉到调用方 TCP 重传耗尽（十几分钟）之后，理论上会看到真正的超时报错 —— 但那样单卡成本从分钟级涨到十几分钟级，80 卡不可承受。现取"哑"作判据，等于接受在 120 秒尺度上定义该类。

**附**
`drop_inbound` 严格遵守 007 的注入作用面硬规则：只 `-I INPUT -p tcp --dport 7070 -j DROP`，实测注入期间目标 netns 内 `iptables -S` 只有这一条规则，`OUTPUT` 链未动 —— `cart` 自身的对外调用与遥测上报不受影响，故 `heartbeat` 在 `dep_timeout` 期持续（36.5→41.5s，正常相位噪声），这一点与设计预期相符。

---

## 011 故障类别 dep_timeout 更名为 blackhole（2026-08-24）

**选了什么**
故障类别枚举中的 `dep_timeout` 改名为 `blackhole`。注入原语脚本 `scripts/primitives/drop_inbound.sh` 的文件名与接口（`apply|revert|probe <service>`）不变；类别分辨依据沿用决策 010（吵 / 哑）。

**为什么**
④ 实测（靶子 `cart`，DROP 进服务端口，250 秒观察窗）**未出现任何超时错误** —— 0 span、0 报错、0 日志，整链静音。原因是 OTel Demo 3.0.0 服务间 gRPC 长连接未设 deadline，调用方无限等待而不是报超时。

`dep_timeout` 描述的是一种**实际没有出现的症状**（超时错误）。保留它会把错误的预期信号写进 schema 指纹、场景 `ground_truth` 与 findings，误导 agent 与评测口径。`blackhole` 描述的是实测机制：请求进入后既不成功也不失败、没有任何回音，即网络术语里的静默丢包。

**放弃了什么**
- **保留 `dep_timeout` 名、只加注释。** 放弃 —— 类别名进入 `ground_truth` 的判分二元组与全部场景文件，名实不符会随 W2 量产扩散到 80 个场景。
- **改造 testbed、给调用方加 deadline 以制造真正的超时错误。** 放弃 —— 这要改 OTel Demo 应用代码，破坏 3.0.0 pin 的复现锚点（见 001）；且现有原语全部在容器 / 网络层（`kill` / `iptables` / `tc`），改应用代码会开出第二条注入通道，增加复现与说明成本。

**trade-off**
与项目原始定义文字"依赖超时"脱钩，靠知识库注记衔接（已加）。

黑洞类的症状是**"信号缺失"而非"信号出错"**：agent 只能靠调用方 span 数下降（symptom 判据 = `caller_spans_total` 低于基线 10%，见 [fault_schema.md](fault_schema.md) §5）而非错误文本来发现 —— 这是该类天然更难的原因，评测结果解读时单列。

若后续某条调用链实测存在客户端超时（例如 HTTP 路径），届时再评估是否新增独立的 `timeout` 类，不预设。

---

## 012 注入周期双档 + 多周期无人值守批跑（2026-08-24）

**选了什么**
周期分**调试档**（30 / 60 / 30）与**入库档**（60 / 120 / 60）两档；指纹表与准入门**只认入库档**；多周期合并为单个后台脚本**串行**连跑，用户不在场，Claude Code 并行写文档。

全文见 [workflow.md](workflow.md)。

**为什么**
- **指纹数字必须同尺度可比。** ④ 实测的"120 秒内 73 条报错 span"换个窗长就失效 —— 数字本身没有意义，只有绑定窗长才有。两档分开，就不会有人拿调试档的短窗数字去填指纹表。
- **调试通路 2 分钟能答的问题不该用 4 分钟。** 原语是否生效、三信号查询是否返回、端口与后端是否可达 —— 这些都不需要满窗。
- **W2 的 80 个场景仅注入周期就 ≥ 320 分钟机器时间**（80 × 4 分钟，且 §6 要求连续 2 轮全过，实际翻倍）。逐个手跑会把人钉在屏幕前；批跑之后人可以走开，只看汇总。

**放弃了什么**
- **单一档位。** 放弃 —— 只留长档则调试太慢，只留短档则入库数据被短窗污染（60 秒窗在 Prometheus 60 秒 scrape 下只拿到 1 个样本，速率直接算不出，见 009）。
- **逐周期手动运行。** 放弃 —— 占用在场时间，且每轮都要人工核对 revert 无残留，容易漏。
- **并行注入提速。** 放弃 —— 同一 testbed 上并行注入会互相污染指纹，无法归因到单一 `(service, class)`，与 006 的根因唯一前提冲突。

**trade-off**
每个新原语**首次要跑两次**（调试档 + 入库档），比一次跑完多花 2 分钟，换来指纹数据的尺度一致。

批跑的失败发现是**滞后**的 —— 人不在场，问题要等汇总才暴露。靠两条兜底：`injected` 失败即停（注入没生效就不往下跑），`recovered` 连续失败中止批次（阈值暂定 2，**待入库档实测校准**）。

批次内测得的基线**随周期变化**（每周期各自测各自的稳定期），因此指纹只保证**周期内**可比；跨批次对照需要另做双基线，留作 W2 议题。

---

## 013 指标导出间隔降至 15s —— 唯一有效旋钮是 SDK 的 OTEL_METRIC_EXPORT_INTERVAL（2026-08-24）

**选了什么**
在 `compose.override.yaml` 给 11 个自研服务注入 `OTEL_METRIC_EXPORT_INTERVAL=15000`；**不改** `prometheus-config.yaml` 的 `scrape_interval`、**不改** Grafana 的 `timeInterval`。

spanmetrics：实测 flush 间隔 **60s**，已在 `src/otel-collector/otelcol-config-extras.yml` 追加 `connectors.span_metrics.metrics_flush_interval: 15s`（extras 是 collector 最后加载的一层，`connectors` 是 map 不是 array，可安全合并，无需改 `otelcol-config.yml`）。改后 `cart` 与 `checkout` 均实测 **15.0s**。

**为什么**
查证发现 **Prometheus 没有任何 scrape 作业**（`/api/v1/targets` 返回 `0 active` / `0 dropped`，配置文件里根本没有 `scrape_configs` 段），指标由 collector 经 OTLP HTTP 推送写入（`otelcol-config-observability.yml:26-27` 的 `otlp_http/prometheus` → `http://prometheus:9090/api/v1/otlp`，配合 `compose.observability.yaml:76` 的 `--web.enable-otlp-receiver`）。

指标的 60s 节奏来自各语言 SDK 的**默认导出间隔**；`OTEL_METRIC_EXPORT_INTERVAL` 在整个上游仓库里**只被引用、从未被赋值**（两处注释把它当作 60s 的对齐锚点，`src/quote/public/index.php:71` 读它，但 `.env` 与全部 compose 文件都没设过）。

降到 15s 后，120s 注入窗内采样点由约 2 个增至约 8 个，agent 的指标查询工具可用于时间定位。

**放弃了什么**
- **改 Prometheus `scrape_interval`。** 放弃 —— 无 scrape 作业，改了不生效。
- **只改 `.env` 加变量。** 放弃 —— compose 未把该变量传入容器 `environment`，仅改 `.env` 无效；必须在 `compose.override.yaml` 里逐服务注入。
- **降到 5s / 10s。** 放弃 —— 写入量 ×6–12，2G 限额与 009 记录的 WAL 重放 OOM 风险不值得。

**trade-off**
样本率 ×4，Prometheus 内存与 WAL 增长（改后 10 分钟实测：**MEM 332.4MiB / WAL 183.4M**，改前同日实测 **319.6MiB / 148.0M**），重启时 WAL 重放压力待下次起机复测（O-P2-3）。

第三方镜像与 collector receiver 采集的指标**不受此变量控制**，间隔各异（见 O-P2-4）。

**未生效服务：`currency`、`frontend`**，两者真实周期仍为 60s。原因均已查到、且都不是环境变量没送达（`docker inspect` 确认两个容器的 `Config.Env` 都含 `OTEL_METRIC_EXPORT_INTERVAL=15000`）：

- `currency`（C++）：`src/currency/src/meter_common.h:26` 默认构造 `PeriodicExportingMetricReaderOptions options;`，从不设置导出间隔；`src/currency/src/` 下仅有的两处 `getenv` 是 `VERSION`（`server.cpp:94`）与 `IPV6_ENABLED`（`server.cpp:257`），**没有任何读取该变量的代码**。
- `frontend`（Node）：`utils/telemetry/Instrumentation.js:25-27` 构造 `PeriodicExportingMetricReader({exporter})` 时**不传 `exportIntervalMillis`**；容器内 `@opentelemetry/sdk-metrics` 2.9.0 的 `PeriodicExportingMetricReader.js:27` 把 `exportIntervalMillis = 60000` 写死在解构默认值里，**该文件全文不含 `OTEL_METRIC_EXPORT_INTERVAL`**，env 永远不被查询。

决策 010「指标不是分类探针信号」**不变**；指标能否用于**时间定位**待 W2 评估。

**勘误**
009 与 010 把 60s 归因于 Prometheus 的 `scrape_interval`，**不准确** —— 真实成因是 SDK 默认导出间隔，Prometheus 侧无 scrape。两条原文按 append-only 不改，以本条为准。012 正文中同类表述亦以本条为准。

---

## 014 latency 原语 delay_outbound：出口 netem + u32 按 sport 过滤，初值 800ms（2026-08-24）

**选了什么**
`tc netem` 挂目标服务容器出口：`prio` 根 qdisc 的 `priomap` 全部指向 band 2 作"直通"，只有 u32 匹配 `tcp sport = 服务端口` 的包被导入挂 `netem delay 800ms` 的 band（`1:1`）。接口 `apply|revert|probe <service> [delay_ms]`，与前两个原语的参数、退出码、日志格式、`state/` 命名（`<service>.delay`，记 `delay_ms`/`iface`/`port`）完全一致。

**为什么**
只延迟该服务**发给调用方的响应**，不碰它自己的下游调用（决策 007 硬规则）—— 否则症状漂到下游、根因不再唯一。出口方向 tc 原生支持、零额外部件；入口整形必须把流量重定向到 ifb 虚设备，多一层机关。

**800ms 的依据**：明显高于基线 —— `caller → cart` 基线实测 **p50 2.43ms / p95 3.77ms**（调试档稳定期 n=29；同日 4 分钟大样本 n=209 为 p50 2.63ms / p95 4.82ms / max 6.40ms），800ms 是基线 p95 的约 200 倍，不可能被噪声淹没。明显低于外层超时上限 —— Envoy 通往 `frontend` 的 catch-all 路由（`src/frontend-proxy/envoy.tmpl.yaml:81` `route: { cluster: frontend }`）**未设 `timeout`**（Envoy 默认，默认值未查证）；压测器 `load-generator` 的 HTTP 请求超时**未设**（`compose` 环境变量中无相关项，`script.js` 里只有浏览器侧的 `waitForSelector timeout: 15000` 与 `waitForTimeout(2000)`，不是请求超时）。本系统 gRPC 无 deadline（决策 010、011 实测），延迟只产生"慢"不产生"错"。

**放弃了什么**
- **延迟目标服务全部出流量。** 放弃 —— 下游调用一并变慢，注入点与症状点混淆。
- **入口整形。** 放弃 —— 需 ifb 设备，多一层机关。
- **用 flagd 内置延迟开关。** 放弃 —— 只覆盖它预置的少数路径，不能任选靶子。

**trade-off**
u32 只匹配 TCP（`match ip protocol 6`），UDP/ICMP 不覆盖 —— 本系统服务间调用全是 TCP（gRPC/HTTP），无影响。

`prio` + `u32` 比单挂一个 netem 多两条命令，靠 `probe` 核对过滤器兜底：`probe` 把端口编码成 u32 的 hex 形式（`7070` → `1b9e0000/ffff0000`）在 `tc filter show` 中比对，netem 存在但过滤器端口不符时判 `injected=false`。

多网卡容器需人工判定接口 —— 脚本用 `ip -o link` 取非 `lo` 接口，**多于一个即停下报错，不猜**。另外 root 已有非默认 qdisc 时拒绝执行，不覆盖别人的规则。

**调试档实测**（30/60/30，靶子 `cart`，800ms，`t_inject=19:19:24Z`）：`caller → cart` 右移 **p50 +800.20ms / p90 +800.15ms / p95 +800.10ms**；注入期报错 span **0**；下游未右移（`cart → valkey-cart` p95 **+0.04ms**，`cart → flagd` p95 +2.45ms，属 1–4ms 量级的采样噪声，非 800ms 量级右移）；revert 后 `tc qdisc show dev eth0` 仅剩 `noqueue`，无残留。
