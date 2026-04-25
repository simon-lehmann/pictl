#!/usr/bin/env bash
# Install pictl on a Raspberry Pi.
#
# Symlinks pictl.py into ~/.local/bin/pictl so it's on PATH and creates
# the ~/.pictl data directory. Idempotent: safe to re-run after a
# `git pull`.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.pictl"

echo "[install] repo dir: ${REPO_DIR}"

mkdir -p "${BIN_DIR}" "${DATA_DIR}" "${DATA_DIR}/sessions"
chmod 700 "${DATA_DIR}"

ln -sfn "${REPO_DIR}/pictl.py" "${BIN_DIR}/pictl"
chmod +x "${REPO_DIR}/pictl.py"
echo "[install] symlinked ${BIN_DIR}/pictl -> ${REPO_DIR}/pictl.py"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "[install] NOTE: ${BIN_DIR} is not on your PATH; add it to ~/.profile" ;;
esac

echo "[install] done. try: pictl stats"
