from cron.workflow_concurrency import (
    can_start_run, acquire_group_slot, release_group_slot,
)


def test_serial_group_allows_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert acquire_group_slot("g", "serial") is True
    assert acquire_group_slot("g", "serial") is False
    release_group_slot("g")
    assert acquire_group_slot("g", "serial") is True
    release_group_slot("g")  # cleanup


def test_parallel_max_n(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert acquire_group_slot("g2", "parallel_max:2") is True
    assert acquire_group_slot("g2", "parallel_max:2") is True
    assert acquire_group_slot("g2", "parallel_max:2") is False
    release_group_slot("g2")
    assert acquire_group_slot("g2", "parallel_max:2") is True
    release_group_slot("g2")
    release_group_slot("g2")  # cleanup


def test_can_start_run_respects_max(tmp_path, monkeypatch):
    from cron.workflow_storage import init_db, create_run
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    init_db()
    create_run("r1", "wf", triggered_by="manual", definition_snapshot={})
    assert can_start_run("wf", max_concurrent_runs=1) is False
    assert can_start_run("wf", max_concurrent_runs=2) is True
    assert can_start_run("other", max_concurrent_runs=1) is True
