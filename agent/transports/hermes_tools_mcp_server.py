"""Hermes-tools-as-MCP server for the codex_app_server runtime.

When the user runs `openai/*` turns through the codex app-server, codex
owns the loop and builds its own tool list. By default, that means
Hermes' richer tool surface — web search, browser automation,
delegate_task subagents, vision analysis, persistent memory, skills,
cross-session search, image generation, TTS — is unreachable.

This module exposes the configured Hermes CLI toolsets, after removing tools
that are unsafe, redundant, or stateful in a Codex-owned loop, to the
spawned codex subprocess via stdio MCP. Codex registers it as a normal
MCP server (per `~/.codex/config.toml [mcp_servers.hermes-tools]`) and
the user gets full Hermes capability inside a Codex turn.

Scope (what we expose):
  - web_search, web_extract              — Firecrawl, no codex equivalent
  - browser_navigate / _click / _type /  — Camofox/Browserbase automation
    _snapshot / _scroll / _back / _press /
    _get_images / _console / _vision
  - vision_analyze                       — image inspection by vision model
  - image_generate                       — image generation
  - skills_list2 / skill_view2 /         — Enumerait-backed Hermes skills
    skill_manage2
  - session_search                       — stateless transcript recall
  - text_to_speech                       — TTS
  - kanban_* (complete/block/comment/    — kanban worker + orchestrator
    heartbeat/show/list/create/            handoff (stateless: read env var,
    unblock/link)                          write ~/.hermes/kanban.db)

What we DO NOT expose:
  - terminal / shell                     — codex's own shell tool
  - read_file / write_file / patch       — codex's apply_patch + shell
  - search_files / process               — codex's shell
  - clarify                              — codex's own UX
  - raw mcp_* tools                       — configured natively by Codex
  - delegate_task / memory / todo        — `_AGENT_LOOP_TOOLS` in Hermes
                                           (model_tools.py). They require
                                           the running AIAgent context to
                                           dispatch (mid-loop state), so a
                                           stateless MCP callback can't
                                           drive them. See the inline
                                           comment on EXPOSED_TOOLS below.

Run with: python -m agent.transports.hermes_tools_mcp_server
Spawned by: CodexAppServerSession.ensure_started() when the runtime is
            active and config opts in.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
from typing import Any, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# JSON Schema type -> Python type mapping for signature generation
_JSON_TO_PY = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _signature_from_schema(schema: dict | None) -> tuple[inspect.Signature, dict[str, type]]:
    """Build a Python function signature and annotations from a JSON schema.

    Args:
        schema: JSON Schema dict with "properties" and "required" keys.

    Returns:
        (signature, annotations_dict) where signature has KEYWORD_ONLY params
        and annotations maps param names to Python types.
    """
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    params, annots = [], {}

    for pname, pspec in props.items():
        if pname.startswith("_"):
            continue
        py = _JSON_TO_PY.get((pspec or {}).get("type"), Any)
        ann, default = (
            (py, inspect.Parameter.empty)
            if pname in required
            else (Optional[py], None)
        )
        annots[pname] = ann
        params.append(
            inspect.Parameter(
                pname, inspect.Parameter.KEYWORD_ONLY, annotation=ann, default=default
            )
        )

    return inspect.Signature(params, return_annotation=str), annots


# Tools we expose. Each name MUST match a registered Hermes tool that
# `model_tools.handle_function_call()` can dispatch.
#
# What we deliberately DO NOT expose:
#   - terminal / shell / read_file / write_file / patch / search_files /
#     process — codex's built-ins cover these and approval routes through
#     codex's own UI.
#   - delegate_task / memory / todo — these are
#     `_AGENT_LOOP_TOOLS` in Hermes (model_tools.py:493). They require
#     the running AIAgent context to dispatch (mid-loop state), so a
#     stateless MCP callback can't drive them. Hermes' default runtime
#     keeps these working; the codex_app_server runtime cannot.
EXPOSED_TOOLS: tuple[str, ...] = (
    "web_search",
    "web_extract",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_snapshot",
    "browser_scroll",
    "browser_back",
    "browser_get_images",
    "browser_console",
    "browser_vision",
    "vision_analyze",
    "image_generate",
    "skills_list2",
    "skill_view2",
    "skill_manage2",
    "session_search",
    "text_to_speech",
    # Kanban worker handoff tools — gated on HERMES_KANBAN_TASK env var
    # (set by the kanban dispatcher when spawning a worker). Without these
    # in the callback, a worker spawned with openai_runtime=codex_app_server
    # could do the work but couldn't report completion back to the kernel,
    # making it hang until timeout. Stateless dispatch — they just read
    # the env var and write to ~/.hermes/kanban.db.
    "kanban_complete",
    "kanban_block",
    "kanban_request_review",
    "kanban_request_changes",
    "kanban_comment",
    "kanban_heartbeat",
    "kanban_show",
    "kanban_list",
    # NOTE: kanban_create / kanban_unblock / kanban_link are orchestrator-
    # only — the kanban tool gates them on HERMES_KANBAN_TASK being unset.
    # They're exposed here for orchestrator agents running on the codex
    # runtime that need to dispatch new tasks.
    "kanban_create",
    "kanban_unblock",
    "kanban_link",
)

_CODEX_NATIVE_TOOLS = {
    "terminal", "shell", "read_file", "write_file", "patch",
    "search_files", "process",
}
_STATEFUL_AGENT_LOOP_TOOLS = {"delegate_task", "memory", "todo"}
_BRIDGE_DENY_TOOLS = _CODEX_NATIVE_TOOLS | _STATEFUL_AGENT_LOOP_TOOLS | {
    "clarify",
    "skills_list", "skill_view", "skill_manage",
    "tool_search", "tool_describe", "tool_call",
}


def _load_config() -> dict:
    from hermes_cli.config import load_config

    return load_config()


def _configured_mcp_toolsets(config: dict[str, Any]) -> set[str]:
    from hermes_cli.tools_config import enabled_mcp_server_names

    return set(enabled_mcp_server_names(config))


def _remove_mcp_toolsets(toolsets: set[str], config: dict[str, Any]) -> list[str]:
    cleaned = set(toolsets) - _configured_mcp_toolsets(config)
    cleaned.discard("no_mcp")
    return sorted(cleaned)


def _resolve_bridge_toolsets() -> list[str]:
    from hermes_cli.tools_config import _get_platform_tools

    config = _load_config()
    platform = os.getenv("HERMES_CODEX_BRIDGE_PLATFORM", "cli").strip() or "cli"
    return _remove_mcp_toolsets(
        set(_get_platform_tools(config, platform, include_default_mcp_servers=False)),
        config,
    )


def _resolve_bridge_dispatch_toolsets(exposed_toolsets: list[str]) -> list[str]:
    """Keep configured MCP dependencies registered but invisible to Codex."""
    from hermes_cli.tools_config import _get_platform_tools

    config = _load_config()
    platform = os.getenv("HERMES_CODEX_BRIDGE_PLATFORM", "cli").strip() or "cli"
    internal = _get_platform_tools(config, platform, include_default_mcp_servers=True)
    combined = set(exposed_toolsets) | set(internal)
    combined.discard("no_mcp")
    return sorted(combined)


def _bridge_tool_allowed(name: str) -> bool:
    return bool(name) and name not in _BRIDGE_DENY_TOOLS and not name.startswith("mcp_")


def _bridge_tool_exposed_by_toolset(name: str, exposed_toolsets: list[str]) -> bool:
    if name in EXPOSED_TOOLS:
        return True
    entry = registry.get_entry(name)
    return entry is None or entry.toolset in set(exposed_toolsets)


def _bridge_spec_allowed(name: str, exposed_toolsets: list[str]) -> bool:
    return _bridge_tool_allowed(name) and _bridge_tool_exposed_by_toolset(name, exposed_toolsets)


def _load_bridge_runtime_env() -> list[Any]:
    from pathlib import Path
    from hermes_cli.env_loader import load_hermes_dotenv

    return load_hermes_dotenv(
        hermes_home=os.getenv("HERMES_HOME"),
        project_env=Path(__file__).resolve().parents[2] / ".env",
    )


def _register_bridge_mcp_dependencies(enabled_toolsets: list[str]) -> None:
    if not (set(enabled_toolsets) & _configured_mcp_toolsets(_load_config())):
        return
    try:
        from tools.mcp_tool import discover_mcp_tools

        discover_mcp_tools()
    except Exception as exc:
        logger.warning("MCP dependency discovery for Codex bridge failed: %s", exc)


def _build_bridge_tool_specs(get_tool_definitions) -> tuple[dict[str, dict[str, Any]], list[str]]:
    _load_bridge_runtime_env()
    exposed_toolsets = _resolve_bridge_toolsets()
    dispatch_toolsets = _resolve_bridge_dispatch_toolsets(exposed_toolsets)
    _register_bridge_mcp_dependencies(dispatch_toolsets)
    definitions = get_tool_definitions(
        enabled_toolsets=dispatch_toolsets,
        quiet_mode=True,
        skip_tool_search_assembly=True,
    ) or []
    specs = {}
    for definition in definitions:
        if not isinstance(definition, dict) or definition.get("type") != "function":
            continue
        spec = definition.get("function")
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if isinstance(name, str) and _bridge_spec_allowed(name, exposed_toolsets):
            specs[name] = spec
    return specs, dispatch_toolsets


def _normalize_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    if set(args) == {"kwargs"} and isinstance(args.get("kwargs"), dict):
        args = args["kwargs"]
    return {key: value for key, value in args.items() if value is not None}


def _dispatch_session_search(args: dict[str, Any]) -> str:
    try:
        from tools.session_search_tool import session_search

        return session_search(
            query=args.get("query", ""),
            role_filter=args.get("role_filter"),
            limit=args.get("limit", 3),
            session_id=args.get("session_id"),
            around_message_id=args.get("around_message_id"),
            window=args.get("window", 5),
            sort=args.get("sort"),
        )
    except Exception as exc:
        logger.exception("session_search raised")
        return json.dumps({"error": str(exc), "tool": "session_search"})


def _dispatch_tool_call(
    tool_name: str,
    args: dict[str, Any],
    handle_function_call,
    *,
    enabled_toolsets: Optional[list[str]] = None,
) -> str:
    normalized = _normalize_tool_args(args or {})
    if tool_name == "session_search":
        return _dispatch_session_search(normalized)
    if enabled_toolsets is None:
        return handle_function_call(tool_name, normalized)
    return handle_function_call(tool_name, normalized, enabled_toolsets=enabled_toolsets)


def _build_server() -> Any:
    """Create the MCP server with Hermes tools attached. Lazy imports
    so the module can be imported without the mcp package installed
    (we degrade to a clear error only when actually run)."""
    try:
        # mcp 2.0 removed `mcp.server.fastmcp`; `mcp.server.MCPServer` is the
        # same decorator/add_tool surface under the new name.
        from mcp.server import MCPServer
    except ImportError as exc:  # pragma: no cover - install hint
        raise ImportError(
            f"hermes-tools MCP server requires the 'mcp' package: {exc}"
        ) from exc

    # Discover Hermes tools so dispatch works.
    from model_tools import (
        get_tool_definitions,
        handle_function_call,
    )

    mcp = MCPServer(
        "hermes-tools",
        instructions=(
            "Hermes Agent's tool surface, exposed for use inside a Codex "
            "session. Use these for capabilities Codex's built-in toolset "
            "doesn't cover: web search/extract, browser automation, "
            "subagent delegation, vision, image generation, persistent "
            "memory, skills, and cross-session search."
        ),
    )

    # Resolve the configured profile surface once. Hidden MCP toolsets remain
    # in dispatch scope for dependencies such as Enumerait but are filtered
    # from the Codex-visible schema.
    exposed_defs, enabled_toolsets = _build_bridge_tool_specs(get_tool_definitions)

    exposed_count = 0

    for name, spec in exposed_defs.items():
        description = spec.get("description") or f"Hermes {name} tool"
        params_schema = spec.get("parameters") or {"type": "object", "properties": {}}

        # The SDK wants a Python callable and derives the input schema from
        # its signature — there is no inputSchema parameter on either the
        # decorator or add_tool(). So build a closure that takes the arguments
        # dict, dispatches via handle_function_call, returns the result
        # string, and carries a __signature__ synthesized from the Hermes
        # JSON Schema (see _signature_from_schema) for the SDK to read.
        def _make_handler(tool_name: str, schema: dict | None):
            sig, annots = _signature_from_schema(schema)

            def _dispatch(**kwargs: Any) -> str:
                try:
                    return _dispatch_tool_call(
                        tool_name,
                        kwargs or {},
                        handle_function_call,
                        enabled_toolsets=enabled_toolsets,
                    )
                except Exception as exc:
                    logger.exception("tool %s raised", tool_name)
                    return json.dumps({"error": str(exc), "tool": tool_name})

            _dispatch.__name__ = tool_name
            _dispatch.__doc__ = description
            _dispatch.__signature__ = sig
            _dispatch.__annotations__ = {**annots, "return": str}
            return _dispatch

        try:
            mcp.add_tool(
                _make_handler(name, params_schema),
                name=name,
                description=description,
            )
        except TypeError:
            # Older mcp SDK signature — fall back to decorator-style. The
            # synthesized __signature__ on the handler still drives schema
            # generation there.
            handler = _make_handler(name, params_schema)
            handler = mcp.tool(name=name, description=description)(handler)

        exposed_count += 1

    logger.info(
        "hermes-tools MCP server registered %d tools from %d configured toolsets",
        exposed_count,
        len(enabled_toolsets),
    )
    return mcp


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for `python -m agent.transports.hermes_tools_mcp_server`."""
    argv = argv or sys.argv[1:]
    verbose = "--verbose" in argv or "-v" in argv

    log_level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        stream=sys.stderr,  # MCP uses stdio for protocol — logs MUST go to stderr
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Quiet mode: keep Hermes' own banners off stdout (which is the MCP wire).
    os.environ.setdefault("HERMES_QUIET", "1")
    os.environ.setdefault("HERMES_REDACT_SECRETS", "true")

    try:
        server = _build_server()
    except ImportError as exc:
        sys.stderr.write(f"hermes-tools MCP server cannot start: {exc}\n")
        return 2

    # MCPServer.run() defaults to stdio transport, which is what codex
    # spawns us on.
    try:
        server.run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("hermes-tools MCP server crashed")
        sys.stderr.write(f"hermes-tools MCP server error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
