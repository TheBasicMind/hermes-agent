from pathlib import Path
import copy
import pytest
from cron.workflow_loader import load_workflow_file, list_workflow_files

def test_load_minimal_workflow_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "demo.yaml").write_text(
        "name: demo\n"
        "trigger: { manual: true }\n"
        "steps:\n"
        "  - { id: a, package: pkg_a }\n"
    )
    paths = list_workflow_files()
    assert [p.name for p in paths] == ["demo.yaml"]
    wf = load_workflow_file(paths[0])
    assert wf["name"] == "demo"
    assert wf["trigger"] == {"manual": True}
    assert wf["steps"][0]["id"] == "a"


# ---- A2: structural schema validation ----

from cron.workflow_loader import validate_schema

GOOD = {
    "name": "demo",
    "trigger": {"manual": True},
    "steps": [
        {"id": "a", "package": "p1"},
        {"id": "b", "package": "p2", "needs": ["a"], "needs_policy": "any_success"},
    ],
}


def test_validate_schema_accepts_good():
    validate_schema(GOOD)  # should not raise


@pytest.mark.parametrize("mutation,msg", [
    (lambda d: d.pop("name"), "name"),
    (lambda d: d.update(trigger={"cron": "* * * * *", "manual": True}), "exactly one"),
    (lambda d: d.update(steps=[]), "non-empty"),
    (lambda d: d["steps"].append({"id": "a", "package": "dup"}), "unique"),
    (lambda d: d["steps"][1].update(needs_policy="bogus"), "needs_policy"),
])
def test_validate_schema_rejects(mutation, msg):
    d = copy.deepcopy(GOOD)
    mutation(d)
    with pytest.raises(ValueError, match=msg):
        validate_schema(d)


# ---- A1 negative-path tests (suggested by code-quality reviewer) ----

def test_load_workflow_file_rejects_non_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = tmp_path / "bad.yaml"
    p.write_text("- just a list\n")
    from cron.workflow_loader import load_workflow_file
    with pytest.raises(ValueError, match="must be a mapping"):
        load_workflow_file(p)


def test_list_workflow_files_returns_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from cron.workflow_loader import list_workflow_files
    assert list_workflow_files() == []


def test_validate_workflow_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "cron" / "jobs.json").write_text(
        '{"jobs": [{"id": "p1", "name": "P1", "prompt": "...", "schedule": {"kind": "manual"}}]}'
    )
    # cron.jobs binds JOBS_FILE at module-load time, so HERMES_HOME alone isn't
    # sufficient to redirect job storage. Match the existing test_jobs.py pattern.
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    (tmp_path / "workflows").mkdir()
    p = tmp_path / "workflows" / "demo.yaml"
    p.write_text(
        "name: demo\n"
        "trigger: { manual: true }\n"
        "steps:\n"
        "  - { id: a, package: p1 }\n"
    )
    from cron.workflow_loader import validate_workflow
    result = validate_workflow(p)
    assert result["topo_order"] == ["a"]
    assert result["name"] == "demo"
