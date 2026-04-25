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

# ---- Preflight: Python 3.10+ and git ---------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "[install] ERROR: python3 not found. apt install python3" >&2
  exit 1
fi

PY_OK=$(python3 -c 'import sys; print("ok" if sys.version_info >= (3, 10) else "old")')
if [ "${PY_OK}" != "ok" ]; then
  PY_VER=$(python3 -c 'import sys; print(".".join(str(x) for x in sys.version_info[:3]))')
  echo "[install] ERROR: pictl requires Python 3.10+; found ${PY_VER}" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "[install] WARN: git not found; pictl needs it for clones. apt install git" >&2
fi

# ---- Create dirs and symlink ----------------------------------------------
mkdir -p "${BIN_DIR}" "${DATA_DIR}" "${DATA_DIR}/sessions"
chmod 700 "${DATA_DIR}"

ln -sfn "${REPO_DIR}/pictl.py" "${BIN_DIR}/pictl"
chmod +x "${REPO_DIR}/pictl.py"
echo "[install] symlinked ${BIN_DIR}/pictl -> ${REPO_DIR}/pictl.py"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "[install] NOTE: ${BIN_DIR} is not on your PATH; add it to ~/.profile" ;;
esac

echo "[install] done. try: pictl doctor"
