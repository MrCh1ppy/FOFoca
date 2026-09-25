#!/usr/bin/env bash
# fofoca-data deployment script for the Aliyun host.
#
# Idempotent. Safe to re-run. Does NOT install any systemd timer. Does NOT
# touch Ariadne. Does NOT run a backfill automatically.
#
# Expected to run as the unprivileged `fofoca` user (or root for the
# user/permission bootstrap, in which case it re-execs as fofoca for the
# application steps).
#
# Layout:
#   /opt/fofoca/FOFoca/fofoca-data     <- this repository
#   /opt/fofoca/.venv                  <- virtualenv (owned by fofoca)
#   /opt/fofoca/fofoca-data.env        <- secrets (0600, fofoca:fofoca, OUTSIDE repo)

set -euo pipefail

APP_USER="${FOFOCA_APP_USER:-fofoca}"
APP_ROOT="${FOFOCA_APP_ROOT:-/opt/fofoca}"
REPO_DIR="${APP_ROOT}/FOFoca"
PROJECT_DIR="${REPO_DIR}/fofoca-data"
VENV_DIR="${APP_ROOT}/.venv"
ENV_FILE="${APP_ROOT}/fofoca-data.env"
CLI="${VENV_DIR}/bin/fofoca-data"

log() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# --- sanity -----------------------------------------------------------------

[[ -d "${PROJECT_DIR}" ]] || fail "repository not found at ${PROJECT_DIR}"
[[ -f "${PROJECT_DIR}/pyproject.toml" ]] || fail "pyproject.toml missing"

# Refuse to run as root for the application steps; re-exec as APP_USER.
if [[ "$(id -un)" == "root" ]]; then
    log "running as root; re-executing application steps as ${APP_USER}"
    exec sudo -u "${APP_USER}" -H "$0" "$@"
fi

[[ "$(id -un)" == "${APP_USER}" ]] || fail "must run as ${APP_USER} (or root to bootstrap)"

# --- python -----------------------------------------------------------------

if ! command -v python3.12 >/dev/null 2>&1; then
    fail "python3.12 not found on PATH"
fi
PY_VERSION="$(python3.12 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "${PY_VERSION}" == "3.12" ]] || fail "expected python3.12, got ${PY_VERSION}"

# --- env file ---------------------------------------------------------------
# Hard requirements, not warnings: a mis-owned or world-readable secrets file
# is a deployment blocker.

[[ -f "${ENV_FILE}" ]] || fail "secrets file missing: ${ENV_FILE} (see deploy/README.md)"
PERMS="$(stat -c '%a' "${ENV_FILE}")"
OWNER="$(stat -c '%U:%G' "${ENV_FILE}")"
[[ "${PERMS}" == "600" ]] || \
    fail "${ENV_FILE} permissions are ${PERMS}, expected exactly 600 (fix: chmod 600 ${ENV_FILE})"
[[ "${OWNER}" == "${APP_USER}:${APP_USER}" ]] || \
    fail "${ENV_FILE} owner is ${OWNER}, expected ${APP_USER}:${APP_USER} (fix: chown ${APP_USER}:${APP_USER} ${ENV_FILE})"

# shellcheck disable=SC1090
set -a; . "${ENV_FILE}"; set +a
[[ -n "${FOFOCA_DATABASE_URL:-}" ]] || fail "FOFOCA_DATABASE_URL not set in ${ENV_FILE}"

# --- virtualenv -------------------------------------------------------------

if [[ ! -d "${VENV_DIR}" ]]; then
    log "creating virtualenv at ${VENV_DIR}"
    python3.12 -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
. "${VENV_DIR}/bin/activate"

log "upgrading pip"
pip install --quiet --upgrade pip

log "installing fofoca-data (non-editable)"
pip install --quiet "${PROJECT_DIR}"

[[ -x "${CLI}" ]] || fail "expected CLI entry point at ${CLI} after install"

# --- schema -----------------------------------------------------------------

log "ensuring schema (init-db)"
"${CLI}" init-db

log "deployment complete."
log "to run a manual backfill:"
log "  sudo -u ${APP_USER} bash -c 'set -a; . ${ENV_FILE}; set +a; ${CLI} backfill'"
log "or for specific codes:"
log "  sudo -u ${APP_USER} bash -c 'set -a; . ${ENV_FILE}; set +a; ${CLI} backfill --code 000001 --code 166009'"
log "read-only query:"
log "  sudo -u ${APP_USER} bash -c 'set -a; . ${ENV_FILE}; set +a; ${CLI} query --code 166009 --start 2024-01-01 --end 2024-12-31'"
