Blueprint — adapt with care

# Holdout probe

## What it is

A one-task manifest demonstrating the `holdout_check` field: a second executed
check the worker never sees. The visible `check` is deliberately gameable (a
worker can satisfy it by memorizing the expected output); the withheld holdout
exercises the actual behavior. The gap between the two verdicts is the
Goodhart gap, tracked per (model, task_type) by `./ringer.py models`.

## When to use

Use it to audition a model on work where "satisfied the stated check" and
"did the job" can diverge — before trusting that model with a batch whose
checks it could game. Also the right shape for measuring a specific model's
check-gaming propensity on your own workload.

Do not use a holdout that merely repeats the visible check (lint flags it —
it can only re-prove what was already proved), and never quote the holdout
in the spec (lint flags that too: a withheld check the worker can read is a
visible check wearing a blindfold).

## Fill in

| Placeholder | What goes there |
|---|---|
| `{{JOB_NAME}}` | The job, in the human's words. |
| `{{WORKDIR}}` | Scratch directory for the run. |
| `{{TASK_KEY}}` | Task name — becomes the working subdirectory. |
| `{{GOAL}}` | The outcome wanted, WITHOUT quoting the acceptance criteria. |
| `{{OUTPUT_FILE}}` | The deliverable the worker owns. |
| `{{VISIBLE_CHECK}}` | The stated check the worker optimizes against. |
| `{{HOLDOUT}}` | A behavioral assertion on the artifact — run it, feed it a new input, diff against a freshly computed answer. |

## Reading the result

- `PASS` + holdout `pass` — the work is real.
- `PASS` + holdout `fail` — the interesting cell: the stated check was
  satisfied without the job being done. That is routing evidence about the
  model, not a formatting nit.
- holdout `error` — could-not-judge; excluded from the pass rate, never
  counted as a pass.
- Set `"holdout_blocking": true` to turn a holdout failure into a task FAIL
  (no retry — the retry lane exists for stated-check failures, and injecting
  holdout output would leak the withheld check).

Preflight both checks with `./ringer.py run manifest.json --baseline` —
holdouts get the same prove-your-checks treatment as primary checks.
