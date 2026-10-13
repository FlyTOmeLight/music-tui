#!/usr/bin/env bash
# Verify the built wheel in a fresh environment outside the source checkout.
set -euo pipefail

repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
task_python=$(uv python find 3.13)
expected_version=$("$task_python" -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["project"]["version"])' "$repository/pyproject.toml")
wheel=${1:-"$repository/dist/melody_tui-${expected_version}-py3-none-any.whl"}
wheel=$("$task_python" -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$wheel")
check_directory=$(mktemp -d "${TMPDIR:-/tmp}/music-tui-install.XXXXXX")
trap 'rm -rf -- "$check_directory"' EXIT

uv venv --python "$task_python" "$check_directory/venv"
uv pip install --python "$check_directory/venv/bin/python" "$wheel"

cd -- "$check_directory"
unset PYTHONPATH
export XDG_DATA_HOME="$check_directory/data"
export XDG_CONFIG_HOME="$check_directory/config"
"$check_directory/venv/bin/python" -I - "$expected_version" <<'PY'
import sys
from importlib.metadata import version
from pathlib import Path

import music_tui

installed_version = version("melody-tui")
assert installed_version == sys.argv[1], (installed_version, sys.argv[1])
assert Path(music_tui.__file__).is_relative_to(Path(sys.prefix)), music_tui.__file__
print(f"Installed music-tui {installed_version}: {music_tui.__file__}")
PY
"$check_directory/venv/bin/music-tui" --help
for option in -v --version; do
    reported_version=$("$check_directory/venv/bin/music-tui" "$option")
    [[ "$reported_version" == "music-tui $expected_version" ]]
    echo "$reported_version"
done
