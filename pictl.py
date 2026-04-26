#!/usr/bin/env python3
"""pictl — Raspberry-Pi-side controller for Claude Code sessions.

Every subcommand prints a single JSON object to stdout and exits 0 on
success. Failures print `{"error": "<msg>"}` and exit 1.

Subcommands:
  stats
  sessions list|start|stop|cleanup|cleanup-dead|logs
  repos    list|add|update|remove|branches
  pats     list|add|remove
  doctor
  version
  exec     --json '<{"command","action","args"}>'   (UI bridge)
  _session_worker <id>   (internal; invoked by the detached worker fork)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Make the bundled lib/ package importable when pictl.py is executed via
# a symlink (e.g. ~/.local/bin/pictl -> ~/pictl/pictl.py).
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib import doctor, pats, repos, sessions, stats, storage, version  # noqa: E402
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


def cmd_version(_args: argparse.Namespace) -> int:
    _emit(version.info())
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    result = doctor.run()
    _emit(result)
    return 0 if result["ok"] else 1


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
    if args.action == "cleanup-dead":
        _emit(sessions.cleanup_dead())
        return 0
    if args.action == "logs":
        _emit(sessions.session_logs(args.id, tail_bytes=args.tail))
        return 0
    return _fail(f"unknown sessions action: {args.action}")


def cmd_repos(args: argparse.Namespace) -> int:
    if args.action == "list":
        _emit(repos.list_repos())
        return 0
    if args.action == "add":
        _emit(repos.add_repo(args.url, args.pat))
        return 0
    if args.action == "update":
        _emit(repos.update_repo(
            args.id,
            url=args.url,
            pat_id=args.pat,
            clear_pat=args.clear_pat,
        ))
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
# JSON dispatch (UI bridge)
#
# The argparse path above is the human contract: shell-friendly flags,
# positional ids, kebab-case actions. UIs that already speak JSON would
# have to re-encode every value as a flag string and parse stderr for
# argparse errors. `pictl exec --json '<blob>'` is a second entry point
# that takes a single object {command, action, args} and dispatches to
# the *same* handlers, so the human and programmatic paths can never
# drift on actual behaviour. Both go through `_dispatch`.
# ---------------------------------------------------------------------------


def _require(args: dict[str, Any], *names: str) -> tuple[Any, ...]:
    missing = [n for n in names if n not in args]
    if missing:
        raise PictlError(f"missing required arg(s): {', '.join(missing)}")
    return tuple(args[n] for n in names)


def _dispatch(command: str, action: str | None, args: dict[str, Any]) -> Any:
    """Route a (command, action, args) triple to the matching lib call.

    Returns the same dict the argparse handlers print. Raises PictlError
    on unknown command/action or missing args (mirrors how argparse would
    fail on the human path, but with a structured message we control).
    """
    if command == "stats":
        return stats.collect()
    if command == "version":
        return version.info()
    if command == "doctor":
        return doctor.run()

    if command == "sessions":
        if action == "list":
            return sessions.list_sessions()
        if action == "start":
            (repo, branch) = _require(args, "repo", "branch")
            return sessions.start_session(repo, branch)
        if action == "stop":
            (sid,) = _require(args, "id")
            return sessions.stop_session(sid)
        if action == "cleanup":
            (sid,) = _require(args, "id")
            return sessions.cleanup_session(sid)
        if action == "cleanup-dead":
            return sessions.cleanup_dead()
        if action == "logs":
            (sid,) = _require(args, "id")
            tail = int(args.get("tail", 8192))
            return sessions.session_logs(sid, tail_bytes=tail)
        raise PictlError(f"unknown sessions action: {action}")

    if command == "repos":
        if action == "list":
            return repos.list_repos()
        if action == "add":
            (url,) = _require(args, "url")
            return repos.add_repo(url, args.get("pat"))
        if action == "update":
            (rid,) = _require(args, "id")
            return repos.update_repo(
                rid,
                url=args.get("url"),
                pat_id=args.get("pat"),
                clear_pat=bool(args.get("clear_pat", False)),
            )
        if action == "remove":
            (rid,) = _require(args, "id")
            return repos.remove_repo(rid)
        if action == "branches":
            (rid,) = _require(args, "id")
            return repos.list_branches(rid)
        raise PictlError(f"unknown repos action: {action}")

    if command == "pats":
        if action == "list":
            return pats.list_pats()
        if action == "add":
            (name, token) = _require(args, "name", "token")
            return pats.add_pat(name, token)
        if action == "remove":
            (pid,) = _require(args, "id")
            return pats.remove_pat(pid)
        raise PictlError(f"unknown pats action: {action}")

    raise PictlError(f"unknown command: {command}")


def cmd_exec(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.json)
    except json.JSONDecodeError as e:
        return _fail(f"exec --json: invalid JSON: {e.msg}")
    if not isinstance(payload, dict):
        return _fail("exec --json: payload must be an object")

    command = payload.get("command")
    if not isinstance(command, str):
        return _fail("exec --json: 'command' must be a string")
    action = payload.get("action")
    if action is not None and not isinstance(action, str):
        return _fail("exec --json: 'action' must be a string or omitted")
    raw_args = payload.get("args") or {}
    if not isinstance(raw_args, dict):
        return _fail("exec --json: 'args' must be an object or omitted")

    result = _dispatch(command, action, raw_args)
    _emit(result)
    # NOTE: unlike the argparse path (`pictl doctor` → exit 1 on any
    # failed check, for shell guards), the JSON dispatcher always exits
    # 0 on a successful dispatch. UIs inspect `ok: false` in the body —
    # if we exited 1 here, the bridge's failure handler would scrape the
    # body looking for `{"error": ...}` and surface a confusing message.
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pictl",
        description="Pi-side Claude session controller. All output is JSON.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # stats
    sp = sub.add_parser("stats", help="Snapshot of CPU/RAM/disk/temp/uptime")
    sp.set_defaults(func=cmd_stats)

    # version
    sp = sub.add_parser("version", help="Print pictl version and git commit")
    sp.set_defaults(func=cmd_version)

    # doctor
    sp = sub.add_parser("doctor", help="Verify host has git, claude, perms, etc.")
    sp.set_defaults(func=cmd_doctor)

    # sessions
    sp = sub.add_parser("sessions", help="Manage Claude Code sessions")
    ssub = sp.add_subparsers(dest="action", required=True)
    ssub.add_parser("list", help="List sessions (reconciles dead PIDs)").set_defaults(func=cmd_sessions)
    s = ssub.add_parser("start", help="Clone a repo and launch claude --remote")
    s.add_argument("--repo", required=True, help="Repo id from `pictl repos list`")
    s.add_argument("--branch", required=True, help="Branch to clone")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("stop", help="SIGTERM (then SIGKILL after 5s) the claude process")
    s.add_argument("id", help="Session id")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("cleanup", help="Stop + rm -rf the session dir")
    s.add_argument("id", help="Session id")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("cleanup-dead", help="Bulk-cleanup all dead/failed/stopped sessions")
    s.set_defaults(func=cmd_sessions)
    s = ssub.add_parser("logs", help="Snapshot of claude.log + worker.log tails")
    s.add_argument("id", help="Session id")
    s.add_argument("--tail", type=int, default=8192, help="Tail size in bytes (default: 8192)")
    s.set_defaults(func=cmd_sessions)

    # repos
    sp = sub.add_parser("repos", help="Manage repositories")
    rsub = sp.add_subparsers(dest="action", required=True)
    rsub.add_parser("list", help="List configured repos").set_defaults(func=cmd_repos)
    r = rsub.add_parser("add", help="Register a repo URL")
    r.add_argument("--url", required=True, help="github.com/u/r or full https/ssh URL")
    r.add_argument("--pat", default=None, help="Optional PAT id for private repos")
    r.set_defaults(func=cmd_repos)
    r = rsub.add_parser("update", help="Change a repo's URL or attached PAT")
    r.add_argument("id", help="Repo id")
    r.add_argument("--url", default=None, help="New URL (optional)")
    r.add_argument("--pat", default=None, help="New PAT id (optional)")
    r.add_argument("--clear-pat", action="store_true", help="Detach the current PAT")
    r.set_defaults(func=cmd_repos)
    r = rsub.add_parser("remove", help="Delete a repo record")
    r.add_argument("id", help="Repo id")
    r.set_defaults(func=cmd_repos)
    r = rsub.add_parser("branches", help="`git ls-remote --heads` against the repo")
    r.add_argument("id", help="Repo id")
    r.set_defaults(func=cmd_repos)

    # pats
    sp = sub.add_parser("pats", help="Manage personal access tokens")
    psub = sp.add_subparsers(dest="action", required=True)
    psub.add_parser("list", help="List PATs (token shown masked)").set_defaults(func=cmd_pats)
    pa = psub.add_parser("add", help="Store a new PAT")
    pa.add_argument("--name", required=True, help="Human label, e.g. 'github'")
    pa.add_argument("--token", required=True, help="The raw token")
    pa.set_defaults(func=cmd_pats)
    pa = psub.add_parser("remove", help="Delete a PAT")
    pa.add_argument("id", help="PAT id")
    pa.set_defaults(func=cmd_pats)

    # exec (JSON dispatch — used by the mobile UI bridge)
    ep = sub.add_parser(
        "exec",
        help="Run a command described as a JSON object (UI bridge)",
    )
    ep.add_argument(
        "--json",
        required=True,
        help='JSON object: {"command":"...","action":"...","args":{...}}',
    )
    ep.set_defaults(func=cmd_exec)

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
