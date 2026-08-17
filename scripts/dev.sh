#!/usr/bin/env bash
#
# Start, stop and inspect the development stack.
#
# Three processes plus a database, all of which have to be up before the UI shows anything
# real: the Postgres container, `cracktrade-api db migrate`, then `serve` and `worker`. Doing
# that by hand is four terminals and a startup order you have to remember, and the failure it
# produces -- a server refusing to start against an unmigrated database -- looks like a bug
# rather than a missing step.
#
#   ./scripts/dev.sh up          # database, migrations, api, worker, web
#   ./scripts/dev.sh status      # what is running, and who holds our ports
#   ./scripts/dev.sh logs api    # follow one, or all of them
#   ./scripts/dev.sh down        # stop everything this script started
#
# ---------------------------------------------------------------------------------------
# Why process groups
#
# `uv run cracktrade-api serve` is three processes, not one: the uv wrapper execs the console
# script, which forks uvicorn's reloader or the worker's multiprocessing forkserver. Killing
# the pid you started leaves the rest orphaned, still bound to the port and -- for the worker
# -- still holding a lease on a run in the database. That is the orphan this script exists to
# prevent.
#
# So each service is started under `setsid`, which makes it a session and process-group leader
# with PGID == PID, and what gets recorded in .run/ is that group id. Stopping a service
# signals the *group*, so every descendant goes with it regardless of how the process tree
# reshaped itself after launch. State lives on disk rather than in a shell, which means a
# closed terminal or a crashed editor cannot lose track of a running server.
set -euo pipefail

cd "$(dirname "$0")/.."

# `.env` is the same file pydantic-settings reads (src/cracktrade/api/settings.py) and compose
# reads. Sourcing it here keeps a developer who moved a port off 8000 from having to tell this
# script separately -- one file, one answer.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091  # optional, developer-local, git-ignored by design.
  . ./.env
  set +a
fi

API_PORT="${CRACKTRADE_API_PORT:-8000}"
WEB_PORT="${CRACKTRADE_WEB_PORT:-5173}"
DB_CONTAINER="cracktrade-postgres"

#: Recorded process groups and captured output. Git-ignored: it is per-checkout runtime state.
RUN_DIR=".run"

#: Services in start order. `worker` binds nothing -- an empty port means "no port to guard".
SERVICES=(api worker web)
declare -A PORTS=([api]="$API_PORT" [worker]="" [web]="$WEB_PORT")

#: Seconds a service gets to exit on SIGTERM before it is killed outright. The worker gets
#: longer on purpose: on SIGTERM it finishes the current run's checkpoint and records the run
#: as interrupted, and cutting that short is how a run ends up looking abandoned instead.
declare -A GRACE=([api]=10 [worker]=25 [web]=10)

command_of() {
  case "$1" in
    api) echo "uv run cracktrade-api serve" ;;
    worker) echo "uv run cracktrade-api worker" ;;
    web) echo "npm --prefix web run dev" ;;
    *) return 1 ;;
  esac
}

# --------------------------------------------------------------------------------- output

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
fail() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

# ------------------------------------------------------------------- process-group tracking

pgid_file() { echo "$RUN_DIR/$1.pgid"; }
log_file() { echo "$RUN_DIR/$1.log"; }

#: The recorded group for a service, if it is still alive. Prints nothing when the service is
#: not running, and clears the record when it names a group that has since exited -- a stale
#: file left by a reboot or a SIGKILL must not read as "running".
live_pgid() {
  local service="$1" file pgid
  file="$(pgid_file "$service")"
  [ -f "$file" ] || return 1
  pgid="$(cat "$file")"
  if [ -n "$pgid" ] && kill -0 -- "-$pgid" 2>/dev/null; then
    echo "$pgid"
    return 0
  fi
  rm -f "$file"
  return 1
}

#: Signal a whole process group and wait for it to actually go, escalating if it does not.
#: Returning while a process still holds the port would make `down && up` a race.
stop_group() {
  local pgid="$1" grace="$2" waited=0
  kill -TERM -- "-$pgid" 2>/dev/null || true
  while kill -0 -- "-$pgid" 2>/dev/null; do
    if [ "$waited" -ge "$grace" ]; then
      kill -KILL -- "-$pgid" 2>/dev/null || true
      sleep 1
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

# ------------------------------------------------------------------------------ port checks

#: The pid listening on a TCP port, or nothing. `ss` only reveals the owning pid for processes
#: the caller may signal, which is exactly the set this script can do anything about; lsof is
#: the fallback where ss is unavailable.
port_holder() {
  local port="$1" pid=""
  if command -v ss >/dev/null 2>&1; then
    pid="$(ss -tlnpH "sport = :$port" 2>/dev/null |
      grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
  fi
  if [ -z "$pid" ] && command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1)"
  fi
  echo "$pid"
}

#: Whether a port is bound at all, even by a process we cannot see the pid of.
port_busy() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    [ -n "$(ss -tlnH "sport = :$port" 2>/dev/null)" ]
  else
    [ -n "$(port_holder "$port")" ]
  fi
}

describe_pid() {
  local pid="$1"
  [ -n "$pid" ] || {
    echo "unknown process (owned by another user)"
    return
  }
  echo "pid $pid -- $(ps -o args= -p "$pid" 2>/dev/null | head -1 | cut -c1-90)"
}

# ---------------------------------------------------------------------------------- database

#: Bring up the Postgres container and wait for it to accept connections. Compose reports the
#: container as started well before the server is ready, and `db migrate` against that gap
#: fails with a connection error that reads like a misconfiguration.
ensure_database() {
  command -v docker >/dev/null 2>&1 ||
    fail "docker is not installed; the API needs the Postgres in docker-compose.yml"

  docker compose up -d >/dev/null 2>&1 || fail "docker compose up failed"

  local waited=0 state
  while [ "$waited" -lt 60 ]; do
    state="$(docker inspect --format '{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || echo missing)"
    case "$state" in
      healthy)
        echo "  database   ready"
        return 0
        ;;
      missing) fail "container $DB_CONTAINER did not start; try: docker compose logs postgres" ;;
    esac
    sleep 1
    waited=$((waited + 1))
  done
  fail "database did not become healthy in 60s; try: docker compose logs postgres"
}

#: `db migrate` is idempotent -- it applies what is pending and says so. Running it every time
#: is what keeps `up` a single command after a migration lands.
ensure_migrated() {
  if ! uv run cracktrade-api db migrate; then
    fail "migrations failed; the API would refuse to start anyway"
  fi
}

# ---------------------------------------------------------------------------------- commands

start_service() {
  local service="$1" port="${PORTS[$service]}" pgid holder cmd log

  if pgid="$(live_pgid "$service")"; then
    echo "  $service already running (group $pgid)"
    return 0
  fi

  # The port is free of *our* processes but held by something else. Refuse rather than let
  # uvicorn or vite fail with an "address already in use" that names no culprit -- the whole
  # point of this check is to answer the question that error leaves open.
  if [ -n "$port" ] && port_busy "$port"; then
    holder="$(port_holder "$port")"
    warn "port $port is already in use by $(describe_pid "$holder")"
    warn "  it was not started by this script. Stop it, or set a different port in .env"
    [ -n "$holder" ] && warn "  e.g. kill $holder"
    return 1
  fi

  cmd="$(command_of "$service")"
  log="$(log_file "$service")"

  # setsid puts the service in a fresh session, so $! is both its pid and its process-group
  # id. bash background jobs in a non-interactive shell inherit the script's group, which is
  # why this indirection matters: signalling that group would kill the script too.
  # shellcheck disable=SC2086  # the command strings above are fixed and intentionally split.
  setsid $cmd >>"$log" 2>&1 &
  pgid=$!

  echo "$pgid" >"$(pgid_file "$service")"

  # A service that dies immediately -- a syntax error, a missing dependency -- should be
  # reported now, not discovered later in a browser showing a connection refused.
  sleep 1
  if ! kill -0 -- "-$pgid" 2>/dev/null; then
    rm -f "$(pgid_file "$service")"
    warn "$service exited immediately; last lines of $log:"
    tail -n 15 "$log" >&2 || true
    return 1
  fi

  if [ -n "$port" ]; then
    echo "  $service       group $pgid, port $port"
  else
    echo "  $service    group $pgid"
  fi
}

stop_service() {
  local service="$1" pgid
  if pgid="$(live_pgid "$service")"; then
    echo "  stopping $service (group $pgid)"
    stop_group "$pgid" "${GRACE[$service]}"
    rm -f "$(pgid_file "$service")"
  else
    echo "  $service not running"
  fi
}

cmd_up() {
  local -a wanted=("$@") failed=0
  [ "${#wanted[@]}" -gt 0 ] || wanted=("${SERVICES[@]}")

  mkdir -p "$RUN_DIR"

  if [ ! -d web/node_modules ]; then
    for service in "${wanted[@]}"; do
      [ "$service" = web ] && fail "web/node_modules is missing; run: npm --prefix web install"
    done
  fi

  # Only pay for the database when something that needs it is being started.
  for service in "${wanted[@]}"; do
    if [ "$service" = api ] || [ "$service" = worker ]; then
      bold "database"
      ensure_database
      ensure_migrated
      break
    fi
  done

  bold "services"
  for service in "${wanted[@]}"; do
    command_of "$service" >/dev/null || fail "unknown service: $service"
    start_service "$service" || failed=1
  done

  echo
  if [ "$failed" -ne 0 ]; then
    warn "some services did not start -- see ./scripts/dev.sh status"
    return 1
  fi
  for service in "${wanted[@]}"; do
    case "$service" in
      web) echo "web  http://localhost:$WEB_PORT" ;;
      api) echo "api  http://127.0.0.1:$API_PORT/api/v1/docs" ;;
    esac
  done
  echo
  echo "logs: ./scripts/dev.sh logs     stop: ./scripts/dev.sh down"
}

cmd_down() {
  local -a wanted=("$@") stop_db=0 filtered=()

  for arg in "${wanted[@]}"; do
    if [ "$arg" = "--db" ]; then stop_db=1; else filtered+=("$arg"); fi
  done
  wanted=("${filtered[@]}")
  [ "${#wanted[@]}" -gt 0 ] || wanted=("${SERVICES[@]}")

  bold "stopping"
  for service in "${wanted[@]}"; do
    command_of "$service" >/dev/null || fail "unknown service: $service"
    stop_service "$service"
  done

  if [ "$stop_db" -eq 1 ]; then
    echo "  stopping database"
    docker compose down >/dev/null 2>&1 || true
  fi
}

cmd_status() {
  local pgid port holder state

  bold "database"
  state="$(docker inspect --format '{{.State.Health.Status}}' "$DB_CONTAINER" 2>/dev/null || echo "not running")"
  printf '  %-10s %s\n' "postgres" "$state"

  echo
  bold "services"
  for service in "${SERVICES[@]}"; do
    port="${PORTS[$service]}"
    if pgid="$(live_pgid "$service")"; then
      printf '  %-10s running   group %-8s %s\n' "$service" "$pgid" "${port:+port $port}"
    else
      printf '  %-10s stopped   %s\n' "$service" "${port:+port $port}"
    fi
  done

  # Anything on our ports that this script did not start is the case worth surfacing: it is
  # precisely what will make the next `up` fail, and it is invisible from the table above.
  echo
  bold "ports"
  for service in "${SERVICES[@]}"; do
    port="${PORTS[$service]}"
    [ -n "$port" ] || continue
    if port_busy "$port"; then
      holder="$(port_holder "$port")"
      if live_pgid "$service" >/dev/null; then
        printf '  %-10s held by this stack\n' "$port"
      else
        printf '  %-10s \033[33mheld by something else\033[0m -- %s\n' "$port" "$(describe_pid "$holder")"
      fi
    else
      printf '  %-10s free\n' "$port"
    fi
  done
}

cmd_logs() {
  local -a wanted=("$@") files=()
  [ "${#wanted[@]}" -gt 0 ] || wanted=("${SERVICES[@]}")
  for service in "${wanted[@]}"; do
    command_of "$service" >/dev/null || fail "unknown service: $service"
    files+=("$(log_file "$service")")
  done
  for file in "${files[@]}"; do [ -f "$file" ] || : >"$file"; done
  # -F rather than -f: a service restarted by `up` writes to a reopened file.
  tail -n 40 -F "${files[@]}"
}

cmd_restart() {
  cmd_down "$@"
  echo
  cmd_up "$@"
}

usage() {
  cat <<'EOF'
Usage: ./scripts/dev.sh <command> [service...]

Commands:
  up [service...]        start the database, apply migrations, start services
  down [--db] [service]  stop services (--db also stops the Postgres container)
  restart [service...]   down then up
  status                 what is running, and who holds our ports
  logs [service...]      follow captured output

Services: api, worker, web. With none given, all three.
EOF
}

case "${1:-}" in
  up) shift && cmd_up "$@" ;;
  down) shift && cmd_down "$@" ;;
  restart) shift && cmd_restart "$@" ;;
  status) cmd_status ;;
  logs) shift && cmd_logs "$@" ;;
  -h | --help | help | "") usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
