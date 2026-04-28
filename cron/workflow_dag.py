"""DAG and reference validation for workflow definitions."""
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Set


class WorkflowError(ValueError):
    pass


def validate_dag(wf: Dict[str, Any], available_packages: Iterable[str]) -> List[str]:
    pkgs = set(available_packages)
    groups = set((wf.get("concurrency_group_defaults") or {}).keys())

    steps = {s["id"]: s for s in wf["steps"]}

    # Reference checks (packages, needs, groups)
    for sid, s in steps.items():
        if s["package"] not in pkgs:
            raise WorkflowError(f"step '{sid}': unknown package '{s['package']}'")
        for n in s.get("needs", []) or []:
            if n not in steps:
                raise WorkflowError(f"step '{sid}': unknown step '{n}' in needs")
        g = s.get("group")
        if g and g not in groups:
            raise WorkflowError(f"step '{sid}': group '{g}' not declared in concurrency_group_defaults")

    # Topological sort (Kahn's algorithm)
    indeg = {sid: 0 for sid in steps}
    edges = {sid: [] for sid in steps}
    for sid, s in steps.items():
        for n in s.get("needs", []) or []:
            edges[n].append(sid)
            indeg[sid] += 1

    ready = [sid for sid, d in indeg.items() if d == 0]
    order: List[str] = []
    while ready:
        ready.sort()  # deterministic
        sid = ready.pop(0)
        order.append(sid)
        for nxt in edges[sid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)

    if len(order) != len(steps):
        unresolved: Set[str] = {sid for sid, d in indeg.items() if d > 0}
        raise WorkflowError(f"cycle detected involving steps: {sorted(unresolved)}")
    return order
