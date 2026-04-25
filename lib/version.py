"""Version information for pictl."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


__version__ = "0.2.0"


def info() -> dict[str, Any]:
    """Return version + best-effort git commit metadata."""
    out: dict[str, Any] = {"version": __version__}
    repo_root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=str(repo_root), check=False,
        )
        if commit.returncode == 0 and commit.stdout.strip():
            out["commit"] = commit.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return out
