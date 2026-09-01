#!/usr/bin/env python3
"""Phase 1 of holdout-and-monitor-plan.md: holdout_check + the Goodhart gap.

The regression pin is the known-dirty fixture: a worker that hard-codes the
primary check's expected output PASSes the visible check and FAILs the
holdout. The leakage tests assert the holdout command never reaches a worker
via any argv, in normal and blocking modes alike.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import (  # noqa: E402
    Manifest,
    TaskSpec,
    Verifier,
    aggregate_model_log_rows,
    lint_manifest,
)

LONG_SPEC = (
    "Create the requested artifact in the current working directory, keep the change scoped, "
    "and make the check command able to explain any failure clearly."
)

GOOD_CHECK = (
    "test -s output.txt && grep -q 'ready' output.txt || "
    "{ echo 'FAIL: output.txt missing or does not contain ready'; exit 1; }"
)

GOOD_HOLDOUT = (
    "grep -q 'behavior' output.txt || "
    "{ echo 'FAIL: output.txt does not demonstrate the behavior'; exit 1; }"
)

# A sentinel that must never appear in anything handed to a worker.
HOLDOUT_SENTINEL = "HOLDOUT_SENTINEL_1f2e3d4c"


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def task_obj(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "key": "alpha",
        "spec": LONG_SPEC,
        "check": GOOD_CHECK,
        "expect_files": ["output.txt"],
        "verified": "output.txt exists and contains ready",
    }
    base.update(overrides)
    return base


class HoldoutTaskSpecTests(unittest.TestCase):
    def test_holdout_fields_parse(self) -> None:
        task = TaskSpec.from_obj(
            task_obj(holdout_check=GOOD_HOLDOUT, holdout_blocking=True)
        )
        self.assertEqual(GOOD_HOLDOUT, task.holdout_check)
        self.assertTrue(task.holdout_blocking)

    def test_holdout_defaults_absent(self) -> None:
        task = TaskSpec.from_obj(task_obj())
        self.assertEqual("", task.holdout_check)
        self.assertFalse(task.holdout_blocking)

    def test_holdout_blocking_without_check_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec.from_obj(task_obj(holdout_blocking=True))

    def test_holdout_check_must_be_string(self) -> None:
        with self.assertRaises(ValueError):
            TaskSpec.from_obj(task_obj(holdout_check=["not", "a", "string"]))


class HoldoutLintTests(unittest.TestCase):
    def manifest_with(self, **overrides: object) -> Manifest:
        return Manifest.from_obj(
            {
                "run_name": "holdout-lint-test",
                "workdir": "/tmp/holdout-lint-test",
                "max_parallel": 2,
                "tasks": [task_obj(**overrides)],
            }
        )

    def findings(self, **overrides: object) -> list[str]:
        return lint_manifest(self.manifest_with(**overrides))

    def test_clean_holdout_produces_no_holdout_findings(self) -> None:
        findings = self.findings(holdout_check=GOOD_HOLDOUT)
        self.assertFalse([f for f in findings if "holdout" in f], findings)

    def test_holdout_leak_in_spec_is_flagged(self) -> None:
        leaky_spec = f"{LONG_SPEC} Afterwards ensure this passes: {GOOD_HOLDOUT}"
        findings = self.findings(spec=leaky_spec, holdout_check=GOOD_HOLDOUT)
        self.assertTrue([f for f in findings if "holdout leak" in f], findings)

    def test_holdout_identical_to_check_is_flagged(self) -> None:
        findings = self.findings(holdout_check="  " + GOOD_CHECK.replace("  ", " "))
        self.assertTrue([f for f in findings if "identical to the primary check" in f], findings)

    def test_holdout_that_cannot_fail_is_flagged(self) -> None:
        findings = self.findings(holdout_check="true")
        self.assertTrue([f for f in findings if "holdout: check cannot fail" in f], findings)

    def test_silent_holdout_is_flagged(self) -> None:
        findings = self.findings(holdout_check="test -f output.txt")
        self.assertTrue(
            [f for f in findings if "holdout: check may fail without printing why" in f],
            findings,
        )


class HoldoutVerifierTests(unittest.TestCase):
    def run_holdout(self, task: TaskSpec, taskdir: Path):
        return asyncio.run(Verifier().run_holdout(task, taskdir))

    def test_pass_fail_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            taskdir = Path(temp)
            (taskdir / "output.txt").write_text("ready behavior\n", encoding="utf-8")
            task = TaskSpec.from_obj(task_obj(holdout_check=GOOD_HOLDOUT))
            result = self.run_holdout(task, taskdir)
            self.assertEqual("pass", result.outcome)
            self.assertEqual(0, result.check_returncode)

            (taskdir / "output.txt").write_text("ready only\n", encoding="utf-8")
            result = self.run_holdout(task, taskdir)
            self.assertEqual("fail", result.outcome)
            self.assertIn("does not demonstrate", result.raw_output_excerpt)

    def test_silent_failure_gets_a_named_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task = TaskSpec.from_obj(task_obj(holdout_check="exit 3"))
            result = self.run_holdout(task, Path(temp))
            self.assertEqual("fail", result.outcome)
            self.assertIn("failed silently", result.raw_output_excerpt)

    def test_unrunnable_holdout_is_error_never_pass(self) -> None:
        # A missing taskdir (the worktree-already-gone shape) must be
        # could-not-judge, not a verdict in either direction.
        missing = Path(tempfile.mkdtemp()) / "never-created"
        task = TaskSpec.from_obj(task_obj(holdout_check=GOOD_HOLDOUT))
        result = self.run_holdout(task, missing)
        self.assertEqual("error", result.outcome)
        self.assertIn("could not run", result.raw_output_excerpt)


class GoodhartAggregationTests(unittest.TestCase):
    @staticmethod
    def row(task_key: str, verdict: str, holdout: str | None, declared: bool = True) -> dict:
        row = {
            "run_id": "r1",
            "task_key": task_key,
            "logged_at": "2026-09-01T00:00:00+00:00",
            "verdict": verdict,
            "worker_engine": "mock",
            "model": "test/model-a",
            "task_type": "probe",
            "retry": False,
            "duration_ms": 100,
            "worker_tokens": 10,
            "holdout_declared": declared,
        }
        if holdout is not None:
            row["holdout"] = holdout
        return row

    def test_gap_over_the_declaring_slice_with_errors_excluded(self) -> None:
        rows = [
            self.row("t1", "PASS", "pass"),
            self.row("t2", "PASS", "fail"),
            self.row("t3", "PASS", "fail"),
            self.row("t4", "PASS", "error"),
            self.row("t5", "FAIL", None),  # declared, primary failed: no holdout ran
            self.row("t6", "PASS", None, declared=False),  # no holdout declared
        ]
        groups = aggregate_model_log_rows(rows)
        self.assertEqual(1, len(groups))
        group = groups[0]
        self.assertEqual(5, group["holdout_tasks"])
        self.assertEqual(1, group["holdout_passed"])
        self.assertEqual(2, group["holdout_failed"])
        self.assertEqual(1, group["holdout_errors"])
        # judged = 3 (errors excluded), so holdout_pass_rate = 1/3;
        # slice primary pass rate = ran/declared = 4/5.
        self.assertAlmostEqual(1 / 3, group["holdout_pass_rate"])
        self.assertAlmostEqual(4 / 5 - 1 / 3, group["goodhart_gap"])

    def test_no_holdout_data_means_none_not_zero(self) -> None:
        groups = aggregate_model_log_rows([self.row("t1", "PASS", None, declared=False)])
        group = groups[0]
        self.assertEqual(0, group["holdout_tasks"])
        self.assertIsNone(group["holdout_pass_rate"])
        self.assertIsNone(group["goodhart_gap"])

    def test_all_errors_means_no_rate_and_no_gap(self) -> None:
        groups = aggregate_model_log_rows([self.row("t1", "PASS", "error")])
        group = groups[0]
        self.assertEqual(1, group["holdout_errors"])
        self.assertIsNone(group["holdout_pass_rate"])
        self.assertIsNone(group["goodhart_gap"])


CAPTURE_WORKER = '''#!/usr/bin/env python3
"""Test worker: dumps its full argv (the spec included) for leak assertions,
then hard-codes the file the primary check expects — the memorizing worker."""
import sys
from pathlib import Path

Path("_argv_dump.txt").write_text("\\n".join(sys.argv), encoding="utf-8")
Path("solution.txt").write_text("the answer is 42\\n", encoding="utf-8")
print("capture-worker: done")
'''


class HoldoutEndToEndTests(unittest.TestCase):
    """One offline run covering the known-dirty fixture, blocking semantics,
    leak absence, log-schema back-compat, and the models scoreboard."""

    def run_ringer(self, root: Path, *argv: str, timeout: int = 60) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["HOME"] = str(root / "home")
        env["RINGER_HOME"] = str(root / "ringer-home")
        env["XDG_CONFIG_HOME"] = str(root / "xdg-config")
        return subprocess.run(
            [sys.executable, "ringer.py", *argv],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )

    def write_config(self, root: Path) -> Path:
        worker = root / "capture_worker.py"
        worker.write_text(CAPTURE_WORKER, encoding="utf-8")
        config_path = root / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    f"state_dir = {toml_string(root / 'state')}",
                    "",
                    "[eval]",
                    'backend = "jsonl"',
                    f"jsonl_path = {toml_string(root / 'runs.jsonl')}",
                    "",
                    "[artifact]",
                    "enabled = false",
                    "",
                    "[engines.capture]",
                    f"bin = {toml_string(sys.executable)}",
                    "args_template = [",
                    f"  {toml_string(worker)},",
                    '  "{spec}",',
                    "]",
                    "sandbox_args = []",
                    "full_access_args = []",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return config_path

    def test_holdout_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "home").mkdir()
            (root / "ringer-home").mkdir()
            workdir = root / "work"
            config_path = self.write_config(root)

            spec = (
                "You are a worker. Write solution.txt containing a computed answer "
                "for input 21 doubled, derived by actually doubling — not by copying "
                "an expected string."
            )
            # The memorizing worker satisfies this visible check by hard-coding.
            primary_check = (
                "grep -q '42' solution.txt || { echo 'FAIL: no answer in solution.txt'; exit 1; }"
            )
            # The behavioral holdout the memorized artifact cannot satisfy.
            dirty_holdout = (
                f"grep -q 'derived from input 21' solution.txt || "
                f"{{ echo 'FAIL {HOLDOUT_SENTINEL}: answer was not derived, likely memorized'; exit 1; }}"
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "holdout-e2e",
                        "workdir": str(workdir),
                        "max_parallel": 2,
                        "tasks": [
                            {
                                "key": "memorizer",
                                "engine": "capture",
                                "spec": spec,
                                "check": primary_check,
                                "expect_files": ["solution.txt"],
                                "verified": "solution.txt carries the answer",
                                "task_type": "probe",
                                "holdout_check": dirty_holdout,
                            },
                            {
                                "key": "honest-pass",
                                "engine": "capture",
                                "spec": spec,
                                "check": primary_check,
                                "expect_files": ["solution.txt"],
                                "verified": "solution.txt carries the answer",
                                "task_type": "probe",
                                "holdout_check": (
                                    "grep -q '42' solution.txt || { echo FAIL: gone; exit 1; }"
                                ),
                            },
                            {
                                "key": "blocked",
                                "engine": "capture",
                                "spec": spec,
                                "check": primary_check,
                                "expect_files": ["solution.txt"],
                                "verified": "solution.txt carries the answer",
                                "task_type": "probe",
                                "holdout_check": dirty_holdout,
                                "holdout_blocking": True,
                            },
                            {
                                "key": "primary-fails",
                                "engine": "capture",
                                "spec": spec,
                                "check": (
                                    "test -f never-written.txt || "
                                    "{ echo 'FAIL: never-written.txt missing'; exit 1; }"
                                ),
                                "expect_files": ["never-written.txt"],
                                "verified": "the impossible file exists",
                                "task_type": "probe",
                                "holdout_check": dirty_holdout,
                            },
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            proc = self.run_ringer(
                root,
                "run",
                str(manifest_path),
                "--config",
                str(config_path),
                "--no-dashboard",
                "--identity",
                "holdout-test",
            )
            output = proc.stdout

            # Known-dirty fixture: visible check PASSes, holdout says fail.
            self.assertRegex(output, re.compile(r"^memorizer\s+pass\s+PASS\s+fail\s+", re.M), output)
            self.assertRegex(output, re.compile(r"^honest-pass\s+pass\s+PASS\s+pass\s+", re.M), output)
            # Blocking: FAIL with NO retry consumed (attempts == 1).
            self.assertRegex(output, re.compile(r"^blocked\s+fail\s+FAIL\s+fail\s+\s*1\s+", re.M), output)
            # Primary failure: holdout never ran (empty holdout column), retry consumed.
            self.assertRegex(output, re.compile(r"^primary-fails\s+fail\s+FAIL\s+\s+2\s+", re.M), output)

            # Leakage: the sentinel reaches NO worker argv, in any task.
            for key in ("memorizer", "honest-pass", "blocked", "primary-fails"):
                dump = (workdir / key / "_argv_dump.txt").read_text(encoding="utf-8")
                self.assertNotIn(HOLDOUT_SENTINEL, dump, key)
                log = (workdir / key / "worker.log").read_text(encoding="utf-8")
                self.assertNotIn(HOLDOUT_SENTINEL, log, key)

            # Eval rows: three outcomes and schema back-compat.
            rows = [
                json.loads(line)
                for line in (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_task: dict[str, list[dict]] = {}
            for row in rows:
                by_task.setdefault(row["task_key"], []).append(row)
            self.assertEqual("fail", by_task["memorizer"][-1]["holdout"])
            self.assertEqual("pass", by_task["honest-pass"][-1]["holdout"])
            self.assertEqual("fail", by_task["blocked"][-1]["holdout"])
            self.assertEqual("FAIL", by_task["blocked"][-1]["verdict"])
            self.assertEqual(1, len(by_task["blocked"]))  # no retry rows
            for row in by_task["primary-fails"]:
                self.assertTrue(row["holdout_declared"])
                self.assertNotIn("holdout", row)  # declared but never ran

            # Scoreboard: the Goodhart gap shows up in `models`.
            models = self.run_ringer(
                root,
                "models",
                "--log",
                str(root / "runs.jsonl"),
                "--json",
            )
            groups = json.loads(models.stdout.strip().splitlines()[-1])
            probe = [g for g in groups if g["task_type"] == "probe"]
            self.assertEqual(1, len(probe), models.stdout)
            group = probe[0]
            self.assertEqual(4, group["holdout_tasks"])
            # judged = 3 (memorizer fail, honest pass, blocked fail): rate 1/3;
            # slice primary pass rate = ran/declared = 3/4.
            self.assertAlmostEqual(1 / 3, group["holdout_pass_rate"])
            self.assertAlmostEqual(3 / 4 - 1 / 3, group["goodhart_gap"])

    def test_baseline_executes_holdouts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "home").mkdir()
            (root / "ringer-home").mkdir()
            config_path = self.write_config(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "holdout-baseline",
                        "workdir": str(root / "work"),
                        "max_parallel": 1,
                        "tasks": [
                            {
                                "key": "alpha",
                                "engine": "capture",
                                "spec": LONG_SPEC,
                                "check": GOOD_CHECK,
                                "expect_files": ["output.txt"],
                                "verified": "output.txt is ready",
                                "holdout_check": GOOD_HOLDOUT,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            proc = self.run_ringer(
                root,
                "run",
                str(manifest_path),
                "--config",
                str(config_path),
                "--baseline",
            )
            # Both checks demand NEW behavior, so both are expected to fail
            # baseline — the point is that the holdout was EXECUTED and named.
            self.assertIn("baseline-holdout: FAIL", proc.stdout, proc.stdout)
            self.assertIn("baseline holdouts: 1 fail", proc.stdout, proc.stdout)

    def test_worktree_holdout_runs_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "home").mkdir()
            (root / "ringer-home").mkdir()
            config_path = self.write_config(root)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
            git_env = os.environ.copy()
            git_env.update(
                {
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.invalid",
                }
            )
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=git_env)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, env=git_env
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "run_name": "holdout-worktree",
                        "workdir": str(root / "work"),
                        "max_parallel": 1,
                        "worktrees": True,
                        "repo": str(repo),
                        "tasks": [
                            {
                                "key": "wt",
                                "engine": "capture",
                                "spec": LONG_SPEC,
                                # Primary and holdout both read files that exist
                                # ONLY inside the live worktree: a holdout run
                                # after cleanup would error, not pass.
                                "check": (
                                    "test -s solution.txt || { echo 'FAIL: no solution'; exit 1; }"
                                ),
                                "verified": "solution exists in the worktree",
                                "holdout_check": (
                                    "grep -q seed seed.txt || { echo 'FAIL: worktree gone'; exit 1; }"
                                ),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            proc = self.run_ringer(
                root,
                "run",
                str(manifest_path),
                "--config",
                str(config_path),
                "--no-dashboard",
                "--identity",
                "holdout-test",
            )
            self.assertRegex(
                proc.stdout, re.compile(r"^wt\s+pass\s+PASS\s+pass\s+", re.M), proc.stdout
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
