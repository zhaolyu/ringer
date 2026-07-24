#!/usr/bin/env python3
"""A non-worktree re-run must not be verified against a previous run's files.

Regression test for the false-PASS hole: plain (non-worktree) task dirs were
reused across runs, so a leftover artifact from an earlier run could satisfy
the check and expect_files without the worker producing anything. The task dir
must be reset before each run so verification only ever sees the current
attempt's output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def toml_string(value: object) -> str:
    return json.dumps(str(value))


def write_config(config_path: Path, state_dir: Path, runs_jsonl: Path) -> None:
    config_path.write_text(
        "\n".join(
            [
                f"state_dir = {toml_string(state_dir)}",
                "",
                "[eval]",
                'backend = "jsonl"',
                f"jsonl_path = {toml_string(runs_jsonl)}",
                "",
                "[artifact]",
                "enabled = false",
                "",
                "[engines.mock]",
                f"bin = {toml_string(sys.executable)}",
                "args_template = [",
                f"  {toml_string(ROOT / 'engines' / 'mock_worker.py')},",
                '  "{spec}",',
                "]",
                "sandbox_args = []",
                "full_access_args = []",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_ringer(
    manifest_path: Path,
    config_path: Path,
    home: Path,
    ringer_home: Path,
    xdg: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["RINGER_HOME"] = str(ringer_home)
    env["XDG_CONFIG_HOME"] = str(xdg)
    env["RINGER_NO_SELF_UPDATE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "ringer.py",
            "run",
            str(manifest_path),
            "--config",
            str(config_path),
            "--no-dashboard",
            "--identity",
            "stale-test",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )


class StaleTaskdirTests(unittest.TestCase):
    def test_non_worktree_rerun_does_not_reuse_prior_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            home = root / "home"
            home.mkdir()
            ringer_home = root / "ringer-home"
            ringer_home.mkdir()
            xdg = root / "xdg-config"
            state_dir = root / "state"
            workdir = root / "work"
            config_path = root / "config.toml"
            write_config(config_path, state_dir, root / "runs.jsonl")

            artifact = workdir / "probe" / "artifact.txt"

            # Run 1: the worker produces artifact.txt and the check passes,
            # leaving artifact.txt behind in the reused plain taskdir.
            producing = {
                "run_name": "stale-probe",
                "workdir": str(workdir),
                "max_parallel": 1,
                "worktrees": False,
                "tasks": [
                    {
                        "key": "probe",
                        "engine": "mock",
                        "task_type": "probe",
                        "spec": (
                            "You are the deterministic mock worker.\n"
                            "MOCK_FILE: artifact.txt\n"
                            "produced by run 1\n"
                            "MOCK_END"
                        ),
                        "check": (
                            "test -f artifact.txt || "
                            "{ echo FAIL: artifact.txt missing; exit 1; }"
                        ),
                        "expect_files": ["artifact.txt"],
                    }
                ],
            }
            producing_path = root / "producing.json"
            producing_path.write_text(json.dumps(producing, indent=2), encoding="utf-8")
            first = run_ringer(producing_path, config_path, home, ringer_home, xdg)
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            self.assertTrue(artifact.exists(), "run 1 should have produced artifact.txt")

            # Run 2: same workdir and key, but the worker writes nothing (no
            # MOCK_FILE block => exit 0, produces no file). The check still
            # demands artifact.txt. If the taskdir were reused, run 1's file
            # would satisfy the check and expect_files -> a false PASS. With the
            # reset, the stale file is gone and run 2 must FAIL.
            noop = json.loads(json.dumps(producing))
            noop["tasks"][0]["spec"] = (
                "You are the deterministic mock worker. Produce no files this run."
            )
            noop_path = root / "noop.json"
            noop_path.write_text(json.dumps(noop, indent=2), encoding="utf-8")
            second = run_ringer(noop_path, config_path, home, ringer_home, xdg)

            combined = second.stdout + second.stderr
            self.assertEqual(1, second.returncode, combined)
            self.assertRegex(
                combined,
                re.compile(r"^probe\s+fail\s+FAIL\s+\d", re.MULTILINE),
                combined,
            )
            self.assertFalse(
                artifact.exists(),
                "the stale artifact from run 1 must not survive into run 2",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
