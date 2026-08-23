# AGENTS.md — ringer

Repository router for Forge slug `ringer`.

**verify**: `python3 -m unittest discover -s tests`



<!-- forge-agent-baseline:v1 begin -->
## Forge agent baseline

This child repository is an independent Git root. Do not assume the parent
Forge `AGENTS.md` was loaded.

- Never commit secrets, environment files, keys, tokens, or credential-bearing
  configuration. Stop and identify the exact file if one appears.
- Never commit, amend, bypass hooks, force-push, or push unless the user
  explicitly authorizes that specific action.
- Run this repository's declared verify command — the `**verify**` line near the
  top of this file — before claiming implementation work is complete. If it
  reads `none`, there is nothing to run. Scope tests to the touched path when
  you know it.
- Keep changes within this repository unless the task explicitly requires a
  cross-repo change.
- Report the commands run, their outcomes, and anything skipped.
<!-- forge-agent-baseline:v1 end -->
