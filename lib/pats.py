"""PAT (Personal Access Token) CRUD.

Tokens live in ~/.pictl/config.json in plain text. The plan accepts
this: the file is mode 0600 on a single-user Pi. The public `list`
command only ever returns a masked preview.
"""

from __future__ import annotations

import secrets
from typing import Any

from . import storage
from .errors import PictlError


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return token[:1] + "…" + token[-1:]
    return f"{token[:4]}...{token[-4:]}"


def _public(pat: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pat["id"],
        "name": pat["name"],
        "token_preview": _mask(pat.get("token", "")),
    }


def _find(pats: list[dict[str, Any]], pat_id: str) -> dict[str, Any] | None:
    for p in pats:
        if p["id"] == pat_id:
            return p
    return None


def get_token(pat_id: str) -> str:
    """Internal lookup used by repos.py for clone URLs. Returns raw token."""
    cfg = storage.read_config()
    pat = _find(cfg.get("pats", []), pat_id)
    if not pat:
        raise PictlError(f"PAT '{pat_id}' not found")
    return pat.get("token", "")


def list_pats() -> dict[str, Any]:
    cfg = storage.read_config()
    return {"pats": [_public(p) for p in cfg.get("pats", [])]}


def add_pat(name: str, token: str) -> dict[str, Any]:
    if not name:
        raise PictlError("name is required")
    if not token:
        raise PictlError("token is required")

    with storage.config_transaction() as cfg:
        pats = cfg.setdefault("pats", [])
        pat_id = secrets.token_hex(3)
        # Astronomically unlikely, but keep IDs unique.
        while _find(pats, pat_id):
            pat_id = secrets.token_hex(3)
        entry = {"id": pat_id, "name": name, "token": token}
        pats.append(entry)

    return _public(entry)


def remove_pat(pat_id: str) -> dict[str, Any]:
    warnings: list[str] = []
    with storage.config_transaction() as cfg:
        pats = cfg.setdefault("pats", [])
        pat = _find(pats, pat_id)
        if not pat:
            raise PictlError(f"PAT '{pat_id}' not found")

        referencing = [r["id"] for r in cfg.get("repos", []) if r.get("pat_id") == pat_id]
        if referencing:
            warnings.append(
                f"PAT still referenced by repos: {', '.join(referencing)}"
            )

        pats.remove(pat)

    result: dict[str, Any] = {"id": pat_id, "status": "removed"}
    if warnings:
        result["warnings"] = warnings
    return result
