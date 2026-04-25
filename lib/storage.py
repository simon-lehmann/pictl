"""Data layer: atomic JSON reads/writes with file locking.

All persistent state lives under ~/.pictl/:
  - config.json     -> repos and PATs
  - sessions.json   -> session metadata
  - .askpass.py     -> helper for git PAT auth (kept out of argv)

Writes go to a temp file and are renamed into place so a power loss
mid-write can never corrupt the store. Reads take a shared lock; writes
take an exclusive lock — both via an `fcntl.flock` on a sidecar file.

If the JSON store ever fails to parse, the bad file is renamed to
`<name>.corrupt-<unix-ts>` (so it can be inspected) and the default
shape is returned. Better than crashing every command.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator


DATA_DIR = Path(os.environ.get("PICTL_HOME", Path.home() / ".pictl"))
CONFIG_PATH = DATA_DIR / "config.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
SESSIONS_DIR = DATA_DIR / "sessions"
LOCK_DIR = DATA_DIR / ".locks"
ASKPASS_PATH = DATA_DIR / ".askpass.py"


DEFAULT_CONFIG: dict[str, Any] = {"repos": [], "pats": []}
DEFAULT_SESSIONS: dict[str, Any] = {"sessions": []}


# Tiny script git invokes via GIT_ASKPASS so the PAT never appears in argv.
# Receives the prompt as argv[1]; reads the secret from the environment.
_ASKPASS_SCRIPT = """\
#!/usr/bin/env python3
import os, sys
prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if "username" in prompt:
    print(os.environ.get("PICTL_GIT_USERNAME", "x-access-token"))
elif "password" in prompt:
    print(os.environ.get("PICTL_GIT_TOKEN", ""))
"""


def ensure_dirs() -> None:
    """Create the data directory tree on first run. Idempotent."""
    for d in (DATA_DIR, SESSIONS_DIR, LOCK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass


def _lock_path_for(target: Path) -> Path:
    ensure_dirs()
    return LOCK_DIR / (target.name + ".lock")


@contextlib.contextmanager
def file_lock(target: Path, exclusive: bool = True) -> Iterator[None]:
    """Advisory lock scoped to `target`.

    Uses a sidecar lock file so the lock fd lifetime is independent of
    the data file (which gets atomically replaced on write). Reads use
    `LOCK_SH` so concurrent readers don't serialise; writers use
    `LOCK_EX` and block until all readers release.
    """
    lock_path = _lock_path_for(target)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _quarantine_corrupt(path: Path) -> Path | None:
    """Rename a corrupt JSON file aside so it can be inspected."""
    target = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        os.replace(path, target)
        return target
    except OSError:
        return None


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return json.loads(json.dumps(default))  # deep copy
    except json.JSONDecodeError:
        # Don't silently overwrite — preserve the bad file for forensics.
        _quarantine_corrupt(path)
        return json.loads(json.dumps(default))


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # config.json holds plaintext PATs — keep it 0600 even if the
        # tempfile umask was looser.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_config() -> dict[str, Any]:
    with file_lock(CONFIG_PATH, exclusive=False):
        return _read_json(CONFIG_PATH, DEFAULT_CONFIG)


def write_config(data: dict[str, Any]) -> None:
    with file_lock(CONFIG_PATH):
        _write_json_atomic(CONFIG_PATH, data)


@contextlib.contextmanager
def config_transaction() -> Iterator[dict[str, Any]]:
    """Read-modify-write under a single exclusive lock.

    If the body raises, control never reaches `_write_json_atomic` —
    the contextlib.contextmanager re-throws at the yield point. So
    mid-transaction failures roll back cleanly.
    """
    with file_lock(CONFIG_PATH):
        data = _read_json(CONFIG_PATH, DEFAULT_CONFIG)
        yield data
        _write_json_atomic(CONFIG_PATH, data)


def read_sessions() -> dict[str, Any]:
    with file_lock(SESSIONS_PATH, exclusive=False):
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


# ---------------------------------------------------------------------------
# Git credential helper (keeps PATs out of argv)
# ---------------------------------------------------------------------------


def ensure_askpass() -> Path:
    """Lazily install the askpass shim and return its path."""
    ensure_dirs()
    try:
        current = ASKPASS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if current != _ASKPASS_SCRIPT:
        ASKPASS_PATH.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
    with contextlib.suppress(OSError):
        ASKPASS_PATH.chmod(0o700)
    return ASKPASS_PATH


def git_env(token: str | None) -> dict[str, str]:
    """Return an env dict with PAT auth wired through GIT_ASKPASS.

    The token is passed via the environment, never argv. `git`'s child
    process can read it; other users on the box cannot (env is per-pid
    and visible only to the owner on Linux).
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        env["GIT_ASKPASS"] = str(ensure_askpass())
        env["PICTL_GIT_USERNAME"] = "x-access-token"
        env["PICTL_GIT_TOKEN"] = token
    return env
