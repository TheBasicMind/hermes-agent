"""Skillified facade tool.

Provides a single stable LLM-visible tool ``skillified_call`` that dispatches
to hidden source tools via ``model_tools.handle_function_call``.  Rich API
contracts for each capability live in ``skd_*`` skill files loaded lazily via
the existing skills progressive-disclosure mechanism.

See the skillify design spec kept at the HermesProject repo root (not in this tree).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


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


def skillified_call_handler(args: Dict[str, Any], **_kw: Any) -> str:
    """Entry point for the facade tool.  Stub; dispatch wired in Task 5."""
    capability = args.get("capability")
    operation = args.get("operation")
    params = args.get("params")

    if not isinstance(capability, str) or not capability:
        return tool_error("capability must be a non-empty string")
    if not isinstance(operation, str) or not operation:
        return tool_error("operation must be a non-empty string")
    if not isinstance(params, dict):
        return tool_error("params must be an object")

    return tool_error(f"Unknown capability: {capability!r}")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class SkillifyCollisionError(RuntimeError):
    """Raised when two discoverable sources define the same capability."""


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
                f"Adapter {path} capability {capability!r} missing "
                f"'operations' key."
            )
        operations = body["operations"]
        if not isinstance(operations, dict):
            raise RuntimeError(
                f"Adapter {path} capability {capability!r}: 'operations' "
                f"must be a mapping."
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
    """Scan ``<skills_dir>/adapters/*.yaml`` across all given skills dirs.

    Returns a mapping ``{capability: AdapterSpec}``.  Raises
    :class:`SkillifyCollisionError` if the same capability name appears in
    more than one source file across any of the provided directories.
    """
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
                        f"Adapter collision for capability "
                        f"{spec.capability!r}: defined in both "
                        f"{existing.source_path} and {spec.source_path}."
                    )
                found[spec.capability] = spec
    return found


registry.register(
    name="skillified_call",
    toolset="skillified",
    schema=SKILLIFIED_CALL_SCHEMA,
    handler=skillified_call_handler,
    emoji="🧩",
)
