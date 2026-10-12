#!/usr/bin/env bash
# Runs the pytest suite inside the iris container (test_iris.py needs IRIS).
# Usage: tests/run_iris_tests.sh [pytest args]
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1                       # Git Bash
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi

"${dc[@]}" exec -T iris sh -c \
  'test -d /tmp/hf2iris-pytest/pytest || pip3 install -q --disable-pip-version-check --target /tmp/hf2iris-pytest pytest'
# irispython ignores PYTHONPATH
"${dc[@]}" exec -T \
  -e IRISUSERNAME="${IRIS_USERNAME:-_SYSTEM}" -e IRISPASSWORD="${IRIS_PASSWORD:-SYS}" -e IRISNAMESPACE=USER \
  -w /home/irisowner/dev iris \
  /usr/irissys/bin/irispython -c '
import sys
sys.path[:0] = ["/tmp/hf2iris-pytest"]
import pytest
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "tests"] + sys.argv[1:]))' "$@"
