#!/usr/bin/env python3
"""pictl — Raspberry-Pi-side controller for Claude Code sessions.

Every subcommand prints a single JSON object to stdout and exits 0 on
success. Failures print `{"error": "<msg>"}` and exit 1. The HTTP shim
and the React Native app both rely on this contract.

Subcommands:
  stats
  sessions list|start|stop|cleanup
  repos    list|add|remove|branches
  pats     list|add|remove
  _session_worker <id>   (internal; invoked by the detached worker fork)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Make the bundled lib/ package importable when pictl.py is executed via
# a symlink (e.g. ~/.local/bin/pictl -> ~/pictl/pictl.py).
import os
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib import pats, repos, sessions, stats, storage  # noqa: E402
from lib.errors import PictlError  # noqa: E402


def _emit(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _fail(msg: str, code: int = 1) -> int:
    _emit({"error": msg})
    return code


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_stats(_args: argparse.Namespace) -> int:
    _emit(stats.collect())
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    if args.action == "list":
        _emit(sessions.list_sessions())
        return 0
    if args.action == "start":
        _emit(sessions.start_session(args.repo, args.branch))
        return 0
    if args.action == "stop":
        _emit(sessions.stop_session(args.id))
        return 0
    if args.action == "cleanup":
        _emit(sessions.cleanup_session(args.id))
        return 0
    return _fail(f"unknown sessions action: {args.action}")


def cmd_repos(args: argparse.Namespace) -> int:
    if args.action == "list":
        _emit(repos.list_repos())
        return 0
    if args.action == "add":
        _emit(repos.add_repo(args.url, args.pat))
        return 0
    if args.action == "remove":
        _emit(repos.remove_repo(args.id))
        return 0
    if args.action == "branches":
        _emit(repos.list_branches(args.id))
        return 0
    return _fail(f"unknown repos action: {args.action}")


def cmd_pats(args: argparse.Namespace) -> int:
    if args.action == "list":
        _emit(pats.list_pats())
        return 0
    if args.action == "add":
        _emit(pats.add_pat(args.name, args.token))
        return 0
    if args.action == "remove":
        _emit(pats.remove_pat(args.id))
        return 0
    return _fail(f"unknown pats action: {args.action}")


def cmd_session_worker(args: argparse.Namespace) -> int:
    """Internal: run the detached session bootstrap."""
    return sessions.run_worker(args.id)


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pictl", description="Pi-side Claude session controller")
    sub = p.add_subparsers(dest="command", required=True)

    # stats
    sp = sub.add_parser("stats", help="Hardware stats")
    sp.set_defaults(func=cmd_stats)

    # sessions
    sp = sub.add_parser("sessions", help="Manage Claude Code sessions")
    ssub = sp.add_subparsers(dest="action", required=True)
    ssub.add_parser("list").set_defaults(func=cmd_sessions)
    s = ssub.add_parser("start")
    s.add_argument("--repo", required=True, help="Repo id")
    s.add_argument("--branch", required=True, help="Branch to clone")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("stop")
    s.add_argument("id", help="Session id")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("cleanup")
    s.add_argument("id", help="Session id")
    s.set_defaults(func=cmd_sessions)

    # repos
    sp = sub.add_parser("repos", help="Manage repositories")
    rsub = sp.add_subparsers(dest="action", required=True)
    rsub.add_parser("list").set_defaults(func=cmd_repos)
    r = rsub.add_parser("add")
    r.add_argument("--url", required=True)
    r.add_argument("--pat", default=None, help="Optional PAT id for private repos")
    r.set_defaults(func=cmd_repos)
    r = rsub.add_parser("remove")
    r.add_argument("id")
    r.set_defaults(func=cmd_repos)
    r = rsub.add_parser("branches")
    r.add_argument("id")
    r.set_defaults(func=cmd_repos)

    # pats
    sp = sub.add_parser("pats", help="Manage personal access tokens")
    psub = sp.add_subparsers(dest="action", required=True)
    psub.add_parser("list").set_defaults(func=cmd_pats)
    pa = psub.add_parser("add")
    pa.add_argument("--name", required=True)
    pa.add_argument("--token", required=True)
    pa.set_defaults(func=cmd_pats)
    pa = psub.add_parser("remove")
    pa.add_argument("id")
    pa.set_defaults(func=cmd_pats)

    # internal: session worker
    sw = sub.add_parser("_session_worker", help=argparse.SUPPRESS)
    sw.add_argument("id")
    sw.set_defaults(func=cmd_session_worker)

    return p


def main(argv: list[str] | None = None) -> int:
    storage.ensure_dirs()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PictlError as e:
        return _fail(str(e))
    except KeyboardInterrupt:
        return _fail("interrupted", code=130)


if __name__ == "__main__":
    raise SystemExit(main())
