from cron.workflow_dispatcher import dispatch_step
from cron.workflow_runtime import start_run
from cron.workflow_storage import get_step


def test_dispatch_injects_preamble_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "cron.workflow_snapshot.get_job",
        lambda pid: {"id": pid, "prompt": "ORIGINAL PROMPT", "skills": [],
                     "skill": None, "model": None, "provider": None,
                     "base_url": None, "script": None, "deliver": "local",
                     "origin": None},
    )
    captured = {}

    def fake_run(job, *, env=None):
        captured["prompt"] = job["prompt"]
        captured["env"] = dict(env or {})
        captured["job"] = dict(job)
        return {"exit_code": 0, "stdout": "OK"}

    monkeypatch.setattr("cron.workflow_dispatcher._run_worker", fake_run)

    wf = {"name": "wf", "trigger": {"manual": True},
          "steps": [{"id": "a", "package": "p1"}], "topo_order": ["a"]}
    run_id = start_run(wf, triggered_by="manual")
    dispatch_step(run_id, "a")

    assert "## Workflow Context" in captured["prompt"]
    assert "ORIGINAL PROMPT" in captured["prompt"]
    assert captured["env"]["HERMES_WORKFLOW_RUN_ID"] == run_id
    assert "HERMES_WORKFLOW_NEEDS_FILE" in captured["env"]
    assert captured["job"]["id"] == f"{run_id}.a"
    assert captured["job"]["name"] == "wf/a"

    s = get_step(run_id, "a")
    assert s["status"] == "succeeded"
    assert s["result"] == "OK"
