"""Integration test: skd_* skills hide registered source tools from emission."""

from pathlib import Path

import pytest


SKD_TEMPLATE = """---
name: {name}
description: Lazy capability.
metadata:
  hermes:
    skillified_from_toolset: {toolset}
---

body.
"""


@pytest.fixture
def fake_browserlike_tools():
    from tools.registry import registry

    def _noop(args, **kw):
        return "{}"

    names = ["bx_navigate", "bx_click"]
    for n in names:
        registry.register(
            name=n,
            toolset="bx",
            schema={"name": n, "parameters": {"type": "object", "properties": {}}},
            handler=_noop,
        )
    yield names
    for n in names:
        registry.deregister(n)


def test_skd_skill_hides_its_toolset_from_tool_definitions(
    tmp_path, monkeypatch, fake_browserlike_tools
):
    import tools.skillified_tool as sk
    import model_tools

    # Arrange: fake skd_bx skill in a tmp skills dir
    skd = tmp_path / "skd_bx"
    skd.mkdir()
    (skd / "SKILL.md").write_text(
        SKD_TEMPLATE.format(name="skd_bx", toolset="bx"), encoding="utf-8"
    )

    # Baseline: before skd_bx is visible, patch to empty skills dir so the
    # hide set is empty and the bx tools are present.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [empty])

    defs = model_tools.get_tool_definitions(
        enabled_toolsets=["bx"], quiet_mode=True
    )
    names_before = {d["function"]["name"] for d in defs}
    assert names_before == {"bx_navigate", "bx_click"}

    # Act: now point at the dir containing skd_bx — tools should be filtered out.
    monkeypatch.setattr(sk, "_resolve_skills_dirs", lambda: [tmp_path])

    defs_after = model_tools.get_tool_definitions(
        enabled_toolsets=["bx"], quiet_mode=True
    )
    names_after = {d["function"]["name"] for d in defs_after}
    assert names_after == set(), (
        f"Expected bx tools to be hidden, got {names_after}"
    )


def test_collision_surfaces_loudly(tmp_path, monkeypatch, fake_browserlike_tools):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    for base in (dir_a, dir_b):
        skd = base / "skd_bx"
        skd.mkdir()
        (skd / "SKILL.md").write_text(
            SKD_TEMPLATE.format(name="skd_bx", toolset="bx"), encoding="utf-8"
        )

    import tools.skillified_tool as sk
    monkeypatch.setattr(
        sk, "_resolve_skills_dirs", lambda: [dir_a, dir_b]
    )

    import model_tools

    with pytest.raises(sk.SkillifyCollisionError):
        model_tools.get_tool_definitions(enabled_toolsets=["bx"], quiet_mode=True)


def test_skillified_call_visible_in_default_toolset():
    """skillified_call belongs to toolset 'skillified' and must be available."""
    import tools.skillified_tool  # noqa: F401
    import model_tools

    defs = model_tools.get_tool_definitions(
        enabled_toolsets=["skillified"], quiet_mode=True
    )
    names = {d["function"]["name"] for d in defs}
    assert "skillified_call" in names
