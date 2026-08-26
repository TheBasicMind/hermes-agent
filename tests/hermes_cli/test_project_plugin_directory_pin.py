"""Container deployment regressions for pinned project-plugin discovery."""

from pathlib import Path

import yaml

from hermes_cli.plugins import PluginManager


def _write_plugin(root: Path, name: str) -> None:
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        yaml.safe_dump({"name": name, "version": "1.0.0"}),
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n    pass\n",
        encoding="utf-8",
    )


def _project_names(manager: PluginManager) -> set[str]:
    return {
        manifest.name
        for manifest in manager._collect_directory_manifests()
        if manifest.source == "project"
    }


def test_explicit_project_plugins_dir_survives_scratch_cwd(tmp_path, monkeypatch):
    pinned = tmp_path / "project" / ".hermes" / "plugins"
    _write_plugin(pinned, "pinned-plugin")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "bundled"))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.setenv("HERMES_PROJECT_PLUGINS_DIR", str(pinned))
    monkeypatch.chdir(scratch)

    assert _project_names(PluginManager()) == {"pinned-plugin"}


def test_explicit_project_plugins_dir_does_not_bypass_enable_gate(
    tmp_path, monkeypatch
):
    pinned = tmp_path / "project" / ".hermes" / "plugins"
    _write_plugin(pinned, "pinned-plugin")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "bundled"))
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)
    monkeypatch.setenv("HERMES_PROJECT_PLUGINS_DIR", str(pinned))

    assert _project_names(PluginManager()) == set()


def test_explicit_project_plugins_dir_is_the_single_canonical_source(
    tmp_path, monkeypatch
):
    pinned = tmp_path / "project" / ".hermes" / "plugins"
    _write_plugin(pinned, "pinned-plugin")
    cwd = tmp_path / "cwd"
    _write_plugin(cwd / ".hermes" / "plugins", "cwd-plugin")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(tmp_path / "bundled"))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "true")
    monkeypatch.setenv("HERMES_PROJECT_PLUGINS_DIR", str(pinned))
    monkeypatch.chdir(cwd)

    assert _project_names(PluginManager()) == {"pinned-plugin"}
