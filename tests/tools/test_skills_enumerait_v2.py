"""Contracts for the Enumerait-backed v2 skill tool overlay."""

from __future__ import annotations

import json
from pathlib import Path


def _skill_text(body: str = "local body", node_id: str | None = None) -> str:
    node = f"nodeId: {node_id}\n" if node_id else ""
    return f"---\nname: demo\ndescription: Demo skill.\n{node}---\n\n{body}\n"


class FakeEnumerait:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])
        self.updates = []
        self.deletes = []

    def get_node(self, node_id):
        return next((n for n in self.nodes if n.get("id") == node_id), None)

    def find_by_title(self, title, parent_id):
        return [n for n in self.nodes if n.get("title") == title]

    def create_skill_node(self, parent_id, title, summary, content, external_ids, metadata):
        node = {
            "id": "node-new",
            "parent_id": parent_id,
            "node_type": "skill",
            "title": title,
            "summary": summary,
            "content": content,
            "external_ids": external_ids,
            "metadata": metadata,
        }
        self.nodes.append(node)
        return node

    def update_skill_node_large(self, node_id, **fields):
        self.updates.append((node_id, fields))
        node = self.get_node(node_id) or {"id": node_id}
        node.update(fields)
        return node

    def delete_node(self, node_id):
        self.deletes.append(node_id)


def test_v2_tools_use_distinct_names_and_current_canonical_schemas():
    import tools.skills_enumerait_v2 as v2
    from tools.skill_manager_tool import SKILL_MANAGE_SCHEMA
    from tools.skills_tool import SKILLS_LIST_SCHEMA, SKILL_VIEW_SCHEMA

    assert v2.SKILLS_LIST2_SCHEMA["name"] == "skills_list2"
    assert v2.SKILL_VIEW2_SCHEMA["name"] == "skill_view2"
    assert v2.SKILL_MANAGE2_SCHEMA["name"] == "skill_manage2"
    assert set(SKILLS_LIST_SCHEMA["parameters"]["properties"]) <= set(
        v2.SKILLS_LIST2_SCHEMA["parameters"]["properties"]
    )
    assert set(SKILL_VIEW_SCHEMA["parameters"]["properties"]) <= set(
        v2.SKILL_VIEW2_SCHEMA["parameters"]["properties"]
    )
    assert set(SKILL_MANAGE_SCHEMA["parameters"]["properties"]) <= set(
        v2.SKILL_MANAGE2_SCHEMA["parameters"]["properties"]
    )
    assert "dry_run" in v2.SKILL_MANAGE2_SCHEMA["parameters"]["properties"]


def test_composed_node_content_preserves_markdown_indentation():
    import tools.skills_enumerait_v2 as v2

    rendered = v2._compose_skill_doc(
        {"name": "demo", "description": "Demo skill."},
        "\n  indented literal\n",
        "node-1",
    )

    assert "\n\n  indented literal\n" in rendered


def test_skill_document_body_round_trips_without_separator_drift():
    import tools.skills_enumerait_v2 as v2

    rendered = v2._compose_skill_doc(
        {"name": "demo", "description": "Demo skill."},
        "remote body\n",
        "node-1",
    )

    _, body = v2._parse_skill_doc(rendered)
    assert body == "remote body\n"


def test_manage2_routes_local_mutation_through_canonical_guarded_manager(
    tmp_path, monkeypatch
):
    import tools.skills_enumerait_v2 as v2
    import tools.skill_manager_tool as manager

    home = tmp_path / ".hermes"
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_text(node_id="node-1"))
    calls = []

    def guarded_gate(action, name, **kwargs):
        calls.append((action, name, kwargs))
        return json.dumps(
            {
                "success": True,
                "staged": True,
                "pending_id": "pending-1",
                "message": "approval required",
            }
        )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(manager, "_apply_skill_write_gate", guarded_gate)
    monkeypatch.setattr(
        manager,
        "skill_manage",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("staged mutation must not reach skill_manage")
        ),
    )
    monkeypatch.setattr(v2, "_get_config", lambda: {"skills": {"config": {"enumerait_skill_node": "root"}}})

    result = json.loads(
        v2.skill_manage2(
            "edit",
            "demo",
            content=_skill_text("changed", "node-1"),
            task_id="task-1",
            session_id="session-1",
        )
    )

    assert result == {
        "success": True,
        "staged": True,
        "pending_id": "pending-1",
        "message": "approval required",
        "enumerait_sync": {"status": "pending_local_approval"},
    }
    assert len(calls) == 1
    assert calls[0][0:2] == ("edit", "demo")
    assert calls[0][2]["_enumerait_v2"] is True
    assert (skill_dir / "SKILL.md").read_text() == _skill_text(node_id="node-1")


def test_approved_pending_v2_replay_returns_to_v2_under_canonical_bypass(monkeypatch):
    import tools.skill_manager_tool as manager
    import tools.skills_enumerait_v2 as v2

    captured = {}

    def replay(**kwargs):
        captured.update(kwargs)
        captured["bypass"] = manager._skill_gate_bypass.get()
        return json.dumps({"success": True, "sync": {"converged": True}})

    monkeypatch.setattr(v2, "skill_manage2", replay)

    result = json.loads(
        manager.apply_skill_pending(
            {
                "_enumerait_v2": True,
                "action": "edit",
                "name": "demo",
                "content": _skill_text("approved"),
            }
        )
    )

    assert result["sync"]["converged"] is True
    assert captured["action"] == "edit"
    assert captured["name"] == "demo"
    assert captured["bypass"] is True
    assert "_enumerait_v2" not in captured


def test_view2_node_authority_reconciles_via_guarded_manager(tmp_path, monkeypatch):
    import tools.skills_enumerait_v2 as v2
    import tools.skill_manager_tool as manager
    import tools.skills_tool as skills_tool

    home = tmp_path / ".hermes"
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_skill_text("local body", "node-1"))
    client = FakeEnumerait(
        [{
            "id": "node-1",
            "parent_id": "root",
            "node_type": "skill",
            "title": "demo",
            "summary": {"name": "demo", "description": "Demo skill."},
            "content": "remote body\n",
            "external_ids": {"hermes_skill_key": "demo"},
            "metadata": {},
        }]
    )
    calls = []

    def guarded_manage(*args, **kwargs):
        calls.append((args, kwargs))
        skill_md.write_text(kwargs["content"])
        return json.dumps({"success": True})

    def canonical_view(args, **kwargs):
        return json.dumps({"success": True, "name": "demo", "content": skill_md.read_text()})

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(v2, "_get_config", lambda: {"skills": {"config": {"enumerait_skill_node": "root", "enumerait_reconcile_on_read": True}}})
    monkeypatch.setattr(v2, "_get_enumerait_client", lambda: client)
    monkeypatch.setattr(manager, "skill_manage", guarded_manage)
    monkeypatch.setattr(skills_tool, "_skill_view_with_bump", canonical_view)

    result = json.loads(v2.skill_view2("demo", task_id="task-1", session_id="session-1"))

    assert result["success"] is True
    assert result["sync"]["reconciled"] is True
    assert "remote body" in result["content"]
    assert len(calls) == 1
    assert calls[0][1]["action"] == "edit"
    assert calls[0][1]["task_id"] == "task-1"
    assert calls[0][1]["session_id"] == "session-1"


def test_view2_reports_pending_approval_without_claiming_reconciliation(
    tmp_path, monkeypatch
):
    import tools.skills_enumerait_v2 as v2
    import tools.skill_manager_tool as manager

    home = tmp_path / ".hermes"
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_skill_text("local body", "node-1"))
    client = FakeEnumerait(
        [{
            "id": "node-1",
            "parent_id": "root",
            "node_type": "skill",
            "title": "demo",
            "summary": {"name": "demo", "description": "Demo skill."},
            "content": "remote body\n",
            "external_ids": {"hermes_skill_key": "demo"},
            "metadata": {},
        }]
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(v2, "_get_config", lambda: {"skills": {"config": {"enumerait_skill_node": "root", "enumerait_reconcile_on_read": True}}})
    monkeypatch.setattr(v2, "_get_enumerait_client", lambda: client)
    monkeypatch.setattr(
        manager,
        "skill_manage",
        lambda **kwargs: json.dumps(
            {"success": True, "staged": True, "pending_id": "pending-node-wins"}
        ),
    )

    result = json.loads(v2.skill_view2("demo"))

    assert result["staged"] is True
    assert result["pending_id"] == "pending-node-wins"
    assert result["enumerait_sync"] == {"status": "pending_local_approval"}
    assert skill_md.read_text() == _skill_text("local body", "node-1")


def test_view2_fails_closed_on_ambiguous_title_match(tmp_path, monkeypatch):
    import tools.skills_enumerait_v2 as v2

    home = tmp_path / ".hermes"
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_skill_text())
    client = FakeEnumerait(
        [
            {"id": "a", "title": "demo", "parent_id": "root", "external_ids": {}},
            {"id": "b", "title": "demo", "parent_id": "root", "external_ids": {}},
        ]
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(v2, "_get_config", lambda: {"skills": {"config": {"enumerait_skill_node": "root", "enumerait_reconcile_on_read": True}}})
    monkeypatch.setattr(v2, "_get_enumerait_client", lambda: client)

    result = json.loads(v2.skill_view2("demo"))

    assert result["success"] is False
    assert result["error"]["code"] == "AMBIGUOUS_NODE_MATCH"
    assert {n["id"] for n in result["error"]["context"]["node_candidates"]} == {"a", "b"}
    assert (skill_dir / "SKILL.md").read_text() == _skill_text()


def test_manage2_sync_failure_is_explicit_partial_commit(tmp_path, monkeypatch):
    import tools.skills_enumerait_v2 as v2
    import tools.skill_manager_tool as manager

    home = tmp_path / ".hermes"
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(_skill_text(node_id="node-1"))

    def canonical_manage(*args, **kwargs):
        skill_md.write_text(kwargs["content"])
        return json.dumps({"success": True})

    class BrokenClient(FakeEnumerait):
        def update_skill_node_large(self, node_id, **fields):
            raise RuntimeError("Enumerait unavailable")

    client = BrokenClient([{"id": "node-1", "title": "demo", "parent_id": "root"}])
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(v2, "_get_config", lambda: {"skills": {"config": {"enumerait_skill_node": "root"}}})
    monkeypatch.setattr(v2, "_get_enumerait_client", lambda: client)
    monkeypatch.setattr(manager, "skill_manage", canonical_manage)

    result = json.loads(v2.skill_manage2("edit", "demo", content=_skill_text("changed", "node-1")))

    assert result["success"] is False
    assert result["error"]["code"] == "PARTIAL_COMMIT_DETECTED"
    assert "Enumerait unavailable" in json.dumps(result)


def test_dry_run_never_calls_local_or_remote_mutators(monkeypatch):
    import tools.skills_enumerait_v2 as v2
    import tools.skill_manager_tool as manager

    monkeypatch.setattr(manager, "skill_manage", lambda *a, **k: (_ for _ in ()).throw(AssertionError("local mutation")))
    monkeypatch.setattr(v2, "_get_enumerait_client", lambda: (_ for _ in ()).throw(AssertionError("remote mutation")))

    result = json.loads(v2.skill_manage2("edit", "demo", content=_skill_text(), dry_run=True))

    assert result == {
        "success": True,
        "dry_run": True,
        "action": "edit",
        "name": "demo",
        "message": "Dry run only; no local skill or Enumerait node was modified.",
    }
