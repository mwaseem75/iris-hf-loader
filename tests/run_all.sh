#!/usr/bin/env bash
# All tests, in order; stops at the first failure.
#   tests/run_all.sh                  no data is changed (the IRIS tests clean up after themselves)
#   tests/run_all.sh --with-imports   also imports real datasets (from the UI and through
#                                     tests/smoke.sh import) and removes everything it added
#   add --screenshots to save fresh screenshots to docs/screenshots
# Needs the running container, internet access, Node 24, Python with pandas, pyarrow, pytest,
# and Chrome for the browser check.
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi
imports=0; shots=""
for arg in "$@"; do
  case "$arg" in
    --with-imports) imports=1 ;;
    --screenshots) shots="--screenshots" ;;
    *) echo "usage: tests/run_all.sh [--with-imports] [--screenshots]"; exit 2 ;;
  esac
done
irispy() {
  "${dc[@]}" exec -T -e IRISUSERNAME="${IRIS_USERNAME:-_SYSTEM}" -e IRISPASSWORD="${IRIS_PASSWORD:-SYS}" \
    -e IRISNAMESPACE=USER iris /usr/irissys/bin/irispython "$@"
}

passed=()
step() {
  local name=$1
  shift
  echo
  echo "=== $name"
  if "$@"; then passed+=("$name"); else
    echo
    echo "FAILED: $name   (passed before it: ${passed[*]:-none})"
    exit 1
  fi
}

if [ "$imports" -eq 1 ]; then
  step "snapshot of the import log and tables" irispy /home/irisowner/dev/tests/iris_snapshot.py snapshot
  trap 'echo; echo "=== cleanup"; irispy /home/irisowner/dev/tests/iris_snapshot.py restore' EXIT
fi
step "python unit tests" python -m pytest -q tests
step "UI unit tests" node --test tests/ui/state.test.mjs
step "import monitoring (fake API)" node tests/ui/mocked.mjs
step "tests inside IRIS" bash tests/run_iris_tests.sh
step "REST discovery checks" bash tests/smoke.sh discovery
if [ "$imports" -eq 1 ]; then
  step "real browser, with an import from the UI" node tests/ui/browser.mjs --imports $shots
else
  step "real browser" node tests/ui/browser.mjs $shots
fi
step "UI views against the server" node tests/ui/live.mjs
if [ "$imports" -eq 1 ]; then
  step "imports and background jobs" bash tests/smoke.sh import
fi
echo
echo "ALL PASSED: ${#passed[@]} steps"
