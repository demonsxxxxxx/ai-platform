#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$repo_dir/scripts/deploy-latest.sh" --profile internal-test "$@"
