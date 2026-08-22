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
