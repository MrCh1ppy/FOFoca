#!/bin/sh
# Run the isolated disposable-PostgreSQL integration suite for fofoca-api.
#
# This script is API-specific: it never touches the Python harness, Ariadne,
# or production resources. Each test creates a unique local cluster under
# /tmp/fofoca-api-it-* and removes it on success or failure.
#
# Requirements on PATH: initdb, pg_ctl, psql, pg_isready. When the gate is
# enabled and tools are missing, tests FAIL rather than silently skip.
#
# Usage: fofoca-api/scripts/run_integration_tests.sh
set -eu

cd "$(dirname "$0")/.."

for tool in initdb pg_ctl psql pg_isready; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: required PostgreSQL tool '$tool' not found on PATH" >&2
        exit 1
    fi
done

# A private parent identifies only this run's clusters. Never inspect or remove
# another run's directories. Empty environment and HOME exclude real PG secrets,
# service files and .pgpass (including all future PG* environment settings).
sandbox=$(mktemp -d /tmp/fofoca-api-suite-XXXXXX)
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    for directory in "$sandbox"/fofoca-api-it-*; do
        if [ -e "$directory" ]; then
            echo "error: cleanup failed; retained diagnostics: $directory/postgres.log" >&2
            result=1
        fi
    done
    if [ "$result" -eq 0 ]; then
        rmdir "$sandbox"
        echo "integration suite passed; disposable clusters cleaned up"
    else
        # Keep any failed-stop cluster and its diagnostics; never blindly delete.
        rmdir "$sandbox" 2>/dev/null || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
env -i PATH="$PATH" HOME="$sandbox" TMPDIR="$sandbox" \
    CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}" \
    RUSTUP_HOME="${RUSTUP_HOME:-$HOME/.rustup}" \
    FOFOCA_API_INTEGRATION=1 \
    cargo test --locked --test integration
