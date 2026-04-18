"""Skillified facade tool.

Provides a single stable LLM-visible tool ``skillified_call`` that dispatches
to hidden source tools via ``model_tools.handle_function_call``.  Rich API
contracts for each capability live in ``skd_*`` skill files loaded lazily via
the existing skills progressive-disclosure mechanism.

See the skillify design spec kept at the HermesProject repo root (not in this tree).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

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


registry.register(
    name="skillified_call",
    toolset="skillified",
    schema=SKILLIFIED_CALL_SCHEMA,
    handler=skillified_call_handler,
    emoji="🧩",
)
