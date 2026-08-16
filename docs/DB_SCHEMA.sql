-- ============================================================================
-- cracktrade — persistence schema                                DRAFT v1
-- ============================================================================
-- Status: proposal for review, not yet normative and not applied anywhere.
-- Once approved, the decisions in here get recorded in docs/ENGINE_SPEC.md
-- (the only normative document); this file then becomes the migration source.
-- Companion document: docs/API.md (the HTTP surface this schema serves).
--
-- Target: PostgreSQL 15+. No extensions required (gen_random_uuid() is core).
-- Validated: applies cleanly on 15; smoke-tested — the overview views produce
-- the design's verdict/staleness semantics, and all guard triggers and CHECK
-- constraints reject the mutations they exist to reject.
--
-- Design position
-- ---------------
-- The engine's serialized result objects (cracktrade.serialize.to_dict, spec
-- §13.1) are already the cross-interface contract, derived properties
-- included. The database therefore stores run results VERBATIM as jsonb
-- rather than exploding them into columns:
--
--   * the worker executing the engine is the single writer;
--   * a result is an immutable record of what a run measured, and
--     re-deriving any of it later would use different (retroactively
--     adjusted) price data — the schema must never invite that;
--   * columns are promoted out of jsonb only where a list view filters or
--     sorts on them (status, kind, version, is_credible, ...).
--
-- The append-only philosophy of the UI ("Restoring never rewinds or deletes")
-- is enforced in the schema, not just promised by the app: strategy versions
-- and run series reject UPDATE/DELETE outright, and runs freeze once they
-- reach a terminal status.
--
-- Single-user assumption: no users/auth tables. If that changes, ownership
-- is one nullable owner_id column on `strategy` — nothing else is shared.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enumerations
-- ----------------------------------------------------------------------------

-- How a strategy came to exist. Drives the lineage crumb in the UI
-- ("authored" / "forked from momentum_breakout · v4" / "promoted from
-- optimization run #14").
CREATE TYPE strategy_origin AS ENUM (
  'authored',   -- created blank/minimal in the New dialog
  'imported',   -- created from an uploaded YAML file
  'forked',     -- copy of another strategy at a chosen version
  'promoted'    -- created from an optimization/validation run's winning config
);

-- How one version of a strategy came to exist.
CREATE TYPE version_origin AS ENUM (
  'created',    -- v1 of an authored strategy
  'imported',   -- v1 of an imported strategy (YAML kept as written)
  'forked',     -- v1 of a fork (copy of parent config)
  'promoted',   -- v1 of a promotion (the run's optimized config)
  'edited',     -- saved from the config editor
  'restored'    -- copy of an older version appended at the head
);

CREATE TYPE run_kind AS ENUM ('backtest', 'optimize', 'walk_forward');

-- 'cancelled' is included pending the open question in docs/API.md §10; it is
-- unused if cancellation is rejected (enum values are cheap to leave, hard to
-- remove).
CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');

-- The four failure categories from the run-failure screen, mapping 1:1 onto
-- engine exit codes (spec §13.3): 2, 3, 4, 5.
CREATE TYPE failure_category AS ENUM (
  'config_invalid',       -- exit 2 — configuration invalid or unreadable
  'market_data',          -- exit 3 — market data unavailable or off-contract
  'engine_failure',       -- exit 4 — simulation or search failed
  'causality_violation'   -- exit 5 — an operation was refused as look-ahead
);


-- ----------------------------------------------------------------------------
-- strategy — identity and lineage. Config lives in strategy_version.
-- ----------------------------------------------------------------------------
-- NOTE: unique names reverse a [DROP] in spec §3.2 ("with no persistence
-- there is no name registry"). Persistence exists now; the UI addresses
-- strategies by name in crumbs and promotion references, so names collide
-- again and must be unique. Record this in the spec on approval.

CREATE TABLE strategy (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 text NOT NULL,
  origin               strategy_origin NOT NULL,

  -- Lineage. Forks record parent + the exact version copied; promotions
  -- record parent + the run whose winning config became this strategy's v1
  -- (FK to run added below — the reference is circular by nature).
  parent_strategy_id   uuid REFERENCES strategy (id),
  parent_version       integer,
  origin_run_id        uuid,

  -- Snapshot taken at promotion time: the parent's walk-forward verdict was
  -- NOT CREDIBLE (or the run was unchecked) when this strategy was created.
  -- "The verdict travels with the promotion" — the detail page shows the
  -- warning until this strategy's own validation passes (derived at read
  -- time; this flag is the frozen historical fact, never updated).
  origin_not_credible  boolean NOT NULL DEFAULT false,

  created_at           timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT strategy_name_unique UNIQUE (name),
  CONSTRAINT strategy_lineage_shape CHECK (
    CASE origin
      WHEN 'forked'   THEN parent_strategy_id IS NOT NULL AND parent_version IS NOT NULL
                           AND origin_run_id IS NULL
      WHEN 'promoted' THEN parent_strategy_id IS NOT NULL AND origin_run_id IS NOT NULL
                           AND parent_version IS NULL
      ELSE                 parent_strategy_id IS NULL AND parent_version IS NULL
                           AND origin_run_id IS NULL
    END
  ),
  CONSTRAINT strategy_warning_only_when_promoted CHECK (
    NOT origin_not_credible OR origin = 'promoted'
  )
);

-- There is deliberately no updated_at and no deleted/archived flag:
-- "edited N hours ago" derives from the head version's created_at, and the
-- API exposes no delete operation of any kind.


-- ----------------------------------------------------------------------------
-- strategy_version — append-only config history. THE core table.
-- ----------------------------------------------------------------------------
-- One row per saved config. Versions are immutable (trigger below), numbered
-- 1..N per strategy with no gaps (app-enforced: insert head+1 inside the same
-- transaction; the unique constraint turns a race into a retryable conflict).

CREATE TABLE strategy_version (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id    uuid NOT NULL REFERENCES strategy (id),
  version        integer NOT NULL CHECK (version >= 1),
  origin         version_origin NOT NULL,

  -- Set only for origin = 'restored': which version was copied forward.
  restored_from  integer,

  -- The validated config, twice:
  --   config       — canonical parsed form (what cracktrade.strategy accepted),
  --                  queryable (head ticker/date-range feed the list view);
  --   config_yaml  — the exact YAML the engine is fed and the YAML pane
  --                  shows/copies. For imports this is the file as written
  --                  ("the file imports as written"); for editor saves it is
  --                  the canonical dump (form and YAML pane are one config).
  config         jsonb NOT NULL,
  config_yaml    text  NOT NULL,

  -- "note — why you changed this (optional)". For imports the API fills it
  -- with the source filename by convention ("imported from momentum_v2.yaml").
  note           text,

  created_at     timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT strategy_version_number_unique UNIQUE (strategy_id, version),
  CONSTRAINT version_restore_shape CHECK (
    (origin = 'restored') = (restored_from IS NOT NULL)
    AND (restored_from IS NULL OR restored_from < version)
  ),
  -- v1 origins are creation origins; later versions can only be edits/restores.
  CONSTRAINT version_origin_matches_position CHECK (
    (version = 1) = (origin IN ('created', 'imported', 'forked', 'promoted'))
  )
);

-- The change summary shown in the History tab ("commission_pct 0.05 → 0.08")
-- and the section-grouped diff view are COMPUTED from config jsonb at read
-- time, never stored: a stored diff can drift from the configs it claims to
-- describe.


-- ----------------------------------------------------------------------------
-- run — one launched execution of the engine against one exact version.
-- ----------------------------------------------------------------------------

CREATE TABLE run (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id       uuid NOT NULL REFERENCES strategy (id),

  -- Runs launch against the head, but the head moves; the run stays pinned
  -- to the exact version it measured. The composite FK guarantees the pair
  -- actually exists — a run can never point at a version of a different
  -- strategy. Staleness ("v5 · stale") is DERIVED: version < current head.
  version           integer NOT NULL,

  -- Per-strategy display number across all kinds ("Optimization run #22",
  -- "Backtest #9"). App assigns max+1 in the launch transaction; the unique
  -- constraint makes a race a retryable conflict, not a duplicate.
  number            integer NOT NULL CHECK (number >= 1),

  kind              run_kind NOT NULL,
  status            run_status NOT NULL DEFAULT 'queued',

  -- The launch request, echoed back by the API. Shapes per kind (defaults
  -- from the engine, exposed via GET /meta):
  --   backtest      {}
  --   optimize      {objective, epochs, cache}
  --   walk_forward  {objective, epochs, folds, scheme}
  params            jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- The seed actually used (recorded even when defaulted) — reproducibility
  -- is a spec-level requirement (D12) and the run header prints it.
  seed              bigint NOT NULL,

  queued_at         timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  finished_at       timestamptz,

  -- Worker-updated while running; drives the "Running now" queue.
  --   {stage: "optimize · generation 4 of 10", percent: 41}
  -- Transient by nature — the only mutable jsonb in the schema.
  progress          jsonb,

  -- On success: cracktrade.serialize.to_dict of the engine result, verbatim —
  --   backtest      → BacktestResult
  --   optimize      → OptimizationResult
  --   walk_forward  → ValidationReport
  -- including contractual derived properties (is_credible, failures,
  -- fold_win_rate, improvement_pct, overfitting_gap_pct, at_bound, ...).
  result            jsonb,

  -- On failure: {category, exit_code, message} where message is the engine's
  -- own error text, preserved verbatim for the failure screen's copy button.
  error             jsonb,
  failure_category  failure_category,

  -- Promoted out of result for list-view filtering. Copied by the worker in
  -- the same UPDATE that lands the result; the guard trigger then freezes
  -- them together.
  is_credible       boolean,   -- walk_forward only: result.is_credible
  suppressed        boolean,   -- backtest/optimize: below the trade floor,
                               -- figures withheld (result.metrics
                               -- .has_enough_trades_to_judge = false)

  CONSTRAINT run_version_exists
    FOREIGN KEY (strategy_id, version)
    REFERENCES strategy_version (strategy_id, version),
  CONSTRAINT run_number_unique UNIQUE (strategy_id, number),

  CONSTRAINT run_result_iff_succeeded  CHECK ((status = 'succeeded') = (result IS NOT NULL)),
  CONSTRAINT run_error_iff_failed      CHECK ((status = 'failed') = (error IS NOT NULL)),
  CONSTRAINT run_category_iff_failed   CHECK ((status = 'failed') = (failure_category IS NOT NULL)),
  CONSTRAINT run_started_when_running  CHECK (status <> 'running' OR started_at IS NOT NULL),
  CONSTRAINT run_finished_when_terminal CHECK (
    (status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)
  ),
  CONSTRAINT run_credible_only_wf      CHECK (is_credible IS NULL OR kind = 'walk_forward'),
  CONSTRAINT run_suppressed_not_wf     CHECK (suppressed IS NULL OR kind <> 'walk_forward')
);

-- Trades, fold tables, parameter changes, diagnostics, checks: all live
-- inside result jsonb. Nothing lists or aggregates across them outside a
-- single run's page, and the largest is ~tens of rows — no child tables.


-- ----------------------------------------------------------------------------
-- run_series — per-bar chart series, captured AT RUN TIME.
-- ----------------------------------------------------------------------------
-- The engine's result objects carry metrics and trades but not the per-bar
-- series the Charts tab draws (equity curve, benchmark equity, drawdown,
-- close, monthly returns, rolling 12-month). These MUST be captured when the
-- run executes: prices are retroactively adjusted, so recomputing a series
-- later would describe different data than the run's own numbers — exactly
-- the lie the vintage block exists to prevent. Requires a small engine
-- extension (docs/API.md §9).
--
-- One row per (run, series, fold). fold 0 = the whole run; folds 1..N carry
-- walk-forward per-fold series for the fold picker. CSV export streams
-- straight from `points`, so what the chart shows and what the file says are
-- the same bytes.

CREATE TABLE run_series (
  run_id  uuid NOT NULL REFERENCES run (id),
  name    text NOT NULL,
  fold    integer NOT NULL DEFAULT 0 CHECK (fold >= 0),

  -- Columnar, shape depending on name (documented in docs/API.md §6):
  --   equity, benchmark_equity, drawdown, close
  --     {"dates": ["2018-01-02", ...], "values": [...]}
  --   monthly_returns
  --     {"months": ["2018-01", ...], "values": [...], "in_market": [...]}
  --   rolling_12m_return
  --     {"dates": [...], "values": [...]}
  points  jsonb NOT NULL,

  PRIMARY KEY (run_id, name, fold)
);


-- ----------------------------------------------------------------------------
-- Append-only enforcement
-- ----------------------------------------------------------------------------

CREATE FUNCTION reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % rejected', TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER strategy_version_append_only
  BEFORE UPDATE OR DELETE ON strategy_version
  FOR EACH ROW EXECUTE FUNCTION reject_change();

CREATE TRIGGER run_series_append_only
  BEFORE UPDATE OR DELETE ON run_series
  FOR EACH ROW EXECUTE FUNCTION reject_change();

-- Runs mutate while in flight (status, progress, result landing) but their
-- identity is fixed at launch and the whole row freezes once terminal.
CREATE FUNCTION run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'runs are never deleted';
  END IF;
  IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN
    RAISE EXCEPTION 'run % is % and immutable', OLD.id, OLD.status;
  END IF;
  IF NEW.strategy_id <> OLD.strategy_id
     OR NEW.version   <> OLD.version
     OR NEW.number    <> OLD.number
     OR NEW.kind      <> OLD.kind
     OR NEW.params    <> OLD.params
     OR NEW.seed      <> OLD.seed
     OR NEW.queued_at <> OLD.queued_at THEN
    RAISE EXCEPTION 'run identity fields are immutable';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER run_guarded
  BEFORE UPDATE OR DELETE ON run
  FOR EACH ROW EXECUTE FUNCTION run_guard();

-- Deferred circular FK: promotion origin.
ALTER TABLE strategy
  ADD CONSTRAINT strategy_origin_run_fk
  FOREIGN KEY (origin_run_id) REFERENCES run (id);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Worker queue claim: UPDATE ... WHERE id = (SELECT id FROM run WHERE
-- status = 'queued' ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED).
-- Also serves the "Running now" sidebar.
CREATE INDEX run_active_idx ON run (status, queued_at)
  WHERE status IN ('queued', 'running');

-- Per-strategy run tabs, newest first.
CREATE INDEX run_by_strategy_idx ON run (strategy_id, kind, number DESC);

-- All-runs page, newest first.
CREATE INDEX run_recent_idx ON run (queued_at DESC);

-- Verdict lookup: the latest succeeded walk-forward per strategy.
CREATE INDEX run_wf_verdict_idx ON run (strategy_id, finished_at DESC)
  WHERE kind = 'walk_forward' AND status = 'succeeded';


-- ----------------------------------------------------------------------------
-- Views — the derived truths the list screens are built from
-- ----------------------------------------------------------------------------

-- Head version per strategy.
CREATE VIEW strategy_head AS
SELECT DISTINCT ON (strategy_id)
  strategy_id, id AS version_id, version, config, config_yaml, created_at
FROM strategy_version
ORDER BY strategy_id, version DESC;

-- One row per strategy with everything the list view renders.
--
-- VERDICT RULE (decision to confirm — docs/API.md §11): the verdict comes
-- from the latest succeeded walk-forward run AGAINST THE CURRENT HEAD
-- version. A credible walk-forward against v3 of a strategy now at v7
-- "describes a config that no longer exists" (the design's own words), so an
-- edited-since-validation strategy reads as unvalidated, not credible.
CREATE VIEW strategy_overview AS
SELECT
  s.id,
  s.name,
  s.origin,
  s.parent_strategy_id,
  s.parent_version,
  s.origin_run_id,
  s.origin_not_credible,
  s.created_at,
  h.version                                   AS head_version,
  h.created_at                                AS edited_at,
  h.config -> 'universe' ->> 'ticker'         AS ticker,
  h.config -> 'universe' ->> 'start_date'     AS start_date,
  h.config -> 'universe' ->> 'end_date'       AS end_date,
  (SELECT count(*) FROM run r WHERE r.strategy_id = s.id AND r.kind = 'optimize')     AS optimize_runs,
  (SELECT count(*) FROM run r WHERE r.strategy_id = s.id AND r.kind = 'backtest')     AS backtest_runs,
  (SELECT count(*) FROM run r WHERE r.strategy_id = s.id AND r.kind = 'walk_forward') AS walk_forward_runs,
  (SELECT count(*) FROM strategy_version v WHERE v.strategy_id = s.id)                AS versions,
  lr.id                                       AS last_run_id,
  lr.kind                                     AS last_run_kind,
  lr.status                                   AS last_run_status,
  coalesce(lr.finished_at, lr.queued_at)      AS last_run_at,
  CASE
    WHEN wf.id IS NOT NULL AND wf.is_credible       THEN 'credible'
    WHEN wf.id IS NOT NULL                          THEN 'not_credible'
    WHEN EXISTS (SELECT 1 FROM run r WHERE r.strategy_id = s.id) THEN 'unvalidated'
    ELSE 'never_run'
  END                                         AS verdict,
  wf.id                                       AS verdict_run_id
FROM strategy s
JOIN strategy_head h ON h.strategy_id = s.id
LEFT JOIN LATERAL (
  SELECT r.* FROM run r
  WHERE r.strategy_id = s.id
  ORDER BY r.queued_at DESC
  LIMIT 1
) lr ON true
LEFT JOIN LATERAL (
  SELECT r.* FROM run r
  WHERE r.strategy_id = s.id
    AND r.kind = 'walk_forward'
    AND r.status = 'succeeded'
    AND r.version = h.version          -- head-only: see VERDICT RULE above
  ORDER BY r.finished_at DESC
  LIMIT 1
) wf ON true;

-- One row per run with the two facts every run row renders that the run
-- table itself cannot know: the strategy's name and whether the run is stale.
CREATE VIEW run_overview AS
SELECT
  r.*,
  s.name                    AS strategy_name,
  (r.version < h.version)   AS stale
FROM run r
JOIN strategy s      ON s.id = r.strategy_id
JOIN strategy_head h ON h.strategy_id = r.strategy_id;
