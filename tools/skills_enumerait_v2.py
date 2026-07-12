#!/usr/bin/env python3
"""Enumerait-synced v2 skill tools.

These tools intentionally use new names (skills_list2, skill_view2,
skill_manage2) so they do not shadow Hermes' built-in skills_list,
skill_view, or skill_manage registrations.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from hermes_constants import get_hermes_home
from hermes_cli.config import cfg_get, load_config
from tools.registry import registry, tool_error
from tools.skills_tool import SKILLS_LIST_SCHEMA, SKILL_VIEW_SCHEMA
from tools.skill_manager_tool import SKILL_MANAGE_SCHEMA

logger = logging.getLogger(__name__)

ERROR_REPAIR = {
    "AMBIGUOUS_NODE_MATCH": [
        "Inspect the listed candidate nodes under skills.config.enumerait_skill_node.",
        "Keep exactly one node for the skill key/title or set a unique external_ids.hermes_skill_key.",
        "Retry the v2 skill operation after ambiguity is resolved.",
    ],
    "NODE_NOT_FOUND": [
        "Verify skills.config.enumerait_skill_node points to an existing Enumerait parent node.",
        "Run skill_view2 on the local skill to auto-provision if the operation allows it.",
    ],
    "SKILL_FILE_NOT_FOUND": ["Verify the skill exists under $HERMES_HOME/skills or use skills_list2."],
    "ALREADY_EXISTS": ["Use a different skill name/category or edit the existing skill."],
    "PATCH_TARGET_NOT_FOUND": ["Read the current target file and retry with an exact/anchored patch string."],
    "ENUMERAIT_WRITE_FAILED": [
        "Check Enumerait MCP/server connectivity and credentials.",
        "Retry the same v2 operation after Enumerait write access is restored.",
    ],
    "FILE_WRITE_FAILED": ["Check filesystem permissions and available disk space, then retry."],
    "PARTIAL_COMMIT_DETECTED": [
        "Do not assume skill and Enumerait are synchronized.",
        "Compare the local SKILL.md with the mapped Enumerait node.",
        "Repair either side manually or retry skill_view2 to reconcile node-wins where appropriate.",
    ],
    "VALIDATION_FAILED": ["Fix the input payload and retry."],
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
            "repair_instructions": ERROR_REPAIR.get(code, ["Inspect context and retry safely."]),
        },
    }


def _parent_id() -> Optional[str]:
    cfg = _get_config()
    return cfg_get(cfg, "skills", "config", "enumerait_skill_node")


def _skills_root() -> Path:
    return get_hermes_home() / "skills"


def _parse_skill_doc(text: str) -> Tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end].strip()
    body_start = text.find("\n", end + 4)
    body = text[body_start + 1 :] if body_start != -1 else ""
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, body


def _compose_skill_doc(frontmatter: dict, body: str, node_id: Optional[str] = None) -> str:
    fm = copy.deepcopy(frontmatter) if isinstance(frontmatter, dict) else {}
    if node_id:
        fm["nodeId"] = node_id
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = body if body.startswith("\n") else "\n" + body
    return f"---\n{dumped}\n---\n{body}"


def _summary_from_fm(fm: dict) -> dict:
    summary = copy.deepcopy(fm) if isinstance(fm, dict) else {}
    summary.pop("nodeId", None)
    return summary


def _resolved_local_skill(name: str) -> Tuple[Optional[Path], Optional[str]]:
    if ":" in (name or ""):
        return None, "plugin_skill"
    root = _skills_root().resolve()
    # Direct path first: category/name or name.
    direct = _skills_root() / name
    if direct.is_dir() and (direct / "SKILL.md").exists():
        try:
            direct.resolve().relative_to(root)
        except Exception:
            return direct, "external_skill"
        return direct, None
    # Fall back to built-in resolver, which may return external dirs.
    from tools.skill_manager_tool import _find_skill

    found = _find_skill(Path(name).name)
    if not found:
        return None, "not_found"
    skill_dir = Path(found["path"])
    try:
        skill_dir.resolve().relative_to(root)
    except Exception:
        return skill_dir, "external_skill"
    return skill_dir, None


def _skill_key(skill_dir: Path) -> str:
    rel = skill_dir.resolve().relative_to(_skills_root().resolve())
    return rel.as_posix()


def _node_candidates_payload(nodes: list[dict]) -> list[dict]:
    return [
        {
            "id": n.get("id"),
            "title": n.get("title"),
            "external_ids": n.get("external_ids"),
            "metadata": n.get("metadata"),
        }
        for n in nodes
    ]


class RegistryEnumeraitClient:
    """Small adapter over configured/registered Enumerait MCP tools.

    Tool names are intentionally configurable because Enumerait MCP naming can
    vary by server alias. Tests patch _get_enumerait_client with a fake.
    """

    def __init__(self) -> None:
        cfg = _get_config()
        tool_cfg = cfg_get(cfg, "skills", "config", "enumerait_tools") or {}
        self.tools = {
            "search_by_title": tool_cfg.get("search_by_title", tool_cfg.get("find_by_title", "mcp_enumerait_search_nodes_by_title_scoped")),
            "read_node": tool_cfg.get("read_node", tool_cfg.get("get_node", "mcp_enumerait_read_node")),
            "upsert_node": tool_cfg.get("upsert_node", tool_cfg.get("create", "mcp_enumerait_upsert_node")),
            "delete_node": tool_cfg.get("delete_node", tool_cfg.get("delete", "mcp_enumerait_delete_node")),
        }
        self._mind_map_cache: dict[str, str] = {}

    def _call(self, logical: str, args: dict) -> Any:
        entry = registry.get_entry(self.tools[logical])
        if not entry:
            raise RuntimeError(f"Enumerait tool '{self.tools[logical]}' is not registered")
        raw = entry.handler(args)
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict) and parsed.get("error"):
            raise RuntimeError(parsed["error"])
        if isinstance(parsed, dict) and parsed.get("success") is False:
            raise RuntimeError(parsed.get("error") or parsed)
        result = parsed.get("result", parsed) if isinstance(parsed, dict) else parsed
        if isinstance(result, str):
            try:
                nested = json.loads(result)
                if isinstance(nested, dict) and nested.get("error"):
                    raise RuntimeError(nested["error"])
                if isinstance(nested, dict) and nested.get("success") is False:
                    raise RuntimeError(nested.get("error") or nested)
                return nested.get("result", nested) if isinstance(nested, dict) else nested
            except json.JSONDecodeError:
                return result
        return result

    @staticmethod
    def _normalize_node_payload(payload: Any) -> Optional[dict]:
        if not isinstance(payload, dict):
            return None
        node = payload.get("node") if isinstance(payload.get("node"), dict) else payload
        if not isinstance(node, dict):
            return None
        if "id" not in node and "node_id" in node:
            node = {**node, "id": node.get("node_id")}
        if "parent_id" not in node and isinstance(node.get("parents"), list) and node.get("parents"):
            node = {**node, "parent_id": node["parents"][0]}
        if "mapGUID" not in node and payload.get("mind_map_id"):
            node = {**node, "mapGUID": payload.get("mind_map_id")}
        if "node_type" not in node and node.get("type"):
            node = {**node, "node_type": node.get("type")}
        return node

    def _mind_map_id(self, parent_id: str) -> str:
        if parent_id not in self._mind_map_cache:
            root_payload = self._call("read_node", {"node_id": parent_id, "content_mode": "full"})
            root = self._normalize_node_payload(root_payload) or {}
            mind_map_id = root_payload.get("mind_map_id") if isinstance(root_payload, dict) else None
            mind_map_id = mind_map_id or root.get("mapGUID")
            if not mind_map_id:
                raise RuntimeError(f"Could not infer Enumerait mind_map_id from skill parent node '{parent_id}'")
            self._mind_map_cache[parent_id] = mind_map_id
        return self._mind_map_cache[parent_id]

    def get_node(self, node_id: str) -> Optional[dict]:
        res = self._call("read_node", {"node_id": node_id, "content_mode": "full"})
        return self._normalize_node_payload(res)

    def find_by_title(self, title: str, parent_id: str) -> list[dict]:
        res = self._call(
            "search_by_title",
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
        raw_nodes = res if isinstance(res, list) else res.get("nodes", []) if isinstance(res, dict) else []
        return [n for n in (self._normalize_node_payload(n) for n in raw_nodes) if n]

    def create_skill_node(self, parent_id: str, title: str, summary: dict, content: str, external_ids: dict, metadata: dict) -> dict:
        res = self._call(
            "upsert_node",
            {
                "mind_map_id": self._mind_map_id(parent_id),
                "parent_id": parent_id,
                "node_type": "skill",
                "title": title,
                "summary": summary if isinstance(summary, str) else yaml.safe_dump(summary, sort_keys=False).strip(),
                "content": content,
                "external_ids": external_ids,
                **metadata,
                "source_system": "hermes-skill",
            },
        )
        node = self._normalize_node_payload(res)
        if not node:
            raise RuntimeError(f"Enumerait upsert_node returned no node: {res}")
        return node

    def update_skill_node_large(self, node_id: str, **fields: Any) -> dict:
        current = self.get_node(node_id) or {"id": node_id}
        parent_id = current.get("parent_id") or _parent_id()
        payload = {"mind_map_id": self._mind_map_id(parent_id), "node_id": node_id}
        for key, value in fields.items():
            if key == "metadata" and isinstance(value, dict):
                payload.update(value)
            elif key == "summary" and isinstance(value, dict):
                payload[key] = yaml.safe_dump(value, sort_keys=False).strip()
            else:
                payload[key] = value
        updated = self._call("upsert_node", payload)
        return self._normalize_node_payload(updated) or self.get_node(node_id) or current

    def delete_node(self, node_id: str) -> None:
        node = self.get_node(node_id) or {}
        parent_id = node.get("parent_id") or _parent_id()
        self._call("delete_node", {"mind_map_id": self._mind_map_id(parent_id), "node_id": node_id})


def _get_enumerait_client():
    return RegistryEnumeraitClient()


def _metadata(skill_dir: Path, skill_md: Path, key: str) -> dict:
    return {
        "skill_dir_abs_path": str(skill_dir.resolve()),
        "skill_file_abs_path": str(skill_md.resolve()),
        "skill_relative_path": key,
    }


def _resolve_node(skill_dir: Path, fm: dict, *, create_if_missing: bool = True) -> Tuple[Optional[dict], Optional[dict], bool]:
    parent = _parent_id()
    if not parent:
        return None, _error("VALIDATION_FAILED", "Missing skills.config.enumerait_skill_node", {"skill": str(skill_dir)}), False
    client = _get_enumerait_client()
    skill_md = skill_dir / "SKILL.md"
    key = _skill_key(skill_dir)
    title = skill_dir.name
    recovered = False

    def one(nodes: list[dict], step: str):
        if len(nodes) > 1:
            return None, _error("AMBIGUOUS_NODE_MATCH", f"Multiple Enumerait skill nodes matched at {step}.", {"skill_key": key, "node_candidates": _node_candidates_payload(nodes), "step": step})
        return (nodes[0], None) if nodes else (None, None)

    node_id = fm.get("nodeId")
    if node_id:
        direct = client.get_node(str(node_id))
        if direct and direct.get("parent_id") == parent:
            return direct, None, recovered

    candidates = client.find_by_title(title, parent)
    external_matches = [n for n in candidates if (n.get("external_ids") or {}).get("hermes_skill_key") == key]
    node, err = one(external_matches or candidates, "title")
    if err or node:
        if node and fm.get("nodeId") != node.get("id"):
            _write_skill_doc(skill_md, _compose_skill_doc(_summary_from_fm(fm), (skill_md.read_text(encoding="utf-8").split("---", 2)[-1] if False else _parse_skill_doc(skill_md.read_text(encoding="utf-8"))[1]), node.get("id")))
            recovered = True
        return node, err, recovered

    if not create_if_missing:
        return None, _error("NODE_NOT_FOUND", "No mapped Enumerait node found.", {"skill_key": key, "path": str(skill_md)}), recovered

    text = skill_md.read_text(encoding="utf-8")
    local_fm, body = _parse_skill_doc(text)
    try:
        node = client.create_skill_node(parent, title, _summary_from_fm(local_fm), body, {"hermes_skill_key": key}, _metadata(skill_dir, skill_md, key))
    except Exception as exc:
        return None, _error("ENUMERAIT_WRITE_FAILED", str(exc), {"skill_key": key, "path": str(skill_md), "action": "create_node"}), recovered
    _write_skill_doc(skill_md, _compose_skill_doc(_summary_from_fm(local_fm), body, node.get("id")))
    return node, None, True


def _write_skill_doc(path: Path, content: str) -> None:
    from tools.skill_manager_tool import _atomic_write_text

    _atomic_write_text(path, content)


def _ensure_node_mapping_fields(node: dict, skill_dir: Path) -> None:
    client = _get_enumerait_client()
    skill_md = skill_dir / "SKILL.md"
    key = _skill_key(skill_dir)
    desired_external = {**(node.get("external_ids") or {}), "hermes_skill_key": key}
    desired_meta = _metadata(skill_dir, skill_md, key)
    node_meta = node.get("metadata") or {}
    if node.get("node_type") != "skill" or node.get("parent_id") != _parent_id() or desired_external != node.get("external_ids") or any((node.get(k) or node_meta.get(k)) != v for k, v in desired_meta.items()):
        client.update_skill_node_large(node["id"], title=skill_dir.name, external_ids=desired_external, metadata=desired_meta)


def _reconcile_node_wins(skill_dir: Path, node: dict) -> dict:
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    local_fm, local_body = _parse_skill_doc(text)
    node_summary = node.get("summary") or {}
    if isinstance(node_summary, str):
        try:
            node_summary = yaml.safe_load(node_summary) or {}
        except Exception:
            node_summary = {"description": node_summary}
    node_body = node.get("content") or ""
    desired_text = _compose_skill_doc(node_summary, node_body, node.get("id"))
    reconciled = False
    if _summary_from_fm(local_fm) != node_summary or local_body != node_body or local_fm.get("nodeId") != node.get("id"):
        _write_skill_doc(skill_md, desired_text)
        reconciled = True
    _ensure_node_mapping_fields(node, skill_dir)
    return {"eligible": True, "node_id": node.get("id"), "reconciled": reconciled}


def _sync_local_to_node(skill_dir: Path, node: dict, action: str) -> dict:
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fm, body = _parse_skill_doc(text)
    fm_no_id = _summary_from_fm(fm)
    client = _get_enumerait_client()
    key = _skill_key(skill_dir)
    try:
        updated = client.update_skill_node_large(
            node["id"],
            title=skill_dir.name,
            summary=fm_no_id,
            content=body,
            external_ids={**(node.get("external_ids") or {}), "hermes_skill_key": key},
            metadata=_metadata(skill_dir, skill_md, key),
        )
        if fm.get("nodeId") != node.get("id"):
            _write_skill_doc(skill_md, _compose_skill_doc(fm_no_id, body, node.get("id")))
        return {"eligible": True, "node_id": node.get("id"), "converged": True, "action": action}
    except Exception as exc:
        return _error("ENUMERAIT_WRITE_FAILED", str(exc), {"skill_key": key, "path": str(skill_md), "action": action})


def _sync_result_for_ineligible(base_json: str, reason: str) -> str:
    try:
        data = json.loads(base_json)
    except Exception:
        data = {"success": False, "error": base_json}
    data["sync_skipped_reason"] = reason
    return json.dumps(data, ensure_ascii=False)


def skills_list2(category: str = None, task_id: str = None) -> str:
    from tools.skills_tool import skills_list

    raw = skills_list(category=category, task_id=task_id)
    try:
        data = json.loads(raw)
        data["enumerait_sync"] = {
            "enabled": bool(_parent_id()),
            "root_node_id": _parent_id(),
            "eligibility": "Only skills physically under $HERMES_HOME/skills are synchronized; plugin and external_dirs skills are skipped.",
        }
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return raw


def skill_view2(name: str, file_path: str = None, task_id: str = None, preprocess: bool = True) -> str:
    if ":" in (name or ""):
        from tools.skills_tool import skill_view
        return _sync_result_for_ineligible(skill_view(name, file_path=file_path, task_id=task_id, preprocess=preprocess), "plugin_skill")

    skill_dir, reason = _resolved_local_skill(name)
    if reason == "external_skill":
        from tools.skills_tool import skill_view
        return _sync_result_for_ineligible(skill_view(name, file_path=file_path, task_id=task_id, preprocess=preprocess), "external_skill")
    if not skill_dir:
        return json.dumps(_error("SKILL_FILE_NOT_FOUND", f"Skill '{name}' not found.", {"name": name}), ensure_ascii=False)

    if file_path:
        from tools.skills_tool import skill_view
        data = json.loads(skill_view(name, file_path=file_path, task_id=task_id, preprocess=preprocess))
        data["sync"] = {"eligible": True, "file_path": file_path, "reconciled": False}
        return json.dumps(data, ensure_ascii=False)

    skill_md = skill_dir / "SKILL.md"
    try:
        fm, _ = _parse_skill_doc(skill_md.read_text(encoding="utf-8"))
        if fm.get("nodeId") and not cfg_get(_get_config(), "skills", "config", "enumerait_reconcile_on_read"):
            from tools.skills_tool import skill_view
            data = json.loads(skill_view(name, file_path=file_path, task_id=task_id, preprocess=preprocess))
            data["sync"] = {
                "eligible": True,
                "node_id": str(fm.get("nodeId")),
                "reconciled": False,
                "checked_remote": False,
                "reason": "cached_node_mapping_fast_path",
            }
            return json.dumps(data, ensure_ascii=False)
        node, err, recovered = _resolve_node(skill_dir, fm, create_if_missing=True)
        if err:
            return json.dumps(err, ensure_ascii=False)
        sync = _reconcile_node_wins(skill_dir, node)
        sync["recovered_node_id"] = recovered
        if recovered:
            sync["reconciled"] = True
        from tools.skills_tool import skill_view
        data = json.loads(skill_view(name, file_path=file_path, task_id=task_id, preprocess=preprocess))
        data["sync"] = sync
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(_error("FILE_WRITE_FAILED", str(exc), {"name": name, "path": str(skill_md)}), ensure_ascii=False)


def _dry_run_manage(action: str, name: str, **kwargs) -> str:
    return json.dumps({"success": True, "dry_run": True, "action": action, "name": name, "message": "Dry run only; no local skill or Enumerait node was modified."}, ensure_ascii=False)


def _eligible_local_manage(action: str, skill_dir: Path, *, content: str = None, file_path: str = None, file_content: str = None, old_string: str = None, new_string: str = None, replace_all: bool = False) -> dict:
    """Apply common eligible-skill file operations without re-resolving roots."""
    from tools.skill_manager_tool import _atomic_write_text, _validate_frontmatter, _validate_file_path, _resolve_skill_target

    skill_md = skill_dir / "SKILL.md"
    if action == "create":
        from tools.skill_manager_tool import _validate_name, _validate_category, _validate_frontmatter, _atomic_write_text
        err = _validate_name(skill_dir.name) or _validate_category(skill_dir.parent.name if skill_dir.parent != _skills_root() else None) or _validate_frontmatter(content or "")
        if err:
            return {"success": False, "error": err}
        if skill_dir.exists():
            return {"success": False, "error": f"A skill named '{skill_dir.name}' already exists at {skill_dir}."}
        skill_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(skill_dir / "SKILL.md", content)
        return {"success": True, "message": f"Skill '{skill_dir.name}' created.", "path": str(skill_dir.relative_to(_skills_root()))}
    if action == "delete":
        import shutil
        if not skill_dir.exists():
            return {"success": False, "error": f"Skill '{skill_dir.name}' not found."}
        shutil.rmtree(skill_dir)
        return {"success": True, "message": f"Skill '{skill_dir.name}' deleted."}
    if action == "edit":
        err = _validate_frontmatter(content or "")
        if err:
            return {"success": False, "error": err}
        _atomic_write_text(skill_md, content)
        return {"success": True, "message": f"Skill '{skill_dir.name}' updated.", "path": str(skill_dir)}
    if action == "patch":
        target = skill_md
        if file_path:
            err = _validate_file_path(file_path)
            if err:
                return {"success": False, "error": err}
            target, err = _resolve_skill_target(skill_dir, file_path)
            if err:
                return {"success": False, "error": err}
        if not target.exists():
            return {"success": False, "error": f"File not found: {target.relative_to(skill_dir)}"}
        from tools.fuzzy_match import fuzzy_find_and_replace
        text = target.read_text(encoding="utf-8")
        new_text, count, _strategy, match_error = fuzzy_find_and_replace(text, old_string, new_string, replace_all)
        if match_error:
            return {"success": False, "error": match_error, "file_preview": text[:500]}
        if not file_path:
            err = _validate_frontmatter(new_text)
            if err:
                return {"success": False, "error": f"Patch would break SKILL.md structure: {err}"}
        _atomic_write_text(target, new_text)
        return {"success": True, "message": f"Patched {'SKILL.md' if not file_path else file_path} in skill '{skill_dir.name}' ({count} replacement{'s' if count != 1 else ''})."}
    if action == "write_file":
        err = _validate_file_path(file_path or "")
        if err:
            return {"success": False, "error": err}
        target, err = _resolve_skill_target(skill_dir, file_path)
        if err:
            return {"success": False, "error": err}
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, file_content or "")
        return {"success": True, "message": f"File '{file_path}' written to skill '{skill_dir.name}'.", "path": str(target)}
    if action == "remove_file":
        err = _validate_file_path(file_path or "")
        if err:
            return {"success": False, "error": err}
        target, err = _resolve_skill_target(skill_dir, file_path)
        if err:
            return {"success": False, "error": err}
        if not target.exists():
            return {"success": False, "error": f"File '{file_path}' not found in skill '{skill_dir.name}'."}
        target.unlink()
        return {"success": True, "message": f"File '{file_path}' removed from skill '{skill_dir.name}'."}
    return {"success": False, "error": f"Unsupported direct action '{action}'."}


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
    dry_run: bool = False,
) -> str:
    from tools.skill_manager_tool import skill_manage

    if dry_run:
        return _dry_run_manage(action, name)
    if ":" in (name or ""):
        raw = skill_manage(action, name, content, category, file_path, file_content, old_string, new_string, replace_all)
        return _sync_result_for_ineligible(raw, "plugin_skill")

    before_dir, before_reason = _resolved_local_skill(name)
    if before_reason == "external_skill":
        raw = skill_manage(action, name, content, category, file_path, file_content, old_string, new_string, replace_all)
        return _sync_result_for_ineligible(raw, "external_skill")

    node_to_delete = None
    if before_dir and action == "delete":
        try:
            fm, _body = _parse_skill_doc((before_dir / "SKILL.md").read_text(encoding="utf-8"))
            node_to_delete, _err, _recovered = _resolve_node(before_dir, fm, create_if_missing=False)
        except Exception:
            node_to_delete = None

    if action == "create":
        create_dir = _skills_root() / category / name if category else _skills_root() / name
        result = _eligible_local_manage("create", create_dir, content=content)
    elif before_dir and action in {"edit", "patch", "write_file", "remove_file", "delete"}:
        result = _eligible_local_manage(
            action,
            before_dir,
            content=content,
            file_path=file_path,
            file_content=file_content,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
    else:
        raw = skill_manage(action, name, content, category, file_path, file_content, old_string, new_string, replace_all)
        try:
            result = json.loads(raw)
        except Exception:
            result = {"success": False, "error": raw}
    if not result.get("success"):
        msg = str(result.get("error", ""))
        code = "PATCH_TARGET_NOT_FOUND" if action == "patch" else "VALIDATION_FAILED"
        if "already exists" in msg:
            code = "ALREADY_EXISTS"
        if "not found" in msg.lower():
            code = "SKILL_FILE_NOT_FOUND"
        return json.dumps(_error(code, msg, {"action": action, "name": name, "path": str(before_dir) if before_dir else None}), ensure_ascii=False)

    if action == "delete":
        if before_dir:
            # Best effort delete mapped node captured before the local directory was removed.
            try:
                if node_to_delete:
                    client = _get_enumerait_client()
                    client.delete_node(node_to_delete["id"])
                result["sync"] = {"eligible": True, "deleted_node": bool(node_to_delete)}
            except Exception as exc:
                return json.dumps(_error("PARTIAL_COMMIT_DETECTED", f"Local delete succeeded but Enumerait delete failed: {exc}", {"action": action, "name": name}), ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    if action == "create" and result.get("success"):
        skill_dir = create_dir
    else:
        skill_dir, reason = _resolved_local_skill(name)
    if not skill_dir:
        # create with category may not resolve by bare name if not patched constants? Treat as file failure.
        return json.dumps(_error("SKILL_FILE_NOT_FOUND", f"Local operation succeeded but skill '{name}' could not be resolved for sync.", {"action": action, "name": name}), ensure_ascii=False)
    try:
        fm, _body = _parse_skill_doc((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        node, err, _recovered = _resolve_node(skill_dir, fm, create_if_missing=True)
        if err:
            return json.dumps(err, ensure_ascii=False)
        sync = _sync_local_to_node(skill_dir, node, action)
        if sync.get("success") is False:
            return json.dumps(_error("PARTIAL_COMMIT_DETECTED", "Local skill write succeeded but Enumerait write did not converge.", {"action": action, "name": name, "sync_error": sync.get("error")}), ensure_ascii=False)
        result["sync"] = sync
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps(_error("PARTIAL_COMMIT_DETECTED", f"Local skill write succeeded but sync failed: {exc}", {"action": action, "name": name, "path": str(skill_dir)}), ensure_ascii=False)


SKILLS_LIST2_SCHEMA = copy.deepcopy(SKILLS_LIST_SCHEMA)
SKILLS_LIST2_SCHEMA["name"] = "skills_list2"
SKILLS_LIST2_SCHEMA["description"] = (
    SKILLS_LIST_SCHEMA["description"].replace("skill_view(name)", "skill_view2(name)")
    + " Returns the same interface as skills_list plus Enumerait sync eligibility metadata."
)

SKILL_VIEW2_SCHEMA = copy.deepcopy(SKILL_VIEW_SCHEMA)
SKILL_VIEW2_SCHEMA["name"] = "skill_view2"
SKILL_VIEW2_SCHEMA["description"] = (
    SKILL_VIEW_SCHEMA["description"]
    + " Same interface as skill_view; eligible local skills are bidirectionally synchronized with Enumerait skill nodes, and node content wins on read."
)
SKILL_VIEW2_SCHEMA["parameters"]["properties"]["name"]["description"] = SKILL_VIEW_SCHEMA["parameters"]["properties"]["name"]["description"].replace(
    "skills_list", "skills_list2"
)

SKILL_MANAGE2_SCHEMA = copy.deepcopy(SKILL_MANAGE_SCHEMA)
SKILL_MANAGE2_SCHEMA["name"] = "skill_manage2"
SKILL_MANAGE2_SCHEMA["description"] = (
    SKILL_MANAGE_SCHEMA["description"].replace("skill_view()", "skill_view2()")
    + "\n\nThis v2 variant keeps the same interface and tool-use instructions as skill_manage, then synchronizes eligible local skills with Enumerait. It also supports dry_run for write actions."
)
SKILL_MANAGE2_SCHEMA["parameters"]["properties"]["content"]["description"] = SKILL_MANAGE_SCHEMA["parameters"]["properties"]["content"]["description"].replace(
    "skill_view()", "skill_view2()"
)
SKILL_MANAGE2_SCHEMA["parameters"]["properties"]["dry_run"] = {
    "type": "boolean",
    "description": "Validate/report without changing local files or Enumerait nodes where feasible.",
}

registry.register(
    name="skills_list2",
    toolset="skills",
    schema=SKILLS_LIST2_SCHEMA,
    handler=lambda args, **kw: skills_list2(category=args.get("category"), task_id=kw.get("task_id")),
    emoji="📚",
)
registry.register(
    name="skill_view2",
    toolset="skills",
    schema=SKILL_VIEW2_SCHEMA,
    handler=lambda args, **kw: skill_view2(name=args.get("name", ""), file_path=args.get("file_path"), task_id=kw.get("task_id")),
    emoji="📖",
)
registry.register(
    name="skill_manage2",
    toolset="skills",
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
        dry_run=args.get("dry_run", False),
    ),
    emoji="📝",
)
