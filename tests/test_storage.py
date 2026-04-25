"""Storage-layer behaviour: atomic writes, locks, corrupt-file handling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import storage


def test_ensure_dirs_creates_tree(pictl_home: Path):
    assert storage.DATA_DIR.exists()
    assert storage.SESSIONS_DIR.exists()
    assert storage.LOCK_DIR.exists()


def test_ensure_dirs_sets_perms(pictl_home: Path):
    mode = storage.DATA_DIR.stat().st_mode & 0o777
    assert mode == 0o700


def test_read_config_default(pictl_home: Path):
    cfg = storage.read_config()
    assert cfg == {"repos": [], "pats": []}


def test_write_then_read_roundtrip(pictl_home: Path):
    storage.write_config({"repos": [{"id": "abc"}], "pats": []})
    assert storage.read_config()["repos"][0]["id"] == "abc"


def test_atomic_write_keeps_perms_0600(pictl_home: Path):
    storage.write_config({"repos": [], "pats": [{"id": "x", "token": "secret"}]})
    mode = storage.CONFIG_PATH.stat().st_mode & 0o777
    assert mode == 0o600


def test_corrupt_json_is_quarantined(pictl_home: Path):
    storage.CONFIG_PATH.write_text("{ not valid json", encoding="utf-8")
    cfg = storage.read_config()
    assert cfg == storage.DEFAULT_CONFIG
    # The bad file should have been renamed aside.
    siblings = list(storage.DATA_DIR.glob("config.json.corrupt-*"))
    assert len(siblings) == 1
    assert "not valid" in siblings[0].read_text(encoding="utf-8")


def test_config_transaction_rolls_back_on_exception(pictl_home: Path):
    """Mid-transaction raise must NOT persist partial mutation."""
    storage.write_config({"repos": [], "pats": []})
    with pytest.raises(RuntimeError):
        with storage.config_transaction() as cfg:
            cfg["repos"].append({"id": "should-not-persist"})
            raise RuntimeError("boom")
    after = storage.read_config()
    assert after["repos"] == []


def test_sessions_transaction_persists_on_clean_exit(pictl_home: Path):
    with storage.sessions_transaction() as data:
        data.setdefault("sessions", []).append({"id": "abc", "status": "running"})
    assert storage.read_sessions()["sessions"][0]["id"] == "abc"


def test_pid_alive_for_self(pictl_home: Path):
    assert storage.pid_alive(os.getpid()) is True


def test_pid_alive_for_obviously_dead(pictl_home: Path):
    # PID 0 / negative are invalid; pid_alive returns False.
    assert storage.pid_alive(0) is False
    assert storage.pid_alive(-1) is False


def test_git_env_omits_token_when_none(pictl_home: Path):
    env = storage.git_env(None)
    assert "PICTL_GIT_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_git_env_wires_askpass_when_token_present(pictl_home: Path):
    env = storage.git_env("ghp_secret")
    assert env["PICTL_GIT_TOKEN"] == "ghp_secret"
    assert env["PICTL_GIT_USERNAME"] == "x-access-token"
    assert env["GIT_ASKPASS"] == str(storage.ASKPASS_PATH)
    assert storage.ASKPASS_PATH.exists()
    assert storage.ASKPASS_PATH.stat().st_mode & 0o777 == 0o700


def test_askpass_script_emits_token_on_password_prompt(pictl_home: Path):
    """End-to-end: invoking the askpass shim with PICTL_GIT_TOKEN set."""
    import subprocess
    storage.ensure_askpass()
    env = {"PICTL_GIT_TOKEN": "abc123", "PICTL_GIT_USERNAME": "x-access-token"}
    out = subprocess.run(
        ["python3", str(storage.ASKPASS_PATH), "Password for 'https://...': "],
        capture_output=True, text=True, env=env, check=True,
    )
    assert out.stdout.strip() == "abc123"

    out2 = subprocess.run(
        ["python3", str(storage.ASKPASS_PATH), "Username for 'https://...': "],
        capture_output=True, text=True, env=env, check=True,
    )
    assert out2.stdout.strip() == "x-access-token"
