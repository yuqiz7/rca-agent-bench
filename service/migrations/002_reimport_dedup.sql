-- 002_reimport_dedup.sql -- make "already imported" a database fact.
--
-- §3 gives runs an `artifact_path` and POST /admin/reimport is specified to
-- deduplicate on it. On disk that key is not unique on its own: run_agent.py
-- writes one file per card, but the two baseline writers
-- (scripts/baselines/keyword_heuristic.py -> baseline1.json,
--  single_shot_llm.py -> baseline2.json) each write ONE file holding a LIST of
-- per-card rows, so a single path legitimately backs 27 different runs. The
-- unique key is therefore the pair.
--
-- Enforcing it here rather than only in the importer's in-memory set is the
-- point: the check and the insert are not atomic across two concurrent callers,
-- and a duplicated run would silently double every count and every dollar in
-- /summary. Partial on NOT NULL so runs the service creates itself -- which have
-- no artefact until they finish -- are unconstrained.

CREATE UNIQUE INDEX runs_artifact ON runs(artifact_path, card_id)
  WHERE artifact_path IS NOT NULL;
