"""Tests for the Agent Runs dashboard plugin backend."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_PLUGIN_API = Path(__file__).resolve().parents[2] / "plugins" / "agent-runs" / "dashboard" / "api.py"


def _load_agent_runs_api(module_name: str = "agent_runs_dashboard_api_test"):
    spec = importlib.util.spec_from_file_location(module_name, _PLUGIN_API)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_runs_root_defaults_to_profile_home_and_creates_storage(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.delenv("HERMES_AGENT_RUNS_HOME", raising=False)

    api = _load_agent_runs_api("agent_runs_dashboard_api_default_test")

    assert api._runs_root() == profile_home / "agent-runs"
    assert (profile_home / "agent-runs" / "transcripts").is_dir()


def test_runs_root_can_be_shared_with_environment_override(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile-home"
    shared_home = tmp_path / "shared-agent-runs"
    profile_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("HERMES_AGENT_RUNS_HOME", str(shared_home))

    api = _load_agent_runs_api("agent_runs_dashboard_api_env_test")

    assert api._runs_root() == shared_home
    assert (shared_home / "transcripts").is_dir()
    assert not (profile_home / "agent-runs").exists()


def test_runs_root_can_be_shared_with_dashboard_config(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile-home"
    shared_home = tmp_path / "shared-from-config"
    profile_home.mkdir()
    (profile_home / "config.yaml").write_text(
        "dashboard:\n"
        f"  agent_runs_home: {shared_home}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.delenv("HERMES_AGENT_RUNS_HOME", raising=False)

    api = _load_agent_runs_api("agent_runs_dashboard_api_config_test")

    assert api._runs_root() == shared_home
    assert (shared_home / "transcripts").is_dir()
    assert not (profile_home / "agent-runs").exists()


def test_runs_root_environment_override_takes_precedence_over_config(monkeypatch, tmp_path):
    profile_home = tmp_path / "profile-home"
    config_home = tmp_path / "shared-from-config"
    env_home = tmp_path / "shared-from-env"
    profile_home.mkdir()
    (profile_home / "config.yaml").write_text(
        "dashboard:\n"
        f"  agent_runs_home: {config_home}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))
    monkeypatch.setenv("HERMES_AGENT_RUNS_HOME", str(env_home))

    api = _load_agent_runs_api("agent_runs_dashboard_api_precedence_test")

    assert api._runs_root() == env_home
    assert (env_home / "transcripts").is_dir()
    assert not config_home.exists()
    assert not (profile_home / "agent-runs").exists()
