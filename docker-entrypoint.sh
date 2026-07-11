#!/bin/sh
set -eu

: "${APP_MODULE:=app.main:create_app}"
: "${APP_PORT:=8020}"

runtime_uid="$(id -u)"
runtime_gid="$(id -g)"
if [ "$runtime_uid" != "10001" ] || [ "$runtime_gid" != "10001" ]; then
  echo "Runtime identity must be 10001:10001" >&2
  exit 77
fi
runtime_groups=" $(id -G) "
case "$runtime_groups" in
  *" 0 "*)
    echo "Runtime supplementary groups must not include GID 0" >&2
    exit 77
    ;;
esac

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

if [ "${1:-}" = "uvicorn" ] && [ "${2:-}" = "" ]; then
  exec "$@" "$APP_MODULE" --factory --host 0.0.0.0 --port "$APP_PORT"
fi

exec "$@"
