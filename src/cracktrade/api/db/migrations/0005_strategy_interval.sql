-- The bar interval a strategy runs on, on the overview.
--
-- A strategy's history may now be 15-minute, 30-minute, hourly or daily bars (spec section
-- 3.3). Every list and header that already labels a strategy with its ticker and date range has
-- to label it with the interval too: "42 bars" and "hold at most 8 bars" mean four different
-- spans of calendar time depending on it, and a client that had to fetch and parse the YAML to
-- find out would either do it on every row or -- far likelier -- not do it at all.
--
-- No table change. The interval travels inside the stored config like every other universe
-- field, and this only lifts it into the view beside the ticker and the dates.
--
-- `coalesce(..., '1d')` is a statement of fact, not a convenience: a config written before the
-- interval existed has no such key, and the run it produced was a daily one. Reading NULL there
-- would force every consumer to re-decide what a missing interval meant, and the answer is not
-- "unknown".
--
-- Dropped and recreated rather than replaced: CREATE OR REPLACE VIEW can only append columns,
-- and `interval` belongs beside the dates rather than after the verdict. Plain DROP, no CASCADE
-- -- an unexpected dependent should stop this migration rather than be quietly destroyed by it.

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
  coalesce(h.config -> 'universe' ->> 'interval', '1d') AS interval,
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


-- ----------------------------------------------------------------------------
-- ...and on a run, from the version that run actually pinned
-- ----------------------------------------------------------------------------
-- Deliberately not the head's interval. A run pins a version at launch (spec section 14.3), and
-- the head may have moved to a different interval since -- so labelling an old run's "42 bars"
-- with the current head's interval would put a wrong unit on numbers that are otherwise
-- correct, which is worse than putting none on them.
--
-- CREATE OR REPLACE rather than DROP here: the column is appended after the existing ones and
-- nothing reorders, which is the one shape OR REPLACE permits.

CREATE OR REPLACE VIEW run_overview AS
SELECT
  r.*,
  s.name                    AS strategy_name,
  (r.version < h.version)   AS stale,
  coalesce(v.config -> 'universe' ->> 'interval', '1d') AS interval
FROM run r
JOIN strategy s      ON s.id = r.strategy_id
JOIN strategy_head h ON h.strategy_id = r.strategy_id
JOIN strategy_version v ON v.strategy_id = r.strategy_id AND v.version = r.version;
