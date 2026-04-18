"""Skillified facade tool.

Provides a single stable LLM-visible tool ``skillified_call`` that dispatches
to hidden source tools via ``model_tools.handle_function_call``.  Rich API
contracts for each capability live in ``skd_*`` skill files loaded lazily via
the existing skills progressive-disclosure mechanism.

See docs/superpowers/specs/2026-04-18-skillify-design.md for the design.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from tools.registry import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillifyCollisionError(RuntimeError):
    """Raised when two discoverable sources define the same capability or toolset."""


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterSpec:
    capability: str
    operations: Mapping[str, Mapping[str, Any]]
    source_path: Path


def _parse_adapter_yaml(path: Path) -> List[AdapterSpec]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid adapter YAML at {path}: {exc}") from exc

    if not isinstance(doc, dict):
        raise RuntimeError(
            f"Adapter {path} must have a top-level mapping of capability "
            f"names to operation specs."
        )

    specs: List[AdapterSpec] = []
    for capability, body in doc.items():
        if not isinstance(body, dict) or "operations" not in body:
            raise RuntimeError(
                f"Adapter {path} capability {capability!r} missing 'operations' key."
            )
        operations = body["operations"]
        if not isinstance(operations, dict):
            raise RuntimeError(
                f"Adapter {path} capability {capability!r}: 'operations' must be a mapping."
            )
        specs.append(
            AdapterSpec(
                capability=str(capability),
                operations={str(k): v for k, v in operations.items()},
                source_path=path,
            )
        )
    return specs


def load_adapters(skills_dirs: List[Path]) -> Dict[str, AdapterSpec]:
    """Scan ``<skills_dir>/adapters/*.yaml`` across all given skills dirs."""
    found: Dict[str, AdapterSpec] = {}
    for skills_dir in skills_dirs:
        adapters_dir = Path(skills_dir) / "adapters"
        if not adapters_dir.is_dir():
            continue
        for entry in sorted(adapters_dir.glob("*.yaml")):
            for spec in _parse_adapter_yaml(entry):
                existing = found.get(spec.capability)
                if existing is not None:
                    raise SkillifyCollisionError(
                        f"Adapter collision for capability {spec.capability!r}: "
                        f"defined in both {existing.source_path} and {spec.source_path}."
                    )
                found[spec.capability] = spec
    return found


# ---------------------------------------------------------------------------
# Skills-dir resolution + caching
# ---------------------------------------------------------------------------


def _resolve_skills_dirs() -> List[Path]:
    """Return the list of skills dirs to scan (core + external).

    Isolated for test monkeypatching.
    """
    from tools.skills_tool import SKILLS_DIR

    dirs: List[Path] = []
    if SKILLS_DIR.exists():
        dirs.append(Path(SKILLS_DIR))
    try:
        from agent.skill_utils import get_external_skills_dirs
        dirs.extend(get_external_skills_dirs())
    except Exception as exc:  # pragma: no cover
        logger.debug("get_external_skills_dirs failed: %s", exc)
    return dirs


_adapter_cache: Optional[Dict[str, AdapterSpec]] = None


def _get_adapters() -> Dict[str, AdapterSpec]:
    global _adapter_cache
    if _adapter_cache is None:
        _adapter_cache = load_adapters(_resolve_skills_dirs())
    return _adapter_cache


def _invalidate_adapter_cache() -> None:
    """Clear the adapter cache.  For tests + ``skillify refresh`` flows."""
    global _adapter_cache
    _adapter_cache = None


# ---------------------------------------------------------------------------
# Facade tool
# ---------------------------------------------------------------------------


SKILLIFIED_CALL_SCHEMA: Dict[str, Any] = {
    "name": "skillified_call",
    "description": (
        "Execute a skillified capability operation using a compact facade "
        "API. Use when a loaded skd_ skill documents the capability and "
        "operation contract. Parameters: capability (string), operation "
        "(string), params (object)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": "Capability name (e.g. 'browser').",
            },
            "operation": {
                "type": "string",
                "description": "Operation name within the capability.",
            },
            "params": {
                "type": "object",
                "description": "Operation parameters per the skd_ contract.",
            },
        },
        "required": ["capability", "operation", "params"],
    },
}


def _apply_param_transform(
    op_spec: Mapping[str, Any], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """Apply an optional 'rename' param map from the operation spec.

    Phase 1 supports pass-through (no transform) and ``rename`` only.
    Collapse transforms via Python adapters are Phase 2.
    """
    rename = op_spec.get("rename") if isinstance(op_spec, Mapping) else None
    if not rename:
        return dict(params)
    result: Dict[str, Any] = {}
    for k, v in params.items():
        result[rename.get(k, k)] = v
    return result


def skillified_call_handler(args: Dict[str, Any], **kwargs: Any) -> str:
    """Entry point for the facade tool."""
    capability = args.get("capability")
    operation = args.get("operation")
    params = args.get("params")

    if not isinstance(capability, str) or not capability:
        return json.dumps({"error": "capability must be a non-empty string"})
    if not isinstance(operation, str) or not operation:
        return json.dumps({"error": "operation must be a non-empty string"})
    if not isinstance(params, dict):
        return json.dumps({"error": "params must be an object"})

    try:
        adapters = _get_adapters()
    except SkillifyCollisionError as exc:
        return json.dumps({"error": f"Adapter configuration error: {exc}"})

    spec = adapters.get(capability)
    if spec is None:
        available = sorted(adapters.keys())
        return json.dumps(
            {
                "error": f"Unknown capability: {capability!r}",
                "available_capabilities": available,
            }
        )

    op_spec = spec.operations.get(operation)
    if op_spec is None:
        available_ops = sorted(spec.operations.keys())
        return json.dumps(
            {
                "error": (
                    f"Unknown operation {operation!r} for capability "
                    f"{capability!r}. Available: {', '.join(available_ops)}."
                ),
                "available_operations": available_ops,
            }
        )

    if not isinstance(op_spec, Mapping) or "target" not in op_spec:
        return json.dumps(
            {"error": f"Adapter for {capability}.{operation} missing 'target'."}
        )

    target_name = str(op_spec["target"])
    target_args = _apply_param_transform(op_spec, params)

    # Import lazily to avoid a tools-package/model_tools import cycle at load time.
    from model_tools import handle_function_call

    return handle_function_call(
        target_name,
        target_args,
        task_id=kwargs.get("task_id"),
        tool_call_id=kwargs.get("tool_call_id"),
        session_id=kwargs.get("session_id"),
        user_task=kwargs.get("user_task"),
        enabled_tools=kwargs.get("enabled_tools"),
        skip_pre_tool_call_hook=False,
    )


registry.register(
    name="skillified_call",
    toolset="skillified",
    schema=SKILLIFIED_CALL_SCHEMA,
    handler=skillified_call_handler,
    emoji="🧩",
)
