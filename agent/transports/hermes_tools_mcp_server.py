"""Hermes-tools-as-MCP server for the codex_app_server runtime.

When the user runs `openai/*` turns through the codex app-server, codex
owns the loop and builds its own tool list. By default, that means
Hermes' richer tool surface — web search, browser automation,
delegate_task subagents, vision analysis, persistent memory, skills,
cross-session search, image generation, TTS — is unreachable.

This module exposes the same configured Hermes tool surface as the CLI
profile (Socrates), with bridge-inappropriate tools removed, to the
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
  - session_search                       — DB-backed transcript recall
  - text_to_speech                       — TTS
  - kanban_* (complete/block/comment/    — kanban worker + orchestrator
    heartbeat/show/list/create/            handoff (stateless: read env var,
    unblock/link)                          write ~/.hermes/kanban.db)

What we DO NOT expose:
  - terminal / shell                     — codex's own shell tool
  - read_file / write_file / patch       — codex's apply_patch + shell
  - search_files / process               — codex's shell
  - clarify                              — codex's own UX
  - MCP server toolsets                  — codex can configure these
                                           independently as native MCP
  - delegate_task / memory / todo        — `_AGENT_LOOP_TOOLS` in Hermes
                                           (model_tools.py). They require
                                           the running AIAgent context to
                                           dispatch (mid-loop state), so a
                                           stateless MCP callback can't
                                           drive them.

Run with: python -m agent.transports.hermes_tools_mcp_server
Spawned by: CodexAppServerSession.ensure_started() when the runtime is
            active and config opts in.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Baseline tools that must remain eligible for this bridge. The live MCP
# surface is resolved from the configured CLI/Socrates toolsets by
# `_build_bridge_tool_specs()`, then filtered through the deny lists below.
# Keep this tuple for tests and as a compatibility guard documenting the
# original bridge contract.
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
#   - session_search is also listed in `_AGENT_LOOP_TOOLS`, but its
#     implementation is already a stateless SessionDB query. We expose it
#     via `_dispatch_tool_call()` instead of the generic dispatcher so the
#     bridge does not need a live AIAgent instance.
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
    "terminal",
    "shell",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "process",
}

_STATEFUL_AGENT_LOOP_TOOLS = {
    "delegate_task",
    "memory",
    "todo",
}

_BRIDGE_DENY_TOOLS = (
    _CODEX_NATIVE_TOOLS
    | _STATEFUL_AGENT_LOOP_TOOLS
    | {
        "clarify",
        "skills_list",
        "skill_view",
        "skill_manage",
        # The bridge exposes concrete Hermes tools directly. Do not nest
        # Hermes' progressive tool-search protocol inside Codex's MCP client.
        "tool_search",
        "tool_describe",
        "tool_call",
    }
)


def _configured_mcp_toolsets(config: dict[str, Any]) -> set[str]:
    """Return configured MCP server names so the bridge can exclude them."""
    mcp_servers = config.get("mcp_servers") or {}
    if not isinstance(mcp_servers, dict):
        return set()
    return {str(name) for name in mcp_servers}


def _remove_mcp_toolsets(
    toolsets: set[str],
    config: dict[str, Any],
) -> list[str]:
    """Drop MCP server toolsets, including explicit per-platform entries."""
    cleaned = set(toolsets)
    cleaned -= _configured_mcp_toolsets(config)
    cleaned.discard("no_mcp")
    return sorted(cleaned)


def _resolve_bridge_toolsets() -> list[str]:
    """Resolve CLI/Socrates toolsets for the Codex MCP bridge, minus MCP."""
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools

    platform = os.getenv("HERMES_CODEX_BRIDGE_PLATFORM", "cli").strip() or "cli"
    config = load_config()
    toolsets = _get_platform_tools(
        config,
        platform,
        include_default_mcp_servers=False,
    )
    return _remove_mcp_toolsets(set(toolsets), config)


def _resolve_bridge_dispatch_toolsets(exposed_toolsets: list[str]) -> list[str]:
    """Resolve internal dispatch toolsets, including hidden dependencies.

    Codex should not see nested MCP tools as callable bridge tools, but some
    exposed Hermes tools depend on MCP-backed registry entries. For example,
    skills_v2 calls mcp_enumerait_* internally. Include configured MCP server
    toolsets for registry/dispatch, then filter their tools out of the exposed
    schema.
    """
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools

    platform = os.getenv("HERMES_CODEX_BRIDGE_PLATFORM", "cli").strip() or "cli"
    config = load_config()
    toolsets = _get_platform_tools(
        config,
        platform,
        include_default_mcp_servers=True,
    )
    combined = set(exposed_toolsets) | set(toolsets)
    combined.discard("no_mcp")
    return sorted(combined)


def _bridge_tool_allowed(name: str) -> bool:
    """Return whether a registered tool is appropriate for this MCP bridge."""
    return bool(name) and name not in _BRIDGE_DENY_TOOLS and not name.startswith("mcp_")


def _bridge_tool_exposed_by_toolset(name: str, exposed_toolsets: list[str]) -> bool:
    """Return whether a tool belongs to a Codex-visible bridge toolset."""
    from tools.registry import registry

    if name in EXPOSED_TOOLS:
        return True
    entry = registry.get_entry(name)
    if entry is None:
        return True
    return entry.toolset in set(exposed_toolsets)


def _bridge_spec_allowed(name: str, exposed_toolsets: list[str]) -> bool:
    return _bridge_tool_allowed(name) and _bridge_tool_exposed_by_toolset(
        name,
        exposed_toolsets,
    )


def _load_bridge_runtime_env() -> list[Any]:
    """Load Hermes runtime .env before availability checks build schemas."""
    from pathlib import Path

    from hermes_cli.env_loader import load_hermes_dotenv

    project_env = Path(__file__).resolve().parents[2] / ".env"
    return load_hermes_dotenv(
        hermes_home=os.getenv("HERMES_HOME"),
        project_env=project_env,
    )


def _register_bridge_mcp_dependencies(enabled_toolsets: list[str]) -> None:
    """Register MCP tools needed by hidden bridge dependencies.

    ``model_tools`` no longer discovers MCP tools as an import side effect.
    The Codex bridge is its own startup surface, so it must initialize MCP
    servers before asking for schemas when the internal dispatch toolsets
    include MCP-backed toolsets.
    """
    from hermes_cli.config import load_config

    mcp_toolsets = set(enabled_toolsets) & _configured_mcp_toolsets(load_config())
    if not mcp_toolsets:
        return
    try:
        from tools.mcp_tool import discover_mcp_tools

        discover_mcp_tools()
    except Exception as exc:
        logger.warning("MCP dependency discovery for Codex bridge failed: %s", exc)


def _build_bridge_tool_specs(
    get_tool_definitions,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Build the Codex-facing Hermes tool schema map from configured toolsets."""
    _load_bridge_runtime_env()
    exposed_toolsets = _resolve_bridge_toolsets()
    enabled_toolsets = _resolve_bridge_dispatch_toolsets(exposed_toolsets)
    _register_bridge_mcp_dependencies(enabled_toolsets)
    tool_defs = get_tool_definitions(
        enabled_toolsets=enabled_toolsets,
        quiet_mode=True,
        skip_tool_search_assembly=True,
    ) or []

    specs: dict[str, dict[str, Any]] = {}
    for tool_def in tool_defs:
        if not isinstance(tool_def, dict) or tool_def.get("type") != "function":
            continue
        spec = tool_def.get("function")
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if not isinstance(name, str) or not _bridge_spec_allowed(name, exposed_toolsets):
            continue
        specs[name] = spec

    return specs, enabled_toolsets


def _normalize_tool_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Unwrap FastMCP's dynamic **kwargs envelope when present."""
    if set(kwargs.keys()) == {"kwargs"} and isinstance(kwargs.get("kwargs"), dict):
        return kwargs["kwargs"]
    return kwargs


def _dispatch_session_search(args: dict[str, Any]) -> str:
    """Run session_search without requiring a live AIAgent loop."""
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
    """Dispatch a Hermes tool call, including stateless bridge exceptions."""
    normalized_args = _normalize_tool_args(args or {})
    if tool_name == "session_search":
        return _dispatch_session_search(normalized_args)
    if enabled_toolsets is None:
        return handle_function_call(tool_name, normalized_args)
    return handle_function_call(
        tool_name,
        normalized_args,
        enabled_toolsets=enabled_toolsets,
    )


def _build_server() -> Any:
    """Create the FastMCP server with Hermes tools attached. Lazy imports
    so the module can be imported without the mcp package installed
    (we degrade to a clear error only when actually run)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - install hint
        raise ImportError(
            f"hermes-tools MCP server requires the 'mcp' package: {exc}"
        ) from exc

    # Discover Hermes tools so dispatch works.
    from model_tools import (
        get_tool_definitions,
        handle_function_call,
    )

    mcp = FastMCP(
        "hermes-tools",
        instructions=(
            "Hermes Agent's tool surface, exposed for use inside a Codex "
            "session. Use these for capabilities Codex's built-in toolset "
            "doesn't cover: web search/extract, browser automation, "
            "subagent delegation, vision, image generation, persistent "
            "memory, skills, and cross-session search."
        ),
    )

    # Pull authoritative Hermes tool schemas for the configured CLI/Socrates
    # toolsets, so MCP clients see the same parameter docs Hermes gives the
    # model. MCP server toolsets are excluded here because Codex can configure
    # MCP independently and should not see nested MCP-through-Hermes tools.
    exposed_defs, enabled_toolsets = _build_bridge_tool_specs(get_tool_definitions)

    exposed_count = 0

    for name, spec in exposed_defs.items():
        description = spec.get("description") or f"Hermes {name} tool"
        params_schema = spec.get("parameters") or {"type": "object", "properties": {}}

        # FastMCP wants a Python callable. Build a closure that takes the
        # arguments dict, dispatches via handle_function_call, and returns
        # the result string. We use add_tool() for full control over the
        # input schema (FastMCP's @tool() decorator inspects type hints,
        # which we can't get from a JSON schema at runtime).
        def _make_handler(tool_name: str):
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
            return _dispatch

        try:
            mcp.add_tool(
                _make_handler(name),
                name=name,
                description=description,
                # FastMCP accepts JSON schema directly via the
                # input_schema parameter on newer versions; older
                # versions use parameters_schema. Try both for compat.
            )
        except TypeError:
            # Older mcp SDK signature — fall back to decorator-style.
            handler = _make_handler(name)
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

    # FastMCP runs with stdio transport by default when launched as a
    # subprocess.
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
