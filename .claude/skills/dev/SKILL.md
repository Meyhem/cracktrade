---
name: dev
description: Start, stop, or check the cracktrade dev stack (Postgres, API, worker, web UI). Use whenever asked to run/start/stop/restart the app, check what's running, or view server logs — instead of invoking uv/npm/docker directly.
---

# Dev stack

The dev stack is three processes plus a database, driven by `scripts/dev.sh` from the repo
root. Always use it instead of running `uv run cracktrade-api serve`, `npm --prefix web run
dev`, etc. directly — those leave orphaned processes behind when killed (see the script's
header comment for why), which is the exact problem this skill exists to avoid.

## Commands

```bash
./scripts/dev.sh up          # start db + api + worker + web
./scripts/dev.sh up web      # start only one/some: api, worker, web
./scripts/dev.sh status      # what's running, and who holds ports 8000/5173 if not us
./scripts/dev.sh logs        # follow all logs (Ctrl-C to stop following, doesn't stop services)
./scripts/dev.sh logs api    # follow one service's log
./scripts/dev.sh down        # stop everything this script started
./scripts/dev.sh down web    # stop just one
./scripts/dev.sh restart     # down then up
```

Endpoints once up: web UI at `http://localhost:5173`, API docs at
`http://127.0.0.1:8000/api/v1/docs`, health at `http://127.0.0.1:8000/api/v1/health`.

## Workflow

1. Run `./scripts/dev.sh up` (or `up` with specific services). It brings up Postgres, waits for
   its healthcheck, runs pending migrations, then starts services in the background.
2. Read the command's own output — it reports per-service success/failure directly, no need to
   follow up with `status` unless something failed or more detail is wanted.
3. If a service fails to start because a port is already in use, the output names the pid and
   command holding it. That process was not started by this script — do not kill it without
   telling the user what it is and confirming, unless the user has already asked for that.
4. To check on a running stack later in the same session, use `status`, not `ps`/`ss` by hand.
5. To see why something crashed or isn't responding, use `logs <service>`.
6. Stop what you started with `down` (or `down <service>`) — don't leave it running past the
   task unless the user wants it running for their own use (e.g. they asked you to start it for
   them to develop against).
