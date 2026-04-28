"""Smoke tests for the `hermes workflow` CLI subcommands.

Uses subprocess to exercise the real argparse wiring + module imports so
"module doesn't import" and "argparse args wrong" failures are caught.
"""
import json
import os
import subprocess


def _venv_python():
    return "/Users/paullancefield/Local_Projects/HermesProject/.hermes-home/hermes-agent/venv/bin/python"


def _cwd():
    return "/Users/paullancefield/.config/superpowers/worktrees/hermes-agent/feat-hermes-job-queue"


def test_workflow_list_cli_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps({"jobs": []}))
    (tmp_path / "workflows").mkdir()
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    out = subprocess.run(
        [_venv_python(), "-m", "hermes_cli", "workflow", "list", "--json"],
        capture_output=True, text=True, env=env,
        cwd=_cwd(),
    )
    assert out.returncode == 0, f"stdout: {out.stdout}\nstderr: {out.stderr}"
    data = json.loads(out.stdout)
    assert data["workflows"] == []


def test_workflow_validate_unknown_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps({"jobs": []}))
    (tmp_path / "workflows").mkdir()
    env = {**os.environ, "HERMES_HOME": str(tmp_path)}
    out = subprocess.run(
        [_venv_python(), "-m", "hermes_cli", "workflow", "validate", "nonexistent"],
        capture_output=True, text=True, env=env,
        cwd=_cwd(),
    )
    assert out.returncode != 0
