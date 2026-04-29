import gc
import json
import sqlite3
import pytest
from cron.workflow_storage import (
    init_db, create_run, get_run, list_runs, update_run,
    create_step, update_step, get_step, list_steps_for_run,
    _cursor,
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
    # auto-stamping pinned: started_at on running, finished_at on terminal
    assert s["started_at"] is not None
    assert s["finished_at"] is not None


def test_update_step_preserves_caller_supplied_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual", definition_snapshot={})
    create_step("r1", "a", snapshot={})
    update_step("r1", "a", status="running", started_at="2026-01-01T00:00:00+00:00")
    assert get_step("r1", "a")["started_at"] == "2026-01-01T00:00:00+00:00"


def test_update_run_writes_arbitrary_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual", definition_snapshot={})
    update_run("r1", status="succeeded", finished_at="2026-04-28T00:00:00+00:00")
    r = get_run("r1")
    assert r["status"] == "succeeded"
    assert r["finished_at"] == "2026-04-28T00:00:00+00:00"


def test_list_runs_filters_by_workflow_and_orders_by_recency(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf_a", triggered_by="manual", definition_snapshot={})
    create_run("r2", "wf_b", triggered_by="cron", definition_snapshot={})
    create_run("r3", "wf_a", triggered_by="manual", definition_snapshot={})
    a_runs = list_runs("wf_a")
    assert [r["run_id"] for r in a_runs] == ["r3", "r1"]
    assert len(list_runs("wf_b")) == 1
    assert len(list_runs()) == 3


def test_list_steps_for_run_orders_by_insertion(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual", definition_snapshot={})
    for sid in ("c", "a", "b"):
        create_step("r1", sid, snapshot={})
    assert [s["step_id"] for s in list_steps_for_run("r1")] == ["c", "a", "b"]


def test_create_step_rejects_unknown_run_via_foreign_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    with pytest.raises(sqlite3.IntegrityError):
        create_step("ghost_run", "a", snapshot={})


def test_storage_does_not_leak_sqlite_connections(tmp_path, monkeypatch):
    """Regression: previously `init_db()` used `with sqlite3_conn:` which is
    a *transaction* context manager — it does NOT close the connection.
    Repeated storage operations therefore leaked one fd per call until gc
    reclaimed the connection, exhausting the gateway's fd table under load."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from cron.workflow_storage import _inited_paths
    _inited_paths.clear()

    def _do_work():
        init_db()
        with _cursor() as c:
            c.execute("SELECT 1").fetchone()

    gc.collect()
    baseline = sum(1 for o in gc.get_objects() if isinstance(o, sqlite3.Connection))

    for _ in range(50):
        _do_work()

    gc.collect()
    final = sum(1 for o in gc.get_objects() if isinstance(o, sqlite3.Connection))
    assert final == baseline, (
        f"sqlite connections leaked: baseline={baseline} final={final} "
        f"(delta={final - baseline})"
    )
