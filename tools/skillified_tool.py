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
import re
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


# ---------------------------------------------------------------------------
# Hide set (init-time scan of skd_* frontmatter)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _read_frontmatter(skill_md: Path) -> Optional[Dict[str, Any]]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _skillified_toolset(frontmatter: Dict[str, Any]) -> Optional[str]:
    hermes = (frontmatter.get("metadata") or {}).get("hermes") or {}
    if not isinstance(hermes, dict):
        return None
    toolset = hermes.get("skillified_from_toolset")
    if not isinstance(toolset, str) or not toolset.strip():
        return None
    return toolset.strip()


def _scan_skd_skills(skills_dirs: List[Path]) -> Dict[str, List[Path]]:
    """Return ``{toolset: [skill_md_path, ...]}`` for every skd_* skill found.

    Only directories whose name starts with ``skd_`` and which contain a
    ``SKILL.md`` with a valid ``skillified_from_toolset`` frontmatter entry
    are considered.
    """
    by_toolset: Dict[str, List[Path]] = {}
    for skills_dir in skills_dirs:
        base = Path(skills_dir)
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("skd_"):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm = _read_frontmatter(skill_md)
            if fm is None:
                continue
            toolset = _skillified_toolset(fm)
            if toolset is None:
                continue
            by_toolset.setdefault(toolset, []).append(skill_md)
    return by_toolset


def _tool_names_in_toolset(toolset: str) -> List[str]:
    """Return tool names that belong to ``toolset``.

    Returns the union of toolset-config-defined tools (via model_tools.resolve_toolset,
    which covers YAML-defined toolsets like browser) and Python-registry-registered
    tools (which covers plugin and test toolsets).
    """
    names: set[str] = set()
    try:
        from model_tools import resolve_toolset
        names.update(resolve_toolset(toolset))
    except Exception:
        pass
    tool_to_toolset = registry.get_tool_to_toolset_map()
    names.update(name for name, ts in tool_to_toolset.items() if ts == toolset)
    return list(names)


def build_skillified_hide_set() -> set[str]:
    """Compute the set of source tool names to hide from ``self.tools``.

    Scans all discoverable skills dirs for ``skd_*`` skills, reads their
    ``skillified_from_toolset`` frontmatter, and returns the union of tool
    names registered in those toolsets.

    Raises :class:`SkillifyCollisionError` if two or more ``skd_*`` skills
    declare the same ``skillified_from_toolset`` value.
    """
    dirs = _resolve_skills_dirs()
    by_toolset = _scan_skd_skills(dirs)

    hide: set[str] = set()
    for toolset, sources in by_toolset.items():
        if len(sources) > 1:
            paths = ", ".join(str(p) for p in sources)
            # Extract skill directory names for clarity.
            skill_names = ", ".join(p.parent.name for p in sources)
            raise SkillifyCollisionError(
                f"Multiple skd_* skills declare "
                f"skillified_from_toolset={toolset!r}: {skill_names} "
                f"(paths: {paths}). Each toolset may be skillified by at "
                f"most one skd_* skill."
            )
        hide.update(_tool_names_in_toolset(toolset))
    return hide


registry.register(
    name="skillified_call",
    toolset="skillified",
    schema=SKILLIFIED_CALL_SCHEMA,
    handler=skillified_call_handler,
    emoji="🧩",
)
