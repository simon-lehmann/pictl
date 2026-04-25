"""PAT CRUD."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import pats, storage
from lib.errors import PictlError


def test_add_returns_masked(pictl_home: Path):
    rec = pats.add_pat("github", "ghp_abcdefghijklmnopqr")
    assert rec["name"] == "github"
    assert "ghp_" in rec["token_preview"]
    assert "abcdef" not in rec["token_preview"]
    assert rec["token_preview"].endswith("nopqr")


def test_list_never_returns_raw_token(pictl_home: Path):
    pats.add_pat("github", "ghp_abcdefghijklmnopqr")
    listed = pats.list_pats()
    assert "ghp_abcdef" not in str(listed)
    assert listed["pats"][0]["token_preview"] != "ghp_abcdefghijklmnopqr"


def test_get_token_returns_raw(pictl_home: Path):
    rec = pats.add_pat("github", "ghp_secret_token_12345")
    assert pats.get_token(rec["id"]) == "ghp_secret_token_12345"


def test_add_requires_name_and_token(pictl_home: Path):
    with pytest.raises(PictlError):
        pats.add_pat("", "tok")
    with pytest.raises(PictlError):
        pats.add_pat("name", "")


def test_remove_with_referencing_repo_warns_but_succeeds(pictl_home: Path):
    pat = pats.add_pat("gh", "tok_xxxxxxxx")
    # Inject a fake repo referencing this PAT.
    with storage.config_transaction() as cfg:
        cfg.setdefault("repos", []).append({
            "id": "r1", "name": "x", "url": "github.com/u/r", "pat_id": pat["id"],
        })
    result = pats.remove_pat(pat["id"])
    assert result["status"] == "removed"
    assert "warnings" in result
    # Token is gone.
    with pytest.raises(PictlError):
        pats.get_token(pat["id"])


def test_short_token_masking(pictl_home: Path):
    rec = pats.add_pat("short", "abc")
    assert rec["token_preview"] == "a…c"
