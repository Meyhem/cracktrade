#!/usr/bin/env bash
#
# The whole quality gate, both interfaces.
#
# The engine and the UI are one product and ship from one repository, so they pass or fail
# together: a green Python tree with a UI that does not compile is not a state anything
# should be pushed from. Runs the Python gate first because it is the faster of the two to
# fail on a broken checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== python =="
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest

echo
echo "== web =="
if [ ! -d web/node_modules ]; then
  echo "web/node_modules is missing; run: npm --prefix web install" >&2
  exit 1
fi
npm --prefix web run check

echo
echo "all green"
