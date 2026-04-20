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
```

`install.sh` symlinks `~/.local/bin/pictl -> ~/pictl/pictl.py` and
creates `~/.pictl/` (mode 0700).

Python 3 standard library only — no pip install needed.

## CLI

```bash
pictl stats
pictl pats   add --name github --token ghp_xxxxx
pictl pats   list
pictl repos  add --url github.com/user/repo --pat <pat_id>
pictl repos  branches <repo_id>
pictl sessions start  --repo <repo_id> --branch main
pictl sessions list
pictl sessions stop <session_id>
pictl sessions cleanup <session_id>
```

All output is JSON; pipe through `python3 -m json.tool` for readability.

## Data layout

```
~/.pictl/
├── config.json        # repos + PATs
├── sessions.json      # session metadata
├── sessions/<id>/     # cloned repo + claude logs, one dir per session
└── .locks/            # advisory file locks
```

The config file stores PAT tokens in plain text — the Pi is single-user
and the dir is chmod 700. The `pats list` command only ever returns a
masked preview.
