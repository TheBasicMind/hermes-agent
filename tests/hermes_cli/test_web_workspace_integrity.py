"""Regression coverage for dashboard workspace dependency validation."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _validator():
    module = importlib.import_module("hermes_cli.web_workspace")
    return module.validate_web_workspace_dependencies


def test_stale_nested_install_shadows_correct_workspace_dependency(tmp_path: Path) -> None:
    web = tmp_path / "web"
    _write_json(web / "package.json", {"dependencies": {"demo-pkg": "2.0.0"}})
    _write_json(tmp_path / "node_modules/demo-pkg/package.json", {"version": "2.0.0"})
    _write_json(web / "node_modules/demo-pkg/package.json", {"version": "1.0.0"})

    errors = _validator()(web)

    assert errors == [
        "demo-pkg declares 2.0.0 in web/package.json but the effective installed package.json reports '1.0.0'"
    ]


def test_imported_subpath_must_be_exported_by_effective_package(tmp_path: Path) -> None:
    web = tmp_path / "web"
    _write_json(web / "package.json", {"dependencies": {"demo-pkg": "2.0.0"}})
    _write_json(
        tmp_path / "node_modules/demo-pkg/package.json",
        {"version": "2.0.0", "exports": {".": "./index.js"}},
    )
    (tmp_path / "node_modules/demo-pkg/index.js").write_text("export {}", encoding="utf-8")
    source = web / "src/App.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("import value from 'demo-pkg/feature';\n", encoding="utf-8")

    errors = _validator()(web)

    assert errors == ["demo-pkg does not export imported subpath ./feature required by web/src"]


def test_build_validates_after_install_and_before_vite(tmp_path: Path) -> None:
    from hermes_cli.main import _do_build_web_ui

    web = tmp_path / "web"
    _write_json(web / "package.json", {"dependencies": {"demo-pkg": "2.0.0"}})
    install_ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    events: list[str] = []

    def _install(*args, **kwargs):
        events.append("install")
        return install_ok

    def _validate(_web):
        events.append("validate")
        return ["dependency tree is stale"]

    def _build(*args, **kwargs):
        events.append("build")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with patch("hermes_cli.main._web_ui_build_needed", return_value=True), \
         patch("hermes_cli.main._resolve_node_runtime_npm", return_value="/usr/bin/npm"), \
         patch("hermes_cli.main._run_npm_install_deterministic", side_effect=_install), \
         patch("hermes_cli.web_workspace.validate_web_workspace_dependencies", side_effect=_validate), \
         patch("hermes_cli.main._run_with_idle_timeout", side_effect=_build):
        assert _do_build_web_ui(web, fatal=True) is False

    assert events == ["install", "validate"]
