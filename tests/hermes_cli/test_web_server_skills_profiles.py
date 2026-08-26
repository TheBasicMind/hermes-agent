"""Regression tests for dashboard profile-scoped skills/toolsets management.

"Set as active" on the Profiles page only flips the sticky ``active_profile``
file (future CLI/gateway runs) — it never retargets the running dashboard
process. Before the ``profile`` parameter existed, toggling a skill after
"activating" a profile silently wrote into the dashboard's own config.
These tests pin the new behavior: reads and writes land in the REQUESTED
profile's HERMES_HOME, and the dashboard's own profile stays untouched.
"""
import pytest
import yaml


def _write_skill(skills_dir, name, description="test skill"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch, _isolate_hermes_home):
    """Isolated default home + one named profile, each with its own skills."""
    from hermes_constants import get_hermes_home
    from hermes_cli import profiles

    default_home = get_hermes_home()
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"
    for home in (default_home, worker_home):
        (home / "skills").mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("{}\n", encoding="utf-8")

    _write_skill(default_home / "skills", "dashboard-skill")
    _write_skill(worker_home / "skills", "worker-skill")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"default": default_home, "worker_alpha": worker_home}


@pytest.fixture
def client(monkeypatch, isolated_profiles):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def _load_cfg(home):
    return yaml.safe_load((home / "config.yaml").read_text()) or {}


class TestProfileScopedSkills:


    def test_toggle_writes_into_target_profile_only(self, client, isolated_profiles):
        resp = client.put(
            "/api/skills/toggle",
            json={"name": "worker-skill", "enabled": False, "profile": "worker_alpha"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "name": "worker-skill", "enabled": False}

        worker_cfg = _load_cfg(isolated_profiles["worker_alpha"])
        assert "worker-skill" in worker_cfg.get("skills", {}).get("disabled", [])
        # The dashboard's own config must stay untouched — this was the bug.
        default_cfg = _load_cfg(isolated_profiles["default"])
        assert "worker-skill" not in default_cfg.get("skills", {}).get("disabled", [])



    def test_scope_restores_module_globals(self, client, isolated_profiles):
        """The SKILLS_DIR swap is per-request; the module global must be
        restored even after a scoped call (cron-style locked swap)."""
        import tools.skills_tool as skills_tool

        before = skills_tool.SKILLS_DIR
        client.get("/api/skills", params={"profile": "worker_alpha"})
        assert skills_tool.SKILLS_DIR == before

    def test_preload_toggle_is_profile_scoped_and_does_not_disable_or_rewrite_skill(
        self, client, isolated_profiles
    ):
        worker_home = isolated_profiles["worker_alpha"]
        skill_md = worker_home / "skills" / "worker-skill" / "SKILL.md"
        before = skill_md.read_bytes()

        resp = client.put(
            "/api/skills/preload/toggle",
            json={"name": "worker-skill", "enabled": False, "profile": "worker_alpha"},
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "name": "worker-skill",
            "inject_frontmatter": False,
            "preload": False,
            "enabled": True,
        }
        worker_cfg = _load_cfg(worker_home)
        assert worker_cfg["skills"]["preload_enabled_skills"] == []
        assert worker_cfg["skills"].get("disabled", []) == []
        assert skill_md.read_bytes() == before
        assert _load_cfg(isolated_profiles["default"]) == {}

        content = client.get(
            "/api/skills/content",
            params={"name": "worker-skill", "profile": "worker_alpha"},
        )
        assert content.status_code == 200
        assert content.json()["content"].encode() == before

        import json
        from hermes_cli.web_server import _profile_scope
        from tools.skills_tool import skill_view

        with _profile_scope("worker_alpha"):
            explicit = json.loads(skill_view("worker-skill", preprocess=False))
        assert explicit["success"] is True

    def test_preload_list_reports_prompt_injection_without_changing_loadability(
        self, client, isolated_profiles
    ):
        worker_home = isolated_profiles["worker_alpha"]
        (worker_home / "config.yaml").write_text(
            "skills:\n  preload_enabled_skills: []\n", encoding="utf-8"
        )

        resp = client.get("/api/skills/preload", params={"profile": "worker_alpha"})

        assert resp.status_code == 200
        by_name = {row["name"]: row for row in resp.json()}
        assert by_name["worker-skill"]["enabled"] is True
        assert by_name["worker-skill"]["inject_frontmatter"] is False
        assert by_name["worker-skill"]["preload"] is False


class TestProfileScopedHubActions:
    def test_hub_install_spawns_with_profile_flag(
        self, client, isolated_profiles, monkeypatch
    ):
        """Hub installs must go through a fresh ``hermes -p <profile>``
        subprocess — the in-process scope can't reach skills_hub's
        import-time SKILLS_DIR binding."""
        import hermes_cli.web_server as web_server

        calls = []

        class _FakeProc:
            pid = 4242

        def _fake_spawn(subcommand, name):
            calls.append((list(subcommand), name))
            return _FakeProc()

        monkeypatch.setattr(web_server, "_spawn_hermes_action", _fake_spawn)
        resp = client.post(
            "/api/skills/hub/install",
            json={"identifier": "official/demo", "profile": "worker_alpha"},
        )
        assert resp.status_code == 200
        assert calls == [
            (
                ["-p", "worker_alpha", "skills", "install", "official/demo", "--yes"],
                web_server._hub_action_name("install", "official/demo"),
            )
        ]


    def test_hub_install_unknown_profile_404(self, client, isolated_profiles):
        resp = client.post(
            "/api/skills/hub/install",
            json={"identifier": "official/demo", "profile": "ghost"},
        )
        assert resp.status_code == 404
