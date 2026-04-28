import pytest

from cron.workflow_snapshot import snapshot_step


def test_snapshot_copies_executable_fields(monkeypatch):
    job = {
        "id": "p1", "name": "P1",
        "prompt": "do X",
        "skills": ["x"], "skill": "x",
        "model": "gpt", "provider": "openrouter", "base_url": None,
        "script": None,
        "deliver": "telegram:123:6",
        "origin": {"platform": "telegram"},
        "schedule": {"kind": "manual"},
        "created_at": "yesterday",   # should NOT carry through
    }
    monkeypatch.setattr("cron.workflow_snapshot.get_job", lambda _id: job)
    snap = snapshot_step({"id": "a", "package": "p1"})
    assert snap["prompt"] == "do X"
    assert snap["deliver"] == "telegram:123:6"
    assert snap["origin"] == {"platform": "telegram"}
    assert "created_at" not in snap
    assert snap["package_id"] == "p1"


def test_snapshot_raises_when_package_not_found(monkeypatch):
    monkeypatch.setattr("cron.workflow_snapshot.get_job", lambda _id: None)
    with pytest.raises(KeyError, match="ghost"):
        snapshot_step({"id": "a", "package": "ghost"})
