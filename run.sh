#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

local_python="$project_dir/.venv/bin/python"
if command -v uv >/dev/null 2>&1; then
    # Keep explicitly installed optional features when starting an existing env.
    if [ -x "$local_python" ]; then
        exec uv run --no-sync run_desktop.py "$@"
    fi
    exec uv run run_desktop.py "$@"
fi

if [ -x "$local_python" ]; then
    exec "$local_python" "$project_dir/run_desktop.py" "$@"
fi

echo "Corpus Cabinet requires uv or a local .venv. Run 'uv sync' first." >&2
exit 1
