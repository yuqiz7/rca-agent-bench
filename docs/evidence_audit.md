# 证据审计（简历前，D83④）

**日期**：2026-09-06 ET　**审计对象**：HEAD `509b461`　**方法**：每条候选事实对到仓库一手证据
（文件路径:行 / commit / CSV·JSON 字段），只读核实，不改代码、不改判据。

**读法**：数字一律以仓库现状为准。候选表里写错的，本文直接给正确值，
并在「措辞边界」一栏写清**这条最强可辩护的说法**与**不能用的词**。
「措辞边界」不是修辞建议，是一旦被追问就要拿出证据的那条线。

---

## A　测试床与注入

| 编号 | 事实 | 证据定位 | 结论 | 措辞边界 |
| --- | --- | --- | --- | --- |
| A1 | OpenTelemetry Demo + 三信号 + Compose 单机 | `docker compose -f compose.yaml -f compose.observability.yaml -f testbed/compose.override.yaml config --services` → **25**；运行中容器 25（`docker ps -q \| wc -l`）；开机门 `scripts/maintenance/wakeup.sh:96` `[ "$RUNNING" = "25" ]`；靶子枚举 `docs/fault_schema.md:64-76` **16 项**；三后端 `scripts/backends.env`（Jaeger 16686 / Prometheus 9090 / OpenSearch 9200） | **数字不符 → 需分开说**。「服务数 N」没有单一答案：compose 服务 **25**（= 容器数），其中**可注入靶子 16**，16 里 **13 个是 demo 自研应用服务**、3 个是第三方镜像（`astronomy-db`=PostgreSQL、`valkey-cart`=Valkey、`flagd`）。剔除的 9 项是观测后端 / 采集管线 / 压测器 / 控制面 / 文档站 | 可说「**25 容器的 OpenTelemetry Demo，16 个服务可作注入靶子**」「Prometheus + Jaeger + OpenSearch 三信号」「Docker Compose 单机」。**不能说「25 个微服务」**——那 25 里含 Jaeger/Prometheus/Grafana/压测器。**也不要只说 16**，16 是靶子数不是系统规模 |
| A2 | 五类故障原语，落脚 `scripts/primitives` | `scripts/primitives/` 下 **4 个脚本**：`kill_container.sh`（crash）、`drop_inbound.sh`（blackhole，netns 内 iptables DROP）、`delay_outbound.sh`（latency，tc netem + u32 按 sport 过滤）、`set_flag.sh`（**misconfig 与 mem_leak 共用**，见文件头 `class: misconfig \| mem_leak`） | **数字不符 → 4 个原语、5 个故障类**。`set_flag.sh` 一个脚本按 flag 覆盖两类 | 可说「**四个注入原语覆盖五类故障**」「统一 apply/revert/probe 三子命令接口」「注入按服务端口过滤，不整块断网」。**不能说「五个原语」或「五个脚本」** |
| A3 | 配方 68 卡、五类齐、三轴难度；在库 43 | `scripts/scenarios/recipe.csv` **68 行**（表头外）；类分布 crash 13 / blackhole 13 / latency 26 / misconfig 13 / mem_leak 3；三轴列 `axis_a,axis_b,axis_c`；难度档 易 25 / 中 38 / 难 5。在库＝卡片 `production.probe.verdict == passed`，**43 张**（blackhole 12 / crash 12 / latency 10 / mem_leak 2 / misconfig 7），commit `ee0cbb3`（41→43）、`d139995`（配方 64→68） | **通过** | 可说「**68 卡配方、43 卡已通过机器判据入库，五类均有**」。**不能把 68 说成「已验证的 68 张」**——25 张还没跑过或没过门。在库里 mem_leak 只有 2 张，**不要说「五类均衡」** |
| A4 | 三探针判据＋残留检查；基线窗 300 s；低流量自适应恢复窗；无 SDK 双臂；Envoy 边识别 | 判据 `scripts/runner/run_batch.py`：`BASELINE_LOOKBACK_S = 300`（:86）、`target_side_arm()`（:419）、`SPANMETRICS_EXPORT_S` 导出间隔守卫（:451）；残留 `probe_after_revert`（批次日志每卡一行）。自适应恢复窗 `scripts/scenarios/recover_window.py`：`recover_s ≥ RECOVER_SKIP_S(30) + MIN_EXPECTED(5)/rate`，上限 300，落到 recipe 的 **34 张卡**（`recover_s=150`×26、`=90`×4、`=70`×4）；R1 注入窗加长 `inject_s=300`×22 张。无 SDK 双臂 `docs/fault_schema.md:262-276`（台阶档 ≥1000 ms 或 ≥100× 基线 p50 / 边静默档）。Envoy 边识别 `scripts/probes/three_signals.py:85-86` `PEER_KEYS` 含 `upstream_cluster.name` / `upstream_cluster` | **通过**。「期望 <5 延长」的准确表述是：恢复窗按靶子实测最小速率解 `rate×(recover_s−30) ≥ 5` 反算 | 可说「**每卡三道机器判据（注入生效/症状出现/恢复）＋残留检查，全部自动**」「恢复窗按靶子实测速率反算，34 张低流量卡加长」。**不能说「自适应」是运行时动态的**——它是离线算好写进 recipe 的 `cycle_override`，一次算定 |
| A5 | 每卡证据包 8 件，评测离线只读 | `evidence/<card>/`：`logs.jsonl`、`metrics.json`、`traces.json`、`config_diff.txt`、`topology.json`、`manifest.json`、`task.json`、`alerts.json` = **8 件**；`manifest.json.files[]` 对前 5 件记 `bytes`+`sha256`（本步复算过批次五两卡，全部匹配）；三件大文件按 `.gitignore` 不入库（单卡约 10.7 MB）；agent 工具面只读包，`scripts/agent/evidence_tools.py` | **通过**，一处要标注：`topology.json` 是**引用文件**（指向共享的 `evidence/_shared/topology.json` + sha256），不是每卡独立拓扑 | 可说「**每卡冻结一份 8 件证据包，带 sha256 清单可复算**」「评测全程离线只读证据包，不连实时后端」。**不能说「8 件全部入库」**——3 件大文件是生成物，靠 manifest 复算 |
| A6 | 检测器 7 条规则（规则 7 含 100 ms 地板），干净窗负对照 0 告警 | `scripts/evidence/detect.py:129-137` `RULES` 字典 **7 条**；规则说明 `detect.py:20-34`；100 ms 地板 `detect.py:115` `METHOD_P95_MIN_DELTA_MS = 100.0`（:109 记了它救回三张卡）；负对照 `tests/test_clean_window_negative_control.py`（CI 门 3，**4 项断言全过**） | **通过** | 可说「**7 条告警规则，在两个无故障观测窗上零告警，作为 CI 门每次 push 复验**」。**不能说「负对照覆盖全部规则」**——fixture 是精简过的，规则 6 之外的 span 被删（文件头写明等价性论证） |

---

## B　Agent

| 编号 | 事实 | 证据定位 | 结论 | 措辞边界 |
| --- | --- | --- | --- | --- |
| B1 | 手写 function-calling 循环（无框架），6 只读工具＋submit，模型 claude-sonnet-5 | `scripts/agent/run_agent.py:144` `while True:` 主循环，直接调 `client.messages.create`，**依赖只有 `anthropic` SDK，无 agent 框架**；工具 `scripts/agent/evidence_tools.py:430 tool_schemas()` 返回 **7 个** schema：`logs_search` / `metrics_query` / `traces_query` / `config_diff` / `topology` / `alerts` + `submit`；模型 `scripts/agent/config.yaml` `models.primary: claude-sonnet-5` | **通过** | 可说「**手写工具循环，六只读工具＋submit 作第七个工具**」「submit 是工具不是解析出来的文本，答案空间由 schema 约束」。**不能说「无依赖」**——用了官方 SDK |
| B2 | 四道保险，各自计数 | `run_agent.py`：参数校验 `validate_submit` / `ToolError` → `counters["validation_rejects"]`（:181,:202）；工具内异常重试 → `counters["tool_retries"]`（:207）；没出工具调用时的格式追问 → `counters["nudges"]`（:237）、超限 `terminated="no_submit"`；步数熔断 `terminated="max_steps"`（:146-147，`max_steps: 20`）；成本熔断 `terminated="cost_cap"`（:224，`max_usd_per_card: 0.20`） | **数字不符 → 保险是 5 道，计数只有 3 个**。`counters` 字典 5 键（`api_calls` / `tool_calls` / `validation_rejects` / `tool_retries` / `nudges`），**两道熔断不进计数器，落在 `terminated` 字段** | 可说「**五道保险：参数校验、工具重试、格式追问、步数熔断、成本熔断，每次运行都记录触发情况**」。**不能说「四道保险各自计数」**——熔断记在 `terminated` 不是计数器 |
| B3 | card_id 泄漏断言 fail-closed；agent 只见 task_view | `scripts/agent/leak_check.py`：`FORBIDDEN_KEYS`（:33）、按值查 card_id（:55）、系统提示与工具 schema 必须是卡无关常量（:69,:71）、user turn 字段必须是 task.json 的投影（:78-83）；调用点 `run_agent.py:116`，**在 :150 第一次 `messages.create` 之前**，抛 `LeakError` 即中止 | **通过（fail-closed 位置正确）**，一处要改口径：agent 输入是 **trigger + `agent_visible_symptom`** 两项，**alerts 不是第三项**，它在 `agent_visible_symptom` 里面。`task_view.WHITELIST` 另含 `card_id` / `evidence_dir`，但那两项**不进模型输入**（进的是工具寻址） | 可说「**答案泄漏在花第一个 token 之前 fail-closed 拦截，是 CI 门之一**」「agent 看到的只有触发语和告警症状」。**不能说「agent 看不到 card_id」**——它看不到，但 harness 用 card_id 寻址证据目录，措辞要落在「模型输入」上 |
| B4 | prompt 一次迭代冻结：19 卡 52.6%→57.9% | `artifacts/agent_runs/devset_20260828/report.md:13`（52.6%，10/19）、`artifacts/agent_runs/devset_v2_20260828/report.md:13`（57.9%，11/19）；决策 `docs/decisions.md:1111` 025 | **通过** | 可说「**prompt 只迭代一次即冻结，开发集 top-1 52.6%→57.9%**」「冻结是留出集测试的前提」。**不能把 +5.3 说成显著**——19 张卡单张值 5.3 个百分点，这一步正好是一张卡。同一次改动里 service-only 从 84.2% **掉到** 78.9%，引用时不要只报涨的那个 |
| B5 | agent 可观测性（OpenTelemetry 追踪） | `scripts/agent/tracing.py`；开关 `--trace` / `config.yaml` 的 `tracing.enabled`（默认 **false**）；层级 card → step N → `model.call` / `tool.<name>`，判分是 card 的子 span `grade`（由 `run_eval` 开，因为 `run_agent` 结构上不能知道答案）；两个落点 `artifacts/agent_runs/<run-id>/traces.jsonl` 与 独立 Jaeger `docker-compose.agent-obs.yml`（16687/4327/4328）；依赖 pin 在 `requirements-agent.txt`。**实测验证**（`artifacts/agent_runs/traceverify_20260906/`，2 张卡）：30 个 span / 2 条 trace（13 + 17），独立 Jaeger `/api/services` 返回 `["rca-agent"]`，测试床 Jaeger `/api/services` 返回 17 个服务且 **不含 `rca-agent`**；关闭时 `sys.modules` 里 opentelemetry 模块数为 **0** | **通过** | 可说「**agent 的每一次模型调用与工具调用都以 OpenTelemetry span 记录（token、单步成本、stop_reason、返回字节数、五道保险的触发标记），可在独立 Jaeger 中按 card → step → call 逐层查看**」「**追踪落点与测试床后端物理隔离，以免评测器混进被评测系统的真值**」。**不能说「生产级可观测性」** —— 没有采样策略、没有告警、没有保留策略、没有多进程聚合，只有单进程 span 导出。**不要提 Langfuse 或任何托管观测平台** —— 本仓刻意没用（决策 035）。**不能说「默认开启」或「零成本」**：默认关闭，开启后每卡多一个导出器 |

---

## C　评测 harness 与基线

| 编号 | 事实 | 证据定位 | 结论 | 措辞边界 |
| --- | --- | --- | --- | --- |
| C1 | 四指标 | `scripts/baselines/compare_report.py:94` 表头字面写「## 四指标对照」，但渲染 **6 列**：top-1 / service-only / 平均步数 / 单卡成本 / p95 延迟 / 合计成本 | **数字不符 → 实际 6 列**。「四指标」是仓库里的历史叫法，表比名字多两列 | 可说「**同集同判分器下比 top-1、service-only、步数、单卡成本、p95 延迟与总成本**」。**不要沿用「四指标」这个词**——一被追问就要解释为什么表里有六列 |
| C2 | 两条基线 | 基线① `scripts/baselines/keyword_heuristic.py`（零 API，读同一 `EvidencePack`，决策 026 写明三条硬约束：无 card_id 特判、阈值全部沿用 harness 既有常数、规则按故障形态而非靶子写）；基线② `scripts/baselines/single_shot_llm.py`（单轮无工具，摘要形状对每张卡固定，**与 agent 同模型** `models.primary`） | **通过** | 可说「**两条基线：零 API 的规则臂，和去掉工具循环的单轮 LLM 臂——后者与主 agent 同模型同答案空间，唯一变量是循环**」。**不能说基线①「无人工调参」**——它是对着 27 卡迭代出来的，这正是 C6 量化的东西 |
| C3 | 留出集 16 卡三臂 | `artifacts/agent_runs/merged_holdout16_20260906/report.md`；卡单 `scripts/baselines/cardset_holdout16.json`；零交集核对见决策 031 | **通过**：agent top-1 **75.0%（12/16）**、service-only **87.5%（14/16）**；规则 **50.0%（8/16）**；单轮 LLM **56.2%（9/16）** | 可说「**在三臂都没见过的 16 张留出卡上，agent top-1 75.0%、服务定位 87.5%，均高于两条基线**」。**必须同时说留出集不含 misconfig 与 mem_leak**——那是规则臂最强的两类。**单张值 6.25 个百分点，不要报小数点后的差值** |
| C4 | 全 43 在库卡三臂 | `artifacts/agent_runs/merged_all43_20260906/report.md`；卡单 `cardset_all43.json` | **通过**：agent **65.1%（28/43）** / **83.7%（36/43）**；规则 **62.8%**；单轮 **46.5%** | 可说「**43 张在库卡全集上 agent top-1 65.1%、服务定位 83.7%**」。**不能把 43 卡叫留出集或泛化结果**——里面含开发集 19 张，三臂各自都有「见过」的部分。**不能说这是一次 43 卡的评测**——它是四次运行（两次开发集、两次留出集）按卡集合并出来的 |
| C5 | agent 成本 / 延迟 / 步数 | 同上两份 report 的四指标行：留出 16 卡 $0.0691 / 63.1 s / 5.19 步；全 43 卡 $0.0726 / 63.8 s / 5.40 步，agent 臂合计 **$3.1197** | **通过**（候选写的 $0.069 / $0.073 / 63.1 / 63.8 / 5.2 / 5.4 / $3.12 全部对得上） | 可说「**单卡约 $0.07、p95 约 64 秒、平均 5.4 步**」。**$3.12 不是「跑一次全集的花费」**——它是四次运行的成本相加；真要一次跑完 43 张，按单卡价约 $3.1，但没这么跑过。**p95 是墙钟不是 API 延迟**，含工具本地读盘 |
| C6 | 规则基线过拟合量化 | 决策 031（`docs/decisions.md:1552`）与 `docs/findings.md` §4.2 类别对齐表：27 卡集截到同类 18 张 66.7%（12/18）→ 留出 16 张 50.0%，**−16.7**；blackhole **3/6 → 0/6** | **通过** | 可说「**规则基线在没见过的卡上掉 16.7 个百分点（类别对齐后），失分几乎全部集中在 blackhole：27 卡上 3/6，两个留出集上 0/6**」。**不能引用未对齐的 −24.9 / −20.4**——那两个数把「留出集没有 misconfig 和 mem_leak」也算进了过拟合。最强的证据是 **0/6 这个计数**，不是百分比 |
| C7 | 跨配置对照（开箱 haiku vs 调优 sonnet） | 五臂表 `artifacts/agent_runs/fivearm_holdout16_20260906/report.md`（16 卡）与 `.../fivearm_all43_20260906/report.md`（43 卡），由 `scripts/baselines/compare_arms.py` 生成可重跑；两臂运行 `.../set{27,5,11}_agent_haiku_20260906/`、`.../set{27,5,11}_haiku_20260906/`；参数差异的依据是 2026-09-06 对线上 API 的实测（haiku 对 `thinking:adaptive` 与 `output_config.effort` 各返回一条 400），落码在 `scripts/agent/config.yaml` 的 `model_api` 段 + `run_agent.run_config_for()`；决策 034 | **通过，但口径受限** —— 这不是模型对照。Haiku 4.5 不接受主 agent 用的 `thinking`/`effort`，**换模型必然同时换掉三个变量**，两臂之间差的不只是模型 | 可说「**在同一份 prompt、同一组工具、同一套熔断下，对照了开箱配置的 haiku 4.5 与调优后的 sonnet 5**」「有工具条件下总差距约 **18.7 个百分点**（16 卡 +18.8 / 43 卡 +18.6，两个卡集互相印证）」「**工具循环的增益在两种配置下都成立**（top-1 四个数全为正）」「**换便宜的模型没有换来便宜的 agent**：agent-haiku 花 $3.30，比 agent-sonnet 的 $3.12 还贵，因为步数是 2.5 倍」。**不能说「模型对照」「haiku vs sonnet」「量化了模型带来的增益」** —— 那需要同配置的第三次运行，本轮没跑（决策 034 放弃项 C）。**不能引用无工具条件下的差距**（16 卡 +31.2 / 43 卡 +7.0，两者差 24 个点，不稳定） |

---

## D　工程质量

| 编号 | 事实 | 证据定位 | 结论 | 措辞边界 |
| --- | --- | --- | --- | --- |
| D1 | CI 三道门 | `.github/workflows/ci.yml`：门 1 `python -m pytest tests/ -q`（本步实跑 **12 passed**，`--collect-only` 亦为 12）、门 2 `generate.py --check`（本步 `cards=68 changed=0` rc=0）、门 3 干净窗负对照。三道门全部离线，CI 不碰 VM / 不碰 API（文件头写明） | **通过** | 可说「**GitHub Actions 三道离线门：12 项测试、场景生成器幂等、检测器干净窗负对照**」。**不能说「CI 跑评测」**——刻意不装 `anthropic`，任何门都不许发网络请求 |
| D2 | None-vs-zero 审计 | `docs/probe_audit.md:113`：**27 条**判据路径，正确 11 / 可接受 9 / **错误 7**；其中 **25 条读码找出、2 条由批次四撞出**（:104）。7 处已修 `docs/probe_audit.md:115` + 决策 029（commit `5182209`）。追溯核查 `docs/probe_audit.md:120`：**41 张在库卡 × 四窗快照零命中** | **数字不符 → 是 27 条路径不是 25**。25 是「读代码找出来的那部分」 | 可说「**审计了 27 条判据路径，改掉 7 处把『取不到』当成零的写法，并回放全部在库卡确认这个缺陷从未放行过任何一张**」。**不能说「审计发现 25 处」**。追溯核查的分母是**当时的 41 张**（2026-08-29），不是现在的 43——那是带日期的历史记录 |
| D3 | findings 与决策留痕 | `docs/open_items.md` **F-1…F-8 共 8 条**；`docs/findings.md` §1.1–§1.4 **四类 agent 失败模式**（1.4 于本轮新增）；`docs/decisions.md` **31 个决策编号 001–031，32 个小节**（018 拆「第一部分/第二部分」两节） | **通过**，一处要注意：小节数 32 ≠ 编号数 31 | 可说「**8 条实测 finding、4 类 agent 失败模式、31 条决策记录，每条写明选了什么/为什么/放弃了什么**」。**不要说「32 条决策」** |
| D4 | 批次 runner 与量产机时 | 串行锁 `scripts/state/runner.lock`（`run_batch.py` 存在性检查 + 探针侧 `flock`，`docs/workflow.md:192`、`prom_wal_restart_probe.sh:36-42`）；nohup 包装 `scripts/runner/run_batch.sh`；中止条件 `--abort-after-recovered-failures`（默认 2）与门失败连击 3。机时按 `artifacts/batches/*.log` 首末时间戳汇总：batch1 110.1 / batch2 109.3 / rerun1 107.0 / batch3 61.0 / batch4 115.5 / batch5 14.0 / batch6 25.1 分钟，**7 批合计 9.03 小时** | **数字给出 → 9.03 h**；**「失败即停」不符**：是**连续**失败到阈值才中止，单张失败继续跑下一张（batch6 就是撞到 3/3 才 ABORT） | 可说「**无人值守批跑，全局串行锁保证同一时刻只有一个故障在注入，连续失败自动中止；累计约 9 小时机时产出 7 个批次**」。**不能说「失败即停」**，也**不能把 9 小时说成「实验总时长」**——它只是批次运行占用的机时，不含开发与评测 |

---

## E　其他

| 编号 | 事实 | 证据定位 | 结论 | 措辞边界 |
| --- | --- | --- | --- | --- |
| E1 | API 总花费 | 权威口径已改为 `scripts/tools/readme_check.py` 的 `api_spend` / `api_runs`（README 由它生成并在 CI 门 1 校验）：**$9.07，237 次单卡运行**（2026-09-06 加入 haiku 两臂后；此前为 $5.38 / 151 次，差额 $3.69 = agent-haiku $3.3025 + 单轮 haiku $0.3904）。逐目录分项仍可由 `artifacts/agent_runs/*/` 的 `cost_usd` 复算 | **给出实际值 $9.07**；此前审计写的 $5.38 是 haiku 两臂之前的数，已过期 | 可说「**全部模型调用有逐卡计价留痕，累计 $9.07 / 237 次单卡运行**」。**引用前先跑一次 `readme_check.py --check`** —— 这个数每加一个评测臂就变，本文件 2026-09-06 已经因此过期过一次。**这不是「项目总 API 花费」**——只覆盖 `artifacts/agent_runs/` 里记账的运行，交互式调试与本步的连通性测试没有记账 |
| E2 | 仓库规模 | **权威口径改为 `readme_check.py` 的 `commits` / `code_loc` / `docs_loc`**（README 生成并由 CI 校验），2026-09-06 值为 **~68 commits / ~8.3k 行代码 / ~6.8k 行文档**。以下为 2026-09-06 早些时候的手算，口径写明供对照：`git rev-list --count HEAD` = **61 commits**，`e734981`(2026-08-22) → `509b461`(2026-09-06)，**16 天**。已跟踪文件行数：`scripts/*.py` **6233**、`scripts/*.sh` **1180**、`tests/*.py` **190**、`tools/*.py` **81** → 代码合计 **7684 行**；`docs/*.md` **6047 行**；`scenarios/*.yaml` **8760 行**（生成物） | **给出实际值** | 可说「**16 天、61 次提交、约 7.7k 行代码与 6k 行设计文档**」。**不要把 scenarios 的 8760 行算进代码**——那是生成器产出的卡片。**不要用 cloc 口径去对**，本表是 `wc -l` 含空行与注释 |

---

## 审计结论

### 一、可直接进简历的原子（证据齐、数字对）

- **A3** 配方 68 卡 / 在库 43 卡 / 五类 / 三轴难度
- **A4** 三探针机器判据 + 残留检查、300 s 基线窗、34 张卡的反算恢复窗、无 SDK 双臂、Envoy 边识别
- **A5** 每卡 8 件证据包 + sha256 可复算、评测离线只读
- **A6** 7 条检测规则 + 100 ms 地板 + 干净窗负对照
- **B1** 手写工具循环、6 只读工具 + submit、claude-sonnet-5
- **B3** 泄漏断言 fail-closed 且在第一个 token 之前（口径见边界栏）
- **B5** OpenTelemetry 追踪与落点隔离（**不能写「生产级可观测性」**，见 B5 边界栏）
- **B4** prompt 一次迭代冻结 52.6% → 57.9%
- **C2 / C3 / C4 / C5 / C6** 两基线、16 卡留出集、43 卡全集、成本延迟步数、过拟合 −16.7 与 blackhole 0/6
- **C7** 开箱 haiku vs 调优 sonnet 的五臂对照（**口径受限，措辞按 C7 那一栏，不能写成「模型对照」**）
- **D1** CI 三道离线门
- **D3** 8 条 finding / 4 类失败模式 / 31 条决策

### 二、需修正后才能用（数字或说法与仓库不符）

| 编号 | 候选写法 | 应改为 |
| --- | --- | --- |
| A1 | 「服务数 N」含糊、易被读成 25 个微服务 | **25 容器 / 16 可注入靶子 / 其中 13 个自研应用服务** |
| A2 | 「五类故障原语」 | **四个原语脚本覆盖五类故障**（`set_flag` 兼管 misconfig 与 mem_leak） |
| B2 | 「四道保险，各自计数」 | **五道保险**；其中三道有计数器，两道熔断记在 `terminated` |
| B3 | 「task_view = trigger + symptom + alerts」 | **trigger + `agent_visible_symptom`**，alerts 在后者内部 |
| C1 | 「四指标」 | 实际 **6 列**；改说具体指标名 |
| D2 | 「审计 25 路径」 | **27 条路径**（25 读码 + 2 批次撞出），7 处修复 |
| D4 | 「失败即停」 | **连续失败到阈值才中止**（recovered 连挂 2 / 门连挂 3） |
| D4 | 量产总机时未填 | **9.03 小时 / 7 个批次** |
| E1 | API 花费未填 | **$9.07 / 237 次单卡运行**（仅限已记账的运行；由 `readme_check.py` 生成、CI 校验） |
| E2 | 仓库规模未填 | **61 commits / 16 天 / 约 7.7k 行代码 / 6k 行文档** |

### 三、无证据或证据不足（不要写进简历）

| 编号 | 缺什么 |
| --- | --- |
| — | **「判据准确」这类说法没有独立验证。** 判据是否判对，目前只有「批次跑完人看日志」与离线回放，**没有对照标注集**。O-P2-21 的假失败率 12.5% 是**算出来的、从未直接测量**（要 N≥20 次空周期，约 2.7 小时机时）。**不能说「判据经过验证」，只能说「判据每一处取不到值的路径都被审计过」** |
| — | **规则基线在 misconfig / mem_leak 上是否过拟合，本轮测不了。** 两个留出集里这两类各 0 张。C6 的结论只覆盖 blackhole / crash / latency |
| — | **agent 与人类基线没有对照。** 全仓没有任何人工诊断的耗时或准确率记录，**不能说「接近/超过人工」** |
| — | **~~没有跨模型对照~~ → 已部分覆盖（2026-09-06，决策 034）：开箱 haiku 与调优 sonnet 两点，非多家、非同配置。** 见 C7。仍然**不能说**的是：「模型对照」「量化了模型本身的增益」「对比了多家模型」——两臂之间同时差着模型、thinking 与 effort 三样，且只有 Anthropic 一家、两个档位 |
| — | **`latency-3000` 高档与 `misconfig` 多变体档的多数卡未入库**（在库 43 里 latency 只有 10 张、misconfig 7 张），**不能说「覆盖全部档位」** |
| — | **valkey-cart 四张卡目前仍未入库**：埋点已恢复（决策 030），但 batch6 三张连挂后中止，**不能说「四张卡已恢复入库」** |

---

**复现本审计**：本文每一行的证据定位都是可直接执行的路径或命令；
数字部分可用 `scripts/scenarios/recipe.csv`、`scenarios/*.yaml` 的
`production.probe.verdict`、`artifacts/agent_runs/*/report.md` 三处重算。
