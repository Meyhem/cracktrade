-- Continuous prospecting (spec section 19): sessions, candidates, and forward scores.
--
-- Three tables rather than reusing `run`, and the reason is structural rather than a
-- preference. A run is pinned to a strategy and a version -- `strategy_id` is NOT NULL and
-- `run_version_exists` ties the pair to a real `strategy_version` row. A prospecting tick has
-- no such thing: its ticker comes from the session's universe, not from a configuration a user
-- wrote, so section 17.1's "the chassis is a strategy" simply does not apply to it. The two
-- alternatives were considered and rejected. Auto-creating a chassis strategy per universe
-- ticker would let ticks be ordinary evolve runs, at the price of twenty-odd machine-authored
-- rows in the list screen that section 14.8's delete semantics then apply to. Making
-- `strategy_id` nullable would weaken a NOT NULL that every existing query and view relies on,
-- to save one table.
--
-- Nothing here is a verdict. Section 19.2 is the rule this schema exists to make
-- unexpressible: a prospecting session publishes candidates, and `is_credible` remains a
-- property of a strategy that has had its own walk-forward. There is deliberately no
-- `is_credible` column below, and `strategy_overview` is left untouched.


-- ----------------------------------------------------------------------------
-- prospect_session -- the long-lived record
-- ----------------------------------------------------------------------------
-- Section 19.7: the session is what persists and each search is an ordinary, terminating unit
-- of work. One immortal `run` row was rejected -- it would have to heartbeat forever and be
-- exempted from the lease sweep, which is the one mechanism guaranteeing a dead worker is
-- reported honestly, and a run that never ends has no meaningful progress percentage.

CREATE TYPE prospect_status AS ENUM ('running', 'stopped', 'failed');

CREATE TABLE prospect_session (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name              text NOT NULL,
  status            prospect_status NOT NULL DEFAULT 'running',

  -- The question, frozen at insert. A session whose universe or costs could be edited would
  -- make its own candidate list incomparable with itself.
  universe          text[] NOT NULL CHECK (cardinality(universe) > 0),
  params            jsonb NOT NULL,
  seed              bigint NOT NULL,

  -- The cursor, and the only field a tick advances. Restoring these two integers *is* resume,
  -- which is why section 19.7 makes the rotation a value rather than an iterator: an
  -- iterator's position is not something that can be written to a row.
  cursor_index      integer NOT NULL DEFAULT 0 CHECK (cursor_index >= 0),
  passes_completed  integer NOT NULL DEFAULT 0 CHECK (passes_completed >= 0),

  ticks_completed   integer NOT NULL DEFAULT 0 CHECK (ticks_completed >= 0),
  ticks_failed      integer NOT NULL DEFAULT 0 CHECK (ticks_failed >= 0),

  created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  stopped_at        timestamptz,

  -- Worker lease, as on `run` and for the same reason. A session whose worker dies goes back
  -- to being claimable: unlike a run, resuming a sweep is not a different measurement, because
  -- each tick is its own measurement and the completed ones are already durable.
  claimed_by        text,
  heartbeat_at      timestamptz,

  -- The API's only write into a live session. One-way, like `run.cancel_requested`.
  stop_requested    boolean NOT NULL DEFAULT false,

  error             jsonb,

  CONSTRAINT prospect_session_name_unique UNIQUE (name),
  CONSTRAINT prospect_session_cursor_in_universe
    CHECK (cursor_index < cardinality(universe)),
  CONSTRAINT prospect_session_stopped_when_terminal CHECK (
    (status IN ('stopped', 'failed')) = (stopped_at IS NOT NULL)
  ),
  CONSTRAINT prospect_session_error_iff_failed CHECK ((status = 'failed') = (error IS NOT NULL))
);

CREATE INDEX prospect_session_claimable
  ON prospect_session (created_at)
  WHERE status = 'running';


-- ----------------------------------------------------------------------------
-- prospect_candidate -- one tick's finding
-- ----------------------------------------------------------------------------
-- Stored verbatim as jsonb per section 14.1, with columns promoted out only where the
-- leaderboard filters or sorts on them. `discovered_at` and `last_bar_seen` are both here and
-- are not the same fact: the first says when the search ran, the second says what it had
-- actually read, and on a stale cache those differ. Forward scoring keys off the second.

CREATE TABLE prospect_candidate (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id          uuid NOT NULL REFERENCES prospect_session (id),

  ticker              text NOT NULL,
  discovered_at       timestamptz NOT NULL,
  last_bar_seen       date NOT NULL,
  seed                bigint NOT NULL,

  -- cracktrade.serialize.to_dict of the Candidate, transfer report included.
  candidate           jsonb NOT NULL,
  strategy_yaml       text NOT NULL,

  -- Promoted for the leaderboard. Written in the same statement as the row and never edited,
  -- exactly as `run.is_credible` is.
  --
  -- Note what is NOT promoted: the holdout return. It is the number the search selected on and
  -- the number a reader most wants to sort by, and section 19.5 forbids ranking on it. Leaving
  -- it inside the jsonb means a leaderboard query cannot casually order by it.
  survived_transfer   boolean NOT NULL,
  transfer_median     double precision NOT NULL,
  transfer_control    double precision NOT NULL,

  created_at          timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX prospect_candidate_by_session ON prospect_candidate (session_id, discovered_at DESC);

-- The leaderboard's working index: survivors first, best transfer first among them.
CREATE INDEX prospect_candidate_leaderboard
  ON prospect_candidate (session_id, transfer_median DESC)
  WHERE survived_transfer;


-- ----------------------------------------------------------------------------
-- prospect_forward_score -- append-only, one row per scoring
-- ----------------------------------------------------------------------------
-- Section 19.3's third rung. A candidate is re-run on bars that arrived after `last_bar_seen`,
-- and the result is *appended* rather than written over the previous one.
--
-- Overwriting would be the obvious design and is wrong here for section 14.1's own reason:
-- prices are retroactively adjusted on every dividend and split (section 4.1), so the same
-- candidate scored today and next month is scored against different prices for the same bars.
-- A stored score is a record of a measurement, not a cache of one. Keeping the series also
-- makes "this has been degrading for six weeks" a thing the leaderboard can show, which is
-- exactly what a forward-validated ranking is for.
--
-- The leaderboard reads the most recent row per candidate, by the same lateral-join pattern
-- `strategy_overview` already uses for its verdict.

CREATE TABLE prospect_forward_score (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_id  uuid NOT NULL REFERENCES prospect_candidate (id),

  scored_at     timestamptz NOT NULL DEFAULT clock_timestamp(),

  -- The window actually measured: strictly after the candidate's last seen bar.
  first_bar     date NOT NULL,
  last_bar      date NOT NULL,
  bars          integer NOT NULL CHECK (bars > 0),

  return_pct    double precision NOT NULL,
  sharpe        double precision NOT NULL,
  trades        integer NOT NULL CHECK (trades >= 0),

  CONSTRAINT prospect_forward_window CHECK (last_bar >= first_bar)
);

CREATE INDEX prospect_forward_latest
  ON prospect_forward_score (candidate_id, scored_at DESC);

-- Append-only, enforced rather than trusted, as section 14.2 requires of every table whose
-- rows are records of a measurement. A forward score that could be revised would let a
-- candidate's history be rewritten to explain its current rank -- which is the one thing a
-- forward-validated ranking exists to prevent.
--
-- Unconditional, unlike `run_series_guard`: a forward score belongs to a candidate and a
-- candidate belongs to a session, neither of which participates in `purging_strategy()`. When
-- session deletion arrives it will need its own arm here, added deliberately rather than
-- inherited by accident.

CREATE FUNCTION prospect_forward_score_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'prospect_forward_score is append-only: % rejected', TG_OP;
END $$;

CREATE TRIGGER prospect_forward_score_append_only
  BEFORE UPDATE OR DELETE ON prospect_forward_score
  FOR EACH ROW EXECUTE FUNCTION prospect_forward_score_guard();
