#!/usr/bin/env bash
# Run ArborFDM from the source tree (Linux / macOS).
#   scripts/run_dev.sh                GUI
#   scripts/run_dev.sh cli ARGS...    command line (python -m arbor ARGS...)
#   scripts/run_dev.sh test [ARGS]    pytest
# ARBOR_VENV=0 uses the current interpreter instead of creating ./.venv
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"          # no cd: relative file arguments keep working
if [[ "${ARBOR_VENV:-1}" != "0" ]]; then
  if [[ ! -d "$ROOT/.venv" ]]; then
    "$PY" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install -e "$ROOT[gui,dev]"
  fi
  PY="$ROOT/.venv/bin/python"
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
case "${1:-gui}" in
  cli)  shift; exec "$PY" -m arbor "$@" ;;
  test) shift; cd "$ROOT"; exec "$PY" -m pytest -q -p no:cacheprovider "$@" ;;
  gui)  [[ $# -gt 0 ]] && shift; exec "$PY" -m arbor.app.main "$@" ;;
  *)    exec "$PY" -m arbor.app.main "$@" ;;
esac
