"""Session lifecycle: list reconciliation, stop, cleanup, cleanup-dead, logs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import repos, sessions, storage
from lib.errors import PictlError


def _seed_session(status: str, sess_id: str = "sess1", pid: int | None = None, **extra) -> dict:
    rec = {
        "id": sess_id,
        "repo": "r",
        "repo_id": "rid",
        "repo_url": "github.com/u/r",
        "branch": "main",
        "status": status,
        "pid": pid,
        "remote_code": None,
        "started_at": "2026-04-25T00:00:00Z",
        "path": str(storage.session_dir(sess_id)),
    }
    rec.update(extra)
    with storage.sessions_transaction() as data:
        data.setdefault("sessions", []).append(rec)
    return rec


def test_list_flips_running_with_dead_pid(pictl_home: Path):
    _seed_session("running", pid=99999999)  # PID we don't expect to exist
    listed = sessions.list_sessions()
    assert listed["sessions"][0]["status"] == "dead"
    # Persisted.
    assert storage.read_sessions()["sessions"][0]["status"] == "dead"


def test_list_keeps_running_when_pid_alive(pictl_home: Path):
    _seed_session("running", pid=os.getpid())
    listed = sessions.list_sessions()
    assert listed["sessions"][0]["status"] == "running"


def test_stop_unknown_raises(pictl_home: Path):
    with pytest.raises(PictlError):
        sessions.stop_session("nope")


def test_stop_marks_stopped_even_with_no_pid(pictl_home: Path):
    _seed_session("running", pid=None)
    out = sessions.stop_session("sess1")
    assert out["status"] == "stopped"
    assert storage.read_sessions()["sessions"][0]["status"] == "stopped"


def test_cleanup_removes_record_and_dir(pictl_home: Path):
    _seed_session("dead", pid=None)
    sess_dir = storage.session_dir("sess1")
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "claude.log").write_text("hello")
    sessions.cleanup_session("sess1")
    assert storage.read_sessions()["sessions"] == []
    assert not sess_dir.exists()


def test_cleanup_dead_only_terminal(pictl_home: Path):
    _seed_session("dead", sess_id="d1")
    _seed_session("failed", sess_id="f1")
    _seed_session("stopped", sess_id="s1")
    _seed_session("running", sess_id="r1", pid=os.getpid())

    out = sessions.cleanup_dead()
    assert set(out["cleaned"]) == {"d1", "f1", "s1"}
    remaining = {s["id"] for s in storage.read_sessions()["sessions"]}
    assert remaining == {"r1"}


def test_logs_returns_tails(pictl_home: Path):
    _seed_session("running", pid=os.getpid())
    sess_dir = storage.session_dir("sess1")
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "claude.log").write_text("line1\nline2\n")
    (sess_dir / "worker.log").write_text("worker did things")
    out = sessions.session_logs("sess1", tail_bytes=1024)
    assert "line2" in out["claude_tail"]
    assert "worker did things" in out["worker_tail"]
    assert out["log_path"].endswith("claude.log")


def test_logs_unknown_session(pictl_home: Path):
    with pytest.raises(PictlError):
        sessions.session_logs("nope")


def test_start_session_requires_repo_and_branch(pictl_home: Path):
    with pytest.raises(PictlError):
        sessions.start_session("", "main")
    with pytest.raises(PictlError):
        sessions.start_session("rid", "")


def test_start_session_writes_starting_record(pictl_home: Path, monkeypatch):
    """start_session forks a worker; we stub the spawn to keep the test hermetic."""
    rec = repos.add_repo("github.com/u/r")
    monkeypatch.setattr(sessions, "_spawn_worker", lambda _id: None)
    out = sessions.start_session(rec["id"], "main")
    assert out["status"] == "starting"
    assert out["pid"] is None
    persisted = storage.read_sessions()["sessions"][0]
    assert persisted["status"] == "starting"
    assert persisted["repo_id"] == rec["id"]
