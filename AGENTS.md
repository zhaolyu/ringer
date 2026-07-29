# AGENTS.md — ringer

Repository router for Forge slug `ringer`.

<!-- forge-agent-baseline:v1 begin -->
## Forge agent baseline

This child repository is an independent Git root. Do not assume the parent
Forge `AGENTS.md` was loaded.

- Never commit secrets, environment files, keys, tokens, or credential-bearing
  configuration. Stop and identify the exact file if one appears.
- Never commit, amend, bypass hooks, force-push, or push unless the user
  explicitly authorizes that specific action.
- Use the repository's own documented build, lint, typecheck, and test commands.
  Run scoped verification before claiming implementation work is complete.
- Keep changes within this repository unless the task explicitly requires a
  cross-repo change.
- For unpublished Forge library changes, use `bin/forge-link` from the Forge
  root when available. Do not use `npm link`, `pnpm link`, or overrides.
- Report the commands run, their outcomes, and anything skipped.
<!-- forge-agent-baseline:v1 end -->
