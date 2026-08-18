-- Deleting a strategy: the one hole in append-only, and the ceremony that keeps it one hole.
--
-- Until this migration nothing could be deleted at any layer, and the schema enforced it with
-- triggers rather than trusting the application (spec section 14.2). That guarantee bought
-- something real -- a run is a record of a measurement, and a history that can be rewritten
-- cannot explain a result -- but it left no way to retire a strategy that was a mistake, and
-- the list screen is the working surface of this application. A registry that only grows is
-- one nobody can read.
--
-- The resolution is not to drop the triggers. Deletion is permitted only inside a transaction
-- that has *named the strategy it is purging*, via a transaction-local setting the triggers
-- read. An ordinary DELETE -- a stray statement, a buggy repository, a psql session -- does
-- not set it and is refused with exactly the message it was refused with before. The
-- permission is also scoped to one strategy's own rows: a purge of A cannot take a version or
-- a run belonging to B along with it, however the statement is written.
--
-- `set_config(..., true)` is reverted at COMMIT or ROLLBACK by PostgreSQL itself, so the
-- permission cannot outlive the transaction that granted it or leak onto the next borrower of
-- a pooled connection.
--
-- What this does NOT do is make deletion safe to reach for. The refusals live in the service
-- layer (a strategy with descendants, or with a run still in flight, is not deletable) and the
-- interface asks the user to type the name. Those are in spec section 14.8.


-- The strategy this transaction has declared it is purging, or NULL for every ordinary
-- transaction. STABLE rather than IMMUTABLE: the value is fixed within a statement but a
-- transaction may set it, and marking it IMMUTABLE would invite the planner to fold it.
CREATE FUNCTION purging_strategy() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
  raw text := current_setting('cracktrade.purge_strategy_id', true);
BEGIN
  IF raw IS NULL OR raw = '' THEN
    RETURN NULL;
  END IF;
  RETURN raw::uuid;
END $$;


-- ----------------------------------------------------------------------------
-- strategy_version: still append-only, still never updated
-- ----------------------------------------------------------------------------

CREATE FUNCTION strategy_version_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND purging_strategy() = OLD.strategy_id THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'strategy_version is append-only: % rejected', TG_OP;
END $$;

DROP TRIGGER strategy_version_append_only ON strategy_version;

CREATE TRIGGER strategy_version_append_only
  BEFORE UPDATE OR DELETE ON strategy_version
  FOR EACH ROW EXECUTE FUNCTION strategy_version_guard();


-- ----------------------------------------------------------------------------
-- run_series: reached through its run, so the ownership check joins
-- ----------------------------------------------------------------------------

CREATE FUNCTION run_series_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- A series row carries no strategy of its own. Checking the join rather than merely that
  -- *some* purge is in progress is what stops a purge of A from being a window in which B's
  -- captured series can be deleted.
  IF TG_OP = 'DELETE' AND EXISTS (
       SELECT 1 FROM run r
       WHERE r.id = OLD.run_id AND r.strategy_id = purging_strategy()
     ) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'run_series is append-only: % rejected', TG_OP;
END $$;

DROP TRIGGER run_series_append_only ON run_series;

CREATE TRIGGER run_series_append_only
  BEFORE UPDATE OR DELETE ON run_series
  FOR EACH ROW EXECUTE FUNCTION run_series_guard();


-- ----------------------------------------------------------------------------
-- run: the immutability of a terminal run is untouched
-- ----------------------------------------------------------------------------
-- Only the DELETE arm changes. A purge removes a succeeded run wholesale; it still cannot
-- edit one, which is the property that made a stored result a record rather than a cache.

CREATE OR REPLACE FUNCTION run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF purging_strategy() = OLD.strategy_id THEN
      RETURN OLD;
    END IF;
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

  IF OLD.cancel_requested AND NOT NEW.cancel_requested THEN
    RAISE EXCEPTION 'cancellation cannot be withdrawn';
  END IF;

  RETURN NEW;
END $$;


-- `reject_change` no longer has a trigger pointing at it. Dropped rather than left behind: a
-- function that nothing calls is one the next reader has to prove nothing calls.
DROP FUNCTION reject_change();


-- ----------------------------------------------------------------------------
-- Lineage lookups the delete guard runs
-- ----------------------------------------------------------------------------
-- Both are refusal checks on a path that must not be slow enough to be skipped, and neither
-- column was indexed: `strategy` is small, but a sequential scan per delete on a growing
-- registry is the kind of thing that gets removed later for the wrong reason.

CREATE INDEX strategy_parent_idx ON strategy (parent_strategy_id)
  WHERE parent_strategy_id IS NOT NULL;

CREATE INDEX strategy_origin_run_idx ON strategy (origin_run_id)
  WHERE origin_run_id IS NOT NULL;
