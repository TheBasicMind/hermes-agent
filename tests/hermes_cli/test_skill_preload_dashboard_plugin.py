"""Static compatibility contract for the skill-preload dashboard plugin."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugins" / "skill-preload" / "dashboard"


def test_plugin_uses_current_sdk_and_existing_skill_content_route() -> None:
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    source = (PLUGIN / manifest["entry"]).read_text(encoding="utf-8")

    assert manifest["name"] == "skill-preload"
    assert "window.__HERMES_PLUGIN_SDK__" in source
    assert "SDK.fetchJSON" in source
    assert '"/api/skills/preload"' in source
    assert '"/api/skills/preload/toggle"' in source
    assert '"/api/skills/content?name="' in source
    assert "/api/skills/\" + encodeURIComponent" not in source
