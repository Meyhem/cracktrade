-- An undefined forward Sharpe is NULL, not infinity.
--
-- `forward_score` reports whatever `extract_metrics` returns, and over a window in which the
-- candidate never traded that is `+Infinity` rather than a number: there are no returns, so
-- there is no variance to divide by. Postgres stores `'Infinity'::double precision` happily,
-- the API serialises it, and the candidate's forward row renders the single most misleading
-- figure this engine can produce -- a strategy that did *nothing* displaying the best Sharpe
-- on the leaderboard. Section 19.3's whole argument is that forward evidence is the only
-- evidence the search could not influence; publishing an infinity there discredits the one
-- rung that decides anything.
--
-- This is the same defect already fixed one rung down, where a single degenerate sibling
-- carried a transfer median to infinity (section 19.4). The response there was to exclude the
-- sibling from the median. It cannot be the response here: there is no aggregate to exclude
-- the value from, and the measurement itself is real. The window happened, the return is
-- honestly 0.00%, the trade count is honestly zero. Only the ratio is undefined.
--
-- So the column becomes nullable and NULL means "undefined over this window". That is a
-- different fact from "not scored yet", which remains the absence of a row -- section 19.9's
-- rule that unmeasured is not zero, applied to the ratio rather than the row.
--
-- Not `CHECK (isfinite(sharpe))`: rejecting the write would cost the sweep the honest return
-- and trade count alongside the meaningless ratio, and would turn a routine measurement into
-- a worker error. The refusal belongs in the library, which now maps any non-finite ratio to
-- NULL before it ever reaches this table.
--
-- Existing rows need no repair: the table is empty on every deployment that has one, because
-- the first score of the first sweep had not yet come due when this was found.

ALTER TABLE prospect_forward_score ALTER COLUMN sharpe DROP NOT NULL;

COMMENT ON COLUMN prospect_forward_score.sharpe IS
  'Annualised Sharpe over the window; NULL when undefined (no trades, so no return variance).';
