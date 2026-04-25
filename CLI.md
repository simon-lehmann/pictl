# pictl CLI summary

`pictl` is a CLI for managing Claude Code sessions on a Raspberry Pi.
Every subcommand prints a single JSON object to stdout and exits 0 on
success; failures print `{"error": "<msg>"}` and exit 1 (130 on
Ctrl-C). No flags are global — each subcommand parses its own.

```
pictl <group> [action] [options]
```

Groups: `stats`, `sessions`, `repos`, `pats`. (`_session_worker` exists
but is internal — invoked by the detached worker fork; don't call it
directly.)

State lives under `~/.pictl/` (override with `PICTL_HOME`):

```
~/.pictl/
├── config.json        # repos + PATs (mode 0600, plaintext tokens)
├── sessions.json      # session metadata
├── sessions/<id>/     # cloned repo + claude logs, one dir per session
└── .locks/            # advisory fcntl locks
```

Writes are atomic (temp file + `os.replace`) and serialised by an
advisory `fcntl` lock per data file, so concurrent invocations are
safe.

---

## `pictl stats`

```
pictl stats
```

Snapshot of the host. Samples CPU over ~0.5s; every field has a safe
fallback so a missing `/proc` entry or absent `vcgencmd` never fails
the command.

| field             | type         | source                                  |
| ----------------- | ------------ | --------------------------------------- |
| `cpu_percent`     | float        | `1 − idle/total` over the sample window |
| `ram_used_gb`     | float        | `MemTotal − MemAvailable`               |
| `ram_total_gb`    | float        | `/proc/meminfo`                         |
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

```
pictl sessions list
```

Returns `{"sessions": [...]}`. Before returning, reconciles each
`running` record against its PID — if the PID is gone the record is
flipped to `dead` and persisted back to `sessions.json`.

### `sessions start --repo <repo_id> --branch <name>`

```
pictl sessions start --repo a1b2c3 --branch main
```

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

```
pictl sessions stop d4e5f6
```

`SIGTERM` the PID, wait up to 5s, then `SIGKILL`. Writes
`status=stopped` and `stopped_at`. Errors if the session id is
unknown; a no-op on the process if it's already gone.

### `sessions cleanup <id>`

```
pictl sessions cleanup d4e5f6
```

Stops the session if still live, then `rm -rf`s
`~/.pictl/sessions/<id>/` and removes the record from `sessions.json`.
Returns `{"id": "...", "status": "cleaned"}`.

---

## `pictl repos …`

Repos are stored in canonical `host/owner/repo` form (no scheme, no
`.git`). Clone URLs — including the PAT — are assembled at use time,
so rotating a PAT takes effect immediately without rewriting config.

### `repos list`

```
pictl repos list
```

`{"repos": [{"id","name","url","pat_id"}, ...]}`.

### `repos add --url <url> [--pat <pat_id>]`

```
pictl repos add --url github.com/user/repo --pat 9f8e7d
```

Accepts `https://github.com/u/r`, `github.com/u/r`,
`git@github.com:u/r.git`, with or without `.git`. Validates the PAT
id if supplied. Returns the new record. Duplicate URLs are **not**
rejected — add-dedup is caller-side.

### `repos remove <id>`

```
pictl repos remove a1b2c3
```

Removes the record. If any session with status `running` or
`starting` references it, the response includes a `warnings` array
listing those session ids — the repo is still removed.

### `repos branches <id>`

```
pictl repos branches a1b2c3
```

`git ls-remote --heads` against the stored URL (with the PAT injected
if one is attached). Returns
`{"repo_id": "...", "branches": ["main", ...]}`. 30s timeout. Errors
scrub the token.

---

## `pictl pats …`

Personal access tokens. Stored in plaintext in `~/.pictl/config.json`
(mode 0600, single-user Pi). `list` never returns the raw token.

### `pats list`

```
pictl pats list
```

`{"pats": [{"id","name","token_preview"}, ...]}`. Preview is
`abcd...wxyz` (first-4…last-4), or `a…z` for tokens ≤ 8 chars.

### `pats add --name <label> --token <value>`

```
pictl pats add --name github --token ghp_xxxxxxxxxxxx
```

Both fields required. Returns the public (masked) record.

### `pats remove <id>`

```
pictl pats remove 9f8e7d
```

Removes the PAT. If any repo still references it, the response
includes a `warnings` array listing those repo ids — the PAT is
still removed (and subsequent clones of those repos will fall back
to anonymous HTTPS).

---

## Exit codes

| code | meaning                                            |
| ---- | -------------------------------------------------- |
| 0    | success; JSON object on stdout                     |
| 1    | `PictlError` or unknown action; `{"error": "..."}` |
| 130  | `KeyboardInterrupt`                                |

## Tips

- Pipe output through `python3 -m json.tool` for pretty-printing.
- Set `PICTL_HOME=/some/path` to relocate the data dir (useful for
  tests).
