"""Repository CRUD plus remote branch listing.

URLs are stored in their canonical "host/owner/repo" form (no scheme,
no .git suffix). Clone URLs are constructed at the point of use so a
PAT swap takes effect immediately without rewriting config.

The PAT itself never appears in argv: we hand it to git through
`GIT_ASKPASS` (see `storage.git_env`), so `ps`, audit logs, and crash
dumps don't capture it.
"""

from __future__ import annotations

import secrets
import subprocess
from typing import Any
from urllib.parse import urlparse

from . import pats, storage
from .errors import PictlError


def _normalize_url(url: str) -> str:
    """Accept `https://github.com/u/r`, `github.com/u/r`, `git@...`, etc.

    Returns `github.com/u/r` (or other host) with no scheme or .git.
    """
    if not url:
        raise PictlError("url is required")
    u = url.strip()
    # SSH form: git@github.com:user/repo.git
    if u.startswith("git@") and ":" in u:
        host, _, path = u[4:].partition(":")
        u = f"{host}/{path}"
    else:
        if "://" not in u:
            u = "https://" + u
        parsed = urlparse(u)
        if not parsed.netloc or not parsed.path.strip("/"):
            raise PictlError(f"invalid repo url: {url}")
        u = f"{parsed.netloc}{parsed.path}"
    if u.endswith(".git"):
        u = u[:-4]
    return u.strip("/")


def _repo_name_from_url(url: str) -> str:
    last = url.rstrip("/").rsplit("/", 1)[-1]
    if last.endswith(".git"):
        last = last[:-4]
    return last


def clone_url(repo: dict[str, Any]) -> str:
    """Construct the https clone URL. Auth is supplied via env, not URL."""
    canonical = _normalize_url(repo["url"])
    return f"https://{canonical}.git"


def _credential_env(repo: dict[str, Any]) -> dict[str, str]:
    token = pats.get_token(repo["pat_id"]) if repo.get("pat_id") else None
    return storage.git_env(token)


def _find(repos: list[dict[str, Any]], repo_id: str) -> dict[str, Any] | None:
    for r in repos:
        if r["id"] == repo_id:
            return r
    return None


def get_repo(repo_id: str) -> dict[str, Any]:
    cfg = storage.read_config()
    repo = _find(cfg.get("repos", []), repo_id)
    if not repo:
        raise PictlError(f"repo '{repo_id}' not found")
    return repo


def list_repos() -> dict[str, Any]:
    cfg = storage.read_config()
    return {"repos": list(cfg.get("repos", []))}


def add_repo(url: str, pat_id: str | None = None) -> dict[str, Any]:
    canonical = _normalize_url(url)
    name = _repo_name_from_url(canonical)

    with storage.config_transaction() as cfg:
        if pat_id:
            existing_pats = cfg.get("pats", [])
            if not any(p["id"] == pat_id for p in existing_pats):
                raise PictlError(f"PAT '{pat_id}' not found")

        repos = cfg.setdefault("repos", [])
        repo_id = secrets.token_hex(3)
        while _find(repos, repo_id):
            repo_id = secrets.token_hex(3)

        entry = {
            "id": repo_id,
            "name": name,
            "url": canonical,
            "pat_id": pat_id,
        }
        repos.append(entry)

    return entry


def update_repo(
    repo_id: str,
    url: str | None = None,
    pat_id: str | None = None,
    clear_pat: bool = False,
) -> dict[str, Any]:
    """Mutate an existing repo record.

    `pat_id=None` leaves the existing PAT alone. Pass `clear_pat=True`
    to detach the PAT (anonymous clones thereafter).
    """
    if url is None and pat_id is None and not clear_pat:
        raise PictlError("nothing to update: pass --url, --pat, or --clear-pat")

    with storage.config_transaction() as cfg:
        repo = _find(cfg.get("repos", []), repo_id)
        if not repo:
            raise PictlError(f"repo '{repo_id}' not found")

        if pat_id is not None:
            existing_pats = cfg.get("pats", [])
            if not any(p["id"] == pat_id for p in existing_pats):
                raise PictlError(f"PAT '{pat_id}' not found")
            repo["pat_id"] = pat_id
        elif clear_pat:
            repo["pat_id"] = None

        if url is not None:
            canonical = _normalize_url(url)
            repo["url"] = canonical
            repo["name"] = _repo_name_from_url(canonical)

        result = dict(repo)

    return result


def remove_repo(repo_id: str) -> dict[str, Any]:
    # Check running sessions outside of the config lock so we don't
    # hold both locks at once.
    sessions_data = storage.read_sessions()
    in_use = [
        s["id"] for s in sessions_data.get("sessions", [])
        if s.get("repo_id") == repo_id and s.get("status") in ("running", "starting")
    ]

    warnings: list[str] = []
    if in_use:
        warnings.append(f"active sessions still using this repo: {', '.join(in_use)}")

    with storage.config_transaction() as cfg:
        repos = cfg.setdefault("repos", [])
        repo = _find(repos, repo_id)
        if not repo:
            raise PictlError(f"repo '{repo_id}' not found")
        repos.remove(repo)

    result: dict[str, Any] = {"id": repo_id, "status": "removed"}
    if warnings:
        result["warnings"] = warnings
    return result


def list_branches(repo_id: str) -> dict[str, Any]:
    repo = get_repo(repo_id)
    url = clone_url(repo)
    env = _credential_env(repo)

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            capture_output=True, text=True, timeout=30, check=False, env=env,
        )
    except FileNotFoundError:
        raise PictlError("git is not installed")
    except subprocess.TimeoutExpired:
        raise PictlError("timed out listing remote branches")

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise PictlError(f"git ls-remote failed: {stderr or 'unknown error'}")

    branches: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        _sha, ref = line.split("\t", 1)
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.append(ref[len(prefix):])

    return {"repo_id": repo_id, "branches": branches}
