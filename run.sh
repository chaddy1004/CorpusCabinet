#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if command -v uv >/dev/null 2>&1; then
    exec uv run run_desktop.py "$@"
fi

local_python="$project_dir/.venv/bin/python"
if [ -x "$local_python" ]; then
    exec "$local_python" "$project_dir/run_desktop.py" "$@"
fi

echo "Corpus Cabinet requires uv or a local .venv. Run 'uv sync' first." >&2
exit 1
