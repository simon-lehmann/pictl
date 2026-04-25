"""`pictl doctor` — health check for the host environment.

Returns a JSON shape:
    {"ok": bool, "checks": [{"name", "ok", "detail", "hint"}, ...]}

Each check is independent and never raises — a missing tool or a perms
problem just means `ok: false` for that row. Exits 1 if any check fails.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

from . import storage


def _check(name: str, ok: bool, detail: str, hint: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "ok": ok, "detail": detail}
    if not ok and hint:
        row["hint"] = hint
    return row


def _python_version() -> dict[str, Any]:
    py = sys.version_info
    ok = (py.major, py.minor) >= (3, 10)
    return _check(
        "python_version",
        ok=ok,
        detail=f"{py.major}.{py.minor}.{py.micro}",
        hint="pictl requires Python 3.10+ for PEP 604 type unions",
    )


def _git_available() -> dict[str, Any]:
    p = shutil.which("git")
    return _check(
        "git_available",
        ok=p is not None,
        detail=p or "not on PATH",
        hint="apt install git",
    )


def _claude_available() -> dict[str, Any]:
    p = shutil.which("claude")
    return _check(
        "claude_available",
        ok=p is not None,
        detail=p or "not on PATH",
        hint="install Claude Code: curl -fsSL https://claude.ai/install.sh | bash",
    )


def _temperature_source() -> dict[str, Any]:
    has_vcgen = shutil.which("vcgencmd") is not None
    has_thermal = Path("/sys/class/thermal/thermal_zone0/temp").exists()
    if has_vcgen:
        detail = "vcgencmd"
    elif has_thermal:
        detail = "/sys/class/thermal/thermal_zone0/temp"
    else:
        detail = "none"
    return _check(
        "temperature_source",
        ok=has_vcgen or has_thermal,
        detail=detail,
        hint="apt install libraspberrypi-bin (Pi OS) or run on a host exposing /sys/class/thermal",
    )


def _data_dir() -> dict[str, Any]:
    dd = storage.DATA_DIR
    if not dd.exists():
        return _check(
            "data_dir",
            ok=False,
            detail=f"missing: {dd}",
            hint="run install.sh, or any pictl command will create it",
        )
    mode = stat.S_IMODE(dd.stat().st_mode)
    ok = mode == 0o700
    return _check(
        "data_dir",
        ok=ok,
        detail=f"{dd} mode={oct(mode)}",
        hint=f"chmod 700 {dd}",
    )


def _config_perms() -> dict[str, Any]:
    cp = storage.CONFIG_PATH
    if not cp.exists():
        return _check(
            "config_perms",
            ok=True,
            detail="config.json not yet created",
        )
    mode = stat.S_IMODE(cp.stat().st_mode)
    ok = mode in (0o600, 0o400)
    return _check(
        "config_perms",
        ok=ok,
        detail=f"{cp} mode={oct(mode)}",
        hint=f"chmod 600 {cp}",
    )


def _local_bin_in_path() -> dict[str, Any]:
    bin_dir = str(Path.home() / ".local" / "bin")
    parts = os.environ.get("PATH", "").split(os.pathsep)
    ok = bin_dir in parts
    return _check(
        "local_bin_in_path",
        ok=ok,
        detail=f"PATH contains {bin_dir}: {ok}",
        hint=f'add {bin_dir} to PATH in ~/.profile',
    )


def run() -> dict[str, Any]:
    checks = [
        _python_version(),
        _git_available(),
        _claude_available(),
        _temperature_source(),
        _data_dir(),
        _config_perms(),
        _local_bin_in_path(),
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
