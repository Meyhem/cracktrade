-- Strategies, their version history, runs, and captured chart series.
--
-- Semantics are normative in docs/ENGINE_SPEC.md section 14; this file is the canonical DDL.
-- Where a choice here is not obvious from the SQL, the reason is in a comment, because the
-- next person to touch a constraint needs to know what it was defending.
--
-- The shape in one paragraph: results are stored verbatim as the engine's own serialisation
-- and never recomputed (prices are retroactively adjusted, so a figure re-derived later would
-- describe different data than the run's vintage block claims); configuration history and run
-- records are append-only, enforced by triggers rather than promised by the application; and
-- the two facts every screen displays -- whether a run is stale, and whether a strategy is
-- credible -- are derived by views, never stored.


-- ----------------------------------------------------------------------------
-- Enumerations
-- ----------------------------------------------------------------------------

-- How a strategy came to exist. Drives the lineage crumb in the UI.
CREATE TYPE strategy_origin AS ENUM (
  'authored',   -- created blank or from a minimal seed
  'imported',   -- created from an uploaded YAML file
  'forked',     -- copy of another strategy at a chosen version
  'promoted'    -- created from a run's winning config
);

-- How one version came to exist.
CREATE TYPE version_origin AS ENUM (
  'created', 'imported', 'forked', 'promoted',  -- v1 origins
  'edited',                                     -- saved from the config editor
  'restored'                                    -- copy of an older version, appended
);

CREATE TYPE run_kind AS ENUM ('backtest', 'optimize', 'walk_forward');

CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');

-- The four failure categories, mapping 1:1 onto engine exit codes 2..5 (spec section 13.3).
-- Exit code 6 ("succeeded but not credible") deliberately has no counterpart: an uncredible
-- run is a *succeeded* run whose verdict says so, and encoding a verdict as a failure would
-- make the two indistinguishable to the queue.
CREATE TYPE failure_category AS ENUM (
  'config_invalid',       -- exit 2
  'market_data',          -- exit 3
  'engine_failure',       -- exit 4
  'causality_violation'   -- exit 5
);


-- ----------------------------------------------------------------------------
-- strategy -- identity and lineage. Config lives in strategy_version.
-- ----------------------------------------------------------------------------

CREATE TABLE strategy (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                 text NOT NULL,
  origin               strategy_origin NOT NULL,

  -- Forks record parent + the exact version copied; promotions record parent + the run whose
  -- winning config became this strategy's v1. The FK to run is added at the end of this file:
  -- the reference is circular by nature.
  parent_strategy_id   uuid REFERENCES strategy (id),
  parent_version       integer,
  origin_run_id        uuid,

  -- Snapshot taken at promotion time: the source run was uncredible (or unchecked) when this
  -- strategy was created. Historical fact, never updated -- the UI's warning is cleared by
  -- this strategy's own validation passing, not by editing the past.
  origin_not_credible  boolean NOT NULL DEFAULT false,

  created_at           timestamptz NOT NULL DEFAULT now(),

  -- Unique names reverse a [DROP] in spec section 3.2, which assumed no persistence. There is
  -- a registry again, and the UI addresses strategies by name in crumbs and promotion records.
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

-- No updated_at and no archived flag: "edited N hours ago" derives from the head version's
-- created_at, and no layer exposes a delete.


-- ----------------------------------------------------------------------------
-- strategy_version -- append-only config history
-- ----------------------------------------------------------------------------

CREATE TABLE strategy_version (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id    uuid NOT NULL REFERENCES strategy (id),
  version        integer NOT NULL CHECK (version >= 1),
  origin         version_origin NOT NULL,

  -- Only for origin = 'restored': which version was copied forward.
  restored_from  integer,

  -- The validated config, twice. `config` is the canonical parsed form and is queryable (the
  -- list view reads ticker and dates out of the head's). `config_yaml` is what the engine is
  -- fed and what the YAML pane shows: for an import it is the file as written, so that
  -- importing and reading back does not silently reformat the user's file.
  config         jsonb NOT NULL,
  config_yaml    text  NOT NULL,

  note           text,
  created_at     timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT strategy_version_number_unique UNIQUE (strategy_id, version),
  CONSTRAINT version_restore_shape CHECK (
    (origin = 'restored') = (restored_from IS NOT NULL)
    AND (restored_from IS NULL OR restored_from < version)
  ),
  -- v1 origins are creation origins; later versions can only be edits or restores.
  CONSTRAINT version_origin_matches_position CHECK (
    (version = 1) = (origin IN ('created', 'imported', 'forked', 'promoted'))
  )
);

-- The change summary in the History tab and the grouped diff view are computed from `config`
-- at read time, never stored: a stored diff can drift from the configs it claims to describe.

-- Runs reference (strategy_id, version), so that pair needs to be a key in its own right.
CREATE UNIQUE INDEX strategy_version_pair_idx ON strategy_version (strategy_id, version);


-- ----------------------------------------------------------------------------
-- run -- one execution of the engine against one exact version
-- ----------------------------------------------------------------------------

CREATE TABLE run (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id       uuid NOT NULL REFERENCES strategy (id),

  -- Runs launch against the head, but the head moves; a run stays pinned to the version it
  -- measured. Staleness is derived (version < head), never stored.
  version           integer NOT NULL,

  -- Per-strategy display number across all kinds, so "#14" is unambiguous within a strategy
  -- without naming its kind.
  number            integer NOT NULL CHECK (number >= 1),

  kind              run_kind NOT NULL,
  status            run_status NOT NULL DEFAULT 'queued',

  -- The launch request, echoed back by the API:
  --   backtest      {}
  --   optimize      {objective, epochs, cache}
  --   walk_forward  {objective, epochs, folds, scheme}
  params            jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- Recorded even when defaulted: reproducibility is a spec-level requirement (defect D12)
  -- and the run header prints it.
  seed              bigint NOT NULL,

  queued_at         timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  finished_at       timestamptz,

  -- Worker lease. A claimed run whose heartbeat goes stale is failed honestly rather than
  -- re-queued: re-running refetches data and is therefore a different measurement.
  claimed_by        text,
  heartbeat_at      timestamptz,

  -- The API's only write into a live run. The worker observes it between generations and
  -- folds. One-way: a cancelled request cannot be un-asked (enforced in run_guard).
  cancel_requested  boolean NOT NULL DEFAULT false,

  -- Transient, worker-updated: {stage: "optimize - generation 4 of 10", percent: 41}.
  progress          jsonb,

  -- On success: cracktrade.serialize.to_dict of the engine result, verbatim --
  -- BacktestResult, OptimizationResult, or ValidationReport -- including the contractual
  -- derived properties (is_credible, failures, fold_win_rate, at_bound, ...).
  result            jsonb,

  -- On failure: {category, exit_code, message}, message being the engine's own error text
  -- preserved for the failure screen.
  error             jsonb,
  failure_category  failure_category,

  -- Promoted out of `result` for list filtering, written in the same statement that lands the
  -- result and frozen with it.
  is_credible       boolean,   -- walk_forward only
  suppressed        boolean,   -- backtest/optimize: below the trade floor, figures withheld

  CONSTRAINT run_version_exists
    FOREIGN KEY (strategy_id, version)
    REFERENCES strategy_version (strategy_id, version),
  CONSTRAINT run_number_unique UNIQUE (strategy_id, number),

  CONSTRAINT run_result_iff_succeeded   CHECK ((status = 'succeeded') = (result IS NOT NULL)),
  CONSTRAINT run_error_iff_failed       CHECK ((status = 'failed') = (error IS NOT NULL)),
  CONSTRAINT run_category_iff_failed    CHECK ((status = 'failed') = (failure_category IS NOT NULL)),
  CONSTRAINT run_started_when_running   CHECK (status <> 'running' OR started_at IS NOT NULL),
  CONSTRAINT run_finished_when_terminal CHECK (
    (status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)
  ),
  CONSTRAINT run_credible_only_wf       CHECK (is_credible IS NULL OR kind = 'walk_forward'),
  CONSTRAINT run_suppressed_not_wf      CHECK (suppressed IS NULL OR kind <> 'walk_forward')
);

-- Trades, fold tables, parameter changes, diagnostics and checks all live inside `result`.
-- Nothing aggregates across them outside a single run's page, and the largest is tens of
-- rows, so they get no tables of their own.


-- ----------------------------------------------------------------------------
-- run_series -- per-bar chart series, captured AT RUN TIME
-- ----------------------------------------------------------------------------
-- The engine's result objects carry metrics and trades but not the series the Charts tab
-- draws. These are captured when the run executes and never recomputed, for the same reason
-- results are: recomputing would describe different prices than the run's own numbers.
--
-- fold 0 is the whole run; folds 1..N carry walk-forward per-fold series. CSV export streams
-- straight from `points`, so the chart and the file are the same bytes.

CREATE TABLE run_series (
  run_id  uuid NOT NULL REFERENCES run (id),
  name    text NOT NULL,
  fold    integer NOT NULL DEFAULT 0 CHECK (fold >= 0),

  -- Columnar, shape depending on name (docs/API.md section 6):
  --   equity, benchmark_equity, drawdown, close
  --     {"dates": [...], "values": [...]}
  --   monthly_returns
  --     {"months": [...], "values": [...], "in_market": [...]}
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

-- A run mutates while in flight -- status, progress, lease, the cancel request, and the single
-- update that lands its result -- but its identity is fixed at launch and the whole row
-- freezes once terminal.
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

  -- A cancellation cannot be withdrawn. The worker may already have acted on it, so allowing
  -- false to be restored would let a run continue that something was told had stopped.
  IF OLD.cancel_requested AND NOT NEW.cancel_requested THEN
    RAISE EXCEPTION 'cancellation cannot be withdrawn';
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER run_guarded
  BEFORE UPDATE OR DELETE ON run
  FOR EACH ROW EXECUTE FUNCTION run_guard();

-- Deferred circular FK: a promoted strategy's originating run.
ALTER TABLE strategy
  ADD CONSTRAINT strategy_origin_run_fk
  FOREIGN KEY (origin_run_id) REFERENCES run (id);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Worker claim (FOR UPDATE SKIP LOCKED) and the "Running now" sidebar.
CREATE INDEX run_active_idx ON run (status, queued_at)
  WHERE status IN ('queued', 'running');

-- Per-strategy run tabs, newest first.
CREATE INDEX run_by_strategy_idx ON run (strategy_id, kind, number DESC);

-- The all-runs page, newest first.
CREATE INDEX run_recent_idx ON run (queued_at DESC);

-- Verdict lookup: the latest succeeded walk-forward per strategy.
CREATE INDEX run_wf_verdict_idx ON run (strategy_id, finished_at DESC)
  WHERE kind = 'walk_forward' AND status = 'succeeded';

-- Lease sweep: claimed runs whose heartbeat may have gone stale.
CREATE INDEX run_heartbeat_idx ON run (heartbeat_at)
  WHERE status = 'running';


-- ----------------------------------------------------------------------------
-- Views -- the derived truths the list screens are built from
-- ----------------------------------------------------------------------------

-- Head version per strategy.
CREATE VIEW strategy_head AS
SELECT DISTINCT ON (strategy_id)
  strategy_id, id AS version_id, version, config, config_yaml, created_at
FROM strategy_version
ORDER BY strategy_id, version DESC;

-- One row per strategy with everything the list view renders.
--
-- The verdict comes from the latest succeeded walk-forward AGAINST THE CURRENT HEAD. A
-- credible run against v3 of a strategy now at v7 describes a config that no longer exists, so
-- an edited-since-validation strategy reads as unvalidated rather than credible (spec 14.4).
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
    WHEN wf.id IS NOT NULL AND wf.is_credible                    THEN 'credible'
    WHEN wf.id IS NOT NULL                                       THEN 'not_credible'
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
    AND r.version = h.version          -- head-only: see the note above
  ORDER BY r.finished_at DESC
  LIMIT 1
) wf ON true;

-- One row per run with the two facts every run row renders that the run table cannot know by
-- itself: the strategy's name, and whether the run is stale.
CREATE VIEW run_overview AS
SELECT
  r.*,
  s.name                    AS strategy_name,
  (r.version < h.version)   AS stale
FROM run r
JOIN strategy s      ON s.id = r.strategy_id
JOIN strategy_head h ON h.strategy_id = r.strategy_id;
