"""
Workflow subcommand for hermes CLI.

Thin adapter over `tools.workflow_tools.workflow(...)` exposing:

    hermes workflow list [--json]
    hermes workflow show <name> [--json]
    hermes workflow validate <name> [--json]
    hermes workflow run <name>
    hermes workflow runs [<name>] [--json]
    hermes workflow status <run_id> [--json]
    hermes workflow logs <run_id> [--step <step_id>] [--json]
    hermes workflow cancel <run_id>
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.colors import Colors, color


def _workflow_api(**kwargs):
    """Call tools.workflow_tools.workflow(...) and surface exceptions."""
    from tools.workflow_tools import workflow as workflow_tool
    return workflow_tool(**kwargs)


def _print_json(data):
    print(json.dumps(data, default=str))


def _truncate(text, limit=400):
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def workflow_list(args):
    try:
        result = _workflow_api(verb="list")
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0

    workflows = result.get("workflows", [])
    if not workflows:
        print(color("No workflows.", Colors.DIM))
        return 0

    for wf in workflows:
        name = wf.get("name", "?")
        if wf.get("valid"):
            steps = wf.get("steps") or []
            tag = color("[valid]", Colors.GREEN)
            step_str = ", ".join(steps)
            print(f"{name} {tag} ({len(steps)} steps): {step_str}")
        else:
            tag = color("[invalid]", Colors.RED)
            err = wf.get("error", "?")
            print(f"{name} {tag}: {err}")
    return 0


def workflow_show(args):
    try:
        result = _workflow_api(verb="show", name=args.name)
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0

    wf = result.get("workflow", {})
    print(color(f"name: {wf.get('name', '?')}", Colors.CYAN))
    trigger = wf.get("trigger", {})
    print(f"trigger: {trigger}")
    topo = wf.get("topo_order") or []
    print(f"topo_order: {topo}")
    print("steps:")
    for s in wf.get("steps", []) or []:
        sid = s.get("id", "?")
        pkg = s.get("package", "?")
        needs = s.get("needs") or []
        needs_str = f" needs={needs}" if needs else ""
        print(f"  - {sid}  package={pkg}{needs_str}")
    return 0


def workflow_validate(args):
    try:
        result = _workflow_api(verb="validate", name=args.name)
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"ok": False, "error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0 if result.get("ok") else 1

    if result.get("ok"):
        print(color(f"OK: topo_order={result.get('topo_order', [])}", Colors.GREEN))
        return 0

    print(color(f"ERROR: {result.get('error', 'invalid')}", Colors.RED))
    return 1


def workflow_run(args):
    try:
        result = _workflow_api(verb="run", name=args.name)
    except Exception as exc:
        print(color(f"ERROR: {exc}", Colors.RED))
        return 1
    print(color(f"started: {result.get('run_id', '?')}", Colors.GREEN))
    return 0


def workflow_runs(args):
    try:
        result = _workflow_api(verb="runs", name=getattr(args, "name", None))
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0

    runs = result.get("runs", []) or []
    if not runs:
        print(color("No runs.", Colors.DIM))
        return 0

    for r in runs:
        rid = r.get("run_id", "?")
        status = r.get("status", "?")
        triggered_by = r.get("triggered_by", "?")
        triggered_at = r.get("triggered_at", "?")
        print(f"{rid}  {status}  {triggered_by}  {triggered_at}")
    return 0


def workflow_status(args):
    try:
        result = _workflow_api(verb="status", run_id=args.run_id)
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0

    run = result.get("run") or {}
    if not run:
        print(color(f"Run not found: {args.run_id}", Colors.RED))
        return 1

    print(color(f"run: {run.get('run_id', '?')}", Colors.CYAN))
    print(f"  workflow:      {run.get('workflow_name', '?')}")
    print(f"  status:        {run.get('status', '?')}")
    print(f"  triggered_by:  {run.get('triggered_by', '?')}")
    print(f"  triggered_at:  {run.get('triggered_at', '?')}")
    if run.get("finished_at"):
        print(f"  finished_at:   {run['finished_at']}")
    print("steps:")
    for s in result.get("steps", []) or []:
        sid = s.get("step_id", "?")
        st = s.get("status", "?")
        attempt = s.get("attempt", "")
        attempt_str = f" attempt={attempt}" if attempt else ""
        print(f"  - {sid}  {st}{attempt_str}")
    return 0


def workflow_logs(args):
    try:
        result = _workflow_api(
            verb="logs",
            run_id=args.run_id,
            step=getattr(args, "step", None),
        )
    except Exception as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(color(f"ERROR: {exc}", Colors.RED))
        return 1

    if getattr(args, "json", False):
        _print_json(result)
        return 0

    # Single-step logs
    if "step" in result and getattr(args, "step", None):
        s = result.get("step") or {}
        if not s:
            print(color(f"Step not found: {args.step}", Colors.RED))
            return 1
        sid = s.get("step_id", args.step)
        st = s.get("status", "?")
        print(color(f"{sid}  [{st}]", Colors.CYAN))
        if s.get("last_error"):
            print(color(f"error: {_truncate(s['last_error'])}", Colors.RED))
        if s.get("result") is not None:
            print(f"result: {_truncate(s['result'])}")
        return 0

    steps = result.get("steps", []) or []
    if not steps:
        print(color("No steps.", Colors.DIM))
        return 0

    for s in steps:
        sid = s.get("step_id", "?")
        st = s.get("status", "?")
        print(color(f"{sid}  [{st}]", Colors.CYAN))
        if s.get("last_error"):
            print(color(f"  error: {_truncate(s['last_error'])}", Colors.RED))
        if s.get("result") is not None:
            print(f"  result: {_truncate(s['result'])}")
    return 0


def workflow_cancel(args):
    try:
        result = _workflow_api(verb="cancel", run_id=args.run_id)
    except Exception as exc:
        print(color(f"ERROR: {exc}", Colors.RED))
        return 1
    print(color(f"cancelled: {result.get('cancelled', args.run_id)}", Colors.GREEN))
    return 0


def workflow_command(args):
    """Dispatch `hermes workflow ...` subcommands."""
    subcmd = getattr(args, "workflow_command", None)

    if subcmd is None or subcmd == "list":
        return workflow_list(args)
    if subcmd == "show":
        return workflow_show(args)
    if subcmd == "validate":
        return workflow_validate(args)
    if subcmd == "run":
        return workflow_run(args)
    if subcmd == "runs":
        return workflow_runs(args)
    if subcmd == "status":
        return workflow_status(args)
    if subcmd == "logs":
        return workflow_logs(args)
    if subcmd == "cancel":
        return workflow_cancel(args)

    print(f"Unknown workflow command: {subcmd}")
    print("Usage: hermes workflow [list|show|validate|run|runs|status|logs|cancel]")
    return 1
