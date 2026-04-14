"""Data layer: atomic JSON reads/writes with file locking.

All persistent state lives under ~/.pictl/:
  - config.json     -> repos and PATs
  - sessions.json   -> session metadata

Writes go to a temp file and are renamed into place so a power loss
mid-write can never corrupt the store. An advisory fcntl lock on the
target path serialises concurrent pictl invocations.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator


DATA_DIR = Path(os.environ.get("PICTL_HOME", Path.home() / ".pictl"))
CONFIG_PATH = DATA_DIR / "config.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
SESSIONS_DIR = DATA_DIR / "sessions"
LOCK_DIR = DATA_DIR / ".locks"


DEFAULT_CONFIG: dict[str, Any] = {"repos": [], "pats": []}
DEFAULT_SESSIONS: dict[str, Any] = {"sessions": []}


def ensure_dirs() -> None:
    """Create the data directory tree on first run. Idempotent."""
    for d in (DATA_DIR, SESSIONS_DIR, LOCK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _lock_path_for(target: Path) -> Path:
    ensure_dirs()
    return LOCK_DIR / (target.name + ".lock")


@contextlib.contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """Exclusive advisory lock scoped to `target`.

    Uses a sidecar lock file so the lock fd lifetime is independent of
    the data file (which gets atomically replaced on write).
    """
    lock_path = _lock_path_for(target)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return json.loads(json.dumps(default))  # deep copy
    except json.JSONDecodeError:
        # Corrupt file: fall back to default rather than crash.
        return json.loads(json.dumps(default))


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then rename.
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_config() -> dict[str, Any]:
    with file_lock(CONFIG_PATH):
        return _read_json(CONFIG_PATH, DEFAULT_CONFIG)


def write_config(data: dict[str, Any]) -> None:
    with file_lock(CONFIG_PATH):
        _write_json_atomic(CONFIG_PATH, data)


@contextlib.contextmanager
def config_transaction() -> Iterator[dict[str, Any]]:
    """Read-modify-write under a single lock."""
    with file_lock(CONFIG_PATH):
        data = _read_json(CONFIG_PATH, DEFAULT_CONFIG)
        yield data
        _write_json_atomic(CONFIG_PATH, data)


def read_sessions() -> dict[str, Any]:
    with file_lock(SESSIONS_PATH):
        return _read_json(SESSIONS_PATH, DEFAULT_SESSIONS)


def write_sessions(data: dict[str, Any]) -> None:
    with file_lock(SESSIONS_PATH):
        _write_json_atomic(SESSIONS_PATH, data)


@contextlib.contextmanager
def sessions_transaction() -> Iterator[dict[str, Any]]:
    with file_lock(SESSIONS_PATH):
        data = _read_json(SESSIONS_PATH, DEFAULT_SESSIONS)
        yield data
        _write_json_atomic(SESSIONS_PATH, data)


def session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id


def pid_alive(pid: int) -> bool:
    """Return True iff a process with `pid` is currently running."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it (different user).
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        return True
    return True
