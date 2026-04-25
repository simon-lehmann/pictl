"""Session lifecycle.

A "session" is one Claude Code process running against a freshly-cloned
working copy of a repo. Lifecycle:

    (create) -> starting -> cloning -> running -> stopped | dead | failed | cleaned

`start` forks a detached worker that does the slow bits (clone, spawn
claude, capture its remote-connection output) and returns immediately.
The CLI call therefore costs ~10ms, not ~30s.

`list` performs the self-healing PID check from the plan: any session
marked "running" whose PID is no longer alive gets flipped to "dead".
"""

from __future__ import annotations

import datetime
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import repos, storage
from .errors import PictlError


MAX_REMOTE_CODE_LINES = 40
REMOTE_CODE_WAIT_SECONDS = 30
REMOTE_CODE_GRACE_SECONDS = 2.0
WORKER_OVERALL_TIMEOUT_SECONDS = 900  # 15 min absolute ceiling


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find(sessions_list: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    for s in sessions_list:
        if s["id"] == session_id:
            return s
    return None


def _update_session(session_id: str, **fields: Any) -> None:
    """Merge `fields` into the named session record. No-op if missing."""
    with storage.sessions_transaction() as data:
        sess = _find(data.get("sessions", []), session_id)
        if sess is None:
            return
        sess.update(fields)


# ---------------------------------------------------------------------------
# Public: list / stop / cleanup / start / logs
# ---------------------------------------------------------------------------


def list_sessions() -> dict[str, Any]:
    """Return all sessions with live-PID reconciliation."""
    with storage.sessions_transaction() as data:
        for s in data.get("sessions", []):
            if s.get("status") == "running":
                pid = s.get("pid")
                if not pid or not storage.pid_alive(int(pid)):
                    s["status"] = "dead"
        sessions = [dict(s) for s in data.get("sessions", [])]
    return {"sessions": sessions}


def stop_session(session_id: str) -> dict[str, Any]:
    sessions = storage.read_sessions()
    sess = _find(sessions.get("sessions", []), session_id)
    if not sess:
        raise PictlError(f"session '{session_id}' not found")

    pid = sess.get("pid")
    if pid and storage.pid_alive(int(pid)):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass

        # Wait up to 5s for graceful exit.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not storage.pid_alive(int(pid)):
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass

    _update_session(session_id, status="stopped", stopped_at=_now_iso())
    return {"id": session_id, "status": "stopped"}


def cleanup_session(session_id: str) -> dict[str, Any]:
    sessions = storage.read_sessions()
    sess = _find(sessions.get("sessions", []), session_id)
    if not sess:
        raise PictlError(f"session '{session_id}' not found")

    if sess.get("status") in ("running", "starting"):
        try:
            stop_session(session_id)
        except PictlError:
            pass

    path = Path(sess.get("path") or storage.session_dir(session_id))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)

    with storage.sessions_transaction() as data:
        data["sessions"] = [
            s for s in data.get("sessions", []) if s["id"] != session_id
        ]

    return {"id": session_id, "status": "cleaned"}


def cleanup_dead() -> dict[str, Any]:
    """Bulk-clean every session in a terminal state (dead/failed/stopped/cleaned)."""
    sessions = storage.read_sessions().get("sessions", [])
    terminal = {"dead", "failed", "stopped"}
    cleaned: list[str] = []
    errors: list[dict[str, str]] = []
    for sess in sessions:
        if sess.get("status") not in terminal:
            continue
        try:
            cleanup_session(sess["id"])
            cleaned.append(sess["id"])
        except Exception as e:
            errors.append({"id": sess["id"], "error": str(e)})

    result: dict[str, Any] = {"cleaned": cleaned, "count": len(cleaned)}
    if errors:
        result["errors"] = errors
    return result


def session_logs(session_id: str, tail_bytes: int = 8192) -> dict[str, Any]:
    """Return path + tail of the claude.log for a session."""
    sessions = storage.read_sessions()
    sess = _find(sessions.get("sessions", []), session_id)
    if not sess:
        raise PictlError(f"session '{session_id}' not found")

    sess_path = Path(sess.get("path") or storage.session_dir(session_id))
    log_path = sess_path / "claude.log"
    worker_log_path = sess_path / "worker.log"

    return {
        "id": session_id,
        "log_path": str(log_path),
        "worker_log_path": str(worker_log_path),
        "claude_tail": _tail(log_path, tail_bytes),
        "worker_tail": _tail(worker_log_path, tail_bytes),
    }


def start_session(repo_id: str, branch: str) -> dict[str, Any]:
    """Create the session record and fork a detached worker.

    Returns immediately with status='starting'. The worker is
    responsible for cloning, launching claude, and transitioning the
    record to 'running' or 'failed'.
    """
    if not repo_id:
        raise PictlError("repo is required")
    if not branch:
        raise PictlError("branch is required")

    repo = repos.get_repo(repo_id)

    with storage.sessions_transaction() as data:
        existing = {s["id"] for s in data.get("sessions", [])}
        session_id = secrets.token_hex(3)
        while session_id in existing:
            session_id = secrets.token_hex(3)

        sess_path = storage.session_dir(session_id)
        record = {
            "id": session_id,
            "repo": repo["name"],
            "repo_id": repo["id"],
            "repo_url": repo["url"],
            "branch": branch,
            "status": "starting",
            "pid": None,
            "remote_code": None,
            "started_at": _now_iso(),
            "path": str(sess_path),
        }
        data.setdefault("sessions", []).append(record)

    sess_path.mkdir(parents=True, exist_ok=True)

    _spawn_worker(session_id)

    return {
        "id": session_id,
        "status": "starting",
        "pid": None,
        "remote_code": None,
        "path": str(sess_path),
    }


# ---------------------------------------------------------------------------
# Worker (runs detached from pictl; invoked via `pictl _session_worker`)
# ---------------------------------------------------------------------------


def _spawn_worker(session_id: str) -> None:
    """Launch a detached child that drives the session to 'running'."""
    here = Path(__file__).resolve().parent.parent
    cli = here / "pictl.py"

    log_dir = storage.session_dir(session_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "worker.log", "ab", buffering=0)

    subprocess.Popen(
        [sys.executable, str(cli), "_session_worker", session_id],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
        close_fds=True,
    )


def run_worker(session_id: str) -> int:
    """Clone repo + launch claude. Designed to be invoked by pictl CLI."""
    started = time.monotonic()
    try:
        sessions = storage.read_sessions()
        sess = _find(sessions.get("sessions", []), session_id)
        if not sess:
            return 1

        repo = repos.get_repo(sess["repo_id"])
        url = repos.clone_url(repo)
        env = repos._credential_env(repo)

        sess_path = Path(sess["path"])
        sess_path.mkdir(parents=True, exist_ok=True)
        checkout = sess_path / "repo"
        log_path = sess_path / "claude.log"

        # ----- clone -----
        _update_session(session_id, status="cloning")
        clone = subprocess.run(
            [
                "git", "clone",
                "--branch", sess["branch"],
                "--single-branch",
                url,
                str(checkout),
            ],
            capture_output=True, text=True, timeout=600, env=env,
        )
        if clone.returncode != 0:
            _update_session(
                session_id,
                status="failed",
                error=f"git clone failed: {clone.stderr.strip()[:500]}",
                failed_at=_now_iso(),
            )
            return 1

        # ----- launch claude -----
        claude_bin = shutil.which("claude") or "claude"
        log_fh = open(log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [claude_bin, "--remote"],
                cwd=str(checkout),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except FileNotFoundError:
            _update_session(
                session_id,
                status="failed",
                error="claude binary not found on PATH",
                failed_at=_now_iso(),
            )
            return 1
        finally:
            log_fh.close()

        _update_session(session_id, pid=proc.pid)

        # ----- capture remote_code from claude's log -----
        remote_code = _poll_remote_code(log_path, proc, started)

        if proc.poll() is not None and proc.returncode != 0:
            tail = _tail(log_path, 2000)
            _update_session(
                session_id,
                status="failed",
                error=f"claude exited with code {proc.returncode}",
                log_tail=tail,
                failed_at=_now_iso(),
            )
            return 1

        _update_session(
            session_id,
            status="running",
            pid=proc.pid,
            remote_code=remote_code,
            ready_at=_now_iso(),
        )
        return 0

    except Exception as e:
        _update_session(
            session_id,
            status="failed",
            error=f"worker crashed: {e!r}",
            failed_at=_now_iso(),
        )
        return 1


def _poll_remote_code(
    log_path: Path,
    proc: subprocess.Popen,
    started_at: float,
) -> str | None:
    """Wait for claude to print its remote-connection details.

    Strategy: tail the log file for up to REMOTE_CODE_WAIT_SECONDS. Any
    line containing 'ssh ', 'https://', or 'claude.ai/' is a strong
    signal. Once we see the first match, keep reading for
    REMOTE_CODE_GRACE_SECONDS so multi-line announcements (URL +
    follow-up ssh line) are captured together.

    The whole worker is also bounded by WORKER_OVERALL_TIMEOUT_SECONDS
    so we never sit here forever if claude misbehaves.
    """
    deadline = time.monotonic() + REMOTE_CODE_WAIT_SECONDS
    overall_deadline = started_at + WORKER_OVERALL_TIMEOUT_SECONDS
    seen_offset = 0
    captured: list[str] = []
    grace_until: float | None = None

    while time.monotonic() < deadline and time.monotonic() < overall_deadline:
        if proc.poll() is not None:
            break
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(seen_offset)
                chunk = f.read()
                seen_offset = f.tell()
        except FileNotFoundError:
            time.sleep(0.25)
            continue

        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            lowered = line.lower()
            if "ssh " in lowered or "https://" in lowered or "claude.ai/" in lowered:
                captured.append(line)
                if len(captured) >= MAX_REMOTE_CODE_LINES:
                    return "\n".join(captured)

        # Once we've captured at least one line, keep reading for a
        # short grace period to pick up follow-on lines, then return.
        if captured:
            if grace_until is None:
                grace_until = time.monotonic() + REMOTE_CODE_GRACE_SECONDS
            elif time.monotonic() >= grace_until:
                return "\n".join(captured)

        time.sleep(0.25)

    return "\n".join(captured) if captured else None


def _tail(path: Path, n_bytes: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
