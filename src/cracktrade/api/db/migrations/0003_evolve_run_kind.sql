-- The `evolve` run kind, alone in its own migration.
--
-- PostgreSQL refuses to *use* an enum value inside the transaction that added it -- "unsafe
-- use of new value", verified against the server in docker-compose.yml rather than taken from
-- the documentation. The migration engine runs each file in its own transaction (spec section
-- 14.6), so splitting the chain here is what lets 0004 write a CHECK constraint and a view
-- that name 'evolve'. Merging the two files would fail at deploy time, not at review time.

ALTER TYPE run_kind ADD VALUE 'evolve';
