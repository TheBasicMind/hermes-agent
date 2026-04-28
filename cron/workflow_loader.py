"""Discovery, raw parsing, and structural schema validation of workflow YAMLs.

Graph and reference checks (cycle detection, package-ID resolution, group
existence) live in `workflow_dag` to keep DAG concerns separate.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List
import yaml

from hermes_constants import get_hermes_home


def workflows_dir() -> Path:
    return get_hermes_home() / "workflows"


def list_workflow_files() -> List[Path]:
    d = workflows_dir()
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.yaml") if p.is_file())


def load_workflow_file(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return data


_NEEDS_POLICIES = {"all_success", "any_success", "all_complete", "any_complete"}


def validate_schema(data: Dict[str, Any]) -> None:
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("workflow.name must be a non-empty string")

    trig = data.get("trigger")
    if not isinstance(trig, dict):
        raise ValueError("workflow.trigger must be a mapping")
    keys = [k for k in ("cron", "manual") if k in trig]
    if len(keys) != 1:
        raise ValueError("workflow.trigger must specify exactly one of cron|manual")
    if "cron" in trig and not isinstance(trig["cron"], str):
        raise ValueError("workflow.trigger.cron must be a string")
    if "manual" in trig and trig["manual"] is not True:
        raise ValueError("workflow.trigger.manual must be literal true")

    mcr = data.get("max_concurrent_runs", 1)
    if not isinstance(mcr, int) or mcr < 1:
        raise ValueError("max_concurrent_runs must be an int >= 1")

    cgd = data.get("concurrency_group_defaults", {}) or {}
    if not isinstance(cgd, dict):
        raise ValueError("concurrency_group_defaults must be a mapping")
    for g, policy in cgd.items():
        if not isinstance(policy, str):
            raise ValueError(f"concurrency_group_defaults.{g} must be a string")
        if policy not in ("serial", "parallel") and not policy.startswith("parallel_max:"):
            raise ValueError(f"concurrency_group_defaults.{g}: invalid policy '{policy}'")

    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("workflow.steps must be a non-empty list")

    seen = set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise ValueError(f"steps[{i}] must be a mapping")
        sid = s.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"steps[{i}].id must be a non-empty string")
        if sid in seen:
            raise ValueError(f"step ids must be unique; duplicate '{sid}'")
        seen.add(sid)
        if not isinstance(s.get("package"), str) or not s["package"].strip():
            raise ValueError(f"steps[{sid}].package must be a non-empty string")
        needs = s.get("needs", []) or []
        if not isinstance(needs, list) or not all(isinstance(x, str) for x in needs):
            raise ValueError(f"steps[{sid}].needs must be a list of strings")
        np = s.get("needs_policy", "all_success")
        if np not in _NEEDS_POLICIES:
            raise ValueError(f"steps[{sid}].needs_policy must be one of {_NEEDS_POLICIES}")
