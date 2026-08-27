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

---

## 015 批次 runner run_batch.py：三探针判定在 runner 侧、非对称失败即停（2026-08-24）

**选了什么**
Python runner，读周期清单串行执行五段周期，按 [fault_schema.md](fault_schema.md) §5 判三探针，落盘到 `out/<batch>/<cycle>/`，批次汇总。锁文件保证同一时刻只有一个批次、一个原语在 apply；`injected` 失败立即中止，`recovered` 失败标红继续、连续 2 次中止；`nohup` 包装无人值守。

**为什么**
探针判定是评测集**生产侧的入库质检**，必须与 agent 完全隔离 —— agent 只见症状与原始三信号，永远不见判定与 ground truth（泄漏隔离，见 §4 与决策 005）。

失败处理刻意做成**非对称**：无效注入产出的全是废数据，所以 `injected` 失败必须停线；而一次 `recovered` 失败可能只是尾巴未散，不值得停线。

**放弃了什么**
- **shell 脚本 runner。** 放弃 —— 判定逻辑与 JSON 汇总用 bash 写易错。
- **判定放进 `three_signals.py`。** 放弃 —— 取样器应只取样不裁决，职责分离。
- **并行周期。** 放弃 —— 同 testbed 互相污染（012 已裁）。

**trade-off**
runner 复用原语 `probe` 做残留核对，`probe` 有误则残留漏检，靠调试档批先验。

**入库档批实测**（`full3_cart_205013`，settle 150s，三周期九项全过）：`crash` 报错 span **90** 条（阈值 >20）、`blackhole` immediate caller span **0**（基线 45）、`latency` p50 右移 **+800.31ms**（需 ≥640）。

**调试档与入库档判定一致性**：方向一致（crash 吵+错、blackhole 哑、latency 慢无错），但**调试档通过不保证入库档通过** —— 调试档 60s 注入窗在窗口终点即刻查询恰好捞到快速失败的报错 span 而通过，入库档 120s 窗下同样的查询时机却查到 0 条。这正是 016 双快照要解决的问题，也说明 012 定的"调试档只验通路、不进准入门"是必要的。

---

## 016 注入窗双快照：immediate（t_revert）+ harvest（t_end+settle，默认 150s）；各类按机制保证信号的快照判 symptom；recovered 按速率比较（2026-08-24）

**选了什么**
注入窗查询**两次** —— `t_revert` 即刻（`immediate`）与 `t_end + settle`（`harvest`）；settle 默认 **150s**（Linux `tcp_syn_retries=6` → 127s 建连重试预算 + 约 20s 导出余量，实测报错 span p95 131.9s、max 134.9s）；基线窗与恢复窗只在 harvest 查。

symptom 读取快照**按类固定**：

| class | 读哪份 | 判据（阈值不变） |
| --- | --- | --- |
| `crash` | `harvest` | 报错 span **> 20**（代码为严格大于，非 ≥） |
| `blackhole` | `immediate` | caller span < 基线 10% |
| `latency` | `harvest` | 右移 ≥ 0.8 × delay、无报错增加 |

新增指纹字段 **`in_flight_at_revert`** = harvest 条数 − immediate 条数。`recovered` 由条数比较改为**每秒速率**比较。窗长 60/120/60 不变（012 不动）。

**为什么**
span 只在**结束时**导出，两类故障的可靠信号出现在**不同时刻**：

- `blackhole` 在 DROP 生效期间**机制性静音** —— 已建连接卡在 TCP 重传、不发新 SYN，所以「哑」只在 immediate 成立。撤除后积压请求在同一秒全部完成并回放：实测 harvest **59 条、无报错、p50 68.6s ≈ 窗口一半**，比基线 45 条还多。
- `crash` 的报错要等 127s 建连预算耗尽后才集中出现，且快速失败比例受调用方 ARP 邻居缓存状态影响（实测一轮 `EHOSTUNREACH` 双峰、一轮 `ETIMEDOUT` 无快速失败）。

**单一观测点必然误判其中一类**（均为实测，非推演）：t_revert 即刻查把 `crash` 判成哑（0 条，`full_cart_194613`）；t_end+settle 查把 `blackhole` 判成吵（61 条，`full2_cart_201241`）。

**放弃了什么**
- **`blackhole` 改判耗时分布。** 放弃 —— 与 `latency` 同形，只靠量级区分。
- **按类设不同 settle。** 放弃 —— 同批次观测点不统一。
- **新增「harvest 时刻仍未完成」计数字段。** 放弃 —— 双快照之差已等价。
- **加长注入窗。** 放弃 —— 推翻 012 且治标。
- **流水线收割。** 放弃 —— 复杂度上升，W2 墙钟成瓶颈时再议。

**trade-off**
每周期墙钟 240s → **390s**，80 卡机器时间约 **8.7h**（决策 004 原估算是"每卡约 8 分钟、80 卡约 11 小时"，含每卡 2 轮；单轮口径下 390s × 80 ≈ 8.7h，与该估算同量级，未推翻）。

注入窗多一次**只读**查询，无系统扰动。settle 与调用方 `tcp_syn_retries` 绑定，换调用方需重校。

**010 表述修正**：吵 / 哑在 **immediate 观测点**判（即 ④ 手工测量的时刻）；「blackhole 持续哑」改为「**注入期间哑、撤除后回放**」。

**W3 牵连**：agent 拿到的是实时视角（immediate）还是事后视角（harvest），决定它看到的 `blackhole` 形态 —— 双快照保留两种选择，裁决留 W3（[O-P2-6](open_items.md)）。

**附**
`crash` 错误形态两日不一致（8/23 与 `full2` 为 `EHOSTUNREACH` 双峰，8/24 `full_cart_194613` 为 `ETIMEDOUT` 无快速失败）。本轮 evidence 显示邻居缓存在注入后约 40–80s 内 `REACHABLE → INCOMPLETE → FAILED`，与「缓存失效则秒级 `EHOSTUNREACH`」假设同向，但 `ETIMEDOUT` 轮次无 evidence 对照，**待确认**（[O-P2-5](open_items.md)）。

---

## 017 fault_schema v1.0 冻结（2026-08-24）

**选了什么**
冻结三类故障（`crash` / `blackhole` / `latency`）的定义、三探针判据（`injected` 由原语 `probe`；`symptom` 按类读 `immediate` / `harvest` 快照；`recovered` 速率比较）、双观测点（`t_revert` 即刻、`t_end + 150s`）、周期双档（012），作为 W2 80 场景量产的标准。

**此后只增不改** —— 改动需新决策并重跑受影响场景。

**为什么**
三类各有一个**独占且机制可解释**的特征（[fingerprints.md](fingerprints.md)「指纹对照表 v1.0」）：

| 类 | 独占特征 | 机制 |
| --- | --- | --- |
| `crash` | 有报错（89–90 条），另两类全程 0 | 容器不存活，调用方连接失败 |
| `blackhole` | 撤除后积压回放，`in_flight_at_revert` = 59 ≈ 注入期全部请求 | DROP 期间已建连接卡在 TCP 重传，撤除即全部完成 |
| `latency` | 常数右移无在途：两快照 p50 同为 802.67ms，`in_flight` 仅 3 | netem 固定延迟，请求照常完成 |

且入库档三类九项探针全过（`full3_cart_205013`）。再改 schema 就要重跑已入库场景，**量产前冻结是止损点**。

**放弃了什么**
**等 `misconfig` / `mem_leak` 两类实测后再冻结。** 放弃 —— 两类原语走 flagd 开关、机制与前三类不同（不在容器 / 网络层），等它们会拖住三类的量产。改为 **v1.0 先冻三类，两类补建后以 v1.1 增补**。

**trade-off**
阈值只对 `cart` 标定，其余靶子 W2 逐靶标定，可能出现**低流量靶子无法达到 N = 20** 的情况（fingerprints.md 已预警）。

`crash` 耗时形态不稳定（四轮三态，p50 从 65 786ms 到 0.51ms，错误文字两种）未解，见 [O-P2-5](open_items.md)。v1.0 **以报错数为判据规避之** —— 报错**数**在四轮里始终稳定（73/55/74/90 量级），不稳定的只是耗时与文字。若 W3 的 agent 需要耗时形态做证据，需先解 O-P2-5。

**W2 首两项**
1. `misconfig` / `mem_leak` 原语（flagd 通道）建成，并按 012 / 016 跑 `cart` 入库档；
2. 靶子清单与逐靶阈值标定（含 `flagd` / `frontend-proxy` 的服务端口人工判定，见 [O-P2-4](open_items.md)）。

---

## 018 set_flag 原语与低流量靶子相对阈值（第一部分，2026-08-24）

> `misconfig` / `mem_leak` 的 `symptom` 判据**待实测后补**（第二部分）。本条只写已定的四项。

**选了什么**

**1. 新原语 `scripts/primitives/set_flag.sh`（flagd 通道）。** 接口 `apply|revert|probe <service> <flag>=<variant>`，比前三个原语多一个参数。实现方式经查实而非假设：compose 里 flagd 的 command 是 `start --uri file:./etc/flagd/demo.flagd.json`，宿主机 `src/flagd` 挂到容器 `/etc/flagd`，启动日志有 `Starting filepath sync notifier`；**实测改挂载的 json 后约 6 秒内 OFREP 即返回新 variant，恢复后回原值** —— 故走「改文件 + 自动重载」，不重启容器。求值走 OFREP（容器 8016，宿主机端口由 `docker port` 动态取，compose 未钉死）。脚本内置 flag → 影响服务映射表并在 `apply` 时校验，防止把 flag 记到错的靶子上。

**2. `crash` 的 `N` 由固定 20 改为相对阈值**：`N = max(5, ceil(0.25 × baseline_rate_per_s × inject_s))`。

**3. 两个数据库靶子改从调用方边取信号。** `valkey-cart` 与 `astronomy-db` 是第三方镜像、无 SDK、不产生 server span，在 Jaeger 的 `/api/services` 里根本不存在。它们的 `caller_edges` 从调用方 client span 的 peer 标签匹配 —— 实测 `cart → valkey-cart` 125 条、`product-catalog → astronomy-db` 199 条（60s 窗），有数可用。

**4. `flagd` / `frontend-proxy` 服务端口人工判定**：`flagd` = **8013**（各服务 `FLAGD_PORT` 指向的求值端口；8016 是 OFREP，不作靶口）；`frontend-proxy` = **8080**（`ENVOY_PORT` 主监听；10000 是 Envoy admin，不作靶口）。`service_ports.env` 的两个 `None` 已填，全表 16 项无 `None`。

**为什么**
- 固定 `N = 20` 只对 `cart` 这类高流量靶子成立。实测被调速率：`frontend` 418/min、`cart` 67/min，但 `email` / `payment` / `checkout` 只有 **4.4/min** —— 120 秒注入窗内总共约 9 次调用，**永远达不到 20**，这三个靶子的 `crash` 卡按旧阈值一张都通不过。相对阈值把 `N` 绑到该靶子自己的基线流量上。
- `cart` 用新公式重算：基线 53 spans / 60s = 0.8833/s，`N = max(5, ceil(0.25 × 0.8833 × 120)) = 27`，harvest 报错 90 条，**90 > 27 仍通过** —— 已入库的 `cart` 结果不受影响。

**放弃了什么**
**改 flagd 的 flag 后重启 flagd 容器。** 放弃 —— 实测文件监视可用，重启会中断全部服务的 flag 求值，把单靶子注入变成全系统扰动。

**trade-off**
`set_flag` 修改的是**上游仓库里的受版本控制文件**（`src/flagd/demo.flagd.json`），不是 `compose.override.yaml`。脚本 `apply` 时备份、`revert` 时恢复，runner 的残留核对增加了一条 `git status --short src/flagd/` 必须为空 —— 三轮批次实测均干净。但这意味着**批次异常中止时该文件可能留脏**，需人工 `git checkout`。

**实测记录（不作判据，供第二部分定判据用）**

入库档 `fullflag_215427`，三周期 `injected` 全通过、无残留、`symptom` / `recovered` 输出「待定」：

| 周期 | 类 | harvest spans / err / p50 | 内存 first→last（max） | 增长 |
| --- | --- | --- | --- | --- |
| `cartFailure=50%` | misconfig | 109 / **0** / 2.29ms | 62.3 → 63.9 (63.9) MiB | +1.6 MiB（0.8 MiB/min） |
| `recommendationCacheFailure=on` | mem_leak | 52 / **0** / 5.55ms | 47.4 → 47.7 (51.9) MiB | **+0.3 MiB（0.1 MiB/min）** |
| `emailMemoryLeak=10000x` | mem_leak | 8 / **0** / 218.71ms | 58.6 → **230.1** (230.1) MiB | **+171.5 MiB（85.8 MiB/min）** |

三个容器 `RestartCount` 前后均为 0、`OOMKilled=false`。`email` 涨到 230.1 MiB，限额 512M（`resource_audit.md`），占 45%，未触碰。

**三条给第二部分的实测结论**

1. **`misconfig` 的症状不在调用方 span 上。** `cartFailure=50%` 的调用方报错 span 是 **0**，而 `cart` **自身** server span 123 条里有 2 条报错（`FailedPrecondition: Can't access cart storage`）。这与 §5 写的「`misconfig` = B 自身错误 span / 日志 > N」一致 —— 但 `three_signals.py` **目前没有 B 自身 span 的字段**（只有 `caller_edges` 与 `downstream_edges`），第二部分需先补 `self_edges`。
2. **`cartFailure=50%` 的实际报错率是 1.6%，不是 50%，差 48.4 个百分点。** 原因经代码核实：该 flag 只在 `EmptyCart` 方法内判断（`src/cart/src/services/CartService.cs:74-90`），`GetCart`（90 条）与 `AddItem`（26 条）完全不受影响；`EmptyCart` 本身只被调 7 次，其中 2 次失败 = 28.6%，接近设定的 50%。**变体名里的百分比是「该方法的失败率」，不是「该服务的失败率」** —— 出题时 `ground_truth.note` 必须写清受影响的方法，否则症状量级完全对不上。
3. **两个 `mem_leak` flag 的强度差三个量级。** `emailMemoryLeak=10000x` 85.8 MiB/min，`recommendationCacheFailure=on` 只有 0.1 MiB/min —— 后者的增长靠 `cached_ids` 列表几何增长（`recommendation_server.py:86-87` 每次 cache miss 追加自身 1/4），但 cache miss 只有 50% 概率触发，且 `recommendation` 被调仅 25.9/min，120 秒窗内攒不出可观增长。该 flag 要做成可判定的 `mem_leak` 卡，需要显著更长的 `observe_s`，或换更高流量的靶子。

---

## 018 第二部分：misconfig / mem_leak 判据（2026-08-24）

承 018 第一部分。两类的 `symptom` / `recovered` 判据定稿，`fault_schema` 随之升 v1.1。

**选了什么**

**`misconfig` symptom**：`harvest` 快照中，目标**自身** server span 在**受影响方法**上的报错数
≥ `max(2, ⌈0.5 × ratio × 该方法调用数⌉)`，**且基线窗目标自身报错为 0**。

- `ratio` 由 variant 名解析（`50%` → 0.5；`on`/`off` 型取 1.0），再乘代码里的**固定概率**
  —— `adFailure` 即使 `on` 也只有 `random.nextInt(10) == 0`，实际 ratio = **0.1**。
- 受影响方法从 [flag_catalog.md](flag_catalog.md) 读，runner 内置 `FLAG_METHOD` 映射表。

**`misconfig` recovered**：恢复窗目标自身 server 报错 = 0。

**`mem_leak` symptom**：注入窗 `growth_mib ≥ max(10, 0.15 × first_mib)` **且**
`last_mib ≥ first_mib + 阈值`。指标 `container_memory_usage_total_bytes`
（`docker_stats` receiver，标签 `container_name`，实测 10s 一个点）。

**`mem_leak` recovered**：恢复窗增长率（MiB/min）≤ 基线窗增长率 + 1。

**为什么 misconfig 判目标自身 span**
实测 `cartFailure=50%`：调用方 span **109 条、0 报错**，而 `cart` 自身 123 条里 2 条报错。
`misconfig` 的失败被调用方吞掉了 —— 用调用方报错数判，该类的卡**一张都通不过**。
这和 §5 原本写的「B 自身错误 span / 日志 > N」一致，只是此前 `three_signals.py`
没有目标自身 span 的字段，本轮补了 `self_edges`。

**入库档验证**（`judge_222727` + `rerun_cart_225459`）

| 周期 | 受影响方法 / 内存 | 阈值 | 实测 | symptom | recovered |
| --- | --- | ---: | ---: | --- | --- |
| `cartFailure=50%`（首跑） | `EmptyCart` 调用 6 次 | N=2 | 报错 **0** | **失败** | 通过 |
| `cartFailure=50%`（重跑） | `EmptyCart` 调用 4 次 | N=2 | 报错 **2** | 通过 | 通过 |
| `adFailure=on` | `GetAds` 调用 23 次，ratio 0.1 | N=2 | 报错 **5** | 通过 | 通过 |
| `emailMemoryLeak=1000x` | 58.5 → 94.0（max 98.7）MiB | 10 MiB | 增长 **35.5** | 通过 | 通过（基线 0.2、恢复 −3.8 MiB/min） |
| `emailMemoryLeak=10000x` | 80.7 → 131.8（max 139.3）MiB | 12.11 MiB | 增长 **51.1** | 通过 | 通过（基线 0.6、恢复 1.4 MiB/min） |

**放弃了什么**
**用调用方报错数判 `misconfig`。** 放弃 —— 实测证否（调用方零报错）。

**trade-off**

**`cartFailure` 这类低调用量方法的判定天然不稳。** `EmptyCart` 只有 **3.9 /min**，
120 秒窗内实测仅 4–6 次调用。在 p=0.5 下，6 次调用全不失败的概率是 1.6%、
4 次调用中失败 ≥2 次的概率是 68.75% —— **首跑失败、重跑通过，两次都在概率范围内**，
不是阈值错。做卡时该 flag 应选 `75%` 以上的 variant，或换调用量更高的方法。

`misconfig` 的判据依赖 `FLAG_METHOD` 这张人工维护的映射表。表错了不会报错，只会
把报错数统计到错的方法上导致判失败 —— 每新增一个 flag 都必须先在
[flag_catalog.md](flag_catalog.md) 里定位到代码行再登记。

**`recommendationCacheFailure` 不入库。** 实测增长 0.1 MiB/min，远低于 10 MiB 阈值。
成因：cache miss 只有 50% 概率触发（`recommendation_server.py:80`），且该服务被调
仅 25.9 /min，120 秒窗内攒不出量。**`mem_leak` 类当前只有 `email` 一个可用靶子**，
见 [O-P2-8](open_items.md)。

**出题要求：`cartFailure` 的百分比是方法级不是服务级。** 实测 `50%` 时 `cart` 整体
报错率仅 1.6%（`EmptyCart` 只占全部调用的 5.7%）。`ground_truth.note` 必须写清
受影响的方法，否则 agent 看到的症状量级与卡面描述完全对不上。

**附**：`paymentUnreachable` 的症状落点与其余四个 `misconfig` flag **不同** ——
它把 payment 客户端换成指向 `badAddress:50051`（`checkout/main.go:567-571`），
症状在 `checkout` 的 **client span** 上，`payment` 全程正常。现有 `misconfig` 判据
读的是 `server_by_method`，对该 flag 不适用，做卡前需单独实测确认落点。


---

## 019 收工用 stop、起床后无条件重启 flag 消费方（2026-08-26 ET）

**选了什么**
收工执行 `scripts/maintenance/shutdown.sh`（`docker compose <三文件> stop`），
不把栈留给 restart policy。起床执行 `scripts/maintenance/wakeup.sh`：起床命令 →
**等 flagd 真的能应答 OFREP** → **无条件重启全部 flag 消费方** → 过三项门
（25 服务 running、`cart` RestartCount=0、`prometheus` 为 `0 false`）。

**为什么**
O-P2-10 的三假设实验（见 [open_items.md](open_items.md)）把根因钉死了：

- VM 开机时 25 个容器被 restart policy 几乎同时拉起。实测 `checkout` 容器启动于
  22:33:55.020，而 flagd 的 `Flag IResolver listening at [::]:8013` 到 22:34:00.957
  才就绪 —— **checkout 早 5.9 秒**。
- Go 服务用 `flagd.NewProvider()` + **非阻塞**的 `openfeature.SetProvider`
  （`checkout/main.go:197,202`；`product-catalog/main.go:147,152` 写法相同），
  首次连接失败**既不阻塞启动也不产生日志**（checkout 全量 stdout 日志 0 行），
  该进程实例此后一直取默认值 `false`。
- 实验证否了另两个假设：**重启 checkout 后注入立即生效**（`PlaceOrder` 10 条 / 5 报错）；
  **不重启也能撤除**（7 条 / 0 报错）与**不重启也能注入**（2 条 / 2 报错）——
  说明缓存失效通道在连接健康时两个方向都正常，问题只在那一次失败的首连。

`depends_on: service_started` 只保证 flagd **容器**已启动，不保证其**监听器**就绪，
所以光靠 compose 的依赖顺序不够，必须显式等 OFREP 应答。

**放弃了什么**
- **改 checkout / product-catalog 源码用 `SetProviderAndWait`。** 放弃 —— 那是测试床
  上游文件，破坏决策 001 的 3.0.0 复现锚点。
- **给每张 flag 卡的注入序列加一步重启。** 放弃 —— 重启会制造空窗与冷启动尖峰、
  污染基线，且把 `misconfig` 卡的语义从「改配置」变成「改配置 + 重启」。
  改为在**起床时**统一重启一次，卡内注入不再需要重启。

**trade-off**
每天多花 1–2 分钟（实测 `wakeup.sh` 全程 15 秒，含 flag 消费方重启 11 秒）。
忘记 `shutdown.sh` 时，起床里那一步无条件重启就是兜底 —— 代价是每天都要重启 10 个
服务，即使昨天规规矩矩 stop 过。选择无条件而非条件重启，是因为「provider 是否连上」
根本**没有可观测信号**（零日志），条件判断无从下手。


---

## 020 事后快照评测模式与两级冻结（2026-08-27 ET）

**选了什么**
agent 评测采用**事后快照模式**：量产每张卡时同步落盘**证据包**五件 —— 日志片段、
指标时间序列、trace 清单、配置 diff、服务拓扑。agent 与全部基线**只读证据包**，
不查询实时系统。

**为什么**
- **可复现、可重跑。** 同一张卡的同一份证据包，任何时候重跑都得到同样的输入；
  实时模式下每次重跑都是一次新的注入，agent 面对的系统状态不可能完全一致。
- **机器时间从约 43 h 降到量产一次约 11 h。** 实时模式下 5 轮 × 80 卡 × 6.5 min
  实时注入 ≈ 43 h；事后模式只在量产时注入一次（80 卡 × 约 6.5 min ≈ 11 h），
  此后所有轮次、所有基线都读同一批证据包。

**放弃了什么**
**实时评测模式**（agent 在故障进行中追问系统）。

**trade-off**
项目叙事从「实时追问系统的 agent」改为「离线评测集 + 可复现评测」。
代价是 **agent 无法主动触发新的观测**，只能在证据包范围内推理 —— 证据包里没有的东西，
agent 再聪明也拿不到。这把「证据包该装什么」变成了评测设计的核心问题
（观测点的选择见决策 016，agent 拿 immediate 还是 harvest 视角的裁决即属此列）。

### 两级冻结与推进序

**v1（可进简历）**
- agent：5 工具 + function-calling 循环、参数校验、重试、步数熔断
- harness 四指标
- **≥40 卡**（五类齐、含难度分档）
- **2 条基线**：关键词启发式 / 无工具单轮 LLM
- findings（含失败模式归类）
- CI 回归门禁

**v1.1**
- **80 卡**
- 第 3 基线（换小模型）
- Langfuse / OTel 追踪
- FastAPI / Postgres
- 云部署

**推进序**
019 配方 → 生成器（含证据包落盘）→ 首批 16 卡 → agent + harness 在 16 卡跑通 →
两条基线 → 量产至 ≥40 卡与 findings / CI 并行 → 证据审计 → 简历。
其余卡到 80 走无人值守。

---

## 021 场景库配方 v1.0 定稿：76 卡 = 45 唯一根因对 × 真实变体（2026-08-27 ET）

**选了什么**

**76 卡配方定稿**，见 [recipe.md](recipe.md) v1.0，真源为 `scripts/scenarios/recipe.csv`。

| 类 | 唯一根因对 | 卡数 | 变体构成 |
| --- | ---: | ---: | --- |
| `crash` | 13 | 13 | 13 个靶子各 1 张 |
| `blackhole` | 13 | 13 | 13 个靶子各 1 张 |
| `latency` | 13 | 26 | 每靶子两档：**800 ms** / **3000 ms** |
| `misconfig` | 5 | 21 | `adFailure` 1、`cartFailure` 3、`paymentFailure` 6、`paymentUnreachable` 1、`productCatalogFailure` 10（10 个 `product_id`） |
| `mem_leak` | 1 | 3 | `emailMemoryLeak` `10000x` / `1000x` / `100x` |
| **合计** | **45** | **76** | |

**靶子准入**：靶子必须**在 trace 路径上**（自身有 SDK，或有带 SDK 的调用方边）。
据此 `image-provider` 出局 —— 它是 `frontend-proxy` 直转的静态图片服务，无 SDK，
出卡后证据包的 trace / 指标 / 配置 diff 三件为空，agent 无从推理。

**开关准入**：开关须有**入库档实测**，或有**指纹相容的预期推算表**。
`paymentFailure` 据后一条入库（6 张变体卡），**全部标 `param_validated=false`**，
首批带一张 `misconfig-payment-100` 做验证。

> **验证卡条款改写（2026-08-27 ET）**：验证卡过门**只解锁该组进入量产排期**，
> **不代表整组入库**。组内**每张卡仍各自过探针门与告警检测**；过不了的卡
> **单独出库、如实记**，不牵连同组其余卡，也不因验证卡通过而豁免。
> 原条款写的是「不过门则 6 张整组出库」—— 那是把一张卡的结果当成六张卡的证据。
> 实测理由：`misconfig-payment-100` 确实过门，但 `Charge` 在 120 s 注入窗内只被调
> **7 次**（阈值 4，裕度 3 条）。7 个样本能证明"这个开关能注进去"，
> 证明不了 `10%` / `25%` 那几档在同样窗口下也拿得到足够样本 ——
> 比例越低，期望报错数越少，越可能连阈值都够不着。

**难度三轴**，各 0–2：

- **轴 A 上浮跳数**（靶子在调用链上的深度：0 / 1 / 2）；
- **轴 B 部分失败比例**（按**服务级实测口径**，不按变体名面值 —— `cartFailure=75%`
  只作用于 `EmptyCart`，占 cart 调用 5.7%，服务级有效比例 ≈4.3%）；
- **轴 C 症状误导**（症状落点与根因不同服务、或形态与类别直觉相反）。

**档位**：总分 0–1 **易**、2–3 **中**、≥4 **难**；**轴 C=2 直接升一档**。
本表的分数是**预估**；量产后按证据包**实测重算 B 与 C**，与预估不一致时以实测为准，
重算结果写回卡片的 `difficulty_measured`。

**首批 16 卡**：按 recipe v1.0 第五节 —— 即 v0.2 的清单，但第 11 位
`crash-quote-01` 换成 `misconfig-payment-100`（承上一条，给无实测的开关配验证卡）。

**放弃了什么（补，2026-08-27 ET）**：**「整组连坐」的入库口径**。
一张验证卡的结果被用来决定六张卡的去留，看起来省事，实际是把
「开关能不能注入」（一次就能证明）和「这个变体在 120 s 窗内够不够样本」
（每张卡各自的问题）混成了一件事。

**为什么**

- **五类靶子集合由 testbed 物理决定，不由配额决定。** 靶子数不是配出来的：
  `crash` / `blackhole` / `latency` 共用同一个 13 靶集合，因为这三类都靠网络面
  或容器生命周期注入，能注的对象就是那 13 个在 trace 路径上的服务；
  `misconfig` / `mem_leak` 的靶子锁死在 flagd 开关影响的服务上，一个开关一个靶子。
  想加靶子只能改应用代码，那会破坏决策 001 的 3.0.0 复现锚点。
- **变体必须是真实参数差异。** 每张卡要么换靶子，要么换一个**在系统里真的产生
  不同行为**的参数（延迟档位、失败比例、targeting 命中的商品、泄漏倍率）。
  这是「45 个唯一根因对撑起 76 张卡」的唯一合法途径。

**放弃了什么**

- **`cartFailure` 的 `10%` / `25%` / `50%` 三档。** 放弃 —— O-P2-9：报错 `EmptyCart`
  span 实测 p50 65.0 s、max 262.1 s，超过 harvest 的 settle 150 s，harvest 会少算；
  且 `EmptyCart` 120 s 窗内只有约 4–8 次调用，低比例下预期报错数贴着阈值，
  **不可判定**。补回这三张要先解决 O-P2-9。
- **`emailMemoryLeak=10x`。** 放弃 —— 按 `1000x` 实测 +35.5 MiB/120s 线性外推，
  `10x` 增量约 **3.5 MiB**，低于 `mem_leak` 判据阈值 `max(10, 0.15 × first_mib)`
  ≈ 10 MiB（决策 018 第二部分），检测不出来。
- **同根因对换注入时刻的弱变体**（同靶子同参数、只改注入时刻出多张卡）。
  放弃 —— 那不是真实参数差异，只是同一张卡跑两遍，对 agent 是同一道题。
- **`adHighCpu` / `adManualGc` / `failedReadinessProbe` / `imageSlowLoad` /
  `intlShippingSlowdown` / `kafkaQueueProblems`**：无任何实测记录，按开关准入不入。
  `recommendationCacheFailure` 按 O-P2-8 排除（0.1 MiB/min，120 s 窗不可判定）。

**trade-off**

- **总数 76，未达 80，差 4 张。** 不为凑数增加参数完全相同的卡 —— 重复卡对评测
  没有信息量，却照样吃 6.5 min/卡的机器时间。可补的方向已记在 recipe 第八节
  （O-P2-9 若解决可补 3 张 `cartFailure`）。
- **总数口径待知识库 D83 修订，用户裁决中。** 在裁决落地前，76 是本项目的工作数字：
  决策 020 的两级冻结里 v1 只要求 **≥40 卡**，76 已越过 v1 门槛，
  「80」只是 v1.1 的目标，不阻塞推进序。
- **难卡 5 张，低于 ≥8 的目标。** 难度是三轴算出来的，不是标出来的；靠改分数凑难卡
  等于自己骗自己。实测重算（轴 B / 轴 C）之后难卡数会变，届时再看是否需要
  专门设计难卡（例如多跳 + 症状误导的组合）。
- **`paymentFailure` 的入卡理由本身有瑕疵。** 该开关只有一张**预期**报错数推算表，
  没有实测行；裁决同时写了「三者均有入库档实测」和「没有实测记录的开关一律不入」，
  两句自相矛盾（recipe 第八节拦路问题 1）。本决策的处理是**折中**：入卡、标未验证、
  首批带一张验证卡、不过门整组出库 —— 代价是这 6 张在验证跑完之前不能算数。

### 首批执行补充（2026-08-27 ET）

**选了什么**
`run_batch.py` 的**批次模式**（`--scenarios` / `--batch`）改为**单卡失败不停批**：
过不了探针门的卡记 `production.probe.verdict = failed`、**不打包证据**、写日志后
**继续下一张**；**连续 3 张**失败才中止批次并在 `summary.md` 写明原因。
决策 015 的「`injected` 失败即停」在 **`--cycles`（单卡 / 指纹）模式下保持不变**。

**为什么**
首批 16 张里有 **8 张 `param_validated=false`** —— 按本决策的开关准入，
`paymentFailure` 整组、以及若干从未实测过的靶子都是**带着「可能不过门」的预期**
进批的。个别卡失败在这里是**预期可能，不是系统异常**，而决策 015 的失败即停是为
「同一组参数连着跑十几个周期」设计的：那时一次无效注入意味着后面每个周期都在
产废数据，停线是对的。批次模式下每张卡参数各不相同，第 3 张失败与第 4 张能不能成
没有因果关系，停批等于把其余十几张卡的机器时间一起赔掉（16 卡约 1.8 h）。

**连续 3 张**是分界线：单张失败是卡的问题，连着 3 张失败是**系统的**问题
（testbed 挂了、flagd 不应答、后端查不动），那时继续跑才真的是在喂废数据。

**放弃了什么**
- **完全不停批、跑完 16 张再看。** 放弃 —— testbed 整体挂掉时会白跑 1.8 h。
- **按失败总数（而非连续数）停批。** 放弃 —— 8 张未验证卡分散失败是完全正常的
  结果，总数门槛会在没有系统性问题时误停。
- **失败的卡照样打包。** 放弃 —— 无效注入的证据包是废数据，进了库还要人回头清。

**trade-off**
批次汇总里「失败」这一列从此有两种含义：`aborted`（停批）与 `failed`（记账继续），
读汇总时要分清。`summary.md` 的关键数字列对后者加 `failed:` 前缀区分。

### 修订 2026-08-27：76 → 69 卡，依据 O-P2-14 份额表

**选了什么**
`productCatalogFailure` 由 **10 张减到 3 张** —— `2ZYFJ3GM2N`（份额最高 11.4%）、
`66VCHSJNUP`（最低 9.1%）、`OLJCESPC7Z`（9.3%，已有实测且在首批）。
总卡数 **76 → 69**，misconfig **21 → 14**，难度直方图 25 易 / **39** 中 / 5 难。
`recipe.csv` 新增 `batch_order` 列，`--batch N` 按该列排序而非文件名字母序。

**为什么**
[O-P2-14](open_items.md) 的份额实测（2026-08-27，27 min，n = 4154）：10 个 `product_id`
的 `GetProduct` 份额极差只有 **2.3 个百分点**（11.4% ~ 9.1%）—— 压测器
`user_browse_product` 是均匀抽样的。份额均匀意味着这 10 张卡**在轴 B 上不可分**，
三轴分数只在 B=1 / B=2 那条 10% 边界上被切成两组，而边界恰好落在分布正中间，
是人为切分不是真实差异。**10 张里 7 张是重复卡**，对 agent 是同一道题，
却各吃 6.5 min 机器时间（7 张 ≈ 45 min）。

本决策原文写的是「变体必须是真实参数差异」—— 那 7 张不满足这条，
是配方定稿时缺份额数据、按「一个命中分支一张卡」机械展开的结果。

**放弃了什么**
- **保留 10 张、把轴 B 的边界挪到分布之外。** 放弃 —— 那是让分数迁就卡数。
- **一张不留、`productCatalogFailure` 整个开关出库。** 放弃 —— targeting 型是本
  testbed 唯一的「部分实体失败」形态，症状形状与概率型开关不同，评测集需要它。
  留 3 张（最高 / 最低 / 已实测）足以覆盖该开关的全部行为。

**trade-off**
总数 69，离 80 更远了（差 11）。这是有意的：决策 020 的 v1 门槛是 **≥40 卡**，
69 已越过；用重复卡把数字堆到 80 只是把机器时间换成一个好看的数。
补卡的方向仍记在 [recipe.md](recipe.md) 第八节（O-P2-9 解决可补 3 张 `cartFailure`）。

---

## 022 判据分母改用 Prometheus 300s 回看；crash / blackhole 增靶子侧判据（2026-08-27 ET）

**选了什么**

1. **判据分母换源。** `symptom` / `recovered` 用的**基线速率与基线 span 数**不再取自
   runner 那 60 s 静默窗，改从 **Prometheus spanmetrics 取 `t_inject` 之前 300 s**
   （计数器差分，与决策 013 / `three_signals` 同一口径）。
   **周期结构不动**（决策 012 的 60/120/60 与决策 016 的 settle 150 照旧），
   变的只是判据的分母。取不到系列时回退到 60 s 窗，并在 `probes.json` 里
   记 `baseline_rate_source`，不静默替换。
2. **`crash` / `blackhole` 的 symptom 改为二选一**，任一成立即通过：
   - **调用方边档**（原判据）：`crash` 看调用方报错 span > `N`；
     `blackhole` 看调用方 span 数 < 基线 × 0.1；
   - **靶子侧档**（新增）：靶子**自身作为 server** 的 spanmetrics 请求速率，
     注入期 ≤ 基线 × **0.1**，**且**基线速率 × 注入窗秒数 ≥ **5**。
3. **两个无 SDK 的数据库靶子**（`valkey-cart` / `astronomy-db`）**仍只从调用方边判**
   —— 它们不产生 server span，也就没有 spanmetrics 系列。靶子侧档对它们
   返回「不适用」而不是「不通过」，两者在 `probes.json` 里分开记。

**为什么**

首批 16 卡里 4 张失败，失败原因全都是**判据分母为零**，不是注入无效：

| 卡 | 失败探针 | 分母实测 |
| --- | --- | --- |
| `blackhole-payment-01` | symptom, recovered | `baseline_spans = 0`，阈值 `0 × 0.1 = 0.0` |
| `crash-payment-01` | symptom, recovered | `baseline_rate_per_s = 0.0`，报错 span 0 |
| `crash-frontend-01` | symptom, recovered | `baseline_rate_per_s = 0.0`，报错 span 0 |
| `blackhole-valkey-cart-01` | symptom, recovered | 分母有值，另一成因见 [fingerprints.md](fingerprints.md) |

两条独立的成因：

- **60 s 基线窗对低流量靶子采样为 0。** `payment` 被调约 **2 /min**，60 s 窗的期望
  样本数是 **2**，实测多次落到 **0**。判据把 0 当成合法基线，于是
  `blackhole` 的阈值成了 `0 × 0.1 = 0`（`d < 0` 恒假），`crash` 的
  `N = max(5, ceil(0.25 × 0 × 120)) = 5` 而调用方一条报错也没采到。
  300 s 回看把 `payment` 的分母做实到 **0.0333 /s（10 次 / 300 s）**。
- **深度 0 的靶子没有调用方 span。** `frontend` 的唯一上游是 `frontend-proxy`，
  它不产生能与 `frontend` 配对的 caller span（决策 021 的靶子准入只要求靶子
  **在 trace 路径上**，没要求它**有带 SDK 的调用方**）。对这类靶子，
  「从调用方侧看 B 是否变哑」这个问法本身不成立 —— 必须看 B 自己。

靶子侧档的两个常数与决策 021 修订里检测器规则 2 的两条守卫**同源**：
`0.1` 是「速率真的塌了」的同一条线（决策 016 的 `blackhole` symptom 也用这个数），
`≥ 5` 是「静默要有足够样本才算数」——每分钟被调 2 次的服务安静半分钟说明不了任何事。

**放弃了什么**

- **拉长基线窗到 300 s（改周期结构）。** 放弃 —— 那会推翻决策 012、让每卡墙钟
  从 390 s 涨到 630 s，69 卡多出 4.6 h，而**判据要的只是一个可靠的分母**，
  不需要那 300 秒是"干净静默期"。Prometheus 里本来就有这段数据，白拿。
- **把 `N` 的下限从 5 抬高。** 放弃 —— 那是在治症状：分母为零时抬下限只是把
  「必然失败」换成「更必然失败」。
- **给低流量靶子单独放宽阈值。** 放弃 —— 阈值一旦按靶子定制，跨卡就不可比了。
- **`frontend` 出卡。** 放弃 —— 深度 0 是难度轴 A 的一端，评测集需要它。

**trade-off**

- **判据的观测源与 agent 的证据包更近了。** 分母来自 Prometheus，与检测器
  （§10）读的是同一批 spanmetrics 计数器；好处是生产侧与 agent 侧不再各说各话，
  代价是 `symptom` 从此依赖 Prometheus 可用 —— 探针少了一层独立性。
  回退路径（60 s 窗）保留，且回退这件事会记进 `probes.json`。
- **`recovered` 的同一处零分母也一并修掉（2026-08-27 ET 补）。** 首批重跑实测
  `crash-frontend-01`：`symptom` 靠靶子侧档通过了，`recovered` 却仍失败 ——
  它的「回到基线」判据是「调用方 span 速率 ≥ 基线 × 0.5」，而 `frontend` 的调用方边
  恒为 0，`span_back` 恒假。补法与 `symptom` 同源：**调用方边基线为 0 时，
  改判靶子自身 server 速率 ≥ 其 300 s 基线 × 0.5**，用哪一档记进 `probes.json` 的 `arm`。
- **靶子侧档对超低流量靶子仍然"不适用"。** `payment` 基线 0.0333 /s，
  120 s 注入窗的期望调用数只有 **4.0 < 5**，靶子侧档直接不适用，
  这两张卡仍然只能靠调用方边档。**这不是本决策能解决的** ——
  要么拉长注入窗（推翻决策 012），要么 `payment` 不作 crash / blackhole 靶子。
  见 [open_items.md](open_items.md) O-P2-18。
