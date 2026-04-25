"""Repo CRUD + URL normalisation + clone-URL construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import pats, repos, storage
from lib.errors import PictlError


@pytest.mark.parametrize("raw,expected", [
    ("https://github.com/u/r", "github.com/u/r"),
    ("https://github.com/u/r.git", "github.com/u/r"),
    ("github.com/u/r", "github.com/u/r"),
    ("github.com/u/r.git", "github.com/u/r"),
    ("git@github.com:u/r.git", "github.com/u/r"),
    ("git@gitlab.example:team/repo", "gitlab.example/team/repo"),
    ("  https://github.com/u/r/  ", "github.com/u/r"),
])
def test_normalize_url(raw: str, expected: str):
    assert repos._normalize_url(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "https://"])
def test_normalize_url_rejects_invalid(bad: str):
    with pytest.raises(PictlError):
        repos._normalize_url(bad)


def test_add_lists_get(pictl_home: Path):
    rec = repos.add_repo("github.com/u/r")
    listed = repos.list_repos()["repos"]
    assert listed[0]["id"] == rec["id"]
    assert repos.get_repo(rec["id"])["name"] == "r"


def test_add_with_unknown_pat_rejects(pictl_home: Path):
    with pytest.raises(PictlError):
        repos.add_repo("github.com/u/r", pat_id="does-not-exist")


def test_clone_url_has_no_token(pictl_home: Path):
    """The whole point of the askpass refactor: token must NOT be in the URL."""
    pat = pats.add_pat("gh", "ghp_super_secret")
    rec = repos.add_repo("github.com/u/r", pat_id=pat["id"])
    repo = repos.get_repo(rec["id"])
    url = repos.clone_url(repo)
    assert "ghp_super_secret" not in url
    assert "x-access-token" not in url
    assert url == "https://github.com/u/r.git"


def test_credential_env_carries_token(pictl_home: Path):
    pat = pats.add_pat("gh", "ghp_super_secret")
    rec = repos.add_repo("github.com/u/r", pat_id=pat["id"])
    repo = repos.get_repo(rec["id"])
    env = repos._credential_env(repo)
    assert env["PICTL_GIT_TOKEN"] == "ghp_super_secret"


def test_update_repo_swaps_pat(pictl_home: Path):
    p1 = pats.add_pat("a", "tok_aaaaaaaa")
    p2 = pats.add_pat("b", "tok_bbbbbbbb")
    rec = repos.add_repo("github.com/u/r", pat_id=p1["id"])
    updated = repos.update_repo(rec["id"], pat_id=p2["id"])
    assert updated["pat_id"] == p2["id"]
    assert repos.get_repo(rec["id"])["pat_id"] == p2["id"]


def test_update_repo_clear_pat(pictl_home: Path):
    p1 = pats.add_pat("a", "tok_aaaaaaaa")
    rec = repos.add_repo("github.com/u/r", pat_id=p1["id"])
    repos.update_repo(rec["id"], clear_pat=True)
    assert repos.get_repo(rec["id"])["pat_id"] is None


def test_update_repo_changes_url(pictl_home: Path):
    rec = repos.add_repo("github.com/u/old")
    repos.update_repo(rec["id"], url="github.com/u/new-name")
    after = repos.get_repo(rec["id"])
    assert after["url"] == "github.com/u/new-name"
    assert after["name"] == "new-name"


def test_update_repo_requires_at_least_one_field(pictl_home: Path):
    rec = repos.add_repo("github.com/u/r")
    with pytest.raises(PictlError):
        repos.update_repo(rec["id"])


def test_update_repo_unknown_id(pictl_home: Path):
    with pytest.raises(PictlError):
        repos.update_repo("nope", url="github.com/u/r")


def test_remove_unknown_repo(pictl_home: Path):
    with pytest.raises(PictlError):
        repos.remove_repo("nope")


def test_remove_warns_when_session_active(pictl_home: Path):
    rec = repos.add_repo("github.com/u/r")
    with storage.sessions_transaction() as data:
        data.setdefault("sessions", []).append({
            "id": "s1", "repo_id": rec["id"], "status": "running", "pid": 1,
        })
    result = repos.remove_repo(rec["id"])
    assert "warnings" in result
    assert result["status"] == "removed"
