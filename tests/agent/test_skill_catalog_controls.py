"""Startup skill catalog controls must filter prompt metadata only."""

from __future__ import annotations

import json
from pathlib import Path

from agent import prompt_builder as pb


def _skill(root: Path, name: str, *, tag: str) -> Path:
    path = root / "general" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} description\ntags: [{tag}]\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return path


def _build(home: Path, local: Path, external: Path, project: Path) -> str:
    token = pb.set_hermes_home_override(str(home))
    try:
        return pb._build_skills_system_prompt_inner(
            local,
            [external],
            None,
            None,
            None,
            project_dirs=[project],
        )
    finally:
        pb.reset_hermes_home_override(token)


def test_preload_allowlist_filters_project_local_org_and_external_on_cold_and_snapshot_paths(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    local = home / "skills"
    external = tmp_path / "external"
    project = tmp_path / "project"
    for root, prefix in ((local, "local"), (external, "external"), (project, "project")):
        _skill(root, f"{prefix}-on", tag="wanted")
        _skill(root, f"{prefix}-off", tag="wanted")

    org_root = local / "_org/acme"
    _skill(org_root, "org-on", tag="wanted")
    _skill(org_root, "org-off", tag="wanted")
    (local / "_org/.active_org").write_text("acme\n", encoding="utf-8")

    enabled = ["project-on", "local-on", "org-on", "external-on"]
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "skills:\n"
        "  inject_catalog: true\n"
        "  catalog_detail: full\n"
        "  catalog_filter_tags: [wanted]\n"
        f"  preload_enabled_skills: {json.dumps(enabled)}\n",
        encoding="utf-8",
    )

    cold = _build(home, local, external, project)
    for name in enabled:
        assert name in cold
    for name in ("project-off", "local-off", "org-off", "external-off"):
        assert name not in cold

    snapshot = home / ".skills_prompt_snapshot.json"
    assert snapshot.exists()
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["version"] >= 3
    assert all("tags" in entry for entry in payload["skills"])

    pb.clear_skills_system_prompt_cache(clear_snapshot=False)
    warm = _build(home, local, external, project)
    assert warm == cold


def test_catalog_config_participates_in_memory_cache_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local = home / "skills"
    _skill(local, "alpha", tag="one")
    _skill(local, "beta", tag="two")
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.yaml"
    cfg.write_text("skills:\n  preload_enabled_skills: [alpha]\n", encoding="utf-8")

    first = _build(home, local, tmp_path / "external", tmp_path / "project")
    assert "alpha" in first and "beta" not in first

    cfg.write_text("skills:\n  preload_enabled_skills: [beta]\n", encoding="utf-8")
    second = _build(home, local, tmp_path / "external", tmp_path / "project")
    assert "beta" in second and "alpha" not in second


def test_project_shadowing_is_preserved_when_catalog_tags_hide_project_entry(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    local = home / "skills"
    project = tmp_path / "project"
    _skill(local, "shared-name", tag="wanted")
    _skill(project, "shared-name", tag="other")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "skills:\n"
        "  catalog_filter_tags: [wanted]\n"
        "  preload_enabled_skills: [shared-name]\n",
        encoding="utf-8",
    )

    result = _build(home, local, tmp_path / "external", project)

    assert "shared-name" not in result
