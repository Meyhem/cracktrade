-- Evolution runs: what the schema has to permit, and what it must keep refusing.
--
-- An evolution run composes a strategy from scratch against a chassis -- the ticker, dates and
-- costs of the strategy it is launched from -- and reports a credibility verdict of its own
-- (spec section 16.6). Two changes follow, and they move in opposite directions on purpose.


-- ----------------------------------------------------------------------------
-- An evolution run may carry a verdict
-- ----------------------------------------------------------------------------
-- `is_credible` was walk-forward only because a walk-forward was the only run that computed
-- one. An evolution run computes the same conjunction over its own checks, on a holdout no
-- part of the search was allowed to see, so the column is now meaningful for both.

ALTER TABLE run DROP CONSTRAINT run_credible_only_wf;

ALTER TABLE run ADD CONSTRAINT run_credible_only_validated
  CHECK (is_credible IS NULL OR kind IN ('walk_forward', 'evolve'));


-- ----------------------------------------------------------------------------
-- ...but it must never become the strategy's own verdict
-- ----------------------------------------------------------------------------
-- This is the whole reason the view is rewritten rather than left alone, and the change is
-- that the verdict lateral does NOT change.
--
-- An evolution run's verdict is about a configuration the chassis strategy does not contain.
-- The strategy holds a ticker and a set of costs; the composition that passed the checks lives
-- in the run's result, and reaches a strategy only by being promoted into one of its own. If
-- this lateral admitted 'evolve', a chassis would light up as CREDIBLE on the strength of a
-- config it does not hold -- the single worst failure available in this schema, because every
-- screen in the application reads that word and none of them would be wrong to trust it.
--
-- So the verdict stays walk-forward-only, and the count below is the only thing evolution adds
-- here. Dropped and recreated rather than replaced: CREATE OR REPLACE VIEW can only append
-- columns, and `evolve_runs` belongs beside the other three counts rather than after the
-- verdict. Plain DROP, no CASCADE -- an unexpected dependent should stop this migration rather
-- than be quietly destroyed by it.

DROP VIEW strategy_overview;

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
  (SELECT count(*) FROM run r WHERE r.strategy_id = s.id AND r.kind = 'evolve')       AS evolve_runs,
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
    AND r.kind = 'walk_forward'      -- deliberately not 'evolve': see the note above
    AND r.status = 'succeeded'
    AND r.version = h.version        -- head-only (spec section 14.4)
  ORDER BY r.finished_at DESC
  LIMIT 1
) wf ON true;
