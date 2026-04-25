"""Shared test fixtures.

Every test runs against a per-test PICTL_HOME so we never touch the
real ~/.pictl/. We set PICTL_HOME *before* importing the lib modules
because storage.DATA_DIR is captured at import time.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def pictl_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point pictl at an empty data dir for the duration of one test."""
    home = tmp_path / "pictl_home"
    home.mkdir()
    monkeypatch.setenv("PICTL_HOME", str(home))

    # Reload modules so they pick up the fresh PICTL_HOME.
    from lib import storage
    importlib.reload(storage)
    from lib import pats, repos, sessions, stats, doctor
    importlib.reload(pats)
    importlib.reload(repos)
    importlib.reload(sessions)
    importlib.reload(stats)
    importlib.reload(doctor)

    storage.ensure_dirs()
    yield home
