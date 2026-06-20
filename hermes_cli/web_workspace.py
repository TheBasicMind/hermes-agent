"""Helpers for validating Hermes dashboard workspace dependencies.

The web dashboard is built from ``web/src`` but depends on packages resolved
from the npm workspace install tree. A stale nested ``web/node_modules`` can
mask the correct workspace-root install and make ``npm run build`` fail with
missing subpath exports even though ``web/package.json`` was updated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$")
_IMPORT_RE = re.compile(
    r"""(?:from\s+|import\s*\()\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def workspace_root_for(web_dir: Path) -> Path:
    """Return the npm workspace root that owns ``web_dir``."""
    return web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent


def validate_web_workspace_dependencies(web_dir: Path) -> list[str]:
    """Return human-readable dependency integrity errors for ``web_dir``.

    Checks:
    - each direct dependency in ``web/package.json`` is installed;
    - exact pinned dependency versions match the installed package version;
    - packages imported from ``web/src`` expose the imported subpaths.
    """
    package_json_path = web_dir / "package.json"
    if not package_json_path.exists():
        return []

    package_json = _read_json(package_json_path)
    dependencies = package_json.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return ["web/package.json has a non-object dependencies field"]

    errors: list[str] = []
    installed_manifests: dict[str, tuple[Path, dict]] = {}
    for package_name, declared in sorted(dependencies.items()):
        if not isinstance(declared, str):
            continue
        manifest_path = _installed_package_manifest_path(web_dir, package_name)
        if manifest_path is None:
            errors.append(
                f"{package_name} is declared in web/package.json but is not installed under the workspace node_modules tree"
            )
            continue
        manifest = _read_json(manifest_path)
        installed_manifests[package_name] = (manifest_path.parent, manifest)
        installed_version = manifest.get("version")
        if _EXACT_VERSION_RE.match(declared) and installed_version != declared:
            errors.append(
                f"{package_name} declares {declared} in web/package.json but installed package.json reports {installed_version!r}"
            )

    for package_name, subpaths in sorted(_imported_package_subpaths(web_dir).items()):
        installed = installed_manifests.get(package_name)
        if installed is None:
            manifest_path = _installed_package_manifest_path(web_dir, package_name)
            if manifest_path is None:
                # Non-dependency imports are handled elsewhere by the TS build.
                continue
            installed = (manifest_path.parent, _read_json(manifest_path))
        package_dir, manifest = installed
        for subpath in sorted(subpaths):
            if not _manifest_exports_subpath(manifest, package_dir, subpath):
                errors.append(
                    f"{package_name} does not export imported subpath ./{subpath} required by web/src"
                )

    return errors


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _installed_package_manifest_path(web_dir: Path, package_name: str) -> Path | None:
    roots = [web_dir, workspace_root_for(web_dir)]
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        candidate = root / "node_modules" / Path(package_name) / "package.json"
        if candidate.exists():
            return candidate
    return None


def _imported_package_subpaths(web_dir: Path) -> dict[str, set[str]]:
    imported: dict[str, set[str]] = {}
    src_dir = web_dir / "src"
    if not src_dir.is_dir():
        return imported

    for ext in ("*.ts", "*.tsx"):
        for path in src_dir.rglob(ext):
            content = path.read_text(encoding="utf-8")
            for spec in _IMPORT_RE.findall(content):
                if spec.startswith((".", "/", "@/")):
                    continue
                package_name, subpath = _split_package_specifier(spec)
                if subpath is None:
                    continue
                imported.setdefault(package_name, set()).add(subpath)
    return imported


def _split_package_specifier(specifier: str) -> tuple[str, str | None]:
    if specifier.startswith("@"):
        parts = specifier.split("/")
        if len(parts) < 2:
            return specifier, None
        package_name = "/".join(parts[:2])
        subpath = "/".join(parts[2:]) or None
        return package_name, subpath
    parts = specifier.split("/", 1)
    package_name = parts[0]
    subpath = parts[1] if len(parts) == 2 else None
    return package_name, subpath


def _manifest_exports_subpath(manifest: dict, package_dir: Path, subpath: str) -> bool:
    exports = manifest.get("exports")
    key = f"./{subpath}"
    if isinstance(exports, str):
        return key == "." and _export_target_exists(package_dir, exports)
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
            middle_end = len(key) - len(suffix) if suffix else len(key)
            wildcard_value = key[len(prefix):middle_end]
            selected = _select_export_target(target)
            if selected is None:
                return False
            if "*" in selected:
                selected = selected.replace("*", wildcard_value)
            return _export_target_exists(package_dir, selected)
        return False
    return (package_dir / subpath).exists() or (package_dir / f"{subpath}.js").exists()


def _select_export_target(target) -> str | None:
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        for key in ("import", "default", "types", "require"):
            value = target.get(key)
            if isinstance(value, str):
                return value
    return None


def _export_target_exists(package_dir: Path, target: str | None) -> bool:
    if not target:
        return False
    normalized = target[2:] if target.startswith("./") else target
    return (package_dir / normalized).exists()
