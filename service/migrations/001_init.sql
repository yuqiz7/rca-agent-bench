-- 001_init.sql -- the six tables of docs/design/service_v1.md §3, plus the
-- migration ledger itself.
--
-- ORDER DIFFERS FROM THE DESIGN DOC, DDL DOES NOT. §3 presents `runs` first
-- because it is the interesting table, but runs.card_id REFERENCES cards(card_id),
-- so `cards` has to exist first for this file to apply. The statements themselves
-- are the design's, character for character.
--
-- `schema_migrations` is declared here as well as bootstrapped by
-- app/migrate.py. Both use IF NOT EXISTS, so whichever runs first wins and the
-- other is a no-op. The duplication is deliberate: the runner needs the ledger
-- before it can read the ledger, and this file needs to stay a complete
-- description of the database -- `psql -f 001_init.sql` on an empty database has
-- to produce the real schema, not one table short of it.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version    text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

-- 卡的快照，不是权威。权威永远是 scenarios/*.yaml。
--
-- No ground_truth column, on purpose: the answer exists only in scenarios/*.yaml
-- and in process memory at the moment of grading. No SQL injection, no
-- misconfigured read-only account and no GET /cards can reach it from here.
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
