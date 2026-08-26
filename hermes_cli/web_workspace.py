"""Integrity checks for the npm dependency tree used by the dashboard build."""

from __future__ import annotations

import json
import re
from pathlib import Path


_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$")
_IMPORT_RE = re.compile(r"""(?:from\s+|import\s*\()\s*['\"]([^'\"]+)['\"]""", re.MULTILINE)


def workspace_root_for(web_dir: Path) -> Path:
    """Return the npm workspace root that owns *web_dir*."""
    return web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent


def validate_web_workspace_dependencies(web_dir: Path) -> list[str]:
    """Return dependency errors for the effective Node resolution tree.

    Node resolves a nested ``web/node_modules`` before the workspace root, so
    the nested manifest is deliberately checked first.  This catches stale
    installs that would otherwise shadow a correct root workspace install.
    """
    package_json_path = web_dir / "package.json"
    if not package_json_path.exists():
        return []
    package_json = _read_json(package_json_path)
    dependencies = {
        **_mapping(package_json.get("dependencies")),
        **_mapping(package_json.get("devDependencies")),
    }
    errors: list[str] = []
    installed: dict[str, tuple[Path, dict]] = {}
    for package_name, declared in sorted(dependencies.items()):
        if not isinstance(declared, str):
            continue
        manifest_path = _installed_package_manifest_path(web_dir, package_name)
        if manifest_path is None:
            errors.append(
                f"{package_name} is declared in web/package.json but is not installed under the effective node_modules tree"
            )
            continue
        manifest = _read_json(manifest_path)
        installed[package_name] = (manifest_path.parent, manifest)
        version = manifest.get("version")
        if _EXACT_VERSION_RE.fullmatch(declared) and version != declared:
            errors.append(
                f"{package_name} declares {declared} in web/package.json but the effective installed package.json reports {version!r}"
            )

    for package_name, subpaths in sorted(_imported_package_subpaths(web_dir).items()):
        package = installed.get(package_name)
        if package is None:
            manifest_path = _installed_package_manifest_path(web_dir, package_name)
            if manifest_path is None:
                continue
            package = (manifest_path.parent, _read_json(manifest_path))
        package_dir, manifest = package
        for subpath in sorted(subpaths):
            if not _manifest_exports_subpath(manifest, package_dir, subpath):
                errors.append(
                    f"{package_name} does not export imported subpath ./{subpath} required by web/src"
                )
    return errors


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _installed_package_manifest_path(web_dir: Path, package_name: str) -> Path | None:
    for root in dict.fromkeys((web_dir, workspace_root_for(web_dir))):
        candidate = root / "node_modules" / Path(package_name) / "package.json"
        if candidate.is_file():
            return candidate
    return None


def _imported_package_subpaths(web_dir: Path) -> dict[str, set[str]]:
    imported: dict[str, set[str]] = {}
    src_dir = web_dir / "src"
    if not src_dir.is_dir():
        return imported
    for extension in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        for path in src_dir.rglob(extension):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for specifier in _IMPORT_RE.findall(content):
                if specifier.startswith((".", "/", "@/")):
                    continue
                package_name, subpath = _split_package_specifier(specifier)
                if subpath:
                    imported.setdefault(package_name, set()).add(subpath)
    return imported


def _split_package_specifier(specifier: str) -> tuple[str, str | None]:
    if specifier.startswith("@"):
        parts = specifier.split("/")
        if len(parts) < 2:
            return specifier, None
        return "/".join(parts[:2]), "/".join(parts[2:]) or None
    parts = specifier.split("/", 1)
    return parts[0], parts[1] if len(parts) == 2 else None


def _manifest_exports_subpath(manifest: dict, package_dir: Path, subpath: str) -> bool:
    exports = manifest.get("exports")
    key = f"./{subpath}"
    if isinstance(exports, dict):
        direct = exports.get(key)
        if direct is not None:
            return _export_target_exists(package_dir, _select_export_target(direct))
        for pattern, target in exports.items():
            if not isinstance(pattern, str) or "*" not in pattern:
                continue
            prefix, suffix = pattern.split("*", 1)
            if not key.startswith(prefix) or (suffix and not key.endswith(suffix)):
                continue
            end = len(key) - len(suffix) if suffix else len(key)
            replacement = key[len(prefix):end]
            selected = _select_export_target(target)
            return bool(selected) and _export_target_exists(
                package_dir, selected.replace("*", replacement)
            )
        return False
    if exports is not None:
        return False
    return (package_dir / subpath).exists() or (package_dir / f"{subpath}.js").exists()


def _select_export_target(target: object) -> str | None:
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        for condition in ("import", "default", "types", "require"):
            value = target.get(condition)
            if isinstance(value, str):
                return value
    return None


def _export_target_exists(package_dir: Path, target: str | None) -> bool:
    if not target:
        return False
    return (package_dir / (target[2:] if target.startswith("./") else target)).exists()
