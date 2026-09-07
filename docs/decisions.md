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

---

## 023 R1/R2/R3：低流量靶子拉长注入窗、无 SDK 靶子双臂判据、Envoy 上游边、检测器规则 7（2026-08-28 ET）

**选了什么**

1. **R1 低流量名单与单卡窗口例外。** 用最近 5 个连续 300 s 窗对 13 个靶子实算基线速率
   （有 SDK 用自身 server span 速率，无 SDK 用被调边速率），**最小窗速率 < 0.05 /s**
   的进名单：**`checkout` / `email` / `payment`**（三者同属下单链路，速率完全同步，
   五窗区间 **0.0433–0.0633 /s**、均值 0.0567）。名单内靶子的**全部 22 张卡**
   设 `inject_s=300`。决策 012 的统一周期**原文不改**，这是以本条为出处的例外。
2. **单卡窗口覆盖走 recipe 列，不手改 yaml。** `recipe.csv` 新增 `cycle_override` 列
   （JSON，如 `{"inject_s":300}`），`cards.cycle_for()` 合并到 `CYCLE` 之上，
   未知键直接报错。手改卡文件的路子封死 —— 生成器一跑就会覆盖回去。
3. **已过卡不溯及。** 已通过探针门**且已打包**的卡不因本条重跑，其窗口以
   `production` 块的实录为准。卡文件里的 `cycle` 是**前向规格**，`production` 是
   **实际发生**，两者允许不一致；本条之后 `misconfig-payment-90`（实录 `inject_s=120`）
   等卡的 `cycle.inject_s` 显示 300，读数以 `production` 为准。
4. **R2 无 SDK 靶子（`valkey-cart` / `astronomy-db`）的 symptom 增两条臂**，与既有的
   调用方边臂、靶子侧臂并列，或关系：
   - **台阶臂**：调用边 during p50 ≥ **1000 ms**，或 ≥ **100 ×** 基线 p50；
   - **边静默臂**：调用边 during 速率 ≤ 基线 × **0.1**，且 基线速率 × `inject_s` ≥ **5**。
5. **R3 latency 判据：扩 `PEER_KEYS` 认 Envoy 命名**，加入
   `upstream_cluster.name` / `upstream_cluster`。**不加靶子自身 SERVER 侧臂**（见「放弃了什么」）。
6. **检测器新增规则 7 `method_latency_jump`**：按 `(service, operation)` 判 p95，
   `p95 ≥ 2 × 基线` **且** `Δp95 ≥ 100 ms`，**或** `Δp95 ≥ 500 ms`；两窗各需 ≥ 5 次调用。
   `queries.py` 新增 `p95_latency_ms_by_operation`。
7. **`no_alert` 卡处置规则**：换新窗或新规则后**重跑一次**；**仍 `no_alert` 则出库**，
   并在 findings 记一条成因。据此本轮 `misconfig-ad-on` 出库（69 → **68** 卡）。

**R1 实算全表（2026-08-28 ET，13 个靶子 × 5 个连续 300 s 窗，单位 /s）**

| 靶子 | 观测臂 | min | 均值 | max | 五窗实测（新→旧） | R1 |
| --- | --- | ---: | ---: | ---: | --- | :---: |
| `checkout` | 自身 server span 速率 | 0.0433 | **0.0567** | 0.0633 | 0.0433, 0.0633, 0.0633, 0.0625, 0.051 | **是** |
| `email` | 自身 server span 速率 | 0.0433 | **0.0567** | 0.0633 | 0.0433, 0.0633, 0.0633, 0.0625, 0.051 | **是** |
| `payment` | 自身 server span 速率 | 0.0433 | **0.0567** | 0.0633 | 0.0433, 0.0633, 0.0633, 0.0625, 0.051 | **是** |
| `quote` | 自身 server span 速率 | 0.0933 | **0.1087** | 0.1233 | 0.0933, 0.1233, 0.1067, 0.1133, 0.1067 | — |
| `shipping` | 自身 server span 速率 | 0.1367 | **0.1631** | 0.1867 | 0.1367, 0.1867, 0.1733, 0.1719, 0.1467 | — |
| `ad` | 自身 server span 速率 | 0.1733 | **0.2067** | 0.2267 | 0.2067, 0.2, 0.1733, 0.2267, 0.2267 | — |
| `recommendation` | 自身 server span 速率 | 0.4033 | **0.4127** | 0.4233 | 0.4167, 0.41, 0.41, 0.4033, 0.4233 | — |
| `currency` | 自身 server span 速率 | 0.45 | **0.4947** | 0.54 | 0.49, 0.52, 0.54, 0.45, 0.4733 | — |
| `cart` | 自身 server span 速率 | 0.9067 | **0.9513** | 1.06 | 0.9067, 1.06, 0.9433, 0.9133, 0.9333 | — |
| `valkey-cart` | 被调边速率（无 SDK） | 1.3033 | **1.43** | 1.7067 | 1.3033, 1.7067, 1.4033, 1.4033, 1.3333 | — |
| `astronomy-db` | 被调边速率（无 SDK） | 2.8467 | **2.934** | 3.0833 | 2.8467, 3.0833, 2.9467, 2.9433, 2.85 | — |
| `product-catalog` | 自身 server span 速率 | 2.7933 | **2.9347** | 3.1 | 2.7933, 3.1, 2.97, 2.9267, 2.8833 | — |
| `frontend` | 自身 server span 速率 | 7.7033 | **8.1427** | 8.71 | 7.78, 8.71, 7.7033, 8.1467, 8.3733 | — |

判据取**最小窗速率 < 0.05 /s**，而不是均值：三张卡的均值 0.0567 恰好落在线的另一侧，
而它们的最小窗 0.0433 落在线内 —— **横跨判据线本身就是要拉长窗口的理由**。
批次 2 运行时实测这三个靶子为 0.04 /s（`checkout` / `email`）与 0.07–0.083 /s（`payment`），
同样横跨。`quote` 最小窗 0.0933 是名单外最接近的一个，120 s 窗期望约 11 次调用，暂不入名单。
原始数据：`artifacts/baseline_audit/r1_baseline_5x300s_2026-08-28.json`。

**为什么**

- **R1**：`checkout` / `email` / `payment` 的 120 s 注入窗期望调用数约 **6.8**，而两条判据
  地板（`N ≥ 5`、`基线速率 × inject_s ≥ 5`）就压在这个数上。批次 2 实测 `checkout`
  0.04 /s → 期望 **4.8**，`crash-checkout-01` 报错 span **4 / 门槛 5** 差一条、
  靶子侧臂因 4.8 < 5 判「不适用」，**两臂同时因样本量失效**。`inject_s=300` 把期望
  抬到约 **17**，两条地板都有了余量。这正是 O-P2-18 (1) 与 O-P2-19 要的东西。
- **R2**：这两个靶子的故障被客户端**吞成了两种完全不同的形状**，共同点不是「报错多」，
  是「这条边不再正常工作」—— 要么慢得离谱，要么干脆没了。故按这两件事各设一臂：
  - `valkey-cart` blackhole：p50 0.49 → **5702 ms**（约 11 600 倍）但**零报错**，
    span 数只掉到 36%。→ **台阶臂**拿下（回放实测 `step_arm_pass=True`）。
  - `astronomy-db` crash：**fail-fast**，注入 +0.52 s 内 2 条 `driver: bad connection`，
    p50 **0.32 ms 低于**基线 1.31 ms，此后 **119.5 s 静默**（期望约 314 条实收 2 条）。
    报错数只有 2 而 `N = 79`。→ **边静默臂**拿下（回放实测 `silence_arm_pass=True`）。

  两条形态成对记入 [fingerprints.md](fingerprints.md)。门槛取值：1000 ms 远高于全部靶子
  实测的正常边耗时（最大 p50 40.96 ms），且低于两个已知台阶；100× 是给低基线边
  （`valkey-cart` 0.49 ms）留的相对口子，绝对臂对它要 2000 倍才够。
- **R3**：查证结论是**上游边存在且好用**，只是命名不对。`frontend-proxy` 是 Envoy，
  不发 OTel semconv 的 peer 标签，只发 `upstream_cluster`（实测取值精确等于服务名）。
  它是**唯一**打到 `frontend` 的上游，900 s 内 `router frontend egress` **1662 条 = 1.85 /s**，
  p50 **5.63 ms** / p95 28.03 ms —— 800 ms 注入是约 **140 倍**台阶，判据分辨率绰绰有余。
  扩键之后实测 `frontend` 的 `caller_spans` 由 **0** 变 **3714**（p50 5.6 ms），
  其余 12 个靶子的边一条没变。
- **规则 7**：`latency-checkout-800` 是 O-P2-17 最后一张 `no_alert`。延迟注在出口、
  落在调用方边上，而调用方 `frontend` 的**服务级** p95 把上百个快操作平均掉了。
  按方法分组后离线重检**由 0 条转为 1 条**：
  `frontend/POST /api/checkout` **87.5 → 990.0 ms（+902.5）**。这与规则 5 对错误数
  做的是同一个动作。

**放弃了什么**

- **latency 加「靶子自身 SERVER span p50 侧臂」。** 放弃 —— **实测证否**。
  `delay_outbound` 按设计只延迟**从服务端口发出的响应包**（决策 007 / 原语 §3），
  netem 排队发生在应用写完响应之后，靶子自己的 server span 量不到这段。
  四张已通过的 latency 卡实测靶子自身 p95 位移 **+0.3 / +0.0 / +0.0 / +0.0 ms**，
  而调用方 p95 位移 **1922 ~ 4727 ms**。该侧臂在任何门槛下都不会命中，落码等于加一段死代码。
- **`payment` / `checkout` 不作 crash / blackhole 靶子**（O-P2-18 的出路 (b)）。
  放弃 —— 拉长注入窗（出路 (a)）代价可算且可控：22 张卡各多 180 s，合计约 **1.1 h**，
  换回 6 张结构上本来不可能通过的卡。靶子多样性是难度轴 A 的一部分，不该用来抵机器时间。
- **规则 7 比例臂不设绝对地板（严格照抄规则 3）。** 放弃 —— 实测会造噪声。
  首次重检产出 46 条规则 7 告警，其中 **16 条 Δ < 100 ms**，全是 6–36 ms 基线的
  frontend 快路由的抖动翻倍。更糟的是**三张卡因此从诚实的 `no_alert` 翻成「有告警但指错人」**：
  `crash-email-01` 的唯一告警是 `frontend/GET /api/cart 6.0 → 16.0 ms`，而真因是
  email 容器被 kill。对评测集来说这比安静更有害。规则 1 / 2 / 5 本来就是
  「相对条件 + 绝对地板」双条件，规则 3 是唯一的例外且靠服务级基线够大侥幸成立；
  给规则 7 加地板是**回到仓库既有形状**，不是发明新东西。加地板后 46 → **30** 条，
  被剔的 16 条 Δ 全部 < 45 ms，`latency-checkout-800` 的 +902 ms 不受影响。
- **`misconfig-ad-on` 改阈值救回来。** 放弃 —— 规则 5 的 `N` 是该方法调用数的 25%，
  而 `adFailure` 只让 **10%** 的 `GetAds` 失败，**结构上不可达**，无论流量多大。
  按第 7 条出库，成因记入 findings。

**trade-off**

- **机器时间**：R1 的 22 张各 +180 s（约 +1.1 h），cartFailure 3 张各 +150 s
  （settle 150 → 300，约 +7.5 min）。相对 68 卡的总墙钟可接受。
- **`cycle` 与 `production` 可能不一致**（第 3 条）。代价是读卡时必须知道看哪个字段；
  收益是不用为「已跑过的卡」在 recipe 里维护一份历史窗口。
- **`symptom` 的臂数增至四条**（调用方边 / 靶子侧 / 台阶 / 边静默），`probes.json`
  逐臂记 `applicable` 与 `pass`，**不适用**与**不通过**始终分开记 —— 臂多了之后
  这条纪律比之前更要紧，否则失败原因会糊成一团。
- **规则 7 让告警总数从 91 涨到 121**（24 卡合计）。多出来的 30 条里绝大多数落在
  `frontend` / `frontend-proxy` 的具体路由上，比服务级告警更接近可行动的信息；
  但 agent 侧的输入变长了，`task.json` 的噪声比需要在评测中复核。

---

## 024 agent 与评测 harness v1：六工具窄接口 + submit 作工具 + 四道保险，开发集 19 张显式清单（2026-08-28 ET）

**选了什么**

1. **六个只读工具 + `submit` 第七工具。** agent 的全部动作面是
   `logs_search` / `metrics_query` / `traces_query` / `config_diff` / `topology` /
   `alerts`，参数与返回都走 JSON schema；**交卷本身也是一次工具调用**
   `submit(service, fault_type)`，调用即终止循环。五类 `fault_type` 与 16 项
   `service` 直接写进 `submit` 的 enum（fault_schema §2 / §3）。
2. **返回是聚合，不是文件。** 每张证据包约 10 MB（9 k 条日志、27 k 条 span、
   205 条指标序列）。`traces_query` 只回 per-`(service, operation)` 的计数 / 报错数 /
   p50 / p95 加 ≤ 20 条样本 span，**永不回全量**；`logs_search` 回按服务与等级分组的
   命中数加尾部 ≤ 200 条（body 截 300 字）；`metrics_query` 默认回 summary，
   要序列才降采样回 ≤ 40 点。上限集中在 `config.yaml` 的 `tools` 段。
3. **手写 function-calling 循环，不用 SDK 的 tool runner。** 四道保险各需要一个
   runner 不暴露的钩子，各自计数入结果：
   - **参数校验拦截**：非法服务名 / 非法 metric / 未知参数名**不执行**，错误信息回喂；
     服务名的合法集是**该证据包里实际出现过的服务**（27 项），不是 16 项 `target_enum` ——
     `load-generator` 不是合法答案但是合法查询对象，按 enum 拒会造成假拦截。
   - **工具重试**：工具实现内部抛异常时有限次重试（默认 2）。`ToolError`（参数问题）
     **不重试** —— 同样的参数重跑必然同样失败。
   - **步数熔断**：默认 20 步，超限强制终止判错。
   - **单卡成本熔断**：每次响应后按 `prices.yaml` 折美元累计，超 `max_usd_per_card` 即停。
4. **判分与执行分家。** `run_agent.py` **不 import `scripts/scenarios/cards.py`**，
   结构上看不到答案，因此也无法判分；`run_eval.py` 是唯一读 `scenarios/` 的一侧，
   从 transcript 判分。四指标：top-1（`(service, fault_type)` 双匹配，决策 006）、
   service-only（参考列）、平均步数、单卡平均美元成本、p95 端到端延迟。
5. **开发集 19 张，显式清单写死进 `config.yaml`，不做运行时扫描。**
   筛选条件三条：`production.probe.verdict == passed`、五件证据齐、**不在重跑批
   `rerun1_20260828T185549Z` 的 12 张名单内**。25 张 passed 且五件齐，其中 6 张
   （`crash-astronomy-db-01` / `crash-checkout-01` / `crash-email-01` /
   `misconfig-cart-75` / `misconfig-payment-50` / `misconfig-payment-75`）正被该批次
   改写，25 − 6 = **19**。
6. **泄漏自检 fail-closed，卡在首个 API 请求之前。** 检四条：无 `FORBIDDEN_KEYS` 键名、
   `card_id` 不出现、系统 prompt 与工具 schema 与「手上没有卡时」产出的字节完全相同、
   用户轮是 `task.json` 的真子集。`scripts/agent/leak_check.py` 同时是
   `tests/test_agent_no_leak.py` 的被测体。

**为什么**

- **`card_id` 是此前没堵上的泄漏面。** `task_view.py` 的白名单包含 `card_id`（管线按它
  寻址证据包，`tests/test_no_leak.py` 查的是**键名**不是值），而 `crash-cart-01`
  这个取值**字面写着 `(cart, crash)` 两半答案**。fault_schema §2 定义 agent 的输入是
  trigger + window + symptom，本就不含它，所以 agent 只收 `trigger` 与
  `agent_visible_symptom`，并把「`card_id` 不得出现在请求字节中」做成硬断言。
  这是全套检查里唯一**按值**查的一条，因为这里泄的是值不是键。
- **判分方不能是执行方。** 准确率数字值钱的前提是 agent 拿不到答案；把判分放进
  `run_agent.py` 只需一次手滑就能把 ground_truth 读进同一个进程。
- **清单写死，是为了让「开发集」这四个字在两次评测之间意义不变。** 运行时扫描下，
  一个批次在两次 eval 之间落地就会悄悄改变卡集，两份 report 不再可比。
- **缓存净省约 37%。** 系统 prompt + 工具 schema 每步一字不变，历史只在尾部增长，
  顶层自动缓存从第二步起整段命中。19 卡合计缓存读 469 k token（$0.20/M）对
  缓存写 209 k（$2.50/M）—— 按基础输入价 $2.00/M 折算，净省约 $0.42。

**实测（`artifacts/agent_runs/devset_20260828/report.md`，claude-sonnet-5，effort=high）**

| 指标 | 数值 |
| --- | --- |
| top-1 准确率 | **52.6%**（10/19） |
| service-only 准确率 | **84.2%**（16/19） |
| 平均诊断步数 | 4.42 |
| 单卡平均成本 | **$0.0563** |
| p95 端到端延迟 | 74.0 s |

19 卡合计 $1.0698，墙钟 517 s。四道保险：参数校验拦截 1 次，工具重试 0，步数熔断 0，
成本熔断 0，19 张全部正常 `submit`。

**放弃了什么**

- **SDK 的 tool runner。** 放弃 —— 四道保险要的每一个钩子它都不暴露：拦截要在执行**之前**
  介入并回喂、重试要区分「参数错」与「工具坏」、两个熔断要在每次响应后读 usage。
  在 runner 外面再包一层判断，比直接写 `while` 更绕。
- **`submit` 做成「最后一轮自由文本 + 正则解析」。** 放弃 —— 那样答案格式要靠 prompt 约束
  并在解析侧兜底，enum 违规只能事后发现。做成工具后 schema 直接把答案空间钉死，
  违规在 `validate_submit` 处被拦下并回喂，还能自然地终止循环。
- **按 16 项 `target_enum` 校验查询工具的服务名。** 放弃 —— 见上文，会把
  `load-generator` / `frontend-web` 这类合法查询对象误判为非法。
- **给 agent 一个「读文件」式的宽工具。** 放弃 —— 单张卡 10 MB，第一步就把上下文吃满，
  之后每一步都为同一堆字节反复付钱。

**trade-off**

- **fault_type 分类是短板，不是定位。** service 定位 84.2%，双匹配只有 52.6% ——
  9 张错卡里 **6 张服务对、类别错**。集中在两个方向：
  - **crash → blackhole**：4 张 crash 卡错了 3 张（`crash-cart-01` /
    `crash-currency-01` / `crash-frontend-01`）。这正是决策 010 划的「吵 vs 哑」分界线，
    而证据包是 **harvest 视角**（决策 016：blackhole 撤除后积压回放会把注入窗填满），
    agent 看到的两类形态比 immediate 视角下接近得多。
  - **mem_leak → latency**：2 张全错。`container_memory_mib` 在工具面里，但系统 prompt
    没有把「先查内存曲线」写成 mem_leak 的必经步骤。
  两条都是**证据面 / prompt 的问题，不是 agent 循环的问题**，留作 findings 与下一轮迭代。
- **单卡 $0.0563 意味着全集 68 卡主模型约 $3.6**，加两条 LLM 基线约 $6.6。本轮按用户裁决
  保持 `effort=high` 不降档、以追加余额覆盖。`effort` 是最直接的成本旋钮
  （输出 token 占单卡成本 26–51%），下调会同时动准确率，不该在没有基线对照时先动。
- **19 张的开发集偏小**，单张卡的对错就值 5.3 个百分点；这些数字是**基线**不是结论。
  重跑批结束后 6 张回归、加上其余卡量产，卡集会变，届时 `config.yaml` 的清单要显式改一次。

---

## 025 agent prompt 一次迭代纪律与冻结：v2 定版，top-1 52.6% → 57.9%（2026-08-28 ET）

**选了什么**

1. **系统 prompt 加两段诊断方法论，一次性改完，一轮定生死。** 改动只动
   `scripts/agent/prompts.py` 的 `SYSTEM_PROMPT` 常量，不动工具面、不动判分、
   不动证据包。两段各针对决策 024 trade-off 里点名的一种错法：
   - **资源类先查内存曲线**：慢而不报错、config diff 为空的服务**还没有**被证明是
     latency —— 内存泄漏正是这个样子。要求先拉 `container_memory_mib`
     看是否单调上涨，看过曲线再下 latency 或 mem_leak 的结论。
   - **crash vs blackhole 看故障头几秒的形态，不看最后是否静默**：连接类硬报错
     （connection refused / 地址不可达 / bad connection）在注入后一秒内出现、
     随后边静默 → 进程没了；报错**条数可以很少**（调用方退避后不再产生流量），
     且失败调用的耗时**低于**基线（fail-fast 比成功还快）。
   - 附带第三段 **MIND THE WINDOW**：证据包两侧有 padding，撤除后的健康流量会
     稀释注入窗形态，判形前要把查询限到 symptom 给的注入窗。
2. **一次迭代纪律：本轮之后冻结 prompt。** 判据在动手前定死 —— **top-1 变好则 v2 定版，
   变差则回滚 v1 并记 finding**。不做"再调一版看看"，因为 19 张卡上单张就值 5.3 个
   百分点，多轮微调等于在噪声上拟合。
3. **裁定：v2 定版。** top-1 **52.6% → 57.9%**（10/19 → 11/19，+5.3）。

**实测（`devset_20260828` → `devset_v2_20260828`，同模型同卡集同参数）**

| 指标 | v1 | v2 | Δ |
| --- | ---: | ---: | ---: |
| top-1 准确率 | 52.6% | **57.9%** | **+5.3** |
| service-only（参考列） | 84.2% | 78.9% | −5.3 |
| 平均诊断步数 | 4.42 | 5.11 | +0.68 |
| 单卡平均成本 | $0.0563 | $0.0676 | +$0.0113 |
| 合计 | $1.0698 | $1.2851 | +$0.2153 |

按真值类别拆开，**两段方法论各自命中了它要打的靶**，代价出在第三个类别上：

| 类别 | v1 | v2 | |
| --- | ---: | ---: | --- |
| `crash` | 0/4 | **2/4** | ↑ 判别知识生效 |
| `mem_leak` | 0/2 | **1/2** | ↑ 内存曲线生效 |
| `blackhole` | 3/4 | **1/4** | ↓ **副作用，见下** |
| `latency` | 4/5 | 4/5 | = |
| `misconfig` | 3/4 | 3/4 | = |

翻盘 4 张（`crash-currency-01`、`crash-frontend-01`、`latency-email-800`、
`memleak-email-1000x`），倒退 3 张（`blackhole-cart-01`、`blackhole-frontend-01`、
`latency-product-catalog-800`）。

**为什么**

- **两个目标类别都动了，方向对。** 决策 024 的 trade-off 把 9 张错卡归成两条
  ——「crash → blackhole」4 张里错 3 张、「mem_leak → latency」2 张全错。
  这两条正是本轮两段方法论的靶子，实测 crash 0/4 → 2/4、mem_leak 0/2 → 1/2，
  **命中的是点名的那两条**，不是碰巧在别处涨了。
- **blackhole 掉 2 张是本轮写坏的一段话，不是知识注入本身的代价。** 见 [F-5](open_items.md)：
  我把 `valkey-cart` 那种**长连接靶子**的「超时台阶」形态写成了 blackhole 的**通例**，
  而 fingerprints.md 归档的通例是「0 span、0 报错、纯静默」（「哑」），
  台阶形态只对有客户端超时的无 SDK 靶子成立。prompt 里那句「p50 抬升到秒级」
  与 latency 的描述几乎同形，两张普通 SDK 靶子的 blackhole 卡因此被判成 latency。
  **这段话违反了本轮自己定的「素材须与 fingerprints.md 一致」前提。**

**放弃了什么**

- **借「blackhole 段写错了」再改一版。** 放弃 —— 一次迭代纪律是**在看到结果之前**
  定的，看到结果之后为了一个更好看的数字给自己开例外，纪律就不存在了。
  改法本身很清楚（把台阶形态降级为长连接靶子的例外、通例回到「哑」），
  但它属于**下一轮**，且下一轮该在更大的卡集上做 —— 见下。
- **拿 service-only 掉 5.3 当否决理由。** 放弃 —— 判据在动手前就写明看 top-1，
  service-only 在决策 024 里本来就标着「参考列」。两个指标反向动是因为
  `blackhole-cart-01` 从 `cart` 滑到了邻居 `valkey-cart`（服务错了一格），
  而 `latency-email-800` 从 `checkout` 回到了 `email`（服务对了一格）。

**trade-off**

- **+5.3 个百分点 = 净 +1 张卡，落在噪声里。** 4 翻盘 3 倒退，19 张卡上单张值 5.3 点。
  **不能说 v2「更好」，只能说 v2「不更差，且两个目标类别确有改善」。**
  定版是因为判据是这么写的，不是因为证据强。真正的判据要等卡集变大 ——
  第三批 16 张跑完后在库预计 38–43 张，届时开发集该重新划一次并重测。
- **步数与成本各涨约 15% / 20%。** 多出来的步用在内存曲线与窗口内复查上，
  是方法论要求的动作，属于预期支出而非浪费。
- **prompt 现在冻结。** 下一次改动的触发条件写死：**卡集扩大后重测一次 v2 基线**，
  在新基线上再谈第三版；F-5 那段修正一并进第三版，不单独发。

---

## 026 两条基线与三方同集可比：规则 70.4% / 单轮 LLM 40.7% / agent 59.3%（2026-08-28 ET）

**选了什么**

1. **基线①「关键词启发式」：规则通用性是硬约束。** `scripts/baselines/keyword_heuristic.py`
   读与 agent 同一证据面（`EvidencePack`，结构上只能看见 `evidence/<card>/`），
   零 API 调用。三条约束写死：
   - **不得有任何 card_id 特判** —— 文件里唯一出现的 card_id 在用法示例的 docstring 里；
   - **阈值一律取 harness 既有常数**，不新调数：静默天花板 `0.1`、mem_leak 地板
     `max(10 MiB, 0.15 × 起始)`、检测器规则 7 的 `2×` 与 `100 ms`、决策 023 台阶臂的 `1000 ms`；
   - **规则按故障形态写，不按靶子写**：`OBSERVER_SERVICES` 只用来给告警权重打折，
     两个入口服务仍是合法答案（`blackhole-frontend-01` 就正确答了 `frontend`）。
2. **基线②「单轮无工具 LLM」：摘要规则固定是硬约束。** `summarise.py` 的**形状对每张卡完全一致** ——
   同样的小节、同样的顺序、同样的截取上限，**不因证据长什么样而分支**。
   这是防止这条臂偷偷变成「一个知道答案的摘要器」的唯一办法：
   摘要一旦能对不同卡给不同待遇，比较就没有意义了。
   告警取自 `task.json` 的 `agent_visible_symptom`，**不读 `alerts.json`** —— 后者带 `card_id`。
   泄漏断言复用 `leak_check` 的 `FORBIDDEN_KEYS` 与按值查 `card_id` 两条；
   `check_payload` 本身不适用（它有两条检查把请求钉在 agent 的固定 prompt 与工具 schema 上）。
3. **三方同集可比原则。** 三臂用**同一 27 张卡、同一判分器 `run_eval.grade`、同一答案空间**。
   卡单冻结在 `scripts/baselines/cardset_27.json`，第三批产出的卡**不纳入** ——
   批次运行期间在库卡数在变，不冻结就没有「同一集合」可言。
   主 agent 补跑了 27 张里不在开发集 19 张中的 8 张（`devset_v2_extra8_20260828`），
   与 `devset_v2_20260828` 合并成 27 张的完整一行。
   基线①②的**步数按 1 计**而不是留空：两者结构上没有调查能力，
   写 1 是事实，留空会被读成「未知」。

**实测（`artifacts/agent_runs/baselines_20260828/report.md`）**

| 臂 | top-1 | service-only | 平均步数 | 单卡成本 | p95 | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基线① 规则 | **70.4%**（19/27） | 77.8% | 1.00 | $0 | 0.3 s | $0 |
| 基线② 单轮 LLM | **40.7%**（11/27） | 66.7% | 1.00 | $0.0241 | 38.9 s | $0.65 |
| 主 agent v2 | **59.3%**（16/27） | **81.5%** | 5.52 | $0.0746 | 90.7 s | $2.01 |

**为什么这三个数字放在一起才有意义**

- **规则臂赢 top-1、输 service-only。** 工具循环买到的是**定位**（81.5% vs 77.8%），
  没买到**机制判断**。agent 多花 5.5 步、贵 3 倍，类别判断反被确定性阈值压过 ——
  规则在延迟 **5/5**、内存泄漏 **2/2**，这两类的判据本来就能写成数值阈值，
  而两个 LLM 臂在同样的数据上把内存泄漏读成延迟。
- **去掉循环掉 18.5 个点**（59.3% → 40.7%，同模型同答案空间）。
  **循环是有价值的**，短板不在「能不能查」，在「查完怎么判」。
- **错配三臂全部 5/7，错的还是同两张** —— 难点在证据面不在推理。

**放弃了什么**

- **把基线①调到更高。** 放弃 —— 已经迭代到 70.4% 时停手。再往上调只能靠贴着这 27 张卡的
  失败模式改结构，那是在评测集上拟合，会把基线本身变成不可信的参照物。
- **让基线②也能翻证据。** 放弃 —— 那就不是基线②了，它存在的全部意义是**去掉循环**这一个变量。

**trade-off**

- **基线①的 70.4% 是乐观的，必须标注。** 规则结构是对着这 27 张卡的失败模式设计的
  （走图方向、starved/broken 的分法都是迭代出来的），而 v2 prompt 在看到本卡集结果之前就冻结了。
  **两个数字不是完全对等的比较。** 阈值虽然全部沿用既有常数、也没有 card_id 特判，
  但「见过这批卡」这件事本身就值几个百分点。
  诚实的对照要在**没见过的卡**上做 —— 第三批产出的 5 张是现成留出集，下一轮三臂在那上面复测。
- **规则臂便宜到可以随时跑**（$0、0.3 s/卡），因此它应当成为**每次改动的回归基线**：
  agent 的任何一版如果打不过它，说明这一版没有在用工具做规则做不到的事。

---

## 027 F-2/F-3/F-6 判据落码；留出集量出「见过卡」值 10 个百分点（2026-08-28 ET）

**选了什么**

1. **F-2：recover 窗按靶子实测速率算，走 R1 的同一条路。**
   `recover_s ≥ 30 + 5 / 速率`，速率取五窗**最小**值、向上取整到 10、上限 300；
   由 `scripts/scenarios/recover_window.py` 算出并写进 `recipe.csv` 的
   `cycle_override`，**不另起第二条覆盖路径**（决策 023 第 2 条同款）。
   落到 `checkout`/`email`/`payment` **150 s**、`quote` **90 s**、`shipping` **70 s**，共 34 张卡。
   **F-4 搭同一班车但理由不同**：`valkey-cart` 默认窗期望 39 次调用，样本量从不是问题，
   它的 150 s 是**待测量的下限**（重连 >30 s，具体多久不知道）。
2. **F-3：`errors_not_up` 改成报错占比上限 10%。** 落码前先在**全部在库 latency 与 misconfig 卡**
   上模拟了两个候选：

   | 方案 | latency 放行 | misconfig 误放 |
   | --- | ---: | ---: |
   | 现状（报错不许涨） | 5/8 | 0/7 |
   | 删掉该合取项 | **7/8** | **0/7** |
   | 报错占比 <10% 不否决 | **7/8** | **0/7** |

   **两方案区分度完全相同**，按既定裁决取更保守的占比上限。
   两个数据点：**800 档 1.45%**（7/484）、**3000 档 8.70%**（2/23）。
   区分度本来就不由这个合取项提供 —— 它由 p50 门提供，
   七张 misconfig 卡的位移是 0.5 ms 或无定义，离 640 ms 门槛差两个数量级。
3. **F-6：`latency-payment-800` 既不是「加窗能救」也不是「物理不可判」。**
   任务给的二选一**前提不成立**：数据一直在 Jaeger 里（证据包里就有 47 条
   `checkout → payment` 父子边），是**探针没去找** ——
   `checkout` 的 gRPC client span 把 peer 写成容器 IP、HTTP 的写服务名，
   而匹配是 `peer 字符串 == 服务名`。修法是把 IP 反解成服务名再比，
   **只增不减**，对既有卡单调。修后 `payment` 600 s 收 38 条边（p50 2.78 ms），
   300 s 窗约 19 条，800 ms 注入是约 290 倍台阶 —— **卡不出库，进第四批**。
4. **留出集复测口径。** 三臂原封不动（prompt 与规则一字不改）搬到第三批新产出的
   5 张卡上。这是决策 026 那条方法论警告的兑现。

**实测（留出集 5 卡 vs 规则见过的 27 卡）**

| 臂 | 27 卡 top-1 | 5 卡 top-1 | Δ | 27 卡 service | 5 卡 service | Δ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 基线① 规则 | 70.4% | **60.0%** | **−10.4** | 77.8% | **60.0%** | **−17.8** |
| 基线② 单轮 LLM | 40.7% | 20.0% | −20.7 | 66.7% | 80.0% | +13.3 |
| 主 agent v2 | 59.3% | **60.0%** | **+0.7** | 81.5% | **100.0%** | **+18.5** |

**为什么**

- **「在同一批卡上迭代」这件事本身值约 10 个百分点**，本轮把它量出来了。
  基线①错的两张（`blackhole-astronomy-db-01` 停在 `product-catalog`、
  `blackhole-email-01` 停在 `frontend-proxy`）**都错在服务定位、都错在走图走偏** ——
  而走图正是 27 卡上迭代最多的部分。调得最狠的部分泛化最差，是过拟合的标准形状。
- **agent 的 59.3% 反而可信**：它在两个卡集上都没见过卡，59.3% 与 60.0% 互相印证。
  它在留出集上 service-only **5/5**，比在 27 卡上还高 —— 逐步查证的定位换卡不掉。
- **决策 026 那句话要重述。** 原话是「agent 打不过规则说明它没在用工具做规则做不到的事」。
  按留出集：两者 top-1 **打平**，而 agent 的 service-only **高 40 个百分点**。
  **工具循环买到的定位能力是真的，没买到的是类别判断** —— 这与 026 的结论方向一致，
  但强度要按留出集的数字说，不是按 27 卡的。

**放弃了什么**

- **把 `MIN_EXPECTED` 从 5 悄悄抬到 10。** 放弃 —— 5 是任务指定的、也是 symptom 侧现有的地板，
  两侧保持一致比多救一张卡重要。代价写在明处：λ=5 时 recovered 仍有 **12.5%** 假失败率，
  且 `ad` 期望 5.20 **恰好在线上、拿不到加窗**，假失败率 10.9%
  —— `blackhole-ad-01` 第三批就是这么挂的，第四批它若再挂，再抬地板。
- **为了让基线①在留出集上好看而改规则。** 放弃 —— 那就把留出集烧了。
  规则冻结，60.0% 就是它的诚实口径。

**trade-off**

- **留出集只有 5 张**，单张 20 个百分点，三臂差值都不显著。
  本条能支撑的只有「规则臂在没见过的卡上明显变差」这一个**方向性**判断，
  它靠的是掉分方向与两张错卡**错法一致**（都在定位、都在走图），不是靠百分比。
- **F-6 暴露的是一类 bug 而不是一个 bug。** `caller_all_dur.p50 = null` 被读成
  「流量太低」已经两次（批次 2 的 frontend、批次 3 的 payment），
  上一次（R3）的修法是「加 Envoy 的命名键」—— 同一类问题**一次修一个键**。
  判据取不到分子时应先分清**分子为零**还是**分子没被计算**，两者处置相反。

---

## 028 CI 三道离线门；None-vs-zero 全路径审计；findings 成稿（2026-08-28 ET）

**选了什么**

1. **CI 三道门，全部离线。** `push` 与 `pull_request` 触发，依赖只有 `pytest` 与 `PyYAML`：
   - **门 1 `pytest`**（12 项）—— 泄漏测试是其中最要紧的，它守的是「卡的答案不得进模型输入」，
     而 docs/ 里每一个准确率数字的可引用性都建立在这条上；
   - **门 2 `generate.py --check`** —— 卡由 `recipe.csv` 生成，手改 yaml 会被下次生成器静默覆盖。
     这道门是「配方是唯一事实源」（决策 023 第 2 条）**是规则还是愿望**的分界；
   - **门 3 干净窗负对照** —— 两份观察窗在规则 1–7 下必须 0 告警。
   **不得依赖 VM、实时 Prometheus/Jaeger 或 Anthropic API** —— CI 必须只靠一次 checkout 就能跑，
   否则它不是门禁，只是另一个坏掉的东西。
2. **门 3 的数据取舍：固化 fixture，而不是退到 pre-push 钩子。**
   真负对照每份带 8 MB `traces.json`，被 `.gitignore` 挡在库外（生成物不是源码）。
   两个选项里选固化，**因为这里的裁剪恰好是精确的而不是抽样**：
   - `metrics.json` 逐字节复制（0.33 MB），规则 1–5 与 7 只读它；
   - `traces.json` 只留**带 `demo.<entity>.id` 标签**的 span、且只留规则 6 实际读的四个字段
     —— 规则 6 跳过一切无标签 span，因此丢弃它们**不可能**改变任何规则的输出。
     29910 → 0 条、29718 → 1347 条；
   - 合计 **896 KB**。
   等价性**不是断言而是复验**：对全量包与 fixture 各跑一次 `detect()`，输出除时间戳外逐字节相同；
   `tools/refresh_clean_fixtures.py` 每次重建都重跑这个比较，不一致就拒绝写。
   **pre-push 钩子被放弃**：它只在一台机器上生效、`--no-verify` 可绕过，
   而它要守的恰恰是「没人预料到自己造成了」的那类回归。
3. **None-vs-zero 全路径审计（只读，未改码）**，产出 `docs/probe_audit.md`：
   25 条判据路径，**正确 11 / 可接受 9 / 错误 5**。
4. **`docs/findings.md` 成稿**，六节；批次四相关数字留 8 处 `TBD`，
   结论方向不依赖它们。`open_items.md` 的 F 系列底稿**保留不迁走**（用途不同）。

**为什么做审计**

「分子取不到」被当成「分子是零」已经两次，**都花了整整一轮才定位**：
R3（批次 2，`frontend`，`PEER_KEYS` 不认 Envoy 命名）、F-6（批次 3，`payment`，gRPC peer 是 IP）。
**两次是同一类 bug，而 R3 的修法是「再加两个命名键」** —— 一次修一个键，
没有回头问「还有多少地方在这么干」。本条是那次没做的回头看。

**审计结论（五处语义错误，全部标记待裁）**

四处是「取不到 → 判失败」（代价：重跑一张卡）：crash 调用方臂、
latency 臂（`p50=null` 与「位移不够」不可分辨，**正是 R3 与 F-6 两次踩的那一格**）、
misconfig 臂、recovered 的 `caller_spans_total`。

**一处是「取不到 → 判通过」，应优先裁决**：
`judge_recovered` 的 misconfig 分支 `got = ase.get("server_error_spans") or 0; return got == 0`
—— trace 采集整体失败即判「恢复窗零报错」放行。
**复验**：同一场故障下 misconfig 恢复门返回 `True`、crash 症状门返回 `False`；
两者都不该给出裁决，现在一个通过一个失败。
其余四处只让卡白跑一遍，**这一处让一张证据不实的卡进库，而入库后没有任何环节会回头查它**。

**正确样板已经在仓库里**：`target_side_arm` 返回
`(None, {"applicable": False, "why": ...})`，docstring 明写「否则又是一个静默的零分母」。

**放弃了什么**

- **在本轮顺手把五处改掉。** 放弃 —— 批次四运行中，判据冻结（红线）。
  且**根治不是照抄五遍**：要在**采集侧**分开「查过但没有」与「根本没被查」
  （建议给 summary 加 `caller_edges_queried` 与 `caller_match_method`），
  否则下一个没见过的 peer 写法还会再来一次。这是一个设计改动，不该塞进收尾。
- **CI 里跑 agent 评测。** 放弃 —— 要 API key、要花钱、结果还带随机性，
  门禁必须是确定性的。评测由人按轮次触发。

**trade-off**

- **fixture 会过期。** 若干净窗重新采集而没跑 `refresh_clean_fixtures.py`，
  门 3 守的就是旧数据。缓解：刷新脚本自带等价性复验；
  代价是这条约束只写在文档和脚本里，CI 无法自动发现 fixture 与源包脱节。
- **门 1 的 12 项里没有一项覆盖 runner 的判据逻辑。** 判据的回归靠的是「改动前后
  在全部在库卡上回放」（决策 027 的 F-3 就是这么做的），这是人工触发的，
  不在 CI 里。把回放做成门需要把窗口快照也固化成 fixture，本轮没做。

---

## 029 七处 None-vs-zero 落码＋采集溯源；追溯核查零命中；valkey-cart 出库（2026-08-29 ET）

**选了什么**

1. **七处「取不到当成零」全部照 `target_side_arm` 样板修**：取不到一律返回
   `(None, {"applicable": False, "why": ...})`，**不给裁决**。
   五处来自决策 028 的审计（crash 调用方臂、blackhole 调用方臂、misconfig 症状臂、
   recovered 的 `caller_spans_total`、**misconfig recovered 假通过**），
   两处由批次四撞出（见下）。`judge_symptom` 三臂全部不适用时返回 `None` 而非 `False`：
   **算不了的臂不算失败的臂**。
2. **采集侧溯源三字段**：`caller_edges_queried`（实际找到边的调用方）、
   `caller_match_method`（`name` / `ip_resolved`）、`callers_queried_names`（问过谁）。
   实测立刻见效：`payment` 只靠 **`ip_resolved`** 才可见（F-6 的修法是它可见的唯一原因），
   `email` 走 `name`，`valkey-cart` 显示**问过 17 个调用方、一条边没有** ——
   「问遍了没人调」与「我们没去查」终于可分辨。
3. **`valkey-cart` 四张卡出库**（配方 68 → **64**），见 F-8。
4. **不动 `MIN_EXPECTED`**，理由见下。

**批次四撞出的两条（审计漏掉的）**

- `prom_rate_in_window` 的 `if len(vals) < 2: return 0.0` —— **样本不足写成速率为零**，
  与 F-6 同型只是低一层。改为 `None`（`target_side_arm` 本来就会正确处理 `None`）。
- **`target_side_arm` 不得在短于一个 spanmetrics 导出间隔的窗口上求值。**
  collector 按间隔聚合导出，计数器是**阶梯函数**，两次导出之间恒平，
  在更短的窗口上算速率**恒为 0.0，与流量无关**。

  实测（事后直接回查 Prometheus）：恢复窗开启后计数器
  **ad 平了约 90 秒、recommendation 约 105 秒**才跳，而同期 Jaeger 已见 5 条与 9 条调用方 span。
  **两个数据源直接矛盾，判据信了说「零」的那个。**

**为什么不动 `MIN_EXPECTED`（预授权条件已触发，但诊断不成立）**

预授权是「`blackhole-ad-01` 若再挂 recovered 就把 5 抬到 10，依据是假失败率 10.9% → 6.7%」。
**卡确实再挂了，但挂的机制不是样本量。** 实测该卡在恢复窗里：
调用方臂健康（6 条基线回来 5 条）、速率臂通过（0.1667 ≥ 0.05），
**失败在靶子侧臂把导出滞后读成静默**。同批 `blackhole-recommendation-01` 完全同型
（25 → 9 条、0.3 ≥ 0.2083）。

抬 `MIN_EXPECTED` 到 10 只会**顺带**救 `ad`（期望 6.3 < 10 → 该臂不适用），
**救不了 `recommendation`**（期望 12.6 ≥ 10，臂照样适用、照样读到 0.0）。
而 `MIN_EXPECTED` 同时管注入窗，抬它会在没有证据的情况下改变一批卡的判据行为。
**按错误的机制施加一个只在一张卡上碰巧生效的改动，是在给症状打补丁。**
正确修法是导出间隔守卫，离线回放已证实两张卡都因此翻盘。
**预授权未执行，理由记在此处，最终取舍留给用户。**

**离线回放（47 张有快照的卡，只读重算不重注入）**

**44 张不变、3 张变化**：

| 卡 | 变化 | 解释 |
| --- | --- | --- |
| `blackhole-ad-01` | recovered `False → True` | 导出间隔守卫，预期内 |
| `blackhole-recommendation-01` | recovered `False → True` | 同上 |
| `blackhole-frontend-01` | recovered `True → **None**` | **修法在一张由该 bug 本身产出的包上生效** |

第三张要写清楚：那个包**早于 R3**，所以它的调用方边本来就是空的（正是 PEER_KEYS 不认
Envoy 命名造成的）。在 30 秒恢复窗里，调用方臂因基线边为 0 判不了、
靶子侧臂被新守卫正确拦下、无 SDK 臂不适用于有 SDK 的 frontend ——
**三条臂全部声明不适用，于是判据说「我判不了」**。
修前三条臂各自返回 `False`，合起来被读成「症状已消失」，卡就这么通过了。
按决策 023 第 3 条「已过卡不溯及」**该卡留在库内**，
但**它的 recovered 证据比记录看起来要薄**，此处留档。

**追溯核查：零命中**

41 张在库卡 × 四个窗口快照逐一检查：**无一条 `error` 非空、
无一处 `caller_spans_total` / `self_edges` 缺失**。
重点复查 7 张 misconfig 卡的 after 窗：`self_edges` **全部存在**、
`server_error_spans` 均为**实测的 0** 而非缺失。
**假通过路径从未实际放行任何一张卡** —— 它是一个真实的缺陷，但没有造成既成事实。

**放弃了什么**

- **把 `blackhole-frontend-01` 从库里撤下。** 放弃 —— 决策 023 第 3 条已定「已过卡不溯及」，
  且它的 recovered 另有靶子侧速率证据（9.2 ≥ 4.755）。改判会让「入库」失去稳定含义。
- **顺手把 F-3 的 10% 上限抬一档救 `latency-shipping-3000`。** 放弃 ——
  决策 027 已写明「加档须连同实测重定」，现在两个数据点（8.70% / 12.0%）**恰好骑在线上**，
  这正是要重新测量的信号，不是把线挪开的理由。该卡不进批次五。
- **重启 `cart` 验证 valkey 埋点假设。** 未能执行 —— 探针被权限策略拦下，
  假设留在 F-8 里未验证，重新入库条件已写明。

**trade-off**

- **判据现在会产出第三种结果 `None`（判不了）**，而批次主循环把 `symptom is None`
  记为「判据待定」不计入通过/失败。这是对的，但意味着**未来会出现更多「待定」卡** ——
  尤其是恢复窗仍为 30 秒（`recover_s=60`）的卡。这是把过去的静默假通过/假失败
  换成了显式的「不知道」，**代价是要有人去看这些待定项**。
- **审计本身也会漏。** 25 条是读代码找出来的，2 条是让批次撞出来的。
  代码审计覆盖得了形状，覆盖不了「我没想到这里也是一次测量」——
  `prom_rate_in_window` 看着像工具函数，导出间隔更是**根本没出现在代码里的物理量**。

---

## 030 批次五收批入库 43；valkey-cart 埋点复验后四张卡回配方 68（2026-09-06 ET）

**选了什么**

1. **批次五收批入库**：`blackhole-ad-01`、`blackhole-recommendation-01` 两张
   `verdict: passed` 的卡正式入库，证据包纳入版本库（三件大文件仍按 `.gitignore` 留在库外），
   批次日志按先例落到 `artifacts/batches/`。**在库 41 → 43**。
2. **先验证埋点、再回配方**：2026-09-06 开机后先只读复查 `cart → valkey-cart`
   的 client span，确认 **1848 条 / 约 17 min ≈ 1.7/s** 之后，才把四张 valkey-cart 卡
   放回 `recipe.csv`。**配方 64 → 68**。
3. **回配方的方式是取回原行、重跑生成器**，不是手写 yaml：
   四行原样取自 `git show e678ef5^:scripts/scenarios/recipe.csv`，
   `generate.py` 重生四张卡，`--check` 干净（CI 门 2）。
4. **判据一个字没动。** 四张卡仍用决策 023 的无 SDK 双臂（台阶档 / 边静默档）
   与 valkey 的分位数口径，`recover_s=150` 也照旧。
5. **O-P2-22 不关闭**，状态改为「埋点已恢复，待实测复验」。

**为什么**

**顺序理由（先验证再回配方，不是同时做）**：F-8 把四张卡出库的依据是
「这条边不再被埋点」，并写明了重新入库条件——重启 `cart` 后 span 是否回来。
如果这一步把「回配方」和「验证埋点」合成一次动作，那么批次一旦再失败，
就分不清是埋点没真的恢复、还是 `recover_s=150` 不够——**两个未知数一个方程**。
先用一次只读查询把埋点这个未知数消掉，四张卡回配方之后，批次的失败就只剩
一种解释可查。这也是 F-8 当初把「重新入库条件」写成一句可执行的话的用处：
条件达成与否是查出来的，不是判断出来的。

**为什么不趁 8 天空窗把配方口径悬着**：配方是 `recipe.csv` 一处权威，
`docs/recipe.md` §4 的表由生成器渲染。让在库/配方数字在文档里停留在
「64，但其实条件可能已经达成」的状态，等于让读者拿一个已知可能过期的数字做判断。

**放弃了什么**

- **放弃「顺手把 F-8 关掉」**：埋点为何会在容器重启后静默失效仍未知，
  与 O-P2-10 同族。这次只证明了「再次重启会恢复」，没有证明「不会再失效」。
  F-8 与 O-P2-22 都保持开放。
- **放弃在本步顺手改判据**：四张卡上一次跑是在没有埋点的条件下失败的，
  那批数据对判据没有任何信息量。**在拿到有埋点的第一组实测之前改判据，
  改的是想象中的失败。**
- **放弃把 `docs/recipe.md` §六的历史沿革改写成一句「68」**：
  76 → 69 → 68 → 64 → 68 这条路径本身是证据，压平了就看不出
  哪一次减卡是判据问题、哪一次是观测问题。

**trade-off**

- 四张卡回配方后，**配方 68 与在库 43 的差距重新变成 25 张**，
  v1 的 ≥40 门槛靠的是在库数，不受影响；但「配方覆盖率」这个口径变差了 4 张。
  这是诚实的代价：卡本来就在那儿，之前是观测坏了才拿掉的。
- `docs/recipe.md` §六的统计块此前停在 v1.0 的 76 卡口径（misconfig 14、
  `param_validated=no` 68 张），与生成表长期不一致。本步按 `recipe.csv` 重算成
  68 / misconfig 13 / no 61 / yes 7。**这是顺手修的旧账，不是本步的结论**，
  记在这里以免下次又有人以为那些数字是新的。

---

## 031 第二留出集 11 张定义与三臂复测；规则臂过拟合复现（2026-09-06 ET）

**选了什么**

1. **第二留出集 = 11 张**：批次四入库的 9 张（`blackhole-payment-01`、
   `blackhole-shipping-01`、`crash-payment-01`、`crash-shipping-01`、`latency-ad-800`、
   `latency-astronomy-db-3000`、`latency-payment-800`、`latency-quote-800`、
   `latency-recommendation-3000`）+ 批次五入库的 2 张（`blackhole-ad-01`、
   `blackhole-recommendation-01`）。冻结在 `scripts/baselines/cardset_holdout11.json`，
   **沿用决策 026/027 的 `cardset_*.json` 落法，没有新机制**。
2. **三臂原封不动**：规则一字未改，v2 prompt 一字未改，`config.yaml` 的温度/步数/
   成本熔断一字未改。这是留出集测试的前提，不是本步的选择余地。
3. **主口径是类别对齐后的对照**，不是原始 Δ。理由见下。
4. **三个卡集口径各出一张表**：11 张（第二留出集）、16 张（合并留出集）、
   43 张（全在库，**含开发集 19 张**，只当覆盖面读）。
5. **新失败模式记录为 findings §1.4，本轮不修。**

**为什么取 11 而不是只取 9**

批次五那两张与批次四那九张，**在评测臂眼里没有任何区别**：都是规则写完之后才入库的卡，
三臂都没见过。唯一可能的反对理由是「它们是批次四的失败卡重跑来的」——
但重跑改的是**判据侧**（决策 029 的导出间隔守卫），
**卡本身、证据面、真值一个字没变**，重跑改变的是它们能不能入库，不是它们长什么样。

反过来，取 9 会留下一个更难解释的口径：27 ∪ 5 ∪ 9 = 41，
而在库是 43，**两张卡既在库、又不属于任何一个评测卡集**。取 11 之后
27 ∪ 5 ∪ 11 **恰好等于 43**，无遗漏无重复 —— 全集表因此是真的全集，不是「除了两张之外的全集」。

**为什么主口径必须类别对齐**

两个留出集里**一张 misconfig、一张 mem_leak 都没有**（批次四五收的全是
blackhole / crash / latency），而规则臂在 27 卡上最强的两类正是
`latency` 5/5 与 `mem_leak` 2/2。直接报原始 Δ 会把「卡类构成不同」
整个算进「过拟合」里。把 27 卡集截到同类的 18 张再比，才是同一个东西的两次测量。

**实测**

| 臂 | 27 卡同类 18 张 | 合并留出 16 张 | Δ | 第二留出 11 张 | Δ |
| --- | ---: | ---: | ---: | ---: | ---: |
| 基线① 规则 | 66.7%（12/18） | **50.0%** | **−16.7** | **45.5%** | **−21.2** |
| 基线② 单轮 LLM | 33.3%（6/18） | 56.2% | +22.9 | 72.7% | +39.4 |
| 主 agent v2 | 55.6%（10/18） | **75.0%** | **+19.4** | **81.8%** | **+26.2** |

**结论：规则臂过拟合复现，且比第一留出集测出的更大。** −10.4 是下界不是上界。
而且掉分**不是均匀的** —— 规则臂 `blackhole` 从 27 卡的 **3/6** 掉到两个留出集合计的
**0/6**，`latency`（5/5 → 4/5）与 `crash`（4/7 → 4/5）基本没动。
**过拟合发生在 blackhole 那几条走图规则上**，正是 27 卡上迭代最狠的地方 ——
第一留出集「两张错卡都错在走图」的定性判断，在更大样本上是同一件事。

**基线②是这里最好的难度校正尺。** 它没有任何可过拟合的结构（单轮、无工具、
摘要形状对每张卡固定），所以它在同一批卡上升 22.9 点**只能**由卡集更好判解释。
同一批卡上规则臂掉 16.7 点，两者之差 **39.6 点**才是规则臂过拟合的诚实量级。

**放弃了什么**

- **放弃只报原始 Δ**（规则臂 −24.9 / −20.4）。数字更好看更有冲击力，但它把
  「留出集里没有 misconfig 和 mem_leak」记在了过拟合账上。**留在正文里做参照，
  但结论按类别对齐的数说。**
- **放弃为了让规则臂在留出集上好看而改规则** —— 与决策 027 同一条理由，改了就把留出集烧了。
- **放弃就地修 findings §1.4 那个失败模式。** agent 两次都把「我查过的下游都不慢」
  写成「没有慢的下游」，而 `frontend → ad` 是它第 1 步取回的 topology 里的第一条边、
  两次一次都没查。修法看得见（把「排除下游前先枚举全部出边」写成硬步骤，或给
  `traces_query` 加按调用方展开的形状），但**在留出集测试的同一轮里改 prompt 就没有留出集了**。
  记在 §1.4，留给下一次 prompt 迭代。
- **放弃把 43 卡表当泛化结论。** 那张表里三臂都带着各自的「见过」优势
  （规则臂见过 27 张、v2 prompt 在其中 19 张上迭代过），只能当覆盖面读。

**trade-off**

- **留出集的类别覆盖是偏的**：16 张全是 blackhole / crash / latency。
  规则臂在 misconfig 与 mem_leak 上是否也过拟合，**本轮没有测、也测不了** ——
  要等批次六及之后收到这两类的新卡。
- **单张仍值 6.25 个百分点**（16 张）。本条最强的证据是「规则臂 blackhole 0/6」
  这个**计数**，不是那些百分比差值。
- **43 卡上 agent 首次在 top-1 上也反超规则臂**（65.1% vs 62.8%，service-only 差 11.6 点）。
  这个反超有一半来自卡集构成变化，不是能力变化 —— **写进 findings 时标注了，
  引用时不要只引那一行。**

---

## 032 两张 latency valkey 卡入库 45；crash / blackhole 两张挂 O-P2-23 不跑（2026-09-06 ET）

**选了什么**

1. **只跑两张 latency valkey 卡**（`batch7_20260906T181514Z`，800 先、3000 后），
   **2/2 全过入库，在库 43 → 45**（latency 10 → 12）。
2. **`crash-valkey-cart-01` 与 `blackhole-valkey-cart-01` 不跑**，挂在 O-P2-23 下。
3. **配方 68 不动** —— 不走 O-P2-23 第六节的 (c)（把这两张出库）。
4. **判据一个字没改**，卡片一个字没改。O-P2-23 列的四条修法**一条都没落**。

**为什么**

**因为 O-P2-23 的判别式说得很具体：断连接的原语杀埋点，`delay_outbound` 不断连接。**
这不是一个「试试看」的赌，是一条有机制、有实测的预测。本批就是它的检验：
两张卡的调用方边在**基线、注入、恢复三个窗里都在**，报错 span 全程 0，
右移 800.24 / 3000.27 ms 对门槛 640 / 2400，恢复窗速率 1.81 / 1.22 对门槛 0.78 / 0.47。
**预测对了，而且是在入库档（60/120/150）上对的，不只是调试档。**

顺带补上了 O-P2-23 开条时缺的一格：**3000 ms 档也不杀埋点**。
它离 cart 客户端约 5 s 的超时还有余量，此前只测过 800。

**为什么先做可逆的那一半**

配方出库是不可逆的口径变更；把两张卡挂起来不是。
O-P2-23 第六节的 (a)（给无 SDK 靶子的 `recovered` 加一路不依赖该埋点的旁证）
一旦成立，`crash` / `blackhole` 两张卡还能救回来 —— **先做可逆的，把能拿的两张先拿到手。**
而 (a) 触及在库 45 张里 3 张已过门的卡（`crash-astronomy-db-01`、
`blackhole-astronomy-db-01`、`latency-astronomy-db-3000`，全部走无 SDK 路径），
**在有离线回放对比数据之前不该落码**。

**放弃了什么**

- **放弃四张卡一起跑。** 批次六已经证明这么跑的代价：第一张 crash 卡杀掉埋点之后，
  后面两张的基线直接变成 0，**三张卡一次全废**。分批不是保守，是 O-P2-23 那条
  「同批次里 crash/blackhole valkey 卡会毒化它后面所有 valkey 卡」的直接后果。
- **放弃顺手把 crash / blackhole 两张出库**（(c)）。理由见上：不可逆，且 (a) 还没被否掉。
  **代价写在明处**：配方 68 里现在有两张**明知当前跑不过**的卡，
  配方与在库的差距因此有 2 张是「结构性跑不过」而不是「还没跑」。
  这一条必须在引用配方覆盖率时说清楚。
- **放弃在本批之后立刻补跑三臂评测。** 新入库这两张卡**不在任何评测卡集里**
  （27 / 5 / 11 三个集合都没有它们），它们是现成的**第三留出集**。
  但 §4 的三张表是同一天的同集对照，中途加卡会把「同集」这个前提破坏掉。
  **留到下一次成规模的复测一起做。**

**trade-off**

- **在库 45 里 latency 占 12 张，是五类里最多的一类**（blackhole 12、crash 12、
  latency 12、misconfig 7、mem_leak 2）。本批加的两张都是 latency，
  **类别分布因此更偏了**，引用「五类均有」时不要连带说「均衡」。
- **这两张卡的入库依赖一个易碎的前置条件**：cart 的 valkey 埋点必须活着。
  今天它活着是因为 17:38 手动重启过 cart。**任何人在这两张卡之前跑一次
  crash/blackhole valkey 卡，它们就会立刻变成不可测** —— 起批前查一次边有没有 span，
  是这两张卡以后每次重跑都要做的事，已写进 O-P2-23。
- **`latency-valkey-cart-3000` 的余量没有量过。** 3000 ms 通过了，但离客户端超时
  （约 5 s）还剩多少、在负载更高时会不会翻过去，本轮没测。
  **不要把「3000 档安全」外推成「delay_outbound 对 valkey 永远安全」。**

---

## 033 v1.1 全量批次：21 张可跑卡一次跑完（2026-09-06 ET，`batch8_20260906T193745Z`）

**选了什么**

1. **卡单 21 张 = 19 张未跑 + 2 张失败复跑**，一次无人值守跑完。
2. **排除 2 张**：`crash-valkey-cart-01`、`blackhole-valkey-cart-01`，按 O-P2-23 挂起不跑。
3. **卡序**：会碰 `cart` 的三张排在最后（19–21）。
4. **`--abort-after-recovered-failures` 显式设为 21**（默认 2）—— 本批目标是全量覆盖，
   失败本身就是数据，不该让前面几张卡的失败把后面十几张卡的机时吃掉。
5. **判据、卡片一个字没改。** `latency-shipping-3000` 照原样跑（见下）。

**卡单（按执行序）**

| # | 卡 | 类 | 靶子 | 周期 b/i/r/settle | 备注 |
| ---: | --- | --- | --- | --- | --- |
| 1 | `latency-ad-3000` | latency | ad | 60/120/60/150 | 未跑 |
| 2 | `latency-astronomy-db-800` | latency | astronomy-db | 60/120/60/150 | 未跑；无 SDK 靶子 |
| 3 | `latency-checkout-3000` | latency | checkout | 60/300/150/150 | 未跑；R1 加长窗 |
| 4 | `latency-currency-3000` | latency | currency | 60/120/60/150 | 未跑 |
| 5 | `latency-email-3000` | latency | email | 60/300/150/150 | 未跑；R1 |
| 6 | `latency-frontend-800` | latency | frontend | 60/120/60/150 | **失败复跑**（rerun1，symptom=False） |
| 7 | `latency-payment-3000` | latency | payment | 60/300/150/150 | 未跑；R1 |
| 8 | `latency-product-catalog-3000` | latency | product-catalog | 60/120/60/150 | 未跑 |
| 9 | `latency-quote-3000` | latency | quote | 60/120/90/150 | 未跑 |
| 10 | `latency-recommendation-800` | latency | recommendation | 60/120/60/150 | 未跑 |
| 11 | `latency-shipping-800` | latency | shipping | 60/120/70/150 | 未跑 |
| 12 | `latency-frontend-3000` | latency | frontend | 60/120/60/150 | 未跑 |
| 13 | `misconfig-payment-10` | misconfig | payment | 60/300/150/150 | 未跑；R1 |
| 14 | `latency-shipping-3000` | latency | shipping | 60/120/70/150 | **失败复跑**（批次四，F-3 报错占比 12.0%） |
| 15 | `misconfig-pc-2ZYFJ3GM2N` | misconfig | product-catalog | 60/120/60/150 | 未跑；targeting 型开关 |
| 16 | `memleak-email-100x` | mem_leak | email | 60/300/150/150 | 未跑；R1 |
| 17 | `misconfig-payment-25` | misconfig | payment | 60/300/150/150 | 未跑；R1 |
| 18 | `misconfig-pc-66VCHSJNUP` | misconfig | product-catalog | 60/120/60/150 | 未跑；targeting 型开关 |
| 19 | `latency-cart-3000` | latency | **cart** | 60/120/60/150 | 未跑；**排在最后，见卡序理由** |
| 20 | `misconfig-cart-90` | misconfig | **cart** | 60/120/60/300 | 未跑；O-P2-9 的 `settle_s=300` |
| 21 | `misconfig-cart-100` | misconfig | **cart** | 60/120/60/300 | 未跑；同上 |

预计总时长 **约 3.06 小时**（每卡按周期 + 打包 40 s 估）。

**为什么这个卡序**

**主约束是 O-P2-23：不能让前面的卡杀掉后面卡要用的埋点。**
`cart` 的 Valkey 客户端埋点在连接被打断后不再恢复，只有重启 `cart` 才回来。
本批没有任何 valkey 卡，所以那条边不是本批的判据依赖 —— **但把碰 `cart` 的三张卡放最后
仍然是对的**：它们跑完之后如果埋点出了事，受影响的是本批之后的东西，
而不是本批里还没跑的十几张卡。**代价为零，收益是把一整类风险挪到批次边界之外。**

**顺带记一条本批的性质**：21 张卡全部是 `delay_outbound` 或 `set_flag`，
**一张 `kill_container` / `drop_inbound` 都没有** ——
按 O-P2-23 的判别式，本批**在结构上不可能自我毒化**。
这不是编排出来的，是「剩下没跑的卡恰好都是这两类」的结果，但值得写下来：
下一批只要出现 crash / blackhole 卡，卡序就得重新按这条约束排。

**次约束是同靶子间隔。** 同一个靶子的卡连着跑，前一张的恢复窗和后一张的基线窗贴在一起，
基线就不干净了。排完之后同靶子最小间隔：
payment 7→13→17（≥4）、product-catalog 8→15→18（≥3）、frontend 6→12（6）、
shipping 11→14（3）、email 5→16（11）。
**唯一没拉开的是 cart 的 19/20/21 三张连排** —— 这是「排最后」这条主约束的代价，
两条约束冲突时按主约束办；`set_flag` 的撤除是瞬时的（改回 json 并验证），
不像网络原语那样留尾巴，所以这三张连排的代价小于把它们插回中间的风险。

**关于 `latency-shipping-3000`：照跑，不挪线。**
它在批次四挂在 F-3 的报错占比上限（实测 12.0%，门槛 10%，上一次是 8.70%）。
决策 027 已记「两点骑线，等专门测量」。**本批给的正是第三个数据点** ——
在测量之前把门槛从 10% 挪到 15% 就是拿评测集迁就一张卡。
挂了就是第三个数据点，过了说明前两次骑线是抖动，两种结果都有用。

**关于 `abort` 阈值：改了一个、另一个改不了，必须写明**

`--abort-after-recovered-failures` 从默认 2 显式设为 **21**（= 卡单长度，等于关掉这条中止）。
理由是本批的目的是覆盖率不是良率：recovered 连挂两张就停，会让一次三小时的批次
在第 20 分钟结束，而后面十几张卡的信息一条都拿不到。

**但 runner 里还有第二条中止路径，本步没有关掉**：
`run_batch.py:126` 的 `BATCH_ABORT_AFTER_FAILURES = 3` 是**模块常量，没有对应的命令行开关**，
**连续 3 张卡门失败**（不只是 recovered，任何一道门）仍会中止批次 ——
批次六就是这么停的。关掉它要改 `run_batch.py`，而本步的红线是批次期间只动 `docs/`，
**所以没有改**。

**这意味着本批的「不中止」只做到一半**：recovered 连挂不会停，
但如果有 3 张卡连着整体判失败，批次仍会在那里停下。
真发生的话，剩余卡另起一批补跑即可，不影响已跑卡的结果。
**这一条是给下一步的输入：要么接受，要么在下一批之前把那个常量提成 CLI 参数。**

**放弃了什么**

- **放弃把 21 张拆成几个小批。** 拆批的好处是每批之间可以人工看一眼，
  坏处是每批都要重付起批门与批次边界的固定成本，且总墙钟更长。
  本批已经关掉了 recovered 连挂中止，拆批的主要理由（早停止损）也就不存在了。
- **放弃为 `latency-shipping-3000` 挪 F-3 的门槛**，理由见上。
- **放弃顺手把 `BATCH_ABORT_AFTER_FAILURES` 提成 CLI 参数。** 那是 runner 改动，
  本步不碰；也不该在一个三小时批次起飞前十分钟改 runner。
- **放弃把两张挂起的 valkey 卡塞进来「再试一次」。** O-P2-23 已经证明它们的
  `recovered` 结构上不可能通过，跑它们只会消耗机时并且**杀掉 cart 的 valkey 埋点**，
  让批次七刚入库的两张卡下次重跑时先要重启 `cart`。

**trade-off**

- **一次 3 小时的无人值守批次，中途没有人看。** 起批门查过锁、残留、25/25 容器，
  但批次中途的环境问题（例如某个容器 OOM）要等收批才会发现。
  未挂 poweroff 守卫是有意的（今天继续施工）。
- **本批全部是 latency / misconfig / mem_leak，没有 crash / blackhole。**
  跑完之后在库的类别分布会进一步偏向 latency 与 misconfig；
  **引用类别覆盖时要看跑完后的实际分布，不要按配方的 68 张去推。**
- **`misconfig-cart-90` / `-100` 连排**，两张都带 `settle_s=300`（O-P2-9），
  批次末尾因此有约 20 分钟花在这两张上。若它们双双失败，
  按上面那条没关掉的中止路径，需要连同 `latency-cart-3000` 三张一起失败才会触发 ——
  但那正好是批次的最后三张，中止与跑完没有区别。

---

## 034 第三、四臂：开箱配置 haiku 4.5 作跨配置对照（2026-09-06 ET）

**选了什么**

1. **新增两臂**：`agent-haiku(开箱)`（v2 prompt + 6 工具 + submit + 五道保险，
   模型 `claude-haiku-4-5`）与 `基线③ 单轮 haiku(开箱)`（同摘要、同 prompt，只换模型）。
2. **haiku 臂不带 `thinking`、不带 `output_config.effort`** —— 选项 A。
3. **实现走 `config.yaml` 的 `model_api` 段 + `run_agent.run_config_for()`**，
   未列出的模型原样拿到 `config["run"]`，**主 agent 的默认值一个字节没动**；
   `single_shot_llm.py` 复用同一个函数，**没有复制代码**。
4. **五臂表由新增的 `scripts/baselines/compare_arms.py` 生成**，
   `compare_report.py` 一行没动 —— 既有的三臂报告被 docs 引用，必须继续逐字节可重现。
5. **口径写死为「开箱配置 haiku 4.5 vs 调优 sonnet 5」**，findings §4.5 全文不出现
   「模型对照」四个字。

**为什么不是「唯一差异是模型」——这个前提被实测证伪**

原计划是「与主 agent 完全同一份配置，唯一差异 model=claude-haiku-4-5」。
**做不到。** 2026-09-06 对着线上 API 实测（每次 `max_tokens ≤ 64`）：

```
haiku   effort=high + thinking adaptive  400 adaptive thinking is not supported on this model
haiku   thinking adaptive only           400 adaptive thinking is not supported on this model
haiku   effort=high only                 400 This model does not support the effort parameter.
haiku   无 effort 无 thinking             200
haiku   thinking={enabled,budget_tokens} 200
sonnet  effort=high + thinking adaptive  200   ← 对照
```

**换模型必然同时换掉 thinking 与 effort 两个变量。** 这不是实现选择，是模型档位差异。
预授权因此失效，三个选项交由用户裁决，用户选 A。

**放弃了什么**

- **放弃 B（haiku 用 `thinking={"type":"enabled","budget_tokens":N}`）。**
  它在功能上更接近「都让它思考」，但 **N 没有任何实测依据**，
  而 N 直接决定成本与准确率。把一个编出来的常数放进对照的正中间，
  正是这个仓库一路在防的东西（比较 F-3 的 10% 上限、`MIN_EXPECTED` 的 5：
  那两个数至少有实测或任务指定的来源，N 一个都没有）。
- **放弃 C（两臂都关掉 thinking/effort，sonnet 重跑一遍，得到真正的单变量对照）。**
  它是唯一能干净拆分「模型贡献」与「配置贡献」的做法，代价是**再花约 $3.1**
  重跑一个已经有结果的臂，并且那个「关掉思考的 sonnet」**不是 v1 交付的那个 agent** ——
  §4 会多出一个跟交付物无关的第六臂，把表读糊。
  **代价写在明处**：本轮**无法回答「模型本身值多少」**，只能回答两个端点之间的总差距。
- **放弃按裁决之外的方式呈现。** §4.5 只报总差距，不拆分；
  evidence_audit 的措辞上限相应封在「开箱 haiku vs 调优 sonnet」。
- **放弃因为 haiku 表现差就调 prompt。** 它的 `validation_rejects` 是 sonnet 的
  **8 倍**（33 vs 4）、**9/43 跑满 20 步不交卷**，看着都像“再调调就好了”。
  但 v2 prompt 是冻结的，为一个新臂改它等于把 §4 全部既有数字作废。
  **计数如实记录在 §1.5，不作为改 prompt 的依据。**

**实测里最值得记的三条**

1. **工具循环的增益在 top-1 上对两种配置都成立**（sonnet +18.8/+18.6，
   haiku +31.2/+7.0），**但在 service-only 上对开箱 haiku 是负的**（−6.3 / −9.3）。
   单轮 haiku 看一份固定摘要能答对 69.8% 的服务，让它自己查反而掉到 60.5%。
   **工具循环对它是净负担。**
2. **换便宜的模型没有换来便宜的 agent。** 43 卡上 agent-haiku 花 **$3.3025**，
   **比 agent-sonnet 的 $3.1197 还贵**，而 top-1 低 18.6 个点 ——
   token 单价便宜一半，步数却是 2.5 倍（13.40 vs 5.40）。
   **单卡成本必须按「跑完一张卡」算。**
3. **新失败模式 §1.5「步数耗尽不交卷」**：9/43（20.9%）跑满 20 步没调过 `submit`，
   sonnet 是 0/43。且**不是卡在循环里** —— `blackhole-ad-01` 20 步打了 35 次
   工具调用、参数无一重复。是查得越来越宽、就是不收敛。
   这九张平均 $0.1181，比交卷的 34 张（$0.0659）贵 1.8 倍：
   **它失败最彻底的卡也是最贵的卡，步数熔断是唯一兜住成本的东西。**

**trade-off**

- **本轮拿到的是两个端点，不是一条曲线。** 「开箱 haiku」与「调优 sonnet」之间
  差 18.7 个点（有工具、两个卡集互相印证），但这 18.7 里多少来自模型、
  多少来自思考与 effort，**本轮答不了**，要答就得付 C 的 $3.1。
- **无工具条件下的差距不可引用**：16 卡 +31.2、43 卡 +7.0，差 24 个点；
  16 卡上单张值 6.25 个百分点。§4.5 已写明只引有工具那一档。
- **跨模型这件事仍然只有两点，且不是多家。** evidence_audit 的禁写第 4 条
  据此改写为「已覆盖：开箱 haiku 与调优 sonnet 两点，非多家、非同配置」，
  见该文件 C7。
- **`model_api` 这个新机制会被下一个人用错。** 它长得像「随便覆盖 run 参数」，
  实际只该用来记**模型 API 不接受某参数**这一类事实。config.yaml 的注释里写了
  这条界线，但机制本身拦不住误用。

---

## 035 agent 侧 OpenTelemetry 追踪：两个落点、默认关闭（2026-09-06 ET）

**选了什么**

1. **span 落两处，都在测试床之外**：
   - **文件**：`artifacts/agent_runs/<run-id>/traces.jsonl`，一行一个 span —— **证据件**；
   - **独立 Jaeger**：`docker-compose.agent-obs.yml`（UI 16687 / OTLP 4327、4328）—— **演示件**。
2. **默认关闭**，`--trace` 或 `config.yaml` 的 `tracing.enabled` 打开。
3. **层级**：card（root）→ step N → model.call / tool.\<name\>，另有 grade 作 card 的子 span。
4. **依赖 pin 进 `requirements-agent.txt`**（`opentelemetry-sdk==1.44.0`、
   `opentelemetry-exporter-otlp-proto-http==1.44.0`），**CI 不装**。

**为什么落点必须避开测试床**

这是本条唯一的硬约束，也是它存在的理由。`scripts/evidence/pack.py` 在每张卡 harvest 时
**把测试床 Jaeger 的 `/api/services` 与 traces 快照进证据包**。
如果 agent 的 span 报到那套后端（collector 4317/4318、Jaeger 16686），
`rca-agent` 就会作为一个服务出现在 `topology.json` 与 `traces.json` 里 ——
**评测器变成被评测系统的一部分**，而且是以「一个新服务」的形态混进 agent 之后要去诊断的真值里。
那不是不方便，是实验被污染。

所以端口全部错开（16687 / 4327 / 4328），compose 文件**不并入测试床工程**，
`wakeup.sh` 的 25/25 容器门也**不该**知道它 —— 那个门数的是 `dc ps`（测试床工程作用域），
本容器在另一个 compose 工程里，天然不计入。**实测已验**：
带 `--trace` 跑完两张卡后，独立 Jaeger 的 `/api/services` 返回 `["rca-agent"]`，
测试床 Jaeger 的 `/api/services` 返回 17 个服务、**不含 `rca-agent`**。

**为什么两个落点都要**

文件是**可 diff、可复核、不依赖任何守护进程、容器没了也还在**的那一份；
Jaeger 是**演示时能点开的层级视图**。二选一都会缺一块：
只有 Jaeger 则结果不可归档、评测跑完就散；只有文件则没法在面试里点两下讲清楚。
**独立 Jaeger 是可选的，它不在时 tracing 照常写文件**，这一点写进了 compose 的注释。

**为什么默认关**

关闭时 `tracing.py` **一个 OpenTelemetry 包都不 import**（已验证：
`sys.modules` 里 `opentelemetry` 前缀模块为 0），所有 span 调用是 no-op 上下文管理器。
一次普通评测的成本因此与追踪存在之前**完全一样**。
反过来，**开启时若依赖缺失，只打一行 stderr 并继续untraced** ——
一次评测绝不能因为可观测性依赖装没装而失败。CI 三道门也照旧只装 `pytest` + `PyYAML`。

**判分为什么是单独的 span 而不是 card 的属性**

`run_agent.py` **结构上不允许知道答案**（它从不 import `cards.py`，见 `leak_check`），
所以 top-1 / service-only 不能由它写进 card span；而判分发生在 `run_eval` 里、
card span 已经结束。做法是把 card span 的 context 存下来，
由 `run_eval` 调 `tracing.grade_span()` **在已结束的父 span 下开一个子 span**。
父子关系是真的，**答案仍然没有进到那个能拿它作弊的模块**。

**放弃了什么**

- **放弃 Langfuse（以及同类托管 LLM 观测平台）。** 它要再引一个外部服务、一个账号、
  一份 SDK 和一条出网路径，**换来的只是一个 UI**。
  本条要的那些东西 —— 每步的 token、单步成本、stop_reason、工具返回字节数、
  五道保险的触发标记、card→step→call 的层级 —— **OpenTelemetry 已经全给了**，
  而且 span 属性是我自己定义的，不受平台字段模型约束。
  再加一个账号还会让「谁看得到评测数据」这个问题多一个答案，
  而这个仓库的评测数据里有真值。
- **放弃把 span 也发到测试床 collector 做“统一视图”。** 见上，这是本条的红线。
- **放弃默认开启。** 默认开会让每次评测都背上一个可选依赖和一个导出器，
  而追踪的用途是**排查与演示**，不是**每次评测**。

**trade-off**

- **`traces.jsonl` 会随评测目录一起入库**，两张卡 30 个 span 约 12 KB；
  43 卡一轮约 600 span。目前不大，但**它是随卡数线性增长的第二份产物**，
  哪天跟证据包的三件大文件一样超阈值，就该按同样的规则 gitignore 掉。
- **span 属性里有 `rca.card_id`。** 它不构成泄漏 ——
  span 只写到 `artifacts/agent_runs/` 与独立 Jaeger，
  **两者都不在 agent 的证据面里**（`EvidencePack` 根在 `evidence/<card>/`），
  已逐条验证：`evidence/` 下没有任何 `traces.jsonl`，
  `leak_check.check_payload` 四项照旧通过，card_id 在 span 文件里、不在模型输入里。
  但这条依赖「谁能读 `artifacts/`」这个边界，**如果将来把 artifacts 挂进工具面，这条就破了**。
- **`jaeger-agent-obs` 用了 `restart: unless-stopped`**，所以它会跟着开机起来，
  宿主上 `docker ps` 会数到 26 个容器。`wakeup.sh` 的门与 README 的
  `containers` 数字都取自 wakeup.sh 里的常量、不数实时容器，因此都不受影响 ——
  但**下一个人拿 `docker ps | wc -l` 去对 25 会对不上**，这里记一笔。

---

## 037 批次八收批在库 63、未跑清零；abort 参数化；服务化 036 的三项前置裁决（2026-09-06 ET）

**选了什么**

1. **批次八收批**：21 张跑完，**18 张过门入库**，3 张失败。
   **在库 45 → 63**（blackhole 12 / crash 12 / latency 24 / mem_leak 3 / misconfig 12），
   配方 68 不变，**未跑卡从 19 张清零** —— 配方里每一张卡都至少跑过一次。
   verdict 分布现在是 **passed 63 / failed 5 / 未跑 0**。
2. **`BATCH_ABORT_AFTER_FAILURES` 提成 `--abort-after-gate-failures`**，
   默认仍是 3、行为不变，与 `--abort-after-recovered-failures` 并列；
   `tests/test_runner_cli.py` 加三条断言钉住它。
3. **F-3 的 10% 报错占比线：不动。**
4. **服务化 v1（决策 036 草案）的三项前置裁决落定**：不开公网、手写 SQL、
   `/summary` 复用 `compare_arms.metrics()`。

**为什么 abort 要参数化**

决策 033 记过这件事，但当时改不了：批次八的目的是覆盖率不是良率，
`--abort-after-recovered-failures 21` 关掉了一条中止路径，
而**门失败连击那条是模块常量、没有开关**，于是「不中止」这个要求**只做到了一半**。
批次八运气好没连挂三张（唯一一次连击到 1 就断了），但那是运气。
**一个只能改一半的开关比没有开关更糟**：它让人以为自己关掉了中止。
默认值不动是刻意的 —— 参数化解决的是「能不能改」，不是「该不该是 3」。

**F-3：第三个数据点让线站住了，但也量出了它的分辨率**

`latency-shipping-3000` 三次：**8.70%（2/23）→ 12.0%（3/25）→ 4.76%（1/21）**，
第三次**过门入库**。三次的绝对量是 **1、2、3 条报错**，分母 19–25。
**这不是一个在 8% 和 12% 之间移动的比例，是一个在「一条、两条、三条」之间跳的计数** ——
n≈21 时每条报错约 4.8 个百分点，所以 10% 这条线的真实语义是
**「≤2 条放行，≥3 条否决」**，四档分辨率，10% 恰好落在两档之间的空隙里。

**因此：12.0% 那次是噪声，不该挪线；但也不该把这次「过了」当成线准。**
真要按比例定线，得把注入窗拉到 n ≥ 100 的量级，是另一次专门测量。

**同一批次给了这条线最好的辩护**：`latency-quote-3000` 在同一档、同一规则下
报错占比 **94.74%（18/19）** —— **在低报错端分辨率粗，不等于在高报错端没有区分度。**

**三张失败卡，三种不同的东西（这一点比失败本身重要）**

| 卡 | 门 | 数字 | 归属 |
| --- | --- | --- | --- |
| `latency-email-3000` | recovered | 调用方 0.0333/s vs 门槛 0.0583，靶子侧 0.0333 vs 0.036666（**差 0.4 次调用**） | **O-P2-21 那张概率表的第一个实测样本**，不是新问题 |
| `latency-quote-3000` | symptom | p50 右移 4998.75（注入只有 3000），报错 18/19 | **新机制，开 O-P2-24** |
| `misconfig-payment-10` | symptom | `Charge` 15 次调用 1 条报错，门槛 N=2 | **O-P2-19 从「可能」升级为「必然低通过率」** |

`misconfig-payment-10` 那条值得单独说：门槛 `N = max(2, ceil(0.5 × ratio × calls))`，
15 次调用在 10% 下期望 **1.5** 条报错，而门槛要 **≥2**，
**P(≥2 | λ=1.5) ≈ 44%** —— 这张卡按设计就是挂多过。
**加窗救不了**：`N` 里含 `calls`，窗越长门槛跟着涨，期望与门槛之比恒为 0.5，与窗长无关。
这不是运气不好，是判据形式在低比例档上的结构性后果。

**服务化的三项前置裁决（正式记入，明日 036 引用本条）**

1. **不开公网，演示走 SSH 端口转发。** 决定性的不是工期而是风险形状：
   每个 `POST /runs` 都是计费的模型调用，公网 + 自动计费下一次 token 泄漏的损失没有上界。
   日预算 $5 是最后一道防线，不该让它变成唯一一道。
2. **迁移用手写 SQL + `schema_migrations`，不用 Alembic。**
   六张表、一个人、一天；Alembic 的价值在长期多分支增量迁移，这里兑现不了，
   代价是依赖 + `env.py` + autogenerate 假阳性。**这是可辩护的反向选择**，
   面试里讲「为什么这个规模不该上 Alembic」比讲「我用了 Alembic」更有内容。
3. **`GET /summary` 进 v1，且直接复用 `compare_arms.metrics()`，不重写聚合。**
   它是对 AI Engineer 面试官最有说服力的端点，但也是唯一一处可能出现
   「同口径两份实现」的地方；复用而不重写把那个风险直接消掉，不靠测试去追。

**放弃了什么**

- **放弃为 `latency-quote-3000` 改真值**（把它标成 `misconfig` 让它过门）。
  真值必须描述**注入了什么**，不是**看起来像什么**。改了它，
  「ground truth 就是注入动作」这条全仓一致的口径就破了，
  而那条口径是所有准确率数字能被引用的前提。
- **放弃趁 `misconfig-payment-10` 挂了就降 `N` 的地板。**
  地板 2 是决策 018 定的，降它会同时松动全部 misconfig 卡的症状门。
  低比例档的问题记在 O-P2-19，等一次专门裁决。
- **放弃把 `--abort-after-gate-failures` 的默认值改掉。** 3 是有理由的：
  连挂三张通常意味着环境坏了而不是卡坏了，继续跑只是浪费机时。
  参数化让「这一批我知道自己在干什么」成为可能，**不等于默认就该放宽**。

**trade-off**

- **在库 63 里 latency 占 24 张（38%）、misconfig 12 张、mem_leak 只有 3 张。**
  未跑清零是里程碑，但**类别更不均衡了** —— 批次八 21 张里 14 张是 latency。
  引用「五类齐全」时不要连带说「均衡」；mem_leak 的 3 张全在 `email` 一个靶子上。
- **未跑清零不等于配方跑透。** 68 = 63 在库 + 5 失败，
  其中 2 张（valkey crash/blackhole）**结构上跑不过**（O-P2-23），
  1 张按判据形式**大概率跑不过**（O-P2-19），
  1 张**高档变形态**（O-P2-24），只有 1 张（`latency-email-3000`）是纯运气。
  **「跑过一次」与「能稳定复现」是两件事。**
- **`--abort-after-gate-failures` 这个开关会被用错。** 它存在的意义是
  「这一批要覆盖率」，但它长得像「让批次别停」。
  默认值和 help 文本写了「set to the batch length to disable」，拦不住误用。
