#!/usr/bin/env bash
# Install beside the standalone checkout; never modifies the global CCB.
set -eu
tool_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
runtime_dir="${1:-$(dirname "$tool_dir")/.venv}"
if [[ ! -x "$runtime_dir/bin/python" ]]; then
  python3 -m venv "$runtime_dir"
fi
"$runtime_dir/bin/python" -I -m pip install -r "$tool_dir/remote-runtime-requirements.txt"
"$runtime_dir/bin/python" -I -c 'import aiohttp, cryptography, watchdog; print("Standalone CCB dependencies ready")'
