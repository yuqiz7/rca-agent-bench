# rca-agent-bench 服务化 v1 设计（2026-09-06 草案）

**状态**：**已实现（2026-09-07 ET）**。代码落在 `000f4c6`（步骤 1）到 `66e3ea8`（步骤 7）
共 7 个 commit，加上本节所属的落痕 commit（步骤 8）。
四道 CI 门与本地 DB / 端到端门全过。
**读者**：一半后端 / SWE，一半 AI Engineer。因此每节先给结论，再给放弃的方案与代价 ——
**看得出取舍的地方比看得出功能的地方更值钱**。

---

## 0. 实现与设计的差异

本文档是**施工前**写的，落码时做了一批它没有覆盖的判断 ——
schema 的 DDL 顺序、唯一索引建在哪一对列上、四道检查的先后、
`RunRef.status` 的取值范围、`/healthz` 在数据库不可达时报什么、
`pick` 的第三个口径、测试为什么用一次性 schema 而不是事务回滚，等等。

**那些判断逐条记在 `docs/decisions.md` 的决策 036「落地时做的判断」一节，本文不重复。**
本文与 036 的分工：**本文说要建什么，036 说建的时候做了哪些本文没写的选择、以及为什么。**
本文正文保持施工前的原样（包括 §7 的工时估算），**不回填实测值** ——
一份被事后改成「预言成真」的设计文档，就不再能说明当时想到了什么、没想到什么。

已知的实现级缺口另记在 `docs/open_items.md`：
**O-P2-25**（崩溃回收备注不耐久）、**O-P2-26**（`duration_ms`/`latency_s` 全 NULL）、
**O-P2-27**（starlette 警告）、**O-P2-28**（GCP 防火墙未从 API 侧核实）、
**O-P2-29**（宿主临时端口归属，结论：不属本仓）。

简历措辞的硬边界见 `docs/evidence_audit.md` **B6 / D5**，不是本文 §8。
§8 是措辞上限的**草案**，B6/D5 是核过证据之后的**定稿**，两者冲突时以 B6/D5 为准。

---

## 1. 定位与服务边界

**一句话**：把已经能跑的评测能力包成一个 HTTP 服务 ——
**收一个卡引用，异步跑一条臂，把诊断结果、判分、每步 trace 存进 Postgres 并可查**。

### 做什么

| 能力 | 说明 |
| --- | --- |
| 提交一次运行 | 给定 `card_id` + `arm`（`agent` / `single_shot` / `rules`）+ 可选 `model`，异步执行，返回 `run_id` |
| 查一次运行 | 状态、答案、判分、成本、步数、终止原因 |
| 查历史 | 按 `card_id` / `arm` / 时间过滤，分页 |
| 查卡 | 在库卡清单与单卡元数据（**不含 ground truth**，见下） |
| 五臂汇总 | 给定 cardset，返回与 `compare_arms.py` 同口径的四指标表 |
| 查 trace 层级 | 一次运行的 card → step → model.call / tool.\* 树 |

### 不做什么（**这一栏比上一栏重要**）

- **不注入故障**。服务永远不调用 `scripts/primitives/`，不碰 `run_batch.py`，不申请那把串行锁。
  故障注入是**有状态、会改动共享测试床**的操作，把它放进一个可被 HTTP 触发的服务里，
  等于给一个能改测试床的按钮开了网络入口。**批次仍然只由人在 VM 上手工挂。**
- **不碰测试床后端**。服务不读实时 Prometheus / Jaeger / OpenSearch，只读**已冻结的证据包**。
  这是 v1 全部准确率数字成立的前提（决策 020 的事后快照口径），服务化不能动它。
- **不改判据**。`run_eval.grade`、检测器规则、探针判据一律 import 现有实现，不重写、不参数化。
- **不返回 ground truth**。`GET /cards/{id}` 返回 `card_id` / `class` / `target` / `difficulty`，
  **不返回 `ground_truth`**。理由：服务一旦对外，任何能调 `GET /cards` 的人都能拿到答案，
  而 `leak_check` 守的正是这条线。判分在服务端做，答案不出服务。

### 放弃的方案

- **放弃「服务也能触发批次」**。它会让这个服务从「只读证据 + 花钱调模型」升级成
  「能改测试床状态」，安全面和故障面都翻倍，而它换来的只是省掉一次 SSH。
- **放弃「服务即评测集分发」**（对外提供证据包下载）。证据包单卡约 10.7 MB、
  含真值线索，且 `.gitignore` 已把三件大文件挡在库外；对外分发是另一个产品决定，不是 v1。

---

## 2. API 设计

**结论**：REST + JSON，**异步提交-轮询**模型，Pydantic v2 做 schema，FastAPI 自动出 OpenAPI。
**无 WebSocket、无 SSE、无 GraphQL。**

### 端点表

| 方法 | 路径 | 用途 | 成功码 |
| --- | --- | --- | ---: |
| `POST` | `/runs` | 提交一次运行 | **202** |
| `GET` | `/runs/{run_id}` | 查一次运行 | 200 / 404 |
| `GET` | `/runs?card=&arm=&status=&limit=&cursor=` | 列出历史运行 | 200 |
| `GET` | `/runs/{run_id}/trace` | 该运行的 span 树 | 200 / 404 |
| `GET` | `/cards?cardset=` | 卡清单（无真值） | 200 |
| `GET` | `/cards/{card_id}` | 单卡元数据（无真值） | 200 / 404 |
| `GET` | `/summary?cardset=&arms=` | 多臂四指标汇总 | 200 |
| `GET` | `/healthz` | 存活 + DB 连通 + 预算余量 | 200 / 503 |

### 请求 / 响应 schema（Pydantic）

```python
class RunCreate(BaseModel):
    card_id: str
    arm: Literal["agent", "single_shot", "rules"]
    model: str | None = None          # None -> config.yaml 的 models.primary
    idempotency_key: str | None = None

class RunRef(BaseModel):              # 202 的响应体
    run_id: UUID
    status: Literal["queued"]
    poll: str                         # "/runs/{run_id}"

class Grade(BaseModel):
    top1_ok: bool
    service_ok: bool

class Answer(BaseModel):
    service: str
    fault_type: str

class Run(BaseModel):
    run_id: UUID
    card_id: str
    arm: str
    model: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    answer: Answer | None
    grade: Grade | None
    terminated: str | None            # submit / max_steps / cost_cap / no_submit / api_error
    steps: int | None
    cost_usd: float | None
    wall_s: float | None
    counters: dict[str, int] | None   # 五道保险
    error: str | None

class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
```

### 错误体（**全端点统一一种形状**）

```json
{"error": {"code": "card_not_found",
           "message": "no card 'crash-foo-01' in the in-stock set",
           "detail": {"card_id": "crash-foo-01"}}}
```

`code` 是**稳定的机器可读串**（`card_not_found` / `arm_unknown` / `budget_exceeded` /
`idempotency_conflict` / `evidence_missing`），`message` 给人看，`detail` 可选。
**HTTP 状态码不承载语义细节** —— 400 家族全部带 `code`，客户端读 `code` 不读文案。

### 为什么异步

单卡实测 **10–100 s**（本仓 43 卡实测 p95：agent-sonnet 63.8 s、agent-haiku 93.0 s，
单次 model.call 最长见过 5.6 s，20 步的卡能跑满 100 s）。同步返回意味着：
一个 100 s 的 HTTP 请求要穿过 SSH 隧道 / 反代 / 客户端超时三层默认值（多数是 30–60 s），
**正常完成的运行会被当成超时失败**。所以 `POST /runs` 立刻返回 **202 + run_id**，
执行在后台，客户端轮询 `GET /runs/{id}`。

### 为什么不上 WebSocket / SSE

v1 的消费者是**面试演示时的 curl 和一个静态页面**，轮询间隔 2 s、单次运行 ~50 次轮询，
负载可以忽略。SSE 会引入连接生命周期、重连、背压三个新问题，
**换来的只是「进度条更顺滑」**。真需要实时时再加，`GET /runs/{id}` 的 `status` 字段
已经是它的数据基础，加 SSE 不需要改数据模型。

### 幂等键

`POST /runs` 可带 `idempotency_key`。服务在 `runs` 上建 `UNIQUE(idempotency_key)`
（partial index，忽略 NULL）：
- 同 key、同 body → 返回**原来那条** run（202，带原 `run_id`）；
- 同 key、不同 body → **409 `idempotency_conflict`**。

**为什么必须有**：每次 `POST /runs` 都会真的花钱调模型。轮询客户端在网络抖动时重发，
或演示时手快按两下，就是两次计费。**幂等键是这里唯一的止损**。

### 分页

游标分页，不是 offset。`cursor` = base64(`created_at`,`run_id`)，
`ORDER BY created_at DESC, run_id DESC`。
**为什么不用 offset**：运行是持续插入的，offset 分页在插入时会重复/跳过行。
数据量很小（几百行），但游标分页多写的代码不到 15 行，**不值得为了省 15 行留一个已知错**。

### 放弃的方案

- **放弃同步端点**（哪怕只给「快臂」`rules`）。规则臂 0.4 s，同步很诱人，
  但**两条路径两套错误处理**，而统一成异步只让规则臂多等一个轮询周期。
- **放弃 GraphQL**。八个端点、一个消费者，GraphQL 的收益（客户端自选字段）为零，
  成本（schema、resolver、N+1）为正。
- **放弃在 v1 做认证以外的多租户 / 用户体系。** 见 §5：v1 根本不开公网。

---

## 3. 数据模型

**结论**：Postgres **是服务态的权威**（状态、队列、可查询投影），
`artifacts/agent_runs/*.json` **仍然是评测证据件**，两者**双写**，不做单向迁移。
迁移工具用**手写 SQL**，不用 Alembic。

### 表

```sql
-- 一次运行。队列状态也在这张表上，见 §4。
CREATE TABLE runs (
  run_id          uuid PRIMARY KEY,
  card_id         text NOT NULL REFERENCES cards(card_id),
  arm             text NOT NULL,                    -- agent | single_shot | rules
  model           text NOT NULL,
  status          text NOT NULL,                    -- queued|running|succeeded|failed
  idempotency_key text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz,
  answer_service  text,
  answer_fault    text,
  terminated      text,
  steps           int,
  cost_usd        numeric(10,6),
  wall_s          numeric(10,3),
  counters        jsonb,                            -- 五道保险
  error           text,
  artifact_path   text,                             -- 对应的 JSON 证据件路径
  run_config      jsonb                             -- 冻结当次的 run 参数
);
CREATE UNIQUE INDEX runs_idem ON runs(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX runs_card_arm_created ON runs(card_id, arm, created_at DESC);
CREATE INDEX runs_created ON runs(created_at DESC);
CREATE INDEX runs_queue ON runs(status, created_at) WHERE status = 'queued';

-- 判分。与 runs 一对一，但单独一张表：判分是评测器写的，运行是执行器写的，
-- 两者的作者不同（见 §4 的 leak 边界），分表让「谁能写这一行」在 schema 上就分开。
CREATE TABLE grades (
  run_id     uuid PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
  top1_ok    boolean NOT NULL,
  service_ok boolean NOT NULL,
  graded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE steps (
  run_id   uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  step_no  int  NOT NULL,
  duration_ms numeric(12,3),
  PRIMARY KEY (run_id, step_no)
);

CREATE TABLE model_calls (
  id            bigserial PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  step_no       int  NOT NULL,
  model         text NOT NULL,
  input_tokens  int, output_tokens int,
  cache_write_tokens int, cache_read_tokens int,
  step_cost_usd numeric(10,6),
  stop_reason   text,
  latency_s     numeric(10,3)
);
CREATE INDEX model_calls_run ON model_calls(run_id, step_no);

CREATE TABLE tool_calls (
  id            bigserial PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  step_no       int  NOT NULL,
  tool          text NOT NULL,
  args_digest   text,
  result_bytes  int,
  ok            boolean,
  validation_reject boolean NOT NULL DEFAULT false,
  retried       boolean NOT NULL DEFAULT false,
  error         text
);
CREATE INDEX tool_calls_run ON tool_calls(run_id, step_no);

-- 卡的快照，不是权威。权威永远是 scenarios/*.yaml。
CREATE TABLE cards (
  card_id       text PRIMARY KEY,
  class         text NOT NULL,
  target        text NOT NULL,
  primitive     text NOT NULL,
  difficulty    text,
  in_stock      boolean NOT NULL,
  evidence_ok   boolean NOT NULL,      -- 八件是否齐
  snapshot_at   timestamptz NOT NULL DEFAULT now(),
  card_sha256   text                   -- yaml 内容哈希，用来发现漂移
);
```

**`cards` 里没有 `ground_truth` 列。** 这是故意的：真值只在
`scenarios/*.yaml` 与判分那一刻的进程内存里存在，**不进服务的数据库**，
因此任何 SQL 注入、任何误配的只读账号、任何 `GET /cards` 都拿不到答案。
判分时由服务进程现读 yaml。

### 索引理由

- `runs_card_arm_created`：`GET /runs?card=&arm=` 与 `/summary` 的主查询形状。
- `runs_queue`（partial）：worker 每 2 s 扫一次队列，只关心 `status='queued'`，
  partial index 让这张表长到几万行时扫描代价仍然是常数级。
- `model_calls_run` / `tool_calls_run`：`GET /runs/{id}/trace` 按 run 取全部子行。

### 与现有 JSON 的关系：**双写，不迁移**

| | 权威范围 |
| --- | --- |
| **Postgres** | **服务态**：运行状态、队列、可查询的历史与汇总 |
| **`artifacts/agent_runs/<run-id>/*.json` + `traces.jsonl`** | **评测证据件**：findings 引用的、`readme_check` 读的、commit 里能 diff 的那一份 |

**为什么不单向迁移到 Postgres**：`docs/findings.md`、`README.md`、
`scripts/tools/readme_check.py`、`scripts/baselines/compare_arms.py`
**全都从 JSON 读数**，而 `readme_check` 是 CI 门之一、从 **HEAD** 读。
把权威迁进数据库，等于让「README 的数字是否属实」依赖一个跑着的数据库 ——
**一个 checkout 就不再能自证**。这是本仓一路守着的性质，不能为服务化让路。

**为什么不只写 JSON、让服务查文件**：`GET /runs?card=&arm=` 与 `/summary`
在文件上是全目录扫描 + 反复解析（现在已有 200+ 个 JSON），
而这正是数据库存在的理由。

**双写的代价（写在明处）**：两份数据可能不一致。处置：
JSON 由现有 `write_result()` 写（不改），Postgres 行在**同一个函数返回之后**写，
失败则 run 标 `failed` 并记 `error`，**JSON 仍在**。
即**JSON 是可以没有数据库行的，数据库行不可以没有 JSON**。
另提供 `POST /admin/reimport`（本机 only）从 `artifacts/` 回填历史运行。

### 迁移工具：**手写 SQL**

`migrations/001_init.sql`、`002_*.sql`…，一张 `schema_migrations(version, applied_at)`，
启动时按序应用未应用的文件。约 30 行 runner。

**为什么不是 Alembic**：Alembic 的价值在**长期、多人、多分支**的增量迁移与自动 diff。
这里是一个人、一天、六张表、v1 之后大概率只加列不改列。
引入它要付：一个依赖、`env.py` 配置、autogenerate 的假阳性、
以及「模型定义与迁移不同步」这类新故障模式。
**但这是一个可辩护的反向选择**：如果目标是让后端面试官看到 Alembic 这个词，
成本约 25 分钟，我可以改。**见文末待拍板 ②。**

---

## 4. 执行与隔离

**结论**：**Postgres 队列表 + 单进程 worker**（`SELECT ... FOR UPDATE SKIP LOCKED`），
不用 FastAPI `BackgroundTasks`，不用进程池，不引 Celery/Redis/RQ。

### 为什么

`BackgroundTasks` 的任务活在 web 进程里：**API 一重启，正在跑的运行就人间蒸发**，
数据库里留下一行永远 `running` 的僵尸。而这个服务的任务一次跑 10–100 s、
每次花真钱，**丢任务不是「重试一下」的事**。

队列表方案的全部代码是：`runs.status` 多两个值 + 一个 worker 循环：

```sql
UPDATE runs SET status='running', started_at=now()
WHERE run_id = (SELECT run_id FROM runs WHERE status='queued'
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```

**它同时解决了三件事**：崩溃后可恢复（启动时把 `running` 且超时的行改回 `queued`）、
天然的并发上限（worker 数即上限）、不需要第二个中间件。

### 放弃的方案

- **放弃 `BackgroundTasks`** —— 见上，丢任务。
- **放弃进程池 / `multiprocessing`** —— 它解决的是 CPU 并行，而这里 99% 时间在等 API。
  且进程池同样活在 web 进程里，重启即丢。
- **放弃 Celery + Redis** —— 两个新组件、一个新协议、一份新部署，
  换来的能力（重试策略、定时、扇出）**v1 一个都不需要**。
  这是本设计里最容易被「看起来专业」诱惑的地方，明确拒绝。

### 并发与成本熔断（**三层，缺一不可**）

| 层 | 值 | 来源 |
| --- | --- | --- |
| 单卡上限 | `$0.20` | 沿用 `config.yaml` 的 `max_usd_per_card`，**服务不覆盖** |
| 并发上限 | **worker = 1** | 一次只跑一张卡；演示够用，且让「今天最多花多少」可算 |
| 每日预算 | **`$5.00`**（env `RCA_DAILY_BUDGET_USD`） | 服务态新增：`SUM(cost_usd) WHERE created_at > date_trunc('day', now())` |

超预算时 `POST /runs` 返回 **429 `budget_exceeded`**，`GET /healthz` 的
`budget_remaining_usd` 变 0 但仍返回 200（**服务是健康的，只是不接活**）。

**为什么日预算必须在服务里而不只在 config 里**：`max_usd_per_card` 挡的是一张卡失控，
挡不住**有人循环 POST 一千次**。服务化把「谁能触发计费」从「能 ssh 的人」
放宽到「能发 HTTP 的人」，**必须补一道以时间为单位的闸**。

### 代码复用路径

服务 **import 现有实现，不复制一行**：

```
service/app/runner.py
    from run_agent import run_card, load_config, load_prices, tracing_enabled
    from run_eval import grade, ground_truth
    from single_shot_llm import run_card as single_shot_run_card
    from keyword_heuristic import ...            # rules arm
```

`sys.path` 追加 `scripts/agent` / `scripts/harness` / `scripts/baselines`，
与 `run_eval.py` 现在的做法一致。**判据、prompt、熔断、计价全部是同一份代码** ——
服务跑出来的数字与 CLI 跑出来的必须逐位相同，否则 §4 那些表就不能引用服务的结果。

### leak_check 在服务入口再过一次

`run_agent.run_card` 内部已经在第一个 token 之前调 `check_payload`（fail-closed）。
服务**再加一道入口检查**：`POST /runs` 收到 `card_id` 后，
在入队之前断言该卡的 `task.json` 通过 `leak_check.check_card`，
不通过直接 **422 `evidence_leak`**，不入队、不花钱。

**为什么要两道**：内层那道守的是「答案不进模型」，
外层这道守的是「一个证据包坏了的卡不该进入一个会自动重试的系统」。
服务化引入了「自动、批量、无人看着」这三个属性，
**在这种环境里，早失败比省一次检查重要**。

---

## 5. 部署

**结论**：docker compose 起 `api` + `postgres`（+ 已存在的 `jaeger-agent-obs`），
**不开公网端口**，演示走 **SSH 端口转发**。compose `restart: unless-stopped`，
**不写 systemd unit**。

### compose 形状

```yaml
# docker-compose.service.yml —— 与测试床 compose、与 agent-obs compose 三者互不合并
services:
  db:
    image: postgres:16-alpine
    environment: [POSTGRES_DB=rca, POSTGRES_USER=rca, POSTGRES_PASSWORD_FILE=/run/secrets/pg]
    volumes: ["rca_pg:/var/lib/postgresql/data"]
    # 不映射端口到宿主：只有 api 需要它
  api:
    build: ./service
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}     # 从 VM 环境变量注入，不进镜像
      RCA_DB_URL: postgresql://rca@db/rca
      RCA_DAILY_BUDGET_USD: "5.00"
    volumes:
      - ./evidence:/repo/evidence:ro              # 证据包只读挂载
      - ./scenarios:/repo/scenarios:ro            # 真值只读，判分用
      - ./artifacts/agent_runs:/repo/artifacts/agent_runs   # JSON 证据件要写
    ports: ["127.0.0.1:8000:8000"]                # 只绑回环
    restart: unless-stopped
volumes: { rca_pg: {} }
```

**端口 8000 只绑 `127.0.0.1`。** 宿主已占用 8080/9090/9200/16686/16687/4327/4328，8000 空闲。

### secrets

`ANTHROPIC_API_KEY` **只从 VM 的 shell 环境注入**（`~/.bashrc` 已有），
经 compose 的 `${...}` 传进容器环境。**不进镜像、不进仓库、不进 compose 文件字面量**。
Postgres 密码走 docker secret 文件，`.gitignore` 挡住 `secrets/`。
**镜像里不 COPY `.env`，Dockerfile 不接受 `--build-arg` 形式的 key**
（build arg 会留在镜像层历史里）。

### 对外暴露：**取 (ii) 不开公网**

| | (i) 公网端口 + Bearer + 限流 | **(ii) 不开公网，SSH 转发** |
| --- | --- | --- |
| 演示 | 给个 URL 就能点 | `ssh -L 8000:localhost:8000 vm` 后本地打开 |
| GCP 防火墙 | 要开一条入站规则 | **不动防火墙** |
| 被刷钱风险 | **真实**：每个 `POST /runs` 都是计费的模型调用，token 泄漏 = 有人替你花钱 | **结构上不存在** |
| 认证代码 | 要写 + 要测 + 要轮换 | 0 行 |
| 工期 | +40 min | 0 |

**取 (ii)。** 决定性的一条不是工期，是**风险的形状**：
公网 + 自动计费的组合下，一次 token 泄漏的损失没有上界（日预算能兜住，
但那是最后一道，不是第一道）。而这个服务的真实用户是**面试时的我自己**，
SSH 转发完全够用。**见文末待拍板 ①。**

### restart 策略：compose，不写 systemd

`restart: unless-stopped` + Docker daemon 开机自启，已经覆盖「VM 重启后服务回来」。
再写一份 systemd unit 会出现**两个都想管这个容器的东西**。
现有 `wakeup.sh` 也不管它 —— 与 `jaeger-agent-obs` 同样的理由：**它不是测试床的一部分**。

### 日志

结构化 JSON 打到 stdout，交给 docker 的 json-file driver（`max-size=10m`, `max-file=3`）。
**不落文件、不接 ELK**。要看就 `docker compose logs`。
每条日志带 `run_id`，与 trace 的 `rca.run_id` 属性同值 —— **日志和 trace 用同一个 join key**。

---

## 6. 测试与 CI

**结论**：
- **单元 / 仓储层用真 Postgres（compose 起的那个），不用 SQLite、不用 testcontainers**；
- **契约测试（OpenAPI 快照）+ schema 单测进 CI 第四道门**；
- **DB 测试与端到端 smoke 是本地门，不进 CI**。

### 为什么仓储层不用 SQLite

schema 用了 `FOR UPDATE SKIP LOCKED`、`jsonb`、partial unique index、`timestamptz` ——
**SQLite 一个都不支持**。用 SQLite 测出来的绿，恰好在最容易出错的地方（队列并发）无效。

### 为什么不用 testcontainers

它需要在测试时**拉镜像**，而本仓 CI 的第一条硬规则是
「三道门全部离线，不许碰网络、不许碰 VM」（`.github/workflows/ci.yml` 文件头）。
testcontainers 会直接违反它。**本地已经有 compose 起的 Postgres**，
本地 DB 测试连它即可。

### CI 第四道门：**只加离线的那一半**

```yaml
- name: "gate 4: API contract"
  run: python -m pytest tests/test_api_contract.py -q
```

它做两件不需要网络也不需要 DB 的事：
1. **Pydantic schema 单测** —— 请求/响应模型的必填、枚举、错误体形状；
2. **OpenAPI 快照** —— `app.openapi()` 与 `tests/fixtures/openapi.json` 逐字节比对，
   变了就红。**这是给「API 契约不能悄悄变」这件事的门**，
   与门 2（生成器幂等）是同一种思路。

### 端到端 smoke：本地门

`make smoke`：起 compose → 等 `/healthz` → `POST /runs`（一张开发集卡，`arm=rules`，
**零 API 花费**）→ 轮询到 `succeeded` → 断言 `grade` 与 CLI 跑同卡一致。
**用 `rules` 臂**是刻意的：端到端要验的是「HTTP → 队列 → worker → 判分 → DB → 查询」这条链路，
不是模型准确率；用零成本的臂让这条 smoke **可以随便跑**。
另有一条 `make smoke-agent`（一张卡、约 $0.07）人工按需跑。

---

## 7. 明日施工序

总计 **约 5 小时 15 分工作 + 约 25 分固定等待**，落在一个工作日内。
每步都以「三道门 + readme_check 全过」结束。**新组件下限 25 min。**

| # | 步骤 | 交付 | 固定等待 | 工作 |
| ---: | --- | --- | ---: | ---: |
| 1 | 骨架与 schema | `service/` 目录、Dockerfile、Pydantic 模型、`migrations/001_init.sql` + runner、`GET /healthz`、compose 起 db+api | 5 min（拉镜像） | **45 min** |
| 2 | 仓储层 + 回填 | repository 函数（runs/grades/steps/model_calls/tool_calls/cards）、`cards` 快照同步、`POST /admin/reimport` 把现有 `artifacts/agent_runs` 灌进 DB | 0 | **40 min** |
| 3 | 提交与执行 | `POST /runs`（含幂等键、入口 leak_check、日预算）、队列表 worker、崩溃恢复、三臂 runner 适配层 | 0 | **60 min** |
| 4 | 查询端点 | `GET /runs/{id}`、`/runs`（游标分页）、`/cards`、`/cards/{id}`、`/summary`、`/runs/{id}/trace` | 0 | **50 min** |
| 5 | 测试 | schema 单测、OpenAPI 快照、仓储层 DB 测试、`make smoke`（rules 臂） | 0 | **45 min** |
| 6 | CI 第四道门 | `ci.yml` 加 gate 4（离线契约测试），本地门写进 `docs/workflow.md` | 5 min（CI 跑） | **25 min** |
| 7 | 部署与验证 | VM 上起 compose、`ssh -L` 验证、`POST` 一张 agent 卡端到端（约 $0.07）、日志与 restart 验证 | 10 min（首次构建） | **35 min** |
| 8 | 落痕 | `decisions.md` 036 正式版、`docs/design/service_v1.md` 状态改「已实现」、README 折叠区一行、`evidence_audit` B6/D5 | 5 min | **35 min** |
| | **合计** | | **25 min** | **5 h 15 min** |

**如果时间不够，砍的顺序**：先砍步骤 6（CI 门，改本地门）→ 再砍 `/summary`（步骤 4 的一半）
→ 再砍 `POST /admin/reimport`（步骤 2 的一半）。
**不砍**步骤 3 的日预算与幂等键，**不砍**步骤 5 的 smoke。

---

## 8. 简历措辞上限（预写 evidence_audit B6 / D5）

### 可写

- 「把评测能力包成 **REST API（FastAPI + Pydantic）**，**PostgreSQL** 持久化运行、
  每步模型调用与工具调用，**docker compose 一键部署**到一台 GCP VM」
- 「**异步任务模型**：提交返回 202 + run_id，Postgres 队列表（`FOR UPDATE SKIP LOCKED`）
  驱动 worker，崩溃后可恢复」
- 「**三层成本熔断**：单卡上限、并发上限、每日预算，超限返回 429」
- 「**幂等键**防重复提交造成的重复计费」
- 「服务 import 评测器现有实现而非复制，保证 API 与 CLI 的数字逐位一致」
- 「**真值不进服务数据库**，判分在服务端完成，`GET /cards` 不返回答案」

### 不能写

- ❌ **「生产级」「production-ready」** —— 单 worker、无认证、不开公网、无监控告警、
  无备份策略、无水平扩展。
- ❌ **「高并发」「可扩展」** —— 并发上限就是 1，且是故意的。
- ❌ **「微服务架构」** —— 是一个进程加一个数据库。
- ❌ **「CI/CD 流水线」** —— CI 只有四道离线门，没有 CD，部署是手工 `docker compose up -d`。
- ❌ 任何暗示**对外开放**的说法 —— v1 只绑回环。
- ⚠️ 提到 Postgres 时**要能答上来「为什么不用 SQLite」**（`SKIP LOCKED`、`jsonb`、
  partial index），否则这一条会变成扣分项。

---

## 附：decisions 036 草案（**明日开工时正式追加到 `docs/decisions.md`**）

> ## 036 评测服务化 v1：REST + Postgres + compose，单机不开公网（2026-09-07 ET）
>
> **选了什么**
> 1. **FastAPI + Pydantic v2 + PostgreSQL 16 + docker compose**，部署在现有 GCP VM。
> 2. **异步提交-轮询**：`POST /runs` → 202 + `run_id`，`GET /runs/{id}` 轮询。
> 3. **Postgres 队列表 + 单 worker**（`FOR UPDATE SKIP LOCKED`），不引 Celery/Redis。
> 4. **Postgres 是服务态权威，`artifacts/agent_runs/*.json` 仍是评测证据件，双写。**
> 5. **不开公网**，演示走 SSH 端口转发。
> 6. **服务不注入故障、不碰测试床、不改判据、不返回 ground truth。**
>
> **为什么**
> - **异步**：单卡 10–100 s，同步会被 SSH/反代/客户端的 30–60 s 默认超时截断，
>   正常完成的运行会被读成失败。
> - **队列表而不是 `BackgroundTasks`**：后者的任务活在 web 进程里，重启即丢，
>   而每个任务花真钱且要跑一分钟以上，丢任务不是「重试一下」的事。
> - **双写而不是迁移**：`readme_check`（CI 门）与 findings 的全部数字都从 **HEAD 的 JSON** 读。
>   把权威搬进数据库，等于让「README 的数字是否属实」依赖一个跑着的数据库 ——
>   **一个 checkout 就不再能自证**。
> - **不开公网**：每个 `POST /runs` 都是计费的模型调用。公网 + 自动计费的组合下，
>   一次 token 泄漏的损失没有上界。真实用户只有面试时的我自己，SSH 转发够用。
> - **真值不进 DB**：服务一旦对外，能查 `GET /cards` 的人就能拿答案，
>   而 `leak_check` 守的正是这条线。判分时现读 yaml。
>
> **放弃了什么**
> - **放弃 Celery + Redis**：两个新组件换来的重试/定时/扇出，v1 一个都不需要。
>   这是本设计里最容易被「看起来专业」诱惑的地方。
> - **放弃 Alembic**：六张表、一个人、一天。它的价值在长期多分支增量迁移，
>   代价是依赖 + `env.py` + autogenerate 假阳性。手写 SQL + `schema_migrations` 约 30 行。
>   **这是可辩护的反向选择，若为简历口径需要，改用 Alembic 约 25 分钟。**
> - **放弃 SQLite 做测试库**：schema 依赖 `SKIP LOCKED` / `jsonb` / partial index，
>   SQLite 一个都不支持，绿灯恰好在最易错处无效。
> - **放弃 testcontainers**：要拉镜像，违反「CI 三道门全部离线」这条硬规则。
> - **放弃服务触发批次**：那会让服务从「只读证据 + 花钱」升级成「能改测试床状态」。
> - **放弃 WebSocket/SSE**：消费者是 curl 和一个静态页，轮询足够；
>   `status` 字段已是将来加 SSE 的数据基础。
>
> **trade-off**
> - **双写会不一致。** 约定：JSON 可以没有 DB 行，DB 行不可以没有 JSON；
>   写 DB 失败时 run 标 `failed` 但 JSON 仍在。多一条要人看的路径。
> - **单 worker 意味着串行。** 演示够用，但「跑一遍 43 卡」在服务上要 45 分钟以上，
>   而 CLI 并行跑更快。**服务不是用来跑全量评测的**，全量仍走 CLI。
> - **不开公网 = 演示要多一步 SSH。** 接受。
> - **服务与 CLI 共用 `run_agent`，因此服务的一次改动可能影响 CLI 的数字。**
>   缓解：服务只 import、不改；任何对 `scripts/agent/` 的改动仍按现有流程走门。
> - **`GET /summary` 与 `compare_arms.py` 是同一口径的两份实现。**
>   它们必须一致，而没有测试保证这一点 —— v1 记为已知风险，
>   缓解是 summary 直接复用 `compare_arms.metrics()` 而不是重写聚合。
