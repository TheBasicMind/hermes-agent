#!/usr/bin/env python3
"""Skillify capability generator CLI.

Reads a capability config (YAML) and emits ``skd_<name>/SKILL.md`` +
``adapters/<capability>.yaml`` into a user-chosen external skills dir.

Usage:
    skillify create browser
    skillify refresh
    skillify --config /path/to/other.yaml create browser

The default config is ``skillify.config.yaml`` next to the Hermes agent root.
Copy ``scripts/skillify/config.example.yaml`` there to get started.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

import yaml


@dataclass(frozen=True)
class CapabilityConfig:
    name: str
    source_toolset: str
    facade_name: str
    operation_mode: str  # "pass-through" | "rename"
    description: str
    emoji: str
    load_on: List[str]
    rename: Dict[str, str]  # source_tool_name -> operation_name


@dataclass(frozen=True)
class SkillifyConfig:
    output_dir: Path
    capabilities: Dict[str, CapabilityConfig]


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p))).resolve()


def load_config(path: Path) -> SkillifyConfig:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError(f"{path} must be a YAML mapping at the root.")
    raw_out = doc.get("output_dir")
    if not isinstance(raw_out, str) or not raw_out.strip():
        raise RuntimeError(f"{path}: output_dir (string) is required.")
    caps_raw = doc.get("capabilities")
    if not isinstance(caps_raw, dict):
        raise RuntimeError(f"{path}: capabilities (mapping) is required.")

    caps: Dict[str, CapabilityConfig] = {}
    for name, body in caps_raw.items():
        if not isinstance(body, dict):
            raise RuntimeError(f"{path}: capabilities.{name} must be a mapping.")
        caps[name] = CapabilityConfig(
            name=str(name),
            source_toolset=str(body["source_toolset"]),
            facade_name=str(body.get("facade_name", f"skd_{name}")),
            operation_mode=str(body.get("operation_mode", "pass-through")),
            description=str(body.get("description", "")).strip(),
            emoji=str(body.get("emoji", "🔧")),
            load_on=[str(s) for s in (body.get("load_on") or [])],
            rename={str(k): str(v) for k, v in (body.get("rename") or {}).items()},
        )
    return SkillifyConfig(output_dir=_expand(raw_out), capabilities=caps)


def _ensure_hermes_path():
    """Add the Hermes root to ``sys.path`` so we can import the tool registry."""
    hermes_root = Path(__file__).resolve().parents[2]  # scripts/skillify/ -> hermes-agent/
    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))


def _load_registered_tools() -> Mapping[str, Mapping[str, Any]]:
    """Import every tool module (so registry.register calls fire) and snapshot."""
    _ensure_hermes_path()
    import tools  # noqa: F401

    tools_dir = Path(tools.__file__).parent
    for py in sorted(tools_dir.glob("*_tool.py")):
        module_name = f"tools.{py.stem}"
        try:
            __import__(module_name)
        except Exception as exc:  # pragma: no cover
            print(f"⚠️  skipping {module_name}: {exc}", file=sys.stderr)

    from tools.registry import registry
    entries = registry._snapshot_entries()  # type: ignore[attr-defined]
    return {
        e.name: {
            "toolset": e.toolset,
            "schema": dict(e.schema),
        }
        for e in entries
    }


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def _ops_for(cap: CapabilityConfig, tools: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve source tools in the toolset to emitted operations."""
    source_tools = [
        (name, meta) for name, meta in tools.items()
        if meta["toolset"] == cap.source_toolset
    ]
    if not source_tools:
        raise RuntimeError(
            f"No registered tools found for toolset {cap.source_toolset!r}. "
            "Is the toolset enabled in the Hermes environment you ran this from?"
        )
    ops: List[Dict[str, Any]] = []
    for tool_name, meta in sorted(source_tools):
        if cap.operation_mode == "rename":
            op_name = cap.rename.get(tool_name)
            if op_name is None:
                continue
        else:
            op_name = tool_name
        ops.append(
            {
                "operation_name": op_name,
                "source_tool": tool_name,
                "schema": meta["schema"],
            }
        )
    if not ops:
        raise RuntimeError(
            f"Capability {cap.name}: no operations resolved. Check the "
            f"'rename' map aligns with the tools registered under toolset "
            f"{cap.source_toolset!r}."
        )
    return ops


def _emit_skill_md(cap: CapabilityConfig, ops: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("---")
    lines.append(f"name: {cap.facade_name}")
    lines.append(f"description: {cap.description or cap.name + ' capability'}")
    lines.append("metadata:")
    lines.append("  hermes:")
    lines.append(f"    capability_domain: {cap.name}")
    lines.append(f"    skillified_from_toolset: {cap.source_toolset}")
    lines.append("    execution_tool: skillified_call")
    if cap.load_on:
        lines.append("    load_on:")
        for item in cap.load_on:
            lines.append(f"      - {item}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {cap.facade_name}")
    lines.append("")
    if cap.description:
        lines.append(cap.description)
        lines.append("")
    lines.append("Call operations through `skillified_call(capability, operation, params)`.")
    lines.append("")
    lines.append("## Operations")
    lines.append("")

    for op in ops:
        op_name = op["operation_name"]
        schema = op["schema"]
        params_node = schema.get("parameters", {}).get("properties", {}) or {}
        required = set(schema.get("parameters", {}).get("required", []) or [])
        lines.append(f"### `{op_name}`")
        lines.append("")
        desc = schema.get("description", "").strip()
        if desc:
            lines.append(desc)
            lines.append("")
        lines.append("```yaml")
        lines.append(f"operation: {op_name}")
        lines.append("params:")
        if not params_node:
            lines.append("  {}")
        for pname, pspec in sorted(params_node.items()):
            ptype = pspec.get("type", "any") if isinstance(pspec, dict) else "any"
            pdesc = (pspec.get("description", "") if isinstance(pspec, dict) else "").strip()
            is_req = pname in required
            lines.append(f"  {pname}:")
            lines.append(f"    type: {ptype}")
            lines.append(f"    required: {'true' if is_req else 'false'}")
            if pdesc:
                lines.append(f"    description: {pdesc!r}")
        lines.append("returns: string  # JSON-encoded result from the source handler")
        lines.append("```")
        lines.append("")
        lines.append("```python")
        example_params = {
            p: "<value>" for p in sorted(params_node.keys()) if p in required
        } or {"example": "<value>"}
        lines.append(
            "skillified_call("
            f"capability={cap.name!r}, "
            f"operation={op_name!r}, "
            f"params={json.dumps(example_params)})"
        )
        lines.append("```")
        lines.append("")

    return "\n".join(lines) + "\n"


def _emit_adapter_yaml(cap: CapabilityConfig, ops: List[Dict[str, Any]]) -> str:
    doc: Dict[str, Any] = {
        cap.name: {
            "operations": {
                op["operation_name"]: {"target": op["source_tool"]} for op in ops
            }
        }
    }
    return yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def do_create(cfg: SkillifyConfig, capability_name: str, tools: Mapping[str, Mapping[str, Any]]) -> None:
    cap = cfg.capabilities.get(capability_name)
    if cap is None:
        raise SystemExit(
            f"Unknown capability {capability_name!r}. Configured: "
            f"{', '.join(sorted(cfg.capabilities))}."
        )
    ops = _ops_for(cap, tools)

    skill_dir = cfg.output_dir / cap.facade_name
    skill_md = skill_dir / "SKILL.md"
    refs = skill_dir / "references" / "source-tools.json"
    adapter_path = cfg.output_dir / "adapters" / f"{cap.name}.yaml"

    _write(skill_md, _emit_skill_md(cap, ops))
    _write(refs, json.dumps(
        [{"name": op["source_tool"], "schema": op["schema"]} for op in ops],
        indent=2,
    ))
    _write(adapter_path, _emit_adapter_yaml(cap, ops))

    print(f"Wrote {skill_md}")
    print(f"Wrote {refs}")
    print(f"Wrote {adapter_path}")


def do_refresh(cfg: SkillifyConfig, tools: Mapping[str, Mapping[str, Any]]) -> None:
    for name in cfg.capabilities:
        do_create(cfg, name, tools)


def do_remove(cfg: SkillifyConfig, capability_name: str) -> None:
    cap = cfg.capabilities.get(capability_name)
    if cap is None:
        raise SystemExit(
            f"Unknown capability {capability_name!r}. Configured: "
            f"{', '.join(sorted(cfg.capabilities))}."
        )
    import shutil

    removed = []
    skill_dir = cfg.output_dir / cap.facade_name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        removed.append(str(skill_dir))

    adapter_path = cfg.output_dir / "adapters" / f"{cap.name}.yaml"
    if adapter_path.exists():
        adapter_path.unlink()
        removed.append(str(adapter_path))

    if removed:
        for r in removed:
            print(f"Removed {r}")
    else:
        print(f"Nothing to remove for capability {capability_name!r} (files not found).")


def do_list(cfg: SkillifyConfig) -> None:
    for name, cap in cfg.capabilities.items():
        active = (cfg.output_dir / cap.facade_name / "SKILL.md").exists()
        tick = "✓" if active else "✗"
        status = "skillified  " if active else "not skillified"
        short_desc = cap.description.split(".")[0].split("\n")[0].strip()
        print(f"  {tick} {status}  {name}  {cap.emoji} {short_desc}")


def _default_config_path() -> Path:
    """Return skillify.config.yaml next to the Hermes agent root."""
    return Path(__file__).resolve().parents[2] / "skillify.config.yaml"


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate skd_* skill files and adapter YAML for Hermes skillified capabilities.\n\n"
            "Examples:\n"
            "  skillify list                    # show all capabilities and status\n"
            "  skillify create browser          # generate/refresh skd_browser\n"
            "  skillify refresh                 # regenerate all capabilities\n"
            "  skillify remove browser          # delete skd_browser files\n"
            "  skillify --config my.yaml create browser"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to skillify config YAML (default: {_default_config_path()})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all configured capabilities and their status.")

    p_create = sub.add_parser("create", help="Create artifacts for one capability.")
    p_create.add_argument("capability", help="Capability name (key under 'capabilities').")

    sub.add_parser("refresh", help="Regenerate artifacts for every configured capability.")

    p_remove = sub.add_parser("remove", help="Delete generated files for one capability.")
    p_remove.add_argument("capability", help="Capability name (key under 'capabilities').")

    args = parser.parse_args(argv)

    config_path = args.config or _default_config_path()
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print(f"Copy scripts/skillify/config.example.yaml to {config_path} to get started.", file=sys.stderr)
        return 1
    cfg = load_config(config_path)

    if args.cmd == "list":
        do_list(cfg)
        return 0

    if args.cmd == "remove":
        do_remove(cfg, args.capability)
        return 0

    tools = _load_registered_tools()

    if args.cmd == "create":
        do_create(cfg, args.capability, tools)
    elif args.cmd == "refresh":
        do_refresh(cfg, tools)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
