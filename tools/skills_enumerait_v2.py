#!/usr/bin/env python3
"""Enumerait-authoritative skill tools layered over Hermes' guarded tools.

The v2 names deliberately do not shadow Hermes' built-in skill tools.  Every
local mutation is delegated to ``skill_manage`` so target-version approval,
security scanning and rollback, audit, prompt-cache, provenance, usage, and
sync hooks remain authoritative.  This module adds only Enumerait mapping and
reconciliation around those guarded operations.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from hermes_constants import get_hermes_home
from hermes_cli.config import cfg_get, load_config
from tools.registry import registry

logger = logging.getLogger(__name__)

ERROR_REPAIR = {
    "AMBIGUOUS_NODE_MATCH": [
        "Inspect the candidate nodes under skills.config.enumerait_skill_node.",
        "Keep one node for the skill key/title or set a unique external_ids.hermes_skill_key.",
    ],
    "NODE_NOT_FOUND": ["Verify the configured Enumerait skill parent and retry."],
    "SKILL_FILE_NOT_FOUND": ["Verify the skill exists locally or use skills_list2."],
    "ENUMERAIT_WRITE_FAILED": ["Restore Enumerait connectivity, then retry the same operation."],
    "PARTIAL_COMMIT_DETECTED": [
        "Do not assume the local skill and Enumerait node are synchronized.",
        "Compare both sides and repair explicitly before continuing.",
    ],
    "VALIDATION_FAILED": ["Fix the input or satisfy the guarded Hermes operation, then retry."],
}


def _get_config() -> dict:
    return load_config()


def _error(code: str, message: str, context: Optional[dict] = None) -> dict:
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "context": context or {},
            "repair_instructions": ERROR_REPAIR.get(code, ["Inspect the context and retry safely."]),
        },
    }


def _parent_id() -> Optional[str]:
    return cfg_get(_get_config(), "skills", "config", "enumerait_skill_node")


def _skills_root() -> Path:
    return get_hermes_home() / "skills"


def _parse_skill_doc(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    try:
        frontmatter = yaml.safe_load(text[3:end].strip()) or {}
    except yaml.YAMLError:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    body_start = text.find("\n", end + 4)
    body = text[body_start + 1 :].lstrip("\n") if body_start >= 0 else ""
    return frontmatter, body


def _compose_skill_doc(frontmatter: dict, body: str, node_id: Optional[str] = None) -> str:
    current = copy.deepcopy(frontmatter) if isinstance(frontmatter, dict) else {}
    if node_id:
        current["nodeId"] = str(node_id)
    dumped = yaml.safe_dump(current, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n\n{body.lstrip(chr(10))}"


def _summary(frontmatter: dict) -> dict:
    result = copy.deepcopy(frontmatter) if isinstance(frontmatter, dict) else {}
    result.pop("nodeId", None)
    return result


def _resolved_local_skill(name: str) -> tuple[Optional[Path], Optional[str]]:
    if ":" in (name or ""):
        return None, "plugin_skill"
    from tools.skill_manager_tool import _find_skill

    found = _find_skill(Path(name).name)
    if not found:
        return None, "not_found"
    skill_dir = Path(found["path"])
    try:
        skill_dir.resolve().relative_to(_skills_root().resolve())
    except (OSError, ValueError):
        return skill_dir, "external_skill"
    return skill_dir, None


def _skill_key(skill_dir: Path) -> str:
    return skill_dir.resolve().relative_to(_skills_root().resolve()).as_posix()


def _node_candidates(nodes: list[dict]) -> list[dict]:
    return [
        {
            "id": node.get("id"),
            "title": node.get("title"),
            "external_ids": node.get("external_ids"),
            "metadata": node.get("metadata"),
        }
        for node in nodes
    ]


class RegistryEnumeraitClient:
    """Adapter over the configured Enumerait MCP registry entries."""

    def __init__(self) -> None:
        configured = cfg_get(_get_config(), "skills", "config", "enumerait_tools") or {}
        self.tools = {
            "search": configured.get("search_by_title", configured.get("find_by_title", "mcp_enumerait_search_nodes_by_title_scoped")),
            "read": configured.get("read_node", configured.get("get_node", "mcp_enumerait_read_node")),
            "upsert": configured.get("upsert_node", configured.get("create", "mcp_enumerait_upsert_node")),
            "delete": configured.get("delete_node", configured.get("delete", "mcp_enumerait_delete_node")),
        }
        self._map_ids: dict[str, str] = {}

    def _call(self, operation: str, args: dict) -> Any:
        entry = registry.get_entry(self.tools[operation])
        if entry is None:
            raise RuntimeError(f"Enumerait tool '{self.tools[operation]}' is not registered")
        value = entry.handler(args)
        value = json.loads(value) if isinstance(value, str) else value
        if isinstance(value, dict) and value.get("success") is False:
            raise RuntimeError(str(value.get("error") or value))
        if isinstance(value, dict) and value.get("error"):
            raise RuntimeError(str(value["error"]))
        value = value.get("result", value) if isinstance(value, dict) else value
        if isinstance(value, str):
            try:
                nested = json.loads(value)
            except json.JSONDecodeError:
                return value
            if isinstance(nested, dict) and nested.get("success") is False:
                raise RuntimeError(str(nested.get("error") or nested))
            if isinstance(nested, dict) and nested.get("error"):
                raise RuntimeError(str(nested["error"]))
            return nested.get("result", nested) if isinstance(nested, dict) else nested
        return value

    @staticmethod
    def _node(value: Any) -> Optional[dict]:
        if not isinstance(value, dict):
            return None
        payload = value.get("node") if isinstance(value.get("node"), dict) else value
        payload = dict(payload)
        if "id" not in payload and payload.get("node_id"):
            payload["id"] = payload["node_id"]
        if "parent_id" not in payload and payload.get("parents"):
            payload["parent_id"] = payload["parents"][0]
        if "mapGUID" not in payload and value.get("mind_map_id"):
            payload["mapGUID"] = value["mind_map_id"]
        if "node_type" not in payload and payload.get("type"):
            payload["node_type"] = payload["type"]
        return payload

    def _mind_map_id(self, parent_id: str) -> str:
        if parent_id not in self._map_ids:
            raw = self._call("read", {"node_id": parent_id, "content_mode": "full"})
            parent = self._node(raw) or {}
            map_id = raw.get("mind_map_id") if isinstance(raw, dict) else None
            map_id = map_id or parent.get("mapGUID")
            if not map_id:
                raise RuntimeError(f"Could not infer Enumerait mind_map_id from parent '{parent_id}'")
            self._map_ids[parent_id] = str(map_id)
        return self._map_ids[parent_id]

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._node(self._call("read", {"node_id": node_id, "content_mode": "full"}))

    def find_by_title(self, title: str, parent_id: str) -> list[dict]:
        value = self._call(
            "search",
            {
                "mind_map_id": self._mind_map_id(parent_id),
                "root_node_id": parent_id,
                "query": title,
                "exact_match": True,
                "case_sensitive": False,
                "node_type": "skill",
                "status": "active",
                "limit": 20,
            },
        )
        nodes = value if isinstance(value, list) else value.get("nodes", []) if isinstance(value, dict) else []
        return [node for node in (self._node(item) for item in nodes) if node]

    def create_skill_node(self, parent_id: str, title: str, summary: dict, content: str, external_ids: dict, metadata: dict) -> dict:
        value = self._call(
            "upsert",
            {
                "mind_map_id": self._mind_map_id(parent_id),
                "parent_id": parent_id,
                "node_type": "skill",
                "title": title,
                "summary": yaml.safe_dump(summary, sort_keys=False).strip(),
                "content": content,
                "external_ids": external_ids,
                **metadata,
                "source_system": "hermes-skill",
            },
        )
        node = self._node(value)
        if node is None:
            raise RuntimeError(f"Enumerait upsert returned no node: {value}")
        return node

    def update_skill_node_large(self, node_id: str, **fields: Any) -> dict:
        current = self.get_node(node_id) or {"id": node_id}
        parent = current.get("parent_id") or _parent_id()
        payload = {"mind_map_id": self._mind_map_id(parent), "node_id": node_id}
        for key, value in fields.items():
            if key == "metadata" and isinstance(value, dict):
                payload.update(value)
            elif key == "summary" and isinstance(value, dict):
                payload[key] = yaml.safe_dump(value, sort_keys=False).strip()
            else:
                payload[key] = value
        return self._node(self._call("upsert", payload)) or current

    def delete_node(self, node_id: str) -> None:
        node = self.get_node(node_id) or {}
        parent = node.get("parent_id") or _parent_id()
        self._call("delete", {"mind_map_id": self._mind_map_id(parent), "node_id": node_id})


def _get_enumerait_client():
    return RegistryEnumeraitClient()


def _mapping_metadata(skill_dir: Path) -> dict:
    skill_md = skill_dir / "SKILL.md"
    return {
        "skill_dir_abs_path": str(skill_dir.resolve()),
        "skill_file_abs_path": str(skill_md.resolve()),
        "skill_relative_path": _skill_key(skill_dir),
    }


def _resolve_node(skill_dir: Path, frontmatter: dict, *, create_if_missing: bool) -> tuple[Optional[dict], Optional[dict], bool]:
    parent = _parent_id()
    if not parent:
        return None, _error("VALIDATION_FAILED", "Missing skills.config.enumerait_skill_node"), False
    client = _get_enumerait_client()
    recovered = False
    node_id = frontmatter.get("nodeId")
    if node_id:
        direct = client.get_node(str(node_id))
        if direct and direct.get("parent_id") == parent:
            return direct, None, False

    title = skill_dir.name
    key = _skill_key(skill_dir)
    candidates = client.find_by_title(title, parent)
    keyed = [node for node in candidates if (node.get("external_ids") or {}).get("hermes_skill_key") == key]
    matched = keyed or candidates
    if len(matched) > 1:
        return None, _error(
            "AMBIGUOUS_NODE_MATCH",
            "Multiple Enumerait skill nodes matched; reconciliation refused.",
            {"skill_key": key, "node_candidates": _node_candidates(matched)},
        ), False
    if matched:
        recovered = str(frontmatter.get("nodeId") or "") != str(matched[0].get("id") or "")
        return matched[0], None, recovered
    if not create_if_missing:
        return None, _error("NODE_NOT_FOUND", "No mapped Enumerait node found.", {"skill_key": key}), False

    skill_md = skill_dir / "SKILL.md"
    local_fm, body = _parse_skill_doc(skill_md.read_text(encoding="utf-8"))
    try:
        node = client.create_skill_node(
            parent,
            title,
            _summary(local_fm),
            body,
            {"hermes_skill_key": key},
            _mapping_metadata(skill_dir),
        )
    except Exception as exc:
        return None, _error("ENUMERAIT_WRITE_FAILED", str(exc), {"skill_key": key, "action": "create_node"}), False
    return node, None, True


def _canonical_manage(*, action: str, name: str, task_id: str | None, session_id: str | None, **kwargs: Any) -> dict:
    from tools.skill_manager_tool import skill_manage

    raw = skill_manage(
        action=action,
        name=name,
        content=kwargs.get("content"),
        category=kwargs.get("category"),
        file_path=kwargs.get("file_path"),
        file_content=kwargs.get("file_content"),
        old_string=kwargs.get("old_string"),
        new_string=kwargs.get("new_string"),
        replace_all=kwargs.get("replace_all", False),
        absorbed_into=kwargs.get("absorbed_into"),
        task_id=task_id,
        session_id=session_id,
    )
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"success": False, "error": str(raw)}
    return parsed if isinstance(parsed, dict) else {"success": False, "error": str(parsed)}


def _canonical_view(name: str, file_path: str | None, task_id: str | None, session_id: str | None, preprocess: bool) -> str:
    from tools.skills_tool import _skill_view_with_bump, skill_view

    if preprocess:
        return _skill_view_with_bump(
            {"name": name, "file_path": file_path},
            task_id=task_id,
            session_id=session_id,
        )
    return skill_view(name, file_path=file_path, task_id=task_id, preprocess=False)


def _guarded_replace_skill(skill_dir: Path, content: str, *, task_id: str | None, session_id: str | None) -> dict:
    return _canonical_manage(
        action="edit",
        name=skill_dir.name,
        content=content,
        task_id=task_id,
        session_id=session_id,
    )


def _ensure_mapping(node: dict, skill_dir: Path) -> None:
    desired_external = {**(node.get("external_ids") or {}), "hermes_skill_key": _skill_key(skill_dir)}
    desired_metadata = _mapping_metadata(skill_dir)
    node_metadata = node.get("metadata") or {}
    if (
        node.get("node_type") != "skill"
        or node.get("parent_id") != _parent_id()
        or node.get("external_ids") != desired_external
        or any((node.get(key) or node_metadata.get(key)) != value for key, value in desired_metadata.items())
    ):
        _get_enumerait_client().update_skill_node_large(
            node["id"],
            title=skill_dir.name,
            external_ids=desired_external,
            metadata=desired_metadata,
        )


def _reconcile_node_wins(skill_dir: Path, node: dict, *, task_id: str | None, session_id: str | None) -> dict:
    skill_md = skill_dir / "SKILL.md"
    local_fm, local_body = _parse_skill_doc(skill_md.read_text(encoding="utf-8"))
    node_summary = node.get("summary") or {}
    if isinstance(node_summary, str):
        try:
            node_summary = yaml.safe_load(node_summary) or {}
        except yaml.YAMLError:
            node_summary = {"description": node_summary}
    if not isinstance(node_summary, dict):
        node_summary = {}
    node_body = str(node.get("content") or "")
    desired = _compose_skill_doc(node_summary, node_body, node.get("id"))
    changed = _summary(local_fm) != node_summary or local_body != node_body or str(local_fm.get("nodeId") or "") != str(node.get("id") or "")
    if changed:
        guarded = _guarded_replace_skill(skill_dir, desired, task_id=task_id, session_id=session_id)
        if guarded.get("staged"):
            guarded["enumerait_sync"] = {"status": "pending_local_approval"}
            return guarded
        if not guarded.get("success"):
            return _error(
                "VALIDATION_FAILED",
                str(guarded.get("error") or "Guarded Hermes reconciliation refused"),
                {"name": skill_dir.name, "local_mutation": guarded},
            )
    try:
        _ensure_mapping(node, skill_dir)
    except Exception as exc:
        return _error("ENUMERAIT_WRITE_FAILED", str(exc), {"name": skill_dir.name, "action": "repair_mapping"})
    return {"eligible": True, "node_id": node.get("id"), "reconciled": changed}


def _sync_local_to_node(skill_dir: Path, node: dict, action: str, *, task_id: str | None, session_id: str | None) -> dict:
    skill_md = skill_dir / "SKILL.md"
    frontmatter, body = _parse_skill_doc(skill_md.read_text(encoding="utf-8"))
    key = _skill_key(skill_dir)
    try:
        _get_enumerait_client().update_skill_node_large(
            node["id"],
            title=skill_dir.name,
            summary=_summary(frontmatter),
            content=body,
            external_ids={**(node.get("external_ids") or {}), "hermes_skill_key": key},
            metadata=_mapping_metadata(skill_dir),
        )
    except Exception as exc:
        return _error("ENUMERAIT_WRITE_FAILED", str(exc), {"skill_key": key, "action": action})
    if str(frontmatter.get("nodeId") or "") != str(node.get("id") or ""):
        guarded = _guarded_replace_skill(
            skill_dir,
            _compose_skill_doc(_summary(frontmatter), body, node.get("id")),
            task_id=task_id,
            session_id=session_id,
        )
        if guarded.get("staged"):
            guarded["enumerait_sync"] = {"status": "pending_local_approval"}
            return guarded
        if not guarded.get("success"):
            return _error(
                "VALIDATION_FAILED",
                str(guarded.get("error") or "Guarded nodeId update refused"),
                {"skill_key": key, "action": "record_node_mapping"},
            )
    return {"eligible": True, "node_id": node.get("id"), "converged": True, "action": action}


def _ineligible(base_json: str, reason: str) -> str:
    try:
        data = json.loads(base_json)
    except (TypeError, json.JSONDecodeError):
        data = {"success": False, "error": str(base_json)}
    data["sync_skipped_reason"] = reason
    return json.dumps(data, ensure_ascii=False)


def skills_list2(category: str = None, task_id: str = None) -> str:
    from tools.skills_tool import skills_list

    raw = skills_list(category=category, task_id=task_id)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if isinstance(data, dict):
        data["enumerait_sync"] = {
            "enabled": bool(_parent_id()),
            "root_node_id": _parent_id(),
            "eligibility": "Only skills under $HERMES_HOME/skills synchronize; plugin and external skills are skipped.",
        }
    return json.dumps(data, ensure_ascii=False)


def skill_view2(
    name: str,
    file_path: str = None,
    task_id: str = None,
    session_id: str = None,
    preprocess: bool = True,
) -> str:
    skill_dir, reason = _resolved_local_skill(name)
    if reason in {"plugin_skill", "external_skill"}:
        return _ineligible(
            _canonical_view(name, file_path, task_id, session_id, preprocess),
            reason,
        )
    if skill_dir is None:
        return json.dumps(_error("SKILL_FILE_NOT_FOUND", f"Skill '{name}' not found.", {"name": name}), ensure_ascii=False)
    if file_path:
        raw = _canonical_view(name, file_path, task_id, session_id, preprocess)
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
        data["sync"] = {"eligible": True, "file_path": file_path, "reconciled": False}
        return json.dumps(data, ensure_ascii=False)

    skill_md = skill_dir / "SKILL.md"
    try:
        frontmatter, _ = _parse_skill_doc(skill_md.read_text(encoding="utf-8"))
        if frontmatter.get("nodeId") and not cfg_get(_get_config(), "skills", "config", "enumerait_reconcile_on_read"):
            data = json.loads(_canonical_view(name, None, task_id, session_id, preprocess))
            data["sync"] = {
                "eligible": True,
                "node_id": str(frontmatter["nodeId"]),
                "reconciled": False,
                "checked_remote": False,
                "reason": "cached_node_mapping_fast_path",
            }
            return json.dumps(data, ensure_ascii=False)
        node, err, recovered = _resolve_node(skill_dir, frontmatter, create_if_missing=True)
        if err:
            return json.dumps(err, ensure_ascii=False)
        sync = _reconcile_node_wins(skill_dir, node, task_id=task_id, session_id=session_id)
        if sync.get("staged"):
            return json.dumps(sync, ensure_ascii=False)
        if sync.get("success") is False:
            return json.dumps(sync, ensure_ascii=False)
        sync["recovered_node_id"] = recovered
        data = json.loads(_canonical_view(name, None, task_id, session_id, preprocess))
        data["sync"] = sync
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(_error("VALIDATION_FAILED", str(exc), {"name": name, "path": str(skill_md)}), ensure_ascii=False)


def skill_manage2(
    action: str,
    name: str,
    content: str = None,
    category: str = None,
    file_path: str = None,
    file_content: str = None,
    old_string: str = None,
    new_string: str = None,
    replace_all: bool = False,
    absorbed_into: str = None,
    dry_run: bool = False,
    task_id: str = None,
    session_id: str = None,
) -> str:
    if dry_run:
        return json.dumps(
            {
                "success": True,
                "dry_run": True,
                "action": action,
                "name": name,
                "message": "Dry run only; no local skill or Enumerait node was modified.",
            },
            ensure_ascii=False,
        )

    before_dir, before_reason = _resolved_local_skill(name)
    if before_reason in {"plugin_skill", "external_skill"}:
        result = _canonical_manage(
            action=action,
            name=name,
            content=content,
            category=category,
            file_path=file_path,
            file_content=file_content,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            absorbed_into=absorbed_into,
            task_id=task_id,
            session_id=session_id,
        )
        result["sync_skipped_reason"] = before_reason
        return json.dumps(result, ensure_ascii=False)

    # Use the target's canonical write-approval staging mechanism, but mark
    # eligible v2 payloads so an approved replay returns through this wrapper
    # and completes the Enumerait half of the transaction.  The canonical
    # bypass is entered only by apply_skill_pending after approval.
    from tools.skill_manager_tool import _apply_skill_write_gate

    gate_result = _apply_skill_write_gate(
        action,
        name,
        content=content,
        category=category,
        file_path=file_path,
        file_content=file_content,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
        absorbed_into=absorbed_into,
        _enumerait_v2=True,
    )
    if gate_result is not None:
        try:
            gated = json.loads(gate_result)
        except (TypeError, json.JSONDecodeError):
            gated = {"success": False, "error": str(gate_result)}
        if gated.get("staged"):
            gated["enumerait_sync"] = {"status": "pending_local_approval"}
        return json.dumps(gated, ensure_ascii=False)

    node_to_delete = None
    if before_dir is not None and action == "delete":
        fm, _ = _parse_skill_doc((before_dir / "SKILL.md").read_text(encoding="utf-8"))
        node_to_delete, _, _ = _resolve_node(before_dir, fm, create_if_missing=False)

    result = _canonical_manage(
        action=action,
        name=name,
        content=content,
        category=category,
        file_path=file_path,
        file_content=file_content,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
        absorbed_into=absorbed_into,
        task_id=task_id,
        session_id=session_id,
    )
    if result.get("staged"):
        result["enumerait_sync"] = {"status": "pending_local_approval"}
        return json.dumps(result, ensure_ascii=False)
    if not result.get("success"):
        if any(result.get(key) for key in ("staged", "pending_approval", "approval_required")):
            result["enumerait_sync"] = {"status": "pending_local_approval"}
            return json.dumps(result, ensure_ascii=False)
        message = str(result.get("error") or "Guarded Hermes operation failed")
        return json.dumps(_error("VALIDATION_FAILED", message, {"action": action, "name": name, "local_result": result}), ensure_ascii=False)

    if action == "delete":
        try:
            if node_to_delete:
                _get_enumerait_client().delete_node(node_to_delete["id"])
            result["sync"] = {"eligible": before_dir is not None, "deleted_node": bool(node_to_delete)}
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps(_error("PARTIAL_COMMIT_DETECTED", f"Local delete succeeded but Enumerait delete failed: {exc}", {"action": action, "name": name}), ensure_ascii=False)

    skill_dir, reason = _resolved_local_skill(name)
    if skill_dir is None and action == "create":
        expected = _skills_root() / category / name if category else _skills_root() / name
        if expected.is_dir():
            skill_dir = expected
    if skill_dir is None:
        return json.dumps(_error("SKILL_FILE_NOT_FOUND", f"Local operation succeeded but skill '{name}' could not be resolved for sync.", {"action": action}), ensure_ascii=False)

    frontmatter, _ = _parse_skill_doc((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    node, err, _ = _resolve_node(skill_dir, frontmatter, create_if_missing=True)
    if err:
        return json.dumps(_error("PARTIAL_COMMIT_DETECTED", "Local skill write succeeded but Enumerait node resolution failed.", {"action": action, "name": name, "sync_error": err.get("error")}), ensure_ascii=False)
    sync = _sync_local_to_node(skill_dir, node, action, task_id=task_id, session_id=session_id)
    if sync.get("staged"):
        sync["local_result"] = result
        return json.dumps(sync, ensure_ascii=False)
    if sync.get("success") is False:
        return json.dumps(_error("PARTIAL_COMMIT_DETECTED", "Local skill write succeeded but Enumerait synchronization failed.", {"action": action, "name": name, "sync_error": sync.get("error")}), ensure_ascii=False)
    result["sync"] = sync
    return json.dumps(result, ensure_ascii=False)


from tools.skill_manager_tool import SKILL_MANAGE_SCHEMA
from tools.skills_tool import SKILLS_LIST_SCHEMA, SKILL_VIEW_SCHEMA, check_skills_requirements

SKILLS_LIST2_SCHEMA = copy.deepcopy(SKILLS_LIST_SCHEMA)
SKILLS_LIST2_SCHEMA["name"] = "skills_list2"
SKILLS_LIST2_SCHEMA["description"] = SKILLS_LIST2_SCHEMA["description"].replace("skill_view(name)", "skill_view2(name)") + " Includes Enumerait synchronization eligibility."

SKILL_VIEW2_SCHEMA = copy.deepcopy(SKILL_VIEW_SCHEMA)
SKILL_VIEW2_SCHEMA["name"] = "skill_view2"
SKILL_VIEW2_SCHEMA["description"] += " Eligible local skills reconcile from their authoritative Enumerait node before being returned."
SKILL_VIEW2_SCHEMA["parameters"]["properties"]["name"]["description"] = SKILL_VIEW2_SCHEMA["parameters"]["properties"]["name"]["description"].replace("skills_list", "skills_list2")

SKILL_MANAGE2_SCHEMA = copy.deepcopy(SKILL_MANAGE_SCHEMA)
SKILL_MANAGE2_SCHEMA["name"] = "skill_manage2"
SKILL_MANAGE2_SCHEMA["description"] = SKILL_MANAGE2_SCHEMA["description"].replace("skill_view()", "skill_view2()") + " Local changes use the canonical guarded skill manager, then synchronize to Enumerait."
SKILL_MANAGE2_SCHEMA["parameters"]["properties"]["dry_run"] = {
    "type": "boolean",
    "description": "Report the requested action without changing local files or Enumerait nodes.",
}

registry.register(
    name="skills_list2",
    toolset="skills_v2",
    schema=SKILLS_LIST2_SCHEMA,
    handler=lambda args, **kw: skills_list2(category=args.get("category"), task_id=kw.get("task_id")),
    check_fn=check_skills_requirements,
    emoji="📚",
)
registry.register(
    name="skill_view2",
    toolset="skills_v2",
    schema=SKILL_VIEW2_SCHEMA,
    handler=lambda args, **kw: skill_view2(
        name=args.get("name", ""),
        file_path=args.get("file_path"),
        task_id=kw.get("task_id"),
        session_id=kw.get("session_id"),
    ),
    check_fn=check_skills_requirements,
    emoji="📚",
)
registry.register(
    name="skill_manage2",
    toolset="skills_v2",
    schema=SKILL_MANAGE2_SCHEMA,
    handler=lambda args, **kw: skill_manage2(
        action=args.get("action", ""),
        name=args.get("name", ""),
        content=args.get("content"),
        category=args.get("category"),
        file_path=args.get("file_path"),
        file_content=args.get("file_content"),
        old_string=args.get("old_string"),
        new_string=args.get("new_string"),
        replace_all=args.get("replace_all", False),
        absorbed_into=args.get("absorbed_into"),
        dry_run=args.get("dry_run", False),
        task_id=kw.get("task_id"),
        session_id=kw.get("session_id"),
    ),
    check_fn=check_skills_requirements,
    emoji="📝",
)
