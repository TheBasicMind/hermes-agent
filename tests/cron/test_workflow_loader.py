from pathlib import Path
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
