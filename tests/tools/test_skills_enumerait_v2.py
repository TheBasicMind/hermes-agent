import json
from pathlib import Path
from unittest.mock import patch

import yaml

from tools import skills_enumerait_v2 as v2


def _content(name="demo", description="Local description", body="Local body", node_id=None):
    fm = {"name": name, "description": description}
    if node_id:
        fm["nodeId"] = node_id
    return "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# Demo\n\n" + body + "\n"


def _make_skill(root: Path, name="demo", category=None, **kw):
    d = root / category / name if category else root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(_content(name=name, **kw), encoding="utf-8")
    return d


class FakeEnumerait:
    def __init__(self, fail_writes=False):
        self.nodes = {}
        self.calls = []
        self.fail_writes = fail_writes
        self.next_id = 1

    def find_by_external_id(self, key, parent_id):
        return [n for n in self.nodes.values() if n.get("parent_id") == parent_id and n.get("external_ids", {}).get("hermes_skill_key") == key]

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def find_by_title(self, title, parent_id):
        return [n for n in self.nodes.values() if n.get("parent_id") == parent_id and n.get("title") == title]

    def create_skill_node(self, parent_id, title, summary, content, external_ids, metadata):
        if self.fail_writes:
            raise RuntimeError("boom")
        node_id = f"node-{self.next_id}"
        self.next_id += 1
        self.nodes[node_id] = {
            "id": node_id,
            "parent_id": parent_id,
            "node_type": "skill",
            "title": title,
            "summary": summary,
            "content": content,
            "external_ids": external_ids,
            "metadata": metadata,
        }
        self.calls.append(("create", node_id))
        return self.nodes[node_id]

    def update_skill_node_large(self, node_id, *, title=None, summary=None, content=None, external_ids=None, metadata=None):
        self.calls.extend([("open", node_id), ("edit", node_id), ("save", node_id), ("close", node_id)])
        if self.fail_writes:
            raise RuntimeError("boom")
        n = self.nodes[node_id]
        if title is not None: n["title"] = title
        if summary is not None: n["summary"] = summary
        if content is not None: n["content"] = content
        if external_ids is not None: n["external_ids"] = external_ids
        if metadata is not None: n["metadata"] = metadata
        return n

    def delete_node(self, node_id):
        if self.fail_writes:
            raise RuntimeError("boom")
        self.nodes.pop(node_id, None)
        self.calls.append(("delete", node_id))


def _patch_env(tmp_path, fake, external_dirs=None):
    skills = tmp_path / "skills"
    skills.mkdir()
    cfg = {"skills": {"config": {"enumerait_skill_node": "root"}, "external_dirs": [str(p) for p in (external_dirs or [])]}}
    return patch.multiple(
        v2,
        get_hermes_home=lambda: tmp_path,
        _get_config=lambda: cfg,
        _get_enumerait_client=lambda: fake,
    ), skills


def test_view2_creates_node_for_eligible_skill_and_persists_node_id(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo", category="cat")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        result = json.loads(v2.skill_view2("cat/demo"))
    assert result["success"] is True
    assert result["sync"]["eligible"] is True
    assert result["sync"]["reconciled"] is True
    node = next(iter(fake.nodes.values()))
    assert node["parent_id"] == "root"
    assert node["node_type"] == "skill"
    assert node["external_ids"]["hermes_skill_key"] == "cat/demo"
    assert node["metadata"]["skill_relative_path"] == "cat/demo"
    assert "nodeId: " + node["id"] in (skills / "cat" / "demo" / "SKILL.md").read_text()


def test_view2_node_wins_and_uses_large_node_update_path_for_metadata(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    d = _make_skill(skills, "demo", description="Local", body="Local")
    fake.nodes["node-9"] = {"id":"node-9","parent_id":"root","node_type":"skill","title":"demo","summary":{"name":"demo","description":"Remote"},"content":"# Demo\n\nRemote body\n","external_ids":{"hermes_skill_key":"demo"},"metadata":{}}
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        result = json.loads(v2.skill_view2("demo"))
    assert result["sync"]["reconciled"] is True
    text = (d / "SKILL.md").read_text()
    assert "description: Remote" in text
    assert "Remote body" in text
    assert ("open", "node-9") in fake.calls
    assert ("save", "node-9") in fake.calls


def test_plugin_skill_is_not_synced(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    with env, patch("tools.skills_tool.skill_view", return_value=json.dumps({"success": True, "content": "plugin"})):
        result = json.loads(v2.skill_view2("plugin:demo"))
    assert result["success"] is True
    assert result["sync_skipped_reason"] == "plugin_skill"
    assert fake.nodes == {}


def test_external_dir_skill_is_not_synced(tmp_path):
    fake = FakeEnumerait()
    external = tmp_path / "external"
    external.mkdir()
    _make_skill(external, "demo")
    env, skills = _patch_env(tmp_path, fake, [external])
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills), patch("agent.skill_utils.get_external_skills_dirs", return_value=[external]), patch("agent.skill_utils.get_all_skills_dirs", return_value=[skills, external]):
        result = json.loads(v2.skill_view2("demo"))
    assert result["success"] is True
    assert result["sync_skipped_reason"] == "external_skill"
    assert fake.nodes == {}


def test_ambiguous_title_fallback_fails_closed(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo")
    for node_id in ["a", "b"]:
        fake.nodes[node_id] = {"id":node_id,"parent_id":"root","node_type":"skill","title":"demo","summary":{},"content":"","external_ids":{},"metadata":{}}
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        result = json.loads(v2.skill_view2("demo"))
    assert result["success"] is False
    assert result["error"]["code"] == "AMBIGUOUS_NODE_MATCH"


def test_list2_reports_enumerait_sync_root(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        result = json.loads(v2.skills_list2())

    assert result["success"] is True
    assert result["enumerait_sync"]["enabled"] is True
    assert result["enumerait_sync"]["root_node_id"] == "root"


def test_registry_client_uses_live_enumerait_tool_names_and_call_shapes(monkeypatch):
    calls = []

    class Entry:
        def __init__(self, name):
            self.name = name
            self.handler = self._handler

        def _handler(self, args):
            calls.append((self.name, args))
            if self.name == "mcp_enumerait_read_node":
                return json.dumps({"success": True, "result": {"mind_map_id": "map-1", "node": {"id": args["node_id"], "mapGUID": "map-1", "parents": ["root"]}}})
            if self.name == "mcp_enumerait_search_nodes_by_title_scoped":
                return json.dumps({"success": True, "result": {"nodes": []}})
            if self.name == "mcp_enumerait_upsert_node":
                return json.dumps({"success": True, "result": {"node": {"id": "node-1", "mapGUID": args["mind_map_id"], "parents": [args.get("parent_id")], "title": args.get("title")}}})
            if self.name == "mcp_enumerait_delete_node":
                return json.dumps({"success": True, "result": {"success": True}})
            raise AssertionError(f"Unexpected tool call: {self.name}")

    def fake_get_entry(name):
        assert name in {
            "mcp_enumerait_read_node",
            "mcp_enumerait_search_nodes_by_title_scoped",
            "mcp_enumerait_upsert_node",
            "mcp_enumerait_delete_node",
        }
        return Entry(name)

    cfg = {"skills": {"config": {"enumerait_skill_node": "root"}}}
    monkeypatch.setattr(v2, "_get_config", lambda: cfg)
    monkeypatch.setattr(v2.registry, "get_entry", fake_get_entry)

    client = v2.RegistryEnumeraitClient()
    assert client.find_by_title("demo", "root") == []
    created = client.create_skill_node("root", "demo", {"name": "demo"}, "body", {"hermes_skill_key": "demo"}, {"skill_relative_path": "demo"})
    client.delete_node(created["id"])

    called_names = [name for name, _args in calls]
    assert "mcp_enumerait_read_node" in called_names
    assert "mcp_enumerait_search_nodes_by_title_scoped" in called_names
    assert "mcp_enumerait_upsert_node" in called_names
    assert "mcp_enumerait_delete_node" in called_names
    assert not any("find_nodes_by_external_id" in name or "find_child_nodes_by_title" in name or "create_node" in name for name in called_names)
    search_args = next(args for name, args in calls if name == "mcp_enumerait_search_nodes_by_title_scoped")
    assert search_args == {
        "mind_map_id": "map-1",
        "root_node_id": "root",
        "query": "demo",
        "exact_match": True,
        "case_sensitive": False,
        "node_type": "skill",
        "status": "active",
        "limit": 20,
    }


def test_skill_view2_second_read_with_mapped_node_avoids_remote_update(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        first = json.loads(v2.skill_view2("demo"))
        fake.calls.clear()
        second = json.loads(v2.skill_view2("demo"))
    assert first["success"] is True
    assert second["success"] is True
    assert not [call for call in fake.calls if call[0] in {"open", "edit", "save", "close"}]


def test_skills_v2_toolset_is_configurable():
    from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS
    from toolsets import _HERMES_CORE_TOOLS, resolve_toolset, validate_toolset

    assert any(key == "skills_v2" for key, _, _ in CONFIGURABLE_TOOLSETS)
    assert validate_toolset("skills_v2") is True
    expected = {"skills_list2", "skill_view2", "skill_manage2"}
    assert set(resolve_toolset("skills_v2")) == expected
    for tool_name in expected:
        assert tool_name in _HERMES_CORE_TOOLS


def test_skills_v2_toolset_exposes_v2_tool_schemas(tmp_path):
    from model_tools import get_tool_definitions

    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)

    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        names = {
            schema["function"]["name"]
            for schema in get_tool_definitions(enabled_toolsets=["skills_v2"], quiet_mode=True)
        }

    assert {"skills_list2", "skill_view2", "skill_manage2"}.issubset(names)
    assert {"skills_list", "skill_view", "skill_manage"}.isdisjoint(names)


def test_manage2_create_and_delete_converge_with_node(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        created = json.loads(v2.skill_manage2("create", "demo", content=_content("demo", body="Created")))
        assert created["success"] is True
        assert fake.nodes
        deleted = json.loads(v2.skill_manage2("delete", "demo"))
    assert deleted["success"] is True
    assert not (skills / "demo").exists()
    assert fake.nodes == {}


def test_manage2_create_with_category_syncs_created_skill_dir(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        created = json.loads(v2.skill_manage2("create", "demo", content=_content("demo", body="Created"), category="cat"))
        deleted = json.loads(v2.skill_manage2("delete", "cat/demo"))
    assert created["success"] is True
    assert created["sync"]["node_id"]
    assert deleted["success"] is True
    assert deleted["sync"]["deleted_node"] is True


def test_manage2_linked_file_write_and_remove_keep_node_mapped(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        json.loads(v2.skill_view2("demo"))
        written = json.loads(v2.skill_manage2("write_file", "demo", file_path="references/a.md", file_content="hello"))
        removed = json.loads(v2.skill_manage2("remove_file", "demo", file_path="references/a.md"))
    assert written["success"] is True
    assert removed["success"] is True
    node = next(iter(fake.nodes.values()))
    assert node["external_ids"]["hermes_skill_key"] == "demo"


def test_manage2_patch_updates_file_and_node_large_edit(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo", body="old body")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        json.loads(v2.skill_view2("demo"))
        result = json.loads(v2.skill_manage2("patch", "demo", old_string="old body", new_string="new body"))
    assert result["success"] is True
    node = next(iter(fake.nodes.values()))
    assert "new body" in node["content"]
    assert ("open", node["id"]) in fake.calls


def test_manage2_enumerait_failure_returns_partial_commit_repair_payload(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    _make_skill(skills, "demo", body="old body")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        json.loads(v2.skill_view2("demo"))
        fake.fail_writes = True
        result = json.loads(v2.skill_manage2("patch", "demo", old_string="old body", new_string="new body"))
    assert result["success"] is False
    assert result["error"]["code"] in {"PARTIAL_COMMIT_DETECTED", "ENUMERAIT_WRITE_FAILED"}
    assert result["error"]["repair_instructions"]


def test_manage2_dry_run_does_not_modify_file_or_node(tmp_path):
    fake = FakeEnumerait()
    env, skills = _patch_env(tmp_path, fake)
    d = _make_skill(skills, "demo", body="old body")
    with env, patch("tools.skills_tool.SKILLS_DIR", skills), patch("tools.skill_manager_tool.SKILLS_DIR", skills):
        result = json.loads(v2.skill_manage2("patch", "demo", old_string="old body", new_string="new body", dry_run=True))
    assert result["success"] is True
    assert result["dry_run"] is True
    assert "old body" in (d / "SKILL.md").read_text()
    assert fake.nodes == {}
