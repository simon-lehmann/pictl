#!/usr/bin/env python3
"""Localhost HTTP shim: POST JSON -> run `pictl ...` -> return JSON.

Binds strictly to 127.0.0.1. The only intended caller is the
Cloudflare Tunnel (auth is handled there + in the Worker); nothing on
the LAN or public internet can reach this port.

Request body:
    {"command": "<group>", "action": "<action>", "args": {...}}

Response: pictl's stdout, verbatim, as application/json. HTTP status
codes: 200 on success, 400 on pictl error (exit 1), 500 on crash/timeout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = os.environ.get("PICTL_SHIM_HOST", "127.0.0.1")
PORT = int(os.environ.get("PICTL_SHIM_PORT", "8080"))
TIMEOUT_SECONDS = int(os.environ.get("PICTL_SHIM_TIMEOUT", "10"))
MAX_BODY_BYTES = 64 * 1024

PICTL_PATH = Path(__file__).resolve().parent / "pictl.py"


# (command, action) -> list[ (arg_name, is_flag, is_positional) ]
#
# is_flag=True  -> emitted as `--name value`
# is_flag=False + is_positional=True -> emitted bare, in order given
# A value of None for an optional flag means "omit".
COMMAND_SPEC: dict[tuple[str, str | None], list[tuple[str, bool, bool, bool]]] = {
    # (cmd, action): [(arg_name, required, is_flag, is_positional)]
    ("stats", None):            [],
    ("sessions", "list"):       [],
    ("sessions", "start"):      [("repo", True, True, False),
                                 ("branch", True, True, False)],
    ("sessions", "stop"):       [("id", True, False, True)],
    ("sessions", "cleanup"):    [("id", True, False, True)],
    ("repos", "list"):          [],
    ("repos", "add"):           [("url", True, True, False),
                                 ("pat", False, True, False)],
    ("repos", "remove"):        [("id", True, False, True)],
    ("repos", "branches"):      [("id", True, False, True)],
    ("pats", "list"):           [],
    ("pats", "add"):            [("name", True, True, False),
                                 ("token", True, True, False)],
    ("pats", "remove"):         [("id", True, False, True)],
}


class ShimError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _build_argv(command: str, action: str | None, args: dict[str, Any]) -> list[str]:
    key = (command, action)
    if key not in COMMAND_SPEC:
        raise ShimError(400, f"unknown command: {command} {action or ''}".strip())

    argv: list[str] = [command]
    if action:
        argv.append(action)

    positional: list[str] = []
    flags: list[str] = []

    for name, required, is_flag, is_positional in COMMAND_SPEC[key]:
        value = args.get(name)
        if value is None or value == "":
            if required:
                raise ShimError(400, f"missing required arg: {name}")
            continue
        # Every arg value must be a string-ish scalar.
        if not isinstance(value, (str, int, float, bool)):
            raise ShimError(400, f"arg '{name}' must be a scalar")
        sval = str(value)
        if is_flag:
            flags.extend([f"--{name}", sval])
        elif is_positional:
            positional.append(sval)

    argv.extend(flags)
    argv.extend(positional)
    return argv


def _run_pictl(argv: list[str]) -> tuple[int, bytes]:
    try:
        proc = subprocess.run(
            [sys.executable, str(PICTL_PATH), *argv],
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ShimError(500, "pictl timed out")
    except FileNotFoundError:
        raise ShimError(500, "pictl not found")

    return proc.returncode, proc.stdout or b""


class Handler(BaseHTTPRequestHandler):
    server_version = "pictl-shim/1.0"

    # ------------------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("[shim] " + (fmt % args) + "\n")

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8") + b"\n"
        self._send_json(status, body)

    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/healthz"):
            self._send_json(200, b'{"ok":true}\n')
            return
        self._send_error_json(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/", ""):
            self._send_error_json(404, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(400, "invalid Content-Length")
            return
        if length <= 0:
            self._send_error_json(400, "empty body")
            return
        if length > MAX_BODY_BYTES:
            self._send_error_json(413, "body too large")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(400, "invalid JSON body")
            return
        if not isinstance(payload, dict):
            self._send_error_json(400, "body must be a JSON object")
            return

        command = payload.get("command")
        action = payload.get("action")
        args = payload.get("args") or {}
        if not isinstance(command, str) or not command:
            self._send_error_json(400, "missing 'command'")
            return
        if action is not None and not isinstance(action, str):
            self._send_error_json(400, "'action' must be a string")
            return
        if not isinstance(args, dict):
            self._send_error_json(400, "'args' must be an object")
            return

        try:
            argv = _build_argv(command, action, args)
            rc, stdout = _run_pictl(argv)
        except ShimError as e:
            self._send_error_json(e.status, e.message)
            return

        # pictl exited 0 -> 200; exited 1 -> 400 (business error);
        # anything else -> 500.
        if rc == 0:
            status = 200
        elif rc == 1:
            status = 400
        else:
            status = 500

        if not stdout:
            stdout = json.dumps({"error": "pictl produced no output"}).encode("utf-8") + b"\n"
        self._send_json(status, stdout)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"[shim] listening on http://{HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[shim] shutting down\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
