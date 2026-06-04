#!/bin/sh
set -eu

: "${APP_MODULE:=app.main:create_app}"
: "${APP_PORT:=8020}"

case "$APP_MODULE" in
  app.main:create_app|app.runtime.sandbox.executor_app:create_executor_app)
    ;;
  *)
    echo "Invalid APP_MODULE: $APP_MODULE" >&2
    exit 64
    ;;
esac

case "$APP_PORT" in
  8020|18000)
    ;;
  *)
    echo "Invalid APP_PORT: $APP_PORT" >&2
    exit 64
    ;;
esac

if [ "${1:-}" = "uvicorn" ]; then
  exec "$@" "$APP_MODULE" --factory --host 0.0.0.0 --port "$APP_PORT"
fi

exec "$@"
