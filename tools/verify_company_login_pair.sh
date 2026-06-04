#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${AI_PLATFORM_BASE_URL:-http://127.0.0.1:8020}"
FRONTEND_URL="${AI_PLATFORM_FRONTEND_URL:-http://10.56.0.211:18001}"
PYTHON_BIN="${AI_PLATFORM_PYTHON:-.venv/bin/python}"
ORDINARY_USERNAME="${AI_PLATFORM_ORDINARY_LOGIN_USERNAME:-}"
ADMIN_USERNAME="${AI_PLATFORM_ADMIN_LOGIN_USERNAME:-}"
RUN_FINAL_GATE=1

usage() {
  cat <<'EOF'
Usage:
  tools/verify_company_login_pair.sh [options]

Options:
  --base-url URL                 ai-platform API URL. Default: http://127.0.0.1:8020
  --frontend-url URL             ai-platform frontend URL for the final gate. Default: http://10.56.0.211:18001
  --python PATH                  Python executable. Default: .venv/bin/python
  --ordinary-username WORK_ID    Ordinary user work-id. Can also use AI_PLATFORM_ORDINARY_LOGIN_USERNAME.
  --admin-username WORK_ID       Admin/developer work-id. Can also use AI_PLATFORM_ADMIN_LOGIN_USERNAME.
  --skip-final-gate              Do not run tools/verify_poc_gate.py after both logins.
  -h, --help                     Show this help.

The script prompts for passwords without echoing them. Passwords are not passed
through command-line arguments and are not printed.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:?missing value for --base-url}"
      shift 2
      ;;
    --frontend-url)
      FRONTEND_URL="${2:?missing value for --frontend-url}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:?missing value for --python}"
      shift 2
      ;;
    --ordinary-username)
      ORDINARY_USERNAME="${2:?missing value for --ordinary-username}"
      shift 2
      ;;
    --admin-username)
      ADMIN_USERNAME="${2:?missing value for --admin-username}"
      shift 2
      ;;
    --skip-final-gate)
      RUN_FINAL_GATE=0
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

read_required() {
  local prompt="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    read -r -p "$prompt: " value
  fi
  if [[ -z "$value" ]]; then
    echo "$prompt is required" >&2
    exit 2
  fi
  printf '%s' "$value"
}

ORDINARY_USERNAME="$(read_required "Ordinary user work-id" "$ORDINARY_USERNAME")"
ADMIN_USERNAME="$(read_required "Admin/developer work-id" "$ADMIN_USERNAME")"

echo "Verifying ordinary user login audit..."
"$PYTHON_BIN" "$SCRIPT_DIR/verify_company_login_gate.py" \
  --base-url "$BASE_URL" \
  --username "$ORDINARY_USERNAME" \
  --prompt-password \
  --expect-user

echo "Verifying admin/developer login audit..."
"$PYTHON_BIN" "$SCRIPT_DIR/verify_company_login_gate.py" \
  --base-url "$BASE_URL" \
  --username "$ADMIN_USERNAME" \
  --prompt-password \
  --expect-admin

if [[ "$RUN_FINAL_GATE" -eq 1 ]]; then
  echo "Running strict aggregate platform gate..."
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_poc_gate.py" --api-url "$BASE_URL" --frontend-url "$FRONTEND_URL"
fi
