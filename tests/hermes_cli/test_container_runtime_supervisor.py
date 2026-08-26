"""Container-only dashboard integration with Hermes' runtime supervisor."""

from unittest.mock import MagicMock, patch

import pytest

from hermes_cli import web_server


@pytest.fixture(autouse=True)
def _reset_restart_state(monkeypatch):
    monkeypatch.setattr(web_server, "_ACTION_PROCS", {})
    monkeypatch.setattr(web_server, "_ACTION_COMMANDS", {})
    monkeypatch.setattr(web_server, "_ACTION_IDS", {})
    monkeypatch.setattr(web_server, "_LAST_GATEWAY_RESTART", None)


def test_runtime_control_is_unavailable_outside_a_container(monkeypatch):
    monkeypatch.setattr(
        web_server, "is_container_restart_context", lambda: False
    )
    monkeypatch.setattr(
        web_server.shutil, "which", lambda _name: "/usr/local/bin/hermes-runtime-control"
    )

    assert web_server._runtime_control_command() is None


def test_status_tsv_selects_named_profile_and_rejects_nonpositive_pid(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "home"
    status_dir = runtime_root / "run" / "runtime"
    status_dir.mkdir(parents=True)
    (status_dir / "status.tsv").write_text(
        "gateway-default\tpid=111\talive=yes\tpolicy=restart\n"
        "gateway-samwise\tpid=4242\talive=yes\tpolicy=restart\n"
        "gateway-chris\tpid=0\talive=yes\tpolicy=restart\n",
        encoding="utf-8",
    )
    profile_dir = runtime_root / "profiles" / "samwise"
    monkeypatch.setattr(
        web_server,
        "_runtime_control_command",
        lambda: "/usr/local/bin/hermes-runtime-control",
    )

    assert web_server._runtime_supervisor_gateway_pid(profile_dir=profile_dir) == 4242
    assert (
        web_server._runtime_supervisor_gateway_pid(
            profile_dir=runtime_root / "profiles" / "chris"
        )
        is None
    )


def test_status_endpoint_uses_container_supervisor_liveness(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    runtime_root = tmp_path / "home"
    status_dir = runtime_root / "run" / "runtime"
    status_dir.mkdir(parents=True)
    (status_dir / "status.tsv").write_text(
        "gateway-default\tpid=4242\talive=yes\tpolicy=restart\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(web_server, "_runtime_base_home", lambda _profile=None: runtime_root)
    monkeypatch.setattr(
        web_server,
        "_runtime_control_command",
        lambda: "/usr/local/bin/hermes-runtime-control",
    )
    monkeypatch.setattr(web_server, "get_running_pid_cached", lambda *a, **k: None)
    monkeypatch.setattr(web_server, "read_runtime_status", lambda *a, **k: None)
    monkeypatch.setattr(
        web_server, "get_runtime_status_running_pid", lambda *a, **k: None
    )
    monkeypatch.setattr(web_server, "_GATEWAY_HEALTH_URL", "")
    monkeypatch.setattr(web_server, "check_config_version", lambda: (38, 38))
    monkeypatch.setattr(
        web_server,
        "_collect_profile_gateway_topology_cached",
        lambda: {
            "profiles": ["default"],
            "gateway_mode": "single",
            "gateways": [],
            "profile_platforms": {},
        },
    )
    monkeypatch.setattr(web_server, "get_install_id", lambda: None)

    async def no_sessions():
        return 0

    monkeypatch.setattr(web_server, "_status_active_sessions", no_sessions)

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["gateway_running"] is True
    assert response.json()["gateway_pid"] == 4242
    assert response.json()["gateway_state"] == "running"


def test_messaging_payload_uses_same_container_supervisor_liveness(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_server, "get_running_pid_cached", lambda *a, **k: None)
    monkeypatch.setattr(
        web_server, "get_runtime_status_running_pid", lambda *a, **k: None
    )
    monkeypatch.setattr(web_server, "_GATEWAY_HEALTH_URL", "")
    monkeypatch.setattr(
        web_server, "_runtime_supervisor_gateway_pid", lambda *a, **k: 4242
    )
    monkeypatch.setattr(web_server, "load_config", lambda: {})

    payload = web_server._messaging_platform_payload(
        {
            "id": "telegram",
            "name": "Telegram",
            "description": "",
            "docs_url": "",
            "env_vars": [],
            "required_env": [],
        },
        {},
        None,
        scoped=True,
        profile_home=tmp_path / "profiles" / "samwise",
    )

    assert payload["gateway_running"] is True


def test_gateway_restart_routes_through_supervisor_and_preserves_action_contract(
    tmp_path, monkeypatch
):
    runtime_root = tmp_path / "home"
    profile_dir = runtime_root / "profiles" / "samwise"
    captured = {}
    proc = MagicMock()
    proc.pid = 4343
    proc.poll.return_value = None

    monkeypatch.setattr(
        web_server,
        "_runtime_control_command",
        lambda: "/usr/local/bin/hermes-runtime-control",
    )
    monkeypatch.setattr(
        web_server,
        "_runtime_supervisor_status",
        lambda: {"gateway-samwise": {"pid": "4242", "alive": "yes"}},
    )
    monkeypatch.setattr(web_server, "_runtime_base_home", lambda _profile=None: runtime_root)
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _profile: profile_dir)

    def fake_spawn(cmd, name, *, command_key=None, env_overrides=None):
        captured.update(
            cmd=cmd,
            name=name,
            command_key=command_key,
            env_overrides=env_overrides,
        )
        return proc

    monkeypatch.setattr(web_server, "_spawn_action_command", fake_spawn)

    with patch("hermes_cli.gateway._reap_unsupervised_gateway_orphans") as reap:
        restarted, reused = web_server._spawn_gateway_restart("samwise")

    assert restarted is proc
    assert reused is False
    reap.assert_called_once_with()
    assert captured == {
        "cmd": [
            "/usr/local/bin/hermes-runtime-control",
            "restart",
            "gateway-samwise",
        ],
        "name": "gateway-restart",
        "command_key": ("runtime-control", "restart", "gateway-samwise"),
        "env_overrides": {"HERMES_HOME": str(runtime_root)},
    }
    assert web_server._LAST_GATEWAY_RESTART[1] is proc


def test_spawn_action_command_scrubs_gateway_env_and_records_action_id(
    tmp_path, monkeypatch
):
    captured = {}

    class Proc:
        pid = 4567

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return Proc()

    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    monkeypatch.setattr(web_server, "_ACTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(web_server.subprocess, "Popen", fake_popen)

    web_server._spawn_action_command(
        ["/usr/local/bin/hermes-runtime-control", "restart", "gateway-default"],
        "gateway-restart",
        command_key=("runtime-control", "restart", "gateway-default"),
        env_overrides={"HERMES_ACTION_ID": "action-123"},
    )

    assert "_HERMES_GATEWAY" not in captured["env"]
    assert captured["env"]["HERMES_NONINTERACTIVE"] == "1"
    assert web_server._ACTION_IDS["gateway-restart"] == "action-123"
