#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --profile <internal-test|production> [--latest] [deployment options]" >&2
}

if [[ ${1:-} == "--help" ]]; then
  usage
  exit 0
fi
if [[ ${1:-} != "--profile" || -z ${2:-} ]]; then
  usage
  exit 2
fi

profile=$2
shift 2
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

case "$profile" in
  internal-test)
    controller="$repo_root/tools/latest_main_quickstart.py"
    ;;
  production)
    controller="$repo_root/tools/production_bootstrap.py"
    ;;
  *)
    echo "deploy-latest: unknown profile: $profile" >&2
    usage
    exit 2
    ;;
esac

exec python3 -I "$controller" "$@"
