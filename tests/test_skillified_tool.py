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


# ---------- Adapter loading ----------


def _write_adapter(dir_path, name: str, content: str):
    adapters = dir_path / "adapters"
    adapters.mkdir(parents=True, exist_ok=True)
    (adapters / name).write_text(content, encoding="utf-8")


def test_load_adapters_from_single_dir(tmp_path):
    _write_adapter(
        tmp_path,
        "browser.yaml",
        """
browser:
  operations:
    navigate: {target: browser_navigate}
    click:    {target: browser_click}
""",
    )

    from tools.skillified_tool import load_adapters

    adapters = load_adapters([tmp_path])
    assert set(adapters.keys()) == {"browser"}
    assert adapters["browser"].operations["navigate"] == {
        "target": "browser_navigate",
    }
    assert adapters["browser"].operations["click"] == {
        "target": "browser_click",
    }


def test_load_adapters_missing_adapters_dir_is_ok(tmp_path):
    from tools.skillified_tool import load_adapters

    adapters = load_adapters([tmp_path])
    assert adapters == {}


def test_load_adapters_collision_across_dirs_raises(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_adapter(dir_a, "browser.yaml", "browser:\n  operations: {navigate: {target: browser_navigate}}\n")
    _write_adapter(dir_b, "browser.yaml", "browser:\n  operations: {click: {target: browser_click}}\n")

    from tools.skillified_tool import SkillifyCollisionError, load_adapters

    with pytest.raises(SkillifyCollisionError) as exc_info:
        load_adapters([dir_a, dir_b])

    msg = str(exc_info.value)
    assert "browser" in msg
    assert str(dir_a) in msg
    assert str(dir_b) in msg
