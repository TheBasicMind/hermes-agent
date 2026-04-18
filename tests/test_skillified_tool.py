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


# ---------- Dispatch ----------


def test_dispatch_routes_to_target_via_handle_function_call(
    tmp_path, monkeypatch
):
    _write_adapter(
        tmp_path,
        "widget.yaml",
        """
widget:
  operations:
    ping: {target: widget_ping}
""",
    )

    # Patch the adapter resolver used by the handler.
    import tools.skillified_tool as sk

    monkeypatch.setattr(
        sk,
        "_resolve_skills_dirs",
        lambda: [tmp_path],
    )
    sk._invalidate_adapter_cache()

    # Patch handle_function_call to observe the call.
    calls = []

    def fake_hfc(function_name, function_args, **kwargs):
        calls.append((function_name, function_args, kwargs))
        return json.dumps({"ok": True, "name": function_name})

    monkeypatch.setattr("model_tools.handle_function_call", fake_hfc)

    result = registry.dispatch(
        "skillified_call",
        {
            "capability": "widget",
            "operation": "ping",
            "params": {"x": 1},
        },
    )
    assert len(calls) == 1
    target_name, target_args, target_kwargs = calls[0]
    assert target_name == "widget_ping"
    assert target_args == {"x": 1}
    assert target_kwargs.get("skip_pre_tool_call_hook") is False

    payload = json.loads(result)
    assert payload == {"ok": True, "name": "widget_ping"}


def test_dispatch_unknown_operation_returns_structured_error(
    tmp_path, monkeypatch
):
    _write_adapter(
        tmp_path,
        "widget.yaml",
        """
widget:
  operations:
    ping: {target: widget_ping}
""",
    )

    import tools.skillified_tool as sk

    monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [tmp_path])
    sk._invalidate_adapter_cache()

    result = registry.dispatch(
        "skillified_call",
        {
            "capability": "widget",
            "operation": "pong",
            "params": {},
        },
    )
    payload = json.loads(result)
    assert "error" in payload
    assert "unknown operation" in payload["error"].lower()
    assert "ping" in payload["error"]  # lists available ops


# ---------- Hide set ----------


SKD_FRONTMATTER_TEMPLATE = """---
name: {name}
description: Lazy capability.
metadata:
  hermes:
    skillified_from_toolset: {toolset}
---

body.
"""


def _write_skd_skill(skills_dir, name: str, toolset: str):
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        SKD_FRONTMATTER_TEMPLATE.format(name=name, toolset=toolset),
        encoding="utf-8",
    )


def test_build_hide_set_empty_when_no_skd_skills(tmp_path, monkeypatch):
    import tools.skillified_tool as sk
    monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [tmp_path])

    from tools.registry import registry
    # Provide a fake toolset resolver so no tool names are guessed.
    assert sk.build_skillified_hide_set() == set()


def test_build_hide_set_scans_frontmatter(tmp_path, monkeypatch):
    _write_skd_skill(tmp_path, "skd_browser", "browser")

    import tools.skillified_tool as sk
    from tools.registry import registry

    # Register two fake tools under toolset 'browser'.
    def _noop(args, **kw):
        return "{}"

    registry.register(
        name="browser_test_navigate",
        toolset="browser",
        schema={"name": "browser_test_navigate", "parameters": {"type": "object", "properties": {}}},
        handler=_noop,
    )
    registry.register(
        name="browser_test_click",
        toolset="browser",
        schema={"name": "browser_test_click", "parameters": {"type": "object", "properties": {}}},
        handler=_noop,
    )

    try:
        monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [tmp_path])
        hide_set = sk.build_skillified_hide_set()
        assert "browser_test_navigate" in hide_set
        assert "browser_test_click" in hide_set
    finally:
        registry.deregister("browser_test_navigate")
        registry.deregister("browser_test_click")


def test_build_hide_set_raises_on_duplicate_toolset(tmp_path, monkeypatch):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_skd_skill(dir_a, "skd_browser", "browser")
    _write_skd_skill(dir_b, "skd_browser_alt", "browser")

    import tools.skillified_tool as sk
    monkeypatch.setattr(
        sk, "_resolve_skills_dirs", lambda: [dir_a, dir_b]
    )

    with pytest.raises(sk.SkillifyCollisionError) as exc_info:
        sk.build_skillified_hide_set()

    msg = str(exc_info.value)
    assert "browser" in msg
    assert "skd_browser" in msg
    assert "skd_browser_alt" in msg


def test_build_hide_set_ignores_non_skd_skills(tmp_path, monkeypatch):
    # A non-skd skill with capability_domain frontmatter must NOT trigger hiding.
    skill_dir = tmp_path / "browser_helper"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: browser_helper\ndescription: helper\nmetadata:\n"
        "  hermes:\n    capability_domain: browser\n    skillified_from_toolset: browser\n---\nbody.\n",
        encoding="utf-8",
    )

    import tools.skillified_tool as sk
    monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [tmp_path])
    assert sk.build_skillified_hide_set() == set()
