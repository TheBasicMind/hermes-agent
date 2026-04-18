"""Tests for the skillified_call facade tool."""

import json

import pytest

from tools.registry import registry


def test_skillified_call_is_registered():
    """Importing the module must register the facade tool."""
    import tools.skillified_tool  # noqa: F401  (side-effect import)

    entry = registry.get_entry("skillified_call")
    assert entry is not None, "skillified_call not in registry"
    assert entry.toolset == "skillified"
    assert entry.schema["name"] == "skillified_call"
    required = set(entry.schema["parameters"]["required"])
    assert required == {"capability", "operation", "params"}


def test_skillified_call_unknown_capability_returns_error():
    """Calling with an unknown capability returns a structured error JSON."""
    import tools.skillified_tool  # noqa: F401

    result = registry.dispatch(
        "skillified_call",
        {"capability": "does_not_exist", "operation": "foo", "params": {}},
    )
    payload = json.loads(result)
    assert "error" in payload
    assert "unknown capability" in payload["error"].lower()
