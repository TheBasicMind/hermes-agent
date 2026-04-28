# Brief for Claude Code — Enumerait MCP New Tools for Synthesizer Phase 1

## Context
We are refactoring the `synthesizer` skill into progressive phases. In phase 1:
- `node_dossier` is used for objective/intents context on node `5c6eecb8-d6cb-462f-89c8-6dbd7cbee54d`
- filed-content retrieval should use **new dedicated tools** (not `node_dossier` refactor)

We validated current MCP surface via `hermes mcp test enumerait`.

## Current relevant tools available
- `node_dossier`
- `read_node`
- `get_node_children`
- `search_nodes_by_tags`
- `list_tags`
- `update_node_tags`

This is a strong base, but phase-1 synthesis needs deterministic subtree/recent/high-value retrieval in fewer calls.

---

## Requested enhancements (priority order)

## P0 — New tool: `list_recent_nodes_under_subtree`
Dedicated feed-style retrieval for filed content.

### Input
- `mind_map_id`
- `root_node_id`
- `max_depth` (nullable)
- `from_date` / `to_date` (ISO)
- `tags_any` / `tags_all`
- `exclude_archived`
- `sort_by` (`updated_at` default; optional `weighted_score`)
- `sort_order` (`desc` default)
- `cursor`, `limit`

### Output
- list entries: `node_id`, `title`, `updated_at`, `tags`, `weighted_score`, `snippet`, `parent_path`
- `next_cursor`

### Why
Phase 1b needs recent + high-value + subtree-scoped retrieval as a deterministic stream.

---

## P0 — New tool: `list_high_value_nodes_under_subtree`
Focused retrieval for resurfacing durable/high-leverage content.

### Input
- `mind_map_id`
- `root_node_id`
- `max_depth`
- `min_weighted_score`
- `tags_any` / `tags_all`
- `from_date` / `to_date` (optional)
- `exclude_archived`
- `cursor`, `limit`

### Output
- list entries: `node_id`, `title`, `weighted_score`, `updated_at`, `tags`, `snippet`, `parent_path`
- `next_cursor`

### Why
Separates “high-value resurfacing” from generic recent-feed retrieval.

---

## P1 — New tool: `read_nodes_batch`
Batch detail retrieval to avoid N x `read_node` loops.

### Input
- `mind_map_id`
- `node_ids: string[]`
- `include_content: boolean` (default true)

### Output
- ordered results with node metadata/content by ID

---

## Note on synthesis helper fields
`themes[]`, `claims[]`, `signal_strength`, and `novelty_hint` will be computed in the Synthesizer phase-1 pipeline, not in Enumerait MCP.

---

## Contract / acceptance criteria
1. **Separation of concerns:** `node_dossier` remains objective/intents tool; filed retrieval handled by new tools.
2. **Deterministic paging:** Stable `next_cursor` behavior over large sets.
3. **Filter correctness:** Date windows, tags, archive exclusion, and score filters are reliable.
4. **Performance:** First page latency practical for synthesis runs (<3s target, <6s acceptable).
5. **Schema clarity:** Tool docs include exact request/response contracts and examples.

---

## Example desired call shape (illustrative)
```json
{
  "tool": "list_recent_nodes_under_subtree",
  "mind_map_id": "<id>",
  "root_node_id": "5c6eecb8-d6cb-462f-89c8-6dbd7cbee54d",
  "max_depth": 4,
  "from_date": "2026-04-01T00:00:00Z",
  "exclude_archived": true,
  "tags_any": ["filed", "high-value"],
  "sort_by": "updated_at",
  "sort_order": "desc",
  "limit": 100
}
```

---

## Why this matters now
The synthesizer is moving to phase-based scheduled runs. Without dedicated retrieval tools for filed content, phase 1b stays fragile and expensive. With these tools, phase 1 becomes deterministic, efficient, and easier to evolve.