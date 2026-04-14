#!/usr/bin/env bash
# Install pictl on a Raspberry Pi.
#
#   - Symlinks pictl.py into ~/.local/bin/pictl so it's on PATH
#   - Installs the systemd unit for the HTTP shim and enables it
#
# Idempotent: safe to re-run after a `git pull`.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${HOME}/.pictl"
UNIT_SRC="${REPO_DIR}/systemd/pictl-shim.service"
UNIT_DST="/etc/systemd/system/pictl-shim.service"

echo "[install] repo dir: ${REPO_DIR}"

mkdir -p "${BIN_DIR}" "${DATA_DIR}" "${DATA_DIR}/sessions"
chmod 700 "${DATA_DIR}"

ln -sfn "${REPO_DIR}/pictl.py" "${BIN_DIR}/pictl"
chmod +x "${REPO_DIR}/pictl.py" "${REPO_DIR}/shim.py"
echo "[install] symlinked ${BIN_DIR}/pictl -> ${REPO_DIR}/pictl.py"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "[install] NOTE: ${BIN_DIR} is not on your PATH; add it to ~/.profile" ;;
esac

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[install] systemctl not found; skipping shim service install"
  exit 0
fi

echo "[install] installing systemd unit (sudo required)"
# The unit references /home/pi; rewrite for the current user if different.
tmp_unit="$(mktemp)"
sed "s|/home/pi|${HOME}|g; s|User=pi|User=${USER}|; s|Group=pi|Group=${USER}|" \
    "${UNIT_SRC}" > "${tmp_unit}"
sudo install -m 0644 "${tmp_unit}" "${UNIT_DST}"
rm -f "${tmp_unit}"
sudo systemctl daemon-reload
sudo systemctl enable --now pictl-shim.service
echo "[install] pictl-shim.service enabled and started"
echo "[install] done. try: pictl stats"
