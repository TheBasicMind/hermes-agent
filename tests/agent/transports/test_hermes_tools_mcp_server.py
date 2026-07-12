"""Tests for the hermes-tools-as-MCP server module surface.

We don't run a live MCP session in unit tests — that requires the codex
subprocess + client + an event loop. These tests pin the bridge contract:
the module imports, the baseline EXPOSED_TOOLS list is sane, and dynamic
configured-toolset exposure keeps Codex-native, MCP, and agent-loop-only
tools out.
"""

from __future__ import annotations




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

    def test_expected_hermes_specific_tools_listed(self):
        """The Hermes-specific tools should be present so users on the
        codex runtime keep access to them."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for required in (
            "web_search",
            "web_extract",
            "browser_navigate",
            "vision_analyze",
            "image_generate",
            "skills_list2",
            "skill_view2",
            "skill_manage2",
            "session_search",
        ):
            assert required in EXPOSED_TOOLS, f"missing {required!r}"

    def test_legacy_skill_tools_not_exposed(self):
        """The bridge should use the Enumerait-backed skills v2 tools."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for legacy_skill_tool in ("skills_list", "skill_view", "skill_manage"):
            assert legacy_skill_tool not in EXPOSED_TOOLS

    def test_stateful_agent_loop_tools_not_exposed(self):
        """delegate_task / memory / todo require the
        running AIAgent context to dispatch, so a stateless MCP callback
        can't drive them. They must NOT be in EXPOSED_TOOLS."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for agent_loop_tool in ("delegate_task", "memory", "todo"):
            assert agent_loop_tool not in EXPOSED_TOOLS, (
                f"{agent_loop_tool!r} requires the agent loop context "
                "and can't be reached through a stateless MCP callback"
            )

    def test_kanban_worker_tools_exposed(self):
        """Kanban workers run as `hermes chat -q` subprocesses; if they
        come up on the codex_app_server runtime, the worker can do the
        actual work via codex's shell but needs the kanban tools through
        the MCP callback to report back to the kernel. Without these
        tools available, the worker would hang at completion time."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        # Worker handoff tools — every dispatched worker uses at least
        # one of {complete, block, comment} to close out its task.
        for worker_tool in (
            "kanban_complete",
            "kanban_block",
            "kanban_comment",
            "kanban_heartbeat",
        ):
            assert worker_tool in EXPOSED_TOOLS, (
                f"{worker_tool!r} missing from codex callback — kanban "
                "workers on codex_app_server runtime would hang"
            )

    def test_kanban_orchestrator_tools_exposed(self):
        """Orchestrator agents need to dispatch new tasks, query the
        board, and unblock/link tasks. Exposed so an orchestrator on
        codex_app_server can do its job."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        for orch_tool in (
            "kanban_create",
            "kanban_show",
            "kanban_list",
            "kanban_unblock",
            "kanban_link",
        ):
            assert orch_tool in EXPOSED_TOOLS, (
                f"{orch_tool!r} missing from codex callback"
            )

    def test_explicit_mcp_toolsets_are_removed(self):
        import agent.transports.hermes_tools_mcp_server as m

        config = {
            "mcp_servers": {
                "enumerait": {"enabled": True},
                "postiz": {"enabled": True},
            }
        }
        toolsets = {"web", "skills_v2", "enumerait", "postiz", "no_mcp"}

        assert m._remove_mcp_toolsets(toolsets, config) == ["skills_v2", "web"]

    def test_dynamic_bridge_specs_include_plugins_and_filter_unsafe_tools(
        self,
        monkeypatch,
    ):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}
        monkeypatch.setattr(
            m,
            "_resolve_bridge_toolsets",
            lambda: ["knowledge_store", "market_intel", "skills_v2"],
        )
        monkeypatch.setattr(
            m,
            "_resolve_bridge_dispatch_toolsets",
            lambda exposed: exposed,
        )

        def fake_get_tool_definitions(**kwargs):
            captured.update(kwargs)
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "knowledge_store_maintain",
                        "description": "Maintain knowledge store",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "market_intel_intake",
                        "description": "Run market intake",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "skill_view2",
                        "description": "View v2 skill",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "description": "Shell",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "skills_list",
                        "description": "Legacy skills",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "memory",
                        "description": "Agent loop memory",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]

        specs, enabled_toolsets = m._build_bridge_tool_specs(fake_get_tool_definitions)

        assert enabled_toolsets == ["knowledge_store", "market_intel", "skills_v2"]
        assert captured == {
            "enabled_toolsets": ["knowledge_store", "market_intel", "skills_v2"],
            "quiet_mode": True,
            "skip_tool_search_assembly": True,
        }
        assert "knowledge_store_maintain" in specs
        assert "market_intel_intake" in specs
        assert "skill_view2" in specs
        assert "terminal" not in specs
        assert "skills_list" not in specs
        assert "memory" not in specs

    def test_bridge_specs_keep_mcp_dependencies_hidden_but_registered(
        self,
        monkeypatch,
    ):
        """skills_v2 depends on Enumerait MCP tools at dispatch time.

        Codex should not see nested MCP tools as callable bridge tools, but
        Hermes still has to register them internally so skill_view2 and
        skill_manage2 can call mcp_enumerait_* through the registry.
        """
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}
        monkeypatch.setattr(m, "_resolve_bridge_toolsets", lambda: ["skills_v2"])
        monkeypatch.setattr(
            m,
            "_resolve_bridge_dispatch_toolsets",
            lambda exposed: sorted(set(exposed) | {"enumerait"}),
            raising=False,
        )

        def fake_get_tool_definitions(**kwargs):
            captured.update(kwargs)
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "skill_manage2",
                        "description": "Manage v2 skill",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "mcp_enumerait_read_node",
                        "description": "Read Enumerait node",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]

        specs, enabled_toolsets = m._build_bridge_tool_specs(fake_get_tool_definitions)

        assert captured["enabled_toolsets"] == ["enumerait", "skills_v2"]
        assert enabled_toolsets == ["enumerait", "skills_v2"]
        assert "skill_manage2" in specs
        assert "mcp_enumerait_read_node" not in specs

    def test_bridge_specs_load_runtime_dotenv_before_tool_discovery(
        self,
        monkeypatch,
    ):
        import agent.transports.hermes_tools_mcp_server as m

        calls = []

        def fake_load_env():
            calls.append("load_env")

        def fake_resolve_toolsets():
            calls.append("resolve_toolsets")
            return ["all_x"]

        def fake_resolve_dispatch_toolsets(exposed):
            calls.append("resolve_dispatch_toolsets")
            return sorted(set(exposed) | {"enumerait"})

        def fake_register_mcp_dependencies(enabled_toolsets):
            calls.append(("register_mcp_dependencies", tuple(enabled_toolsets)))

        def fake_get_tool_definitions(**_kwargs):
            calls.append("get_defs")
            return []

        monkeypatch.setattr(m, "_load_bridge_runtime_env", fake_load_env)
        monkeypatch.setattr(m, "_resolve_bridge_toolsets", fake_resolve_toolsets)
        monkeypatch.setattr(m, "_resolve_bridge_dispatch_toolsets", fake_resolve_dispatch_toolsets)
        monkeypatch.setattr(m, "_configured_mcp_toolsets", lambda _config: {"enumerait"})
        monkeypatch.setattr(m, "_register_bridge_mcp_dependencies", fake_register_mcp_dependencies, raising=False)

        m._build_bridge_tool_specs(fake_get_tool_definitions)

        assert calls == [
            "load_env",
            "resolve_toolsets",
            "resolve_dispatch_toolsets",
            ("register_mcp_dependencies", ("all_x", "enumerait")),
            "get_defs",
        ]

    def test_fastmcp_kwargs_envelope_is_unwrapped(self):
        """FastMCP builds a kwargs-shaped schema for the dynamic handlers.
        The callback must unwrap that envelope before handing arguments to
        Hermes tools, otherwise browser_navigate receives {"kwargs": {...}}
        instead of {"url": "..."} and URL validation fails."""
        from agent.transports.hermes_tools_mcp_server import _normalize_tool_args

        assert _normalize_tool_args({"kwargs": {"url": "http://example.com"}}) == {
            "url": "http://example.com"
        }

    def test_non_enveloped_tool_args_pass_through(self):
        from agent.transports.hermes_tools_mcp_server import _normalize_tool_args

        assert _normalize_tool_args({"url": "http://example.com"}) == {
            "url": "http://example.com"
        }

    def test_session_search_uses_stateless_dispatcher(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}

        def fake_session_search(args):
            captured.update(args)
            return '{"success": true}'

        monkeypatch.setattr(m, "_dispatch_session_search", fake_session_search)

        def forbidden_dispatch(*_args, **_kwargs):
            raise AssertionError("generic dispatcher should not handle session_search")

        result = m._dispatch_tool_call(
            "session_search",
            {"kwargs": {"query": "email triage", "limit": 2}},
            forbidden_dispatch,
        )

        assert result == '{"success": true}'
        assert captured["query"] == "email triage"
        assert captured["limit"] == 2

    def test_other_tools_use_generic_dispatcher(self):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}

        def fake_dispatch(name, args):
            captured["name"] = name
            captured["args"] = args
            return '{"ok": true}'

        result = m._dispatch_tool_call(
            "web_extract",
            {"kwargs": {"urls": ["https://example.com"]}},
            fake_dispatch,
        )

        assert result == '{"ok": true}'
        assert captured == {
            "name": "web_extract",
            "args": {"urls": ["https://example.com"]},
        }

    def test_generic_dispatcher_receives_enabled_toolsets_when_supplied(self):
        import agent.transports.hermes_tools_mcp_server as m

        captured = {}

        def fake_dispatch(name, args, **kwargs):
            captured["name"] = name
            captured["args"] = args
            captured["kwargs"] = kwargs
            return '{"ok": true}'

        result = m._dispatch_tool_call(
            "knowledge_store_maintain",
            {"kwargs": {"mode": "status"}},
            fake_dispatch,
            enabled_toolsets=["knowledge_store"],
        )

        assert result == '{"ok": true}'
        assert captured == {
            "name": "knowledge_store_maintain",
            "args": {"mode": "status"},
            "kwargs": {"enabled_toolsets": ["knowledge_store"]},
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
