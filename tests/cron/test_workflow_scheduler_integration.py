import json
from cron.workflow_storage import list_runs


def test_due_workflow_starts_a_run(tmp_path, monkeypatch):
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
        "trigger: { cron: '* * * * *' }\n"
        "steps:\n"
        "  - { id: a, package: p1 }\n"
    )
    monkeypatch.setattr(
        "cron.workflow_dispatcher._run_worker",
        lambda job, env=None: {"exit_code": 0, "stdout": "ok"},
    )
    from cron.scheduler import tick
    tick()  # the * * * * * cron expression matches every minute, so it should be due
    runs = list_runs("demo")
    assert len(runs) == 1
    assert runs[0]["status"] in ("succeeded", "running")
