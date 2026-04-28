import json
from cron.workflow_storage import (
    init_db, create_run, get_run, list_runs,
    create_step, update_step, get_step, list_steps_for_run,
)


def test_create_and_fetch_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual",
               definition_snapshot={"name": "wf", "steps": []})
    r = get_run("r1")
    assert r["workflow_name"] == "wf"
    assert r["status"] == "running"
    assert json.loads(r["definition_snapshot"])["name"] == "wf"


def test_step_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual", definition_snapshot={})
    create_step("r1", "a", snapshot={"prompt": "do thing"})
    update_step("r1", "a", status="running")
    update_step("r1", "a", status="succeeded", result="ok")
    s = get_step("r1", "a")
    assert s["status"] == "succeeded"
    assert s["result"] == "ok"
    assert json.loads(s["snapshot"])["prompt"] == "do thing"
