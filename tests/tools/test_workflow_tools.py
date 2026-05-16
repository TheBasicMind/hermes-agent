import json
import pytest
from tools.workflow_tools import workflow as workflow_tool


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps(
        {"jobs": [{"id": "p1", "name": "P1", "prompt": "x",
                   "schedule": {"kind": "manual"}}]}
    ))
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "demo.yaml").write_text(
        "name: demo\n"
        "trigger: { manual: true }\n"
        "steps:\n"
        "  - { id: a, package: p1 }\n"
    )
    monkeypatch.setattr(
        "cron.workflow_dispatcher._run_worker",
        lambda job, env=None: {"exit_code": 0, "stdout": "ok"},
    )


def test_workflow_list(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="list")
    assert any(w["name"] == "demo" for w in out["workflows"])


def test_workflow_validate_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="validate", name="demo")
    assert out["ok"] is True
    assert out["topo_order"] == ["a"]


def test_workflow_validate_error_for_unknown(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        workflow_tool(verb="validate", name="missing")


def test_workflow_run_and_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="run", name="demo")
    rid = out["run_id"]
    s = workflow_tool(verb="status", run_id=rid)
    assert s["run"]["status"] in ("succeeded", "running")
    assert any(step["step_id"] == "a" for step in s["steps"])


def test_workflow_logs_per_step(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="run", name="demo")
    rid = out["run_id"]
    logs = workflow_tool(verb="logs", run_id=rid, step="a")
    assert logs["step"]["step_id"] == "a"
    # workflow_tool(run) is intentionally non-blocking: it queues ready steps
    # for the gateway cron tick instead of dispatching synchronously.  The
    # result may be absent until the workflow dispatcher runs.
    assert "result" in logs


def test_workflow_logs_all_steps(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="run", name="demo")
    rid = out["run_id"]
    logs = workflow_tool(verb="logs", run_id=rid)
    assert any(step["step_id"] == "a" for step in logs["steps"])


def test_workflow_runs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    workflow_tool(verb="run", name="demo")
    out = workflow_tool(verb="runs", name="demo")
    assert len(out["runs"]) == 1


def test_workflow_show(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="show", name="demo")
    assert out["workflow"]["name"] == "demo"
    assert out["workflow"]["topo_order"] == ["a"]


def test_workflow_cancel(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = workflow_tool(verb="run", name="demo")
    rid = out["run_id"]
    workflow_tool(verb="cancel", run_id=rid)
    s = workflow_tool(verb="status", run_id=rid)
    # If the run already succeeded (instant under fake worker), cancel is a no-op finalize.
    # Either succeeded or cancelled is acceptable; what we want to verify is no error.
    assert s["run"]["status"] in ("succeeded", "cancelled")


def test_workflow_unknown_verb(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unknown verb"):
        workflow_tool(verb="bogus")
