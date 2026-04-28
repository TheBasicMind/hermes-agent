# Synthesizer Progressive-Disclosure Refactor Plan

> **For Claude Code / implementation agent:** Execute sequentially. Keep V1 intentionally simple (no speculative orchestration).

## Goal
Refactor the `marketing/synthesizer` skill into a progressive-disclosure architecture with phase-specific docs, vertical-specific phase-2 docs, explicit phase handoff contracts, and Telegram-based run telemetry.

## Scope (V1)
- Refactor skill structure and guidance docs only (plus minimum scheduling/message contracts in skill instructions).
- Add phase 1 objective intake from Enumerait via `node_dossier` on node `5c6eecb8-d6cb-462f-89c8-6dbd7cbee54d`.
- Define storage policy and schema for “synthesized-for-output ideas”.
- Scaffold original-writing skill for later enrichment.

## Non-goals (V1)
- No advanced multi-agent decomposition.
- No auto-retry orchestration beyond simple scheduled phase chaining.
- No deep optimization of scoring models.

---

## Task 1 — Baseline inventory and backup
**Objective:** Snapshot current synthesizer state so migration is reversible.

**Actions**
1. Copy current files under:
   - `/Users/paullancefield/Local_Projects/HermesProject/.hermes-home/skills/marketing/synthesizer/SKILL.md`
   - `/Users/paullancefield/Local_Projects/HermesProject/.hermes-home/skills/marketing/synthesizer/templates/*`
2. Save snapshot as `references/pre-refactor-YYYYMMDD.md` in the same skill folder.

**Done when**
- Snapshot exists and captures old phase guidance.

---

## Task 2 — Enumerait capability check (required)
**Objective:** Validate current Enumerait tool surface and identify what blocks phase 1b efficiency.

**Known available tools (from `hermes mcp test enumerait`)**
- `node_dossier`, `read_node`, `get_node_children`, `search_mind_maps`, `search_nodes_by_tags`, `list_tags`, `update_node_tags`, plus editing/file tools.

**Actions**
1. Document the exact tools relevant for phase 1 retrieval in `references/enumerait-capability-check.md`.
2. Map each phase-1 retrieval need to existing tools vs missing properties.
3. Record enhancement requests required for efficient subtree/recent/high-value retrieval.

**Done when**
- Capability check doc exists and feeds directly into MCP enhancement brief.

---

## Task 3 — Define canonical synthesized-idea schema
**Objective:** Prevent downstream drift by locking one data contract.

**Create**
- `references/synthesized-idea-schema-v1.md`

**Minimum fields**
- `idea_id`, `title`, `thesis`, `objective_tags`, `source_set`, `evidence_refs`,
- `channel_candidates`, `novelty_score`, `leverage_score`, `confidence`,
- `status`, `created_by_phase`, `created_at`, `provenance`, `next_action`.

**Done when**
- Schema can be consumed by phase 2 and phase 3 without translation logic.

---

## Task 4 — Storage policy decision and contract
**Objective:** Codify where synthesized-for-output records live.

**Policy (default)**
- Primary record: market-intel database.
- Mirror pointer/index node: Enumerait mind map.

**Create**
- `references/storage-policy-v1.md`

**Include**
- Why primary in market-intel (scoring/lifecycle/resurfacing).
- Why mirror in Enumerait (navigation/context/human browse).
- Rules for backlinking and dedupe.

**Done when**
- Storage destination is unambiguous for all three phases.

---

## Task 5 — Convert SKILL.md into orchestrator shell
**Objective:** Make top-level skill lightweight and phase-routed.

**Modify**
- `/Users/paullancefield/Local_Projects/HermesProject/.hermes-home/skills/marketing/synthesizer/SKILL.md`

**Changes**
1. Keep only mission, trigger, run envelope, and orchestration rules.
2. Replace heavy phase internals with references to:
   - `references/phase_1.md`
   - `references/phase_2.md`
   - `references/phase_3.md`
3. Add explicit rule: execute only current phase guidance, not full pipeline internals in one context.

**Done when**
- SKILL.md is concise and delegates deep guidance to phase docs.

---

## Task 6 — Author Phase 1 guidance doc
**Objective:** Implement pan-interest juxtaposition workflow.

**Create**
- `references/phase_1.md`

**Must include**
1. Start step: `node_dossier` on `5c6eecb8-d6cb-462f-89c8-6dbd7cbee54d`.
2. Retrieve and juxtapose:
   - newly filed signals,
   - currently high-value evidence,
   - enduring synthesized assets worth resurfacing.
3. Produce `phase_1_dossier` records as synthesized-for-output ideas using schema v1.
4. Mark output as phase-2-ready artifact set.

**Done when**
- A run can produce a dossier without invoking phase-2/3 internals.

---

## Task 7 — Author Phase 2 core + vertical docs
**Objective:** Make phase 2 run as one job per vertical.

**Create**
- `references/phase_2.md`
- `references/phase_2_agentpaul.md`
- `references/phase_2_x.md`
- `references/phase_2_skills_publication.md`

**Must include**
- Scheduler contract: phase 1 schedules one phase-2 run per vertical.
- In each run: read `phase_2.md` + one `phase_2_<vertical>.md`.
- Output format: detailed content proposal(s), linked to phase-1 idea IDs.
- Logging contract: after each proposal, send short log/title to Telegram topic `283`.

**Done when**
- Vertical-specific runs can execute independently and consistently.

---

## Task 8 — Author Phase 3 guidance doc
**Objective:** Consolidate and finalize decisioning.

**Create**
- `references/phase_3.md`

**Must include**
- Input contract: consumes phase-2 proposal artifacts.
- Critical pass + prioritization rubric.
- Final outputs: approved queue + hold/reject rationale.
- Completion notification contract: send summary to Telegram topic `6`.

**Done when**
- Phase 3 can run once all targeted vertical jobs are complete.

---

## Task 9 — Scaffold original-writing skill
**Objective:** Introduce reusable anti-agentic writing style layer.

**Create**
- `/Users/paullancefield/Local_Projects/HermesProject/.hermes-home/skills/marketing/original-writing/SKILL.md`

**V1 scaffold contents**
- Purpose and trigger.
- Tone controls per vertical.
- "Human voice" checklist.
- Minimal rewrite pass protocol.
- Explicit TODO section for future advanced techniques/examples.

**Done when**
- Phase-2 vertical docs can reference this skill cleanly.

---

## Task 10 — Add scheduling + telemetry recipe examples
**Objective:** Make operations reproducible and easy to run.

**Create**
- `references/operations.md`

**Include**
- Example cronjob prompts for phase chaining.
- Delivery targets:
  - logs → `telegram:-1003871243192:283`
  - notify → `telegram:-1003871243192:6`
- Failure handling (simple rerun policy) and idempotency notes.

**Done when**
- Operator can run pipeline end-to-end with minimal interpretation.

---

## Task 11 — Validate skill quality and consistency
**Objective:** Ensure all docs follow house style and don’t regress into monolith guidance.

**Checklist**
- Progressive disclosure achieved (thin orchestrator, detailed phase docs).
- Each phase has explicit inputs/outputs.
- Schema references are consistent.
- Telegram contracts are explicit.
- No contradictory storage guidance.

**Done when**
- Dry-run review passes without ambiguity.

---

## Task 12 — Produce implementation summary
**Objective:** Close with precise deltas for review.

**Deliver**
- List of created/modified files.
- Key behavior changes from old synthesizer.
- Open questions requiring product decision.

**Done when**
- Reviewer can approve or request focused edits quickly.

---

## Open decisions to keep explicit during implementation
1. Exact status lifecycle labels for synthesized-for-output records.
2. Minimum threshold for phase-2 proposal count per vertical.
3. Whether phase 3 waits for all verticals or can run on partial completion in exceptional cases.
