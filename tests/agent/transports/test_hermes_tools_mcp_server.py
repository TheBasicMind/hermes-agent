"""Tests for the hermes-tools-as-MCP server module surface.

We don't run a live MCP session in unit tests — that requires the codex
subprocess + client + an event loop. These tests pin the static
contract: the module imports, the EXPOSED_TOOLS list is sane, and the
build helper assembles a server when the SDK is present.
"""

from __future__ import annotations

import inspect
from typing import get_args

from agent.transports.hermes_tools_mcp_server import (
    _signature_from_schema,
)


class TestSignatureFromSchema:
    """Test the JSON Schema -> Python signature conversion."""

    def test_simple_required_string_param(self):
        """A required string param becomes str with no default."""
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        sig, annots = _signature_from_schema(schema)

        assert len(sig.parameters) == 1
        param = sig.parameters["query"]
        assert param.name == "query"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert annots["query"] == str
        assert param.default is inspect.Parameter.empty



    def test_skip_private_params(self):
        """Params starting with '_' are excluded from the signature."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "_internal": {"type": "string"},
            },
            "required": ["query", "_internal"],
        }
        sig, annots = _signature_from_schema(schema)

        assert "_internal" not in sig.parameters
        assert "_internal" not in annots
        assert "query" in sig.parameters

    def test_all_json_types(self):
        """All JSON schema types map to correct Python types."""
        schema = {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "a": {"type": "array"},
                "o": {"type": "object"},
            },
            "required": ["s", "i", "n", "b", "a", "o"],
        }
        sig, annots = _signature_from_schema(schema)

        assert annots["s"] == str
        assert annots["i"] == int
        assert annots["n"] == float
        assert annots["b"] == bool
        assert annots["a"] == list
        assert annots["o"] == dict








class TestModuleSurface:
    def test_module_imports_clean(self):
        from agent.transports import hermes_tools_mcp_server as m
        assert callable(m.main)
        assert callable(m._build_server)
        assert isinstance(m.EXPOSED_TOOLS, tuple)
        assert len(m.EXPOSED_TOOLS) > 0

    def test_exposed_tools_are_safe_subset(self):
        """We MUST NOT expose tools codex already has, because codex'
        own builtins are better-integrated with its sandbox + approvals.
        Specifically: no terminal/shell, no read_file/write_file, no
        patch — those are codex's built-in tools."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        forbidden = {
            "terminal", "shell", "read_file", "write_file", "patch",
            "search_files", "process",
        }
        leaked = forbidden & set(EXPOSED_TOOLS)
        assert not leaked, (
            f"these tools must NOT be exposed via the codex callback "
            f"because codex has built-in equivalents: {leaked}"
        )

    def test_configured_bridge_surface_filters_unsafe_and_nested_tools(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        monkeypatch.setattr(m, "_load_bridge_runtime_env", lambda: [])
        monkeypatch.setattr(m, "_resolve_bridge_toolsets", lambda: ["skills_v2", "custom"])
        monkeypatch.setattr(
            m,
            "_resolve_bridge_dispatch_toolsets",
            lambda exposed: [*exposed, "enumerait"],
        )
        monkeypatch.setattr(m, "_register_bridge_mcp_dependencies", lambda enabled: None)

        def definitions(**kwargs):
            definitions.kwargs = kwargs
            names = (
                "skill_view2", "custom_tool", "mcp_enumerait_read_node",
                "skill_view", "terminal", "memory", "tool_search",
            )
            return [
                {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}
                for name in names
            ]

        specs, dispatch_toolsets = m._build_bridge_tool_specs(definitions)

        assert set(specs) == {"skill_view2", "custom_tool"}
        assert dispatch_toolsets == ["skills_v2", "custom", "enumerait"]
        assert definitions.kwargs == {
            "enabled_toolsets": dispatch_toolsets,
            "quiet_mode": True,
            "skip_tool_search_assembly": True,
        }

    def test_hidden_mcp_dependency_is_registered_for_internal_dispatch(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m
        import tools.mcp_tool as mcp_tool

        calls = []
        monkeypatch.setattr(m, "_configured_mcp_toolsets", lambda config: {"enumerait"})
        monkeypatch.setattr(m, "_load_config", lambda: {"mcp_servers": {"enumerait": {}}})
        monkeypatch.setattr(mcp_tool, "discover_mcp_tools", lambda: calls.append("discover"))

        m._register_bridge_mcp_dependencies(["skills_v2", "enumerait"])

        assert calls == ["discover"]

    def test_stateless_session_search_and_none_filtering(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}
        monkeypatch.setattr(
            m,
            "_dispatch_session_search",
            lambda args: captured.setdefault("search", args) or '{"success": true}',
        )

        result = m._dispatch_tool_call(
            "session_search",
            {"query": "triage", "role_filter": None, "limit": 2},
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("generic dispatch")),
        )

        assert result == {"query": "triage", "limit": 2}
        assert captured["search"] == {"query": "triage", "limit": 2}

    def test_generic_dispatch_keeps_hidden_dependencies_in_scope_and_filters_none(self):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}

        def dispatch(name, args, **kwargs):
            captured.update(name=name, args=args, kwargs=kwargs)
            return '{"success": true}'

        result = m._dispatch_tool_call(
            "skill_manage2",
            {"name": "demo", "file_path": None},
            dispatch,
            enabled_toolsets=["skills_v2", "enumerait"],
        )

        assert result == '{"success": true}'
        assert captured == {
            "name": "skill_manage2",
            "args": {"name": "demo"},
            "kwargs": {"enabled_toolsets": ["skills_v2", "enumerait"]},
        }






class TestMain:
    def test_main_returns_2_when_mcp_unavailable(self, monkeypatch):
        """When the mcp package isn't installed, main() should exit
        cleanly with code 2 and an install hint, not crash."""
        import agent.transports.hermes_tools_mcp_server as m

        def boom_build(*a, **kw):
            raise ImportError("mcp not installed")

        monkeypatch.setattr(m, "_build_server", boom_build)
        rc = m.main(["--verbose"])
        assert rc == 2

    def test_main_handles_keyboard_interrupt(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class FakeServer:
            def run(self):
                raise KeyboardInterrupt()

        monkeypatch.setattr(m, "_build_server", lambda: FakeServer())
        rc = m.main([])
        assert rc == 0

    def test_main_returns_1_on_runtime_error(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class CrashingServer:
            def run(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(m, "_build_server", lambda: CrashingServer())
        rc = m.main([])
        assert rc == 1
