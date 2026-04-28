"""Discovery and raw parsing of workflow YAML files.

Loaded shape is intentionally a plain dict — structural validation lives in
`workflow_dag` to keep parse and validate concerns separate.
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
