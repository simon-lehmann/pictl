# pictl CLI summary

Reference for every `pictl` subcommand. Every command prints a single
JSON object to stdout. On success the process exits 0; on failure it
prints `{"error": "<msg>"}` and exits 1 (130 on Ctrl-C).

```
pictl <group> <action> [options]
```

Groups: `stats`, `sessions`, `repos`, `pats`.
(`_session_worker` exists but is internal — invoked by the detached
worker fork; don't call it directly.)

State lives under `~/.pictl/` (see README for layout).

---

## `pictl stats`

Snapshot of the host. Samples CPU over ~0.5s; every field has a safe
fallback so a missing `/proc` entry or absent `vcgencmd` never fails
the command.

Response:

| field             | type         | notes                                   |
| ----------------- | ------------ | --------------------------------------- |
| `cpu_percent`     | float        | 1 − idle/total over the sample window   |
| `ram_used_gb`     | float        | MemTotal − MemAvailable                 |
| `ram_total_gb`    | float        |                                         |
| `disk_used_gb`    | float        | `shutil.disk_usage("/")`                |
| `disk_total_gb`   | float        |                                         |
| `temp_celsius`    | float / null | `vcgencmd`, else `/sys/class/thermal`   |
| `uptime_seconds`  | int          | `/proc/uptime`                          |
| `active_sessions` | int          | status=`running` **and** PID alive      |

---

## `pictl sessions …`

A session is one `claude --remote` process running against a
freshly-cloned working copy. State machine:

```
starting → cloning → running → stopped | dead | failed | cleaned
```

### `sessions list`

Returns `{"sessions": [...]}`. Before returning, reconciles each
`running` record against its PID: if the PID is gone the record is
flipped to `dead` on disk.

### `sessions start --repo <repo_id> --branch <name>`

Reserves an id, writes a `starting` record, and forks a detached
worker that clones the repo, launches `claude --remote`, and tails
the claude log for up to 30s looking for `ssh `, `https://`, or
`claude.ai/` lines (capped at 40) to capture as `remote_code`.
Returns immediately (~10ms) with:

```json
{"id": "...", "status": "starting", "pid": null,
 "remote_code": null, "path": "~/.pictl/sessions/<id>"}
```

Poll `sessions list` to watch the record transition. On failure the
worker writes `status=failed` with an `error` field (and `log_tail`
if claude exited early). Git/clone errors have the PAT scrubbed.

### `sessions stop <id>`

`SIGTERM` the PID, wait up to 5s, then `SIGKILL`. Writes
`status=stopped` and `stopped_at`. Errors if the session id is
unknown; a no-op if the process is already gone.

### `sessions cleanup <id>`

Stops the session if still live, then `rm -rf`s `~/.pictl/sessions/<id>/`
and removes the record from `sessions.json`. Returns
`{"id": "...", "status": "cleaned"}`.

---

## `pictl repos …`

Repos are stored in canonical `host/owner/repo` form (no scheme, no
`.git`). Clone URLs — including the PAT — are assembled at use time,
so rotating a PAT takes effect immediately without rewriting config.

### `repos list`

`{"repos": [{"id","name","url","pat_id"}, ...]}`.

### `repos add --url <url> [--pat <pat_id>]`

Accepts `https://github.com/u/r`, `github.com/u/r`, `git@github.com:u/r.git`,
with or without `.git`. Validates the PAT id if supplied. Returns the
new record. Duplicate URLs are **not** rejected — add-dedup is caller-side.

### `repos remove <id>`

Removes the record. If any session with status `running` or
`starting` references it, the response includes a `warnings` array
listing those session ids — the repo is still removed.

### `repos branches <id>`

`git ls-remote --heads` against the stored URL (with the PAT injected
if one is attached). Returns `{"repo_id": "...", "branches": ["main", ...]}`.
30s timeout. Errors scrub the token.

---

## `pictl pats …`

Personal access tokens. Stored in plaintext in `~/.pictl/config.json`
(mode 0600, single-user Pi). `list` never returns the raw token.

### `pats list`

`{"pats": [{"id","name","token_preview"}, ...]}`. Preview is
`abcd...wxyz` (first-4…last-4), or `a…z` for tokens ≤ 8 chars.

### `pats add --name <label> --token <value>`

Both fields required. Returns the public (masked) record.

### `pats remove <id>`

Removes the PAT. If any repo still references it, the response
includes a `warnings` array listing those repo ids — the PAT is
still removed (and subsequent clones of those repos will fall back
to anonymous HTTPS).

---

## Exit codes

| code | meaning                                           |
| ---- | ------------------------------------------------- |
| 0    | success; JSON object on stdout                    |
| 1    | `PictlError` or unknown action; `{"error": "..."}`|
| 130  | `KeyboardInterrupt`                               |

## Tips

- Pipe output through `python3 -m json.tool` for pretty-printing.
- The HTTP shim (`shim.py`) is a thin translator over this CLI; every
  field documented here is what the Worker / app also sees.
