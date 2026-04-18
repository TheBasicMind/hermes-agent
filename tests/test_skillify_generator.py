"""Tests for the skillify generator CLI."""

import json
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fake_bx_tools():
    from tools.registry import registry

    def _noop(args, **kw):
        return "{}"

    entries = [
        (
            "bx_navigate",
            {
                "name": "bx_navigate",
                "description": "Navigate to a URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Target URL."},
                    },
                    "required": ["url"],
                },
            },
        ),
        (
            "bx_click",
            {
                "name": "bx_click",
                "description": "Click an element.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "Element reference."},
                    },
                    "required": ["ref"],
                },
            },
        ),
    ]
    for name, schema in entries:
        registry.register(name=name, toolset="bx", schema=schema, handler=_noop)
    yield {name for name, _ in entries}
    for name, _ in entries:
        registry.deregister(name)


def test_generator_create_produces_expected_files(tmp_path, fake_bx_tools, monkeypatch):
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "skillify_generator",
        Path(__file__).resolve().parents[1] / "scripts" / "skillify" / "skillify.py",
    )
    generator = _ilu.module_from_spec(_spec)
    sys.modules["skillify_generator"] = generator
    _spec.loader.exec_module(generator)

    def _fake_loader():
        from tools.registry import registry
        entries = registry._snapshot_entries()
        return {
            e.name: {"toolset": e.toolset, "schema": dict(e.schema)}
            for e in entries
        }

    monkeypatch.setattr(generator, "_load_registered_tools", _fake_loader)

    cfg_path = tmp_path / "sk.yaml"
    out_dir = tmp_path / "out"
    cfg_path.write_text(
        f"""
output_dir: {out_dir}
capabilities:
  bx:
    source_toolset: bx
    facade_name: skd_bx
    operation_mode: rename
    description: Browser-lite capability for testing.
    rename:
      bx_navigate: navigate
      bx_click: click
""",
        encoding="utf-8",
    )

    rc = generator.main(["--config", str(cfg_path), "create", "bx"])
    assert rc == 0

    skill_md = out_dir / "skd_bx" / "SKILL.md"
    refs = out_dir / "skd_bx" / "references" / "source-tools.json"
    adapter = out_dir / "adapters" / "bx.yaml"

    assert skill_md.is_file()
    assert refs.is_file()
    assert adapter.is_file()

    text = skill_md.read_text(encoding="utf-8")
    assert "skillified_from_toolset: bx" in text
    assert "### `navigate`" in text
    assert "### `click`" in text
    assert "### `bx_navigate`" not in text

    adapter_doc = yaml.safe_load(adapter.read_text(encoding="utf-8"))
    assert adapter_doc == {
        "bx": {
            "operations": {
                "navigate": {"target": "bx_navigate"},
                "click": {"target": "bx_click"},
            }
        }
    }

    refs_doc = json.loads(refs.read_text(encoding="utf-8"))
    refs_names = {entry["name"] for entry in refs_doc}
    assert refs_names == {"bx_navigate", "bx_click"}


def test_generator_errors_on_empty_toolset(tmp_path, monkeypatch):
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "skillify_generator2",
        Path(__file__).resolve().parents[1] / "scripts" / "skillify" / "skillify.py",
    )
    generator = _ilu.module_from_spec(_spec)
    sys.modules["skillify_generator2"] = generator
    _spec.loader.exec_module(generator)

    def _empty_loader():
        return {}

    monkeypatch.setattr(generator, "_load_registered_tools", _empty_loader)

    cfg_path = tmp_path / "sk.yaml"
    cfg_path.write_text(
        f"""
output_dir: {tmp_path}/out
capabilities:
  bx:
    source_toolset: bx
    facade_name: skd_bx
    operation_mode: pass-through
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="No registered tools"):
        generator.main(["--config", str(cfg_path), "create", "bx"])
