# pictl

Raspberry-Pi-side controller for Claude Code sessions.

A Python 3 CLI that manages Claude Code sessions, repos, and GitHub
PATs, plus reports hardware stats. Every command emits a single JSON
object to stdout (exit 0), or `{"error": "..."}` + exit 1.

## Install

```bash
git clone https://github.com/simon-lehmann/pictl.git ~/pictl
cd ~/pictl
./install.sh
pictl doctor   # verify prerequisites
```

`install.sh` checks for Python 3.10+, symlinks
`~/.local/bin/pictl -> ~/pictl/pictl.py`, and creates `~/.pictl/`
(mode 0700).

Python 3 standard library only — no pip install needed.

## CLI

```bash
pictl stats
pictl version
pictl doctor

pictl pats     add --name github --token ghp_xxxxx
pictl pats     list

pictl repos    add --url github.com/user/repo --pat <pat_id>
pictl repos    update <repo_id> --pat <new_pat_id>
pictl repos    branches <repo_id>

pictl sessions start  --repo <repo_id> --branch main
pictl sessions list
pictl sessions logs <session_id> --tail 4096
pictl sessions stop <session_id>
pictl sessions cleanup <session_id>
pictl sessions cleanup-dead    # bulk-clean terminal-state sessions
```

All output is JSON; pipe through `python3 -m json.tool` for readability.

## Data layout

```
~/.pictl/
├── config.json        # repos + PATs (mode 0600)
├── sessions.json      # session metadata
├── sessions/<id>/     # cloned repo + claude logs, one dir per session
├── .askpass.py        # GIT_ASKPASS shim (keeps PATs out of argv)
├── .cpu-sample.json   # cached /proc/stat sample for fast `pictl stats`
└── .locks/            # advisory file locks
```

The config file stores PAT tokens in plain text — the Pi is single-user
and the dir is chmod 700. The `pats list` command only ever returns a
masked preview. `git clone` and `git ls-remote` receive the token via
`GIT_ASKPASS` so it never appears in `ps` output.

## Development

```bash
pip install pytest ruff mypy
pytest -v
ruff check .
mypy --ignore-missing-imports pictl.py lib/
```

CI runs ruff, mypy, pytest, and shellcheck on every push.
