#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$REPOSITORY_ROOT"
PYTHONPATH=src "$PYTHON_BIN" scripts/check_contract.py
PYTHONPATH=src "$PYTHON_BIN" -m unittest discover -s tests -v
PYTHONPATH=src "$PYTHON_BIN" scripts/run_release_drill.py

printf '%s\n' "StateWeave repository gate: OK"
