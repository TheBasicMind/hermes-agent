import pytest
from cron.workflow_dag import validate_dag, WorkflowError


def _wf(steps, **extra):
    return {"name": "x", "trigger": {"manual": True}, "steps": steps, **extra}


def test_dag_topo_order_simple():
    wf = _wf([{"id": "a", "package": "p"},
              {"id": "b", "package": "p", "needs": ["a"]}])
    assert validate_dag(wf, available_packages={"p"}) == ["a", "b"]


def test_dag_rejects_cycle():
    wf = _wf([
        {"id": "a", "package": "p", "needs": ["b"]},
        {"id": "b", "package": "p", "needs": ["a"]},
    ])
    with pytest.raises(WorkflowError, match="cycle"):
        validate_dag(wf, available_packages={"p"})


def test_dag_rejects_unknown_needs():
    wf = _wf([{"id": "a", "package": "p", "needs": ["ghost"]}])
    with pytest.raises(WorkflowError, match="unknown step 'ghost'"):
        validate_dag(wf, available_packages={"p"})


def test_dag_rejects_unknown_package():
    wf = _wf([{"id": "a", "package": "missing"}])
    with pytest.raises(WorkflowError, match="unknown package 'missing'"):
        validate_dag(wf, available_packages={"p"})


def test_dag_rejects_unknown_group():
    wf = _wf([{"id": "a", "package": "p", "group": "phase_2"}])
    with pytest.raises(WorkflowError, match="group 'phase_2'"):
        validate_dag(wf, available_packages={"p"})


def test_dag_accepts_inline_group():
    wf = _wf(
        [{"id": "a", "package": "p", "group": "phase_2"}],
        concurrency_group_defaults={"phase_2": "serial"},
    )
    validate_dag(wf, available_packages={"p"})  # no raise
