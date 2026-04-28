import json
from cron.workflow_context import render_preamble, write_needs_file


def _parents():
    return [
        {"step_id": "phase_2_x", "status": "succeeded",
         "result": "proposals=2", "last_error": None,
         "run_id": "wf1.phase_2_x"},
        {"step_id": "phase_2_skills", "status": "failed",
         "result": None, "last_error": "tool timeout",
         "run_id": "wf1.phase_2_skills"},
    ]


def test_preamble_contains_required_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    p = render_preamble("wf1", "synth-daily", "phase_3", _parents())
    assert "## Workflow Context" in p
    assert "step `phase_3`" in p
    assert "wf1" in p
    assert "phase_2_x: succeeded" in p
    assert "phase_2_skills: failed" in p
    assert "$HERMES_WORKFLOW_NEEDS_FILE" in p


def test_needs_file_written_to_expected_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = write_needs_file("wf1", "phase_3", _parents())
    assert path.exists()
    assert path.parent.name == "wf1"
    assert path.name == "phase_3.needs.json"
    data = json.loads(path.read_text())
    assert {p["step_id"] for p in data["parents"]} == {"phase_2_x", "phase_2_skills"}
