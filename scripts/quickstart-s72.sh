#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 -I "$repo_dir/tools/sandbox_quickstart.py"
