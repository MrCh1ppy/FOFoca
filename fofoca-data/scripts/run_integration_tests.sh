#!/usr/bin/env bash
# Start (or reuse) a disposable local PostgreSQL cluster for fofoca-data
# integration tests, create a disposable database, and run pytest.
#
# This script NEVER touches Ariadne. It uses a private data directory under
# /tmp, a random high port on 127.0.0.1, and a random database name.
#
# Usage:
#   ./scripts/run_integration_tests.sh          # start cluster, run tests, stop cluster
#   ./scripts/run_integration_tests.sh --keep   # keep cluster running after tests
#
# Env overrides:
#   FOFOCA_IT_PORT     (default: random 55432..55499)
#   FOFOCA_IT_DATADIR  (default: /tmp/fofoca-it-pg)
#   FOFOCA_IT_DBNAME   (default: fofoca_it_<random>)

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${VENV_PY}" ]]; then
    echo "virtualenv python not found at ${VENV_PY}; create .venv first" >&2
    exit 1
fi

DATADIR="${FOFOCA_IT_DATADIR:-/tmp/fofoca-it-pg}"
PORT="${FOFOCA_IT_PORT:-$(( (RANDOM % 68) + 55432 ))}"
DBNAME="${FOFOCA_IT_DBNAME:-fofoca_it_$RANDOM$RANDOM}"
KEEP=false
[[ "${1:-}" == "--keep" ]] && KEEP=true

if ! command -v initdb >/dev/null 2>&1; then
    echo "initdb not found; install postgresql server binaries" >&2
    exit 1
fi

mkdir -p "${DATADIR}"
chmod 700 "${DATADIR}"

# If a stale postmaster for this datadir is still alive but unreachable on our
# random port (e.g. left behind by a crashed earlier run), stop it first.
if [[ -f "${DATADIR}/postmaster.pid" ]]; then
    OLD_PID="$(head -n1 "${DATADIR}/postmaster.pid" 2>/dev/null || true)"
    if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
        echo ">> stopping stale postmaster (pid ${OLD_PID})"
        pg_ctl -D "${DATADIR}" -m fast stop >/dev/null 2>&1 || kill "${OLD_PID}" 2>/dev/null || true
        sleep 1
    fi
fi

STARTED_HERE=false
if [[ ! -s "${DATADIR}/PG_VERSION" ]]; then
    echo ">> initializing disposable cluster at ${DATADIR}"
    initdb -D "${DATADIR}" -U postgres --auth=trust --no-locale --encoding=UTF8 >/dev/null
fi

if ! pg_ctl -D "${DATADIR}" status >/dev/null 2>&1; then
    echo ">> starting disposable cluster on 127.0.0.1:${PORT}"
    pg_ctl -D "${DATADIR}" \
        -o "-p ${PORT} -k /tmp -c listen_addresses=127.0.0.1 -c logging_collector=off" \
        -l "${DATADIR}/server.log" -w start >/dev/null
    STARTED_HERE=true
else
    echo ">> reusing running cluster at ${DATADIR}"
fi

cleanup() {
    if [[ "${KEEP}" == "false" && "${STARTED_HERE}" == "true" ]]; then
        echo ">> stopping disposable cluster"
        pg_ctl -D "${DATADIR}" -m fast stop >/dev/null || true
    fi
}
trap cleanup EXIT

# Wait until the server accepts connections
for _ in $(seq 1 30); do
    if pg_isready -h 127.0.0.1 -p "${PORT}" -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 0.3
done

# Drop and recreate the disposable database to guarantee a clean slate.
psql -h 127.0.0.1 -p "${PORT}" -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${DBNAME}" \
    -c "CREATE DATABASE ${DBNAME}" >/dev/null

export FOFOCA_INTEGRATION_DB=true
export FOFOCA_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:${PORT}/${DBNAME}"

echo ">> running integration tests against ${FOFOCA_TEST_DATABASE_URL}"
cd "${PROJECT_ROOT}"
exec "${VENV_PY}" -m pytest tests/ -v "$@"
