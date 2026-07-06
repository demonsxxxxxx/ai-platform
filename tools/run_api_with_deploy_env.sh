#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${AI_PLATFORM_DEPLOY_ENV:-/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform/.env}"
HOST="${AI_PLATFORM_HOST:-0.0.0.0}"
PORT="${AI_PLATFORM_PORT:-8020}"
PYTHON_BIN="${AI_PLATFORM_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"
APP_TARGET="app.main:create_app"
CHECK_ENV=0

usage() {
  cat <<'EOF'
Usage:
  tools/run_api_with_deploy_env.sh [options]

Options:
  --env PATH       Deployment .env path.
  --host HOST      Bind host. Default: 0.0.0.0
  --port PORT      Bind port. Default: 8020
  --python PATH    Python executable. Default: .venv/bin/python
  --check-env      Print required runtime keys as SET after derivation, then exit.
  -h, --help       Show this help.

This script derives app-level DATABASE_URL and S3_* settings from the deployment
POSTGRES_* and MINIO_* variables. It never prints secret values.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      DEPLOY_ENV="${2:?missing value for --env}"
      shift 2
      ;;
    --host)
      HOST="${2:?missing value for --host}"
      shift 2
      ;;
    --port)
      PORT="${2:?missing value for --port}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:?missing value for --python}"
      shift 2
      ;;
    --check-env)
      CHECK_ENV=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$DEPLOY_ENV" ]]; then
  echo "Deployment env file not found: $DEPLOY_ENV" >&2
  exit 2
fi

set -a
. "$DEPLOY_ENV"
DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"
S3_ENDPOINT_URL="http://localhost:${MINIO_API_PORT}"
S3_ACCESS_KEY_ID="${MINIO_ROOT_USER}"
S3_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}"
CLAUDE_AGENT_SDK_ENABLED=false
set +a

if [[ "$CHECK_ENV" -eq 1 ]]; then
  env | grep -E '^(DATABASE_URL|S3_ENDPOINT_URL|S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|TRUSTED_PRINCIPAL_SECRET)=' | sed -E 's/=.*/=SET/' | sort
  exit 0
fi

cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m uvicorn "$APP_TARGET" --factory --host "$HOST" --port "$PORT"
