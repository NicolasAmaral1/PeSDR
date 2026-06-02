# FlowEngine FE-01b — Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal end-to-end FlowEngine v2 pipeline: receive inbound → resolve Lead/Talk/State → build layered system prompt → one LLM call returning `TurnDecision` → validate + apply state changes → send response via existing MessagingAdapter → audit. Replaces LangGraph runtime for tenants where `architecture_version=2`; LangGraph stays alive for `=1` (default). No objection treatment, Sentinel runtime, adapter framework, voice, A/B testing, or event emission — those are FE-03+ scope.

**Architecture:** A new module `src/ai_sdr/flowengine/pipeline.py` exposes one async function `run_turn(tenant, inbound_message)` that orchestrates the 12 steps from spec §4. State persistence uses the SQLAlchemy models created in FE-01a (Talk, TalkFlowState, Lead). The main LLM call uses `langchain.chat_models.init_chat_model` plus `.with_structured_output(TurnDecision)`. The Python guardrails validator (regex + whitelist) replaces the critic LLM. `process_lead_inbox` (existing worker job) gets a feature-flag branch: tenants with `architecture_version=2` route to `run_turn`; everything else continues to the current LangGraph runtime. LangGraph itself stays untouched (FE-02 deletes it).

**Tech Stack:** SQLAlchemy 2.0 async, asyncpg, Pydantic v2, LangChain (init_chat_model + with_structured_output + ChatPromptTemplate), pytest + pytest-asyncio, FakeListChatModel for testing.

**Source spec:** `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md` — §4 (pipeline), §6 (layered prompt), §7 (routing), §10 (TreeFlow YAML — minimal parsing only), §11.2 (llm_judge — reserved, not implemented in FE-01b), §15 (critic removal), §21 (cutover).

**Depends on:** FE-01a (schema + Pydantic + repositories must be in place).

**Out of scope for this plan:**
- Objection treatment runtime (FE-03)
- Talks lifecycle enforcement / closure rules (FE-03)
- Human escalation runtime (FE-03)
- Humanization post-processor (FE-03)
- Sentinel heuristic + LLM call (FE-04)
- Adapter framework generalization beyond existing MessagingAdapter (FE-05)
- VoiceAdapter / audio inbound or outbound (FE-05)
- Event bus / event emission (FE-06)
- API surface (FE-06)
- LGPD endpoints / health check endpoint (FE-06)
- A/B testing assignment (FE-07)
- HITL response_reviews runtime (FE-07)
- LangGraph deletion (FE-02) — coexists with FE-01b via feature flag
- TreeFlow YAML v2 features beyond what FE-01b consumes (FE-03+)

---

## File Structure

### Files created

```
src/ai_sdr/flowengine/
  pipeline.py            — run_turn orchestrator (12 steps from spec §4)
  preprocessing.py       — Lead/Talk/State resolution, opt-out detect, advisory lock
  system_prompt.py       — layered builder (cached + fresh)
  llm_client.py          — init_chat_model wrapper + with_structured_output(TurnDecision)
  routing.py             — validate_transition + corrective retry helpers
  post_processing.py     — apply TurnDecision state changes, audit row
  treeflow_loader.py     — minimal YAML v2 parser (persona + current_node + next_nodes only)

src/ai_sdr/guardrails/
  validator.py           — Python regex + whitelist replacing critic LLM

src/ai_sdr/db/
  advisory_lock.py       — per-(tenant, lead) pg_advisory_lock helper

tests/unit/
  test_treeflow_loader_v2.py
  test_system_prompt_builder.py
  test_routing_validate_transition.py
  test_guardrails_validator.py
  test_post_processing_state_apply.py

tests/integration/
  test_pipeline_smoke_end_to_end.py — fake-LLM full turn against FakeMessagingAdapter
  test_pipeline_corrective_retry.py — invalid transition triggers retry
  test_pipeline_guardrails_violation.py — price hallucination → retry → escalate
  test_pipeline_feature_flag_routing.py — architecture_version routes correctly
  test_advisory_lock_serialization.py — two concurrent jobs serialize per lead

tests/fixtures/
  avelum_treeflow_v2.yaml — minimal valid v2 TreeFlow for tests
  avelum_tenant_v2.yaml   — minimal tenant config with architecture_version=2 + sdr_persona
```

### Files modified

```
src/ai_sdr/worker/jobs/inbound.py     — feature flag branch routing v2 to run_turn
src/ai_sdr/tenant_loader/loader.py    — accept architecture_version + sdr_persona (slot)
src/ai_sdr/cli/simulate.py            — `--arch-v2` flag to drive the FlowEngine path
tests/fixtures/avelum/tenant.yaml     — already exists; add architecture_version: 2 toggle
```

### Files NOT modified (sanity)

```
src/ai_sdr/treeflow/                  — LangGraph code stays untouched (FE-02 deletes)
src/ai_sdr/guardrails/critic.py       — kept alive for v1 path (FE-02 deletes)
src/ai_sdr/guardrails/runner.py       — v1 path stays as-is
src/ai_sdr/models/                    — schema is fixed by FE-01a; no further changes
migrations/versions/                  — no new migrations in FE-01b
```

---

## Branch and worktree

Branch this off `dev/nicolas-fe01a-schema` (the FE-01a delivery branch), not `dev/nicolas` directly, so the FE-01b implementation can reference FE-01a's commits. Suggested branch name: `dev/nicolas-fe01b-pipeline`.

When FE-01a is merged into `dev/nicolas`, this branch should be rebased onto the merge commit.

---

## Task List

This plan has 22 tasks across 6 phases. Each task is one focused change (file or two) + tests + commit. Phases inform decomposition but tasks within a phase still execute sequentially.

### Phase 1 — Preprocessing + DB plumbing (4 tasks)

1. **Advisory lock helper** — `db/advisory_lock.py` async context manager wrapping `pg_advisory_xact_lock(hash(tenant_id, lead_id))`. Tests assert two concurrent acquisitions serialize.
2. **Minimal TreeFlow v2 YAML loader** — `flowengine/treeflow_loader.py` parses `sdr_persona`, `entry_node`, and per-node `objetivo`, `collects`, `bridge_instruction`, `next_nodes[].target`, `next_nodes[].condition`. Discards everything else (treatment_mode, on_collected, etc. — FE-03+). Returns dataclasses, NOT Pydantic. Tests cover happy path + error cases (unknown entry_node, malformed node).
3. **PipelineContext + Preprocessing module** — `flowengine/preprocessing.py` carries through: Lead resolution (via LeadRepository.find_by_channel_identifier; create-if-missing path), opt-out detection (regex against `tenant.conversation.opt_out_keywords`), Talk resolution (find_active_for_lead; create-if-missing with entry_node from TreeFlow). Returns `PipelineContext(lead, talk, state)`.
4. **TalkFlowState bootstrap on new Talk** — extend `TalkFlowStateRepository.initialize` callsite to seed `messages` with the inbound message (turn_index=1, role=user, source=lead). Tests verify state is correct after new Talk.

### Phase 2 — System Prompt Builder (3 tasks)

5. **Cached layer builder** — `flowengine/system_prompt.py::build_cached_layer(tenant, treeflow, prompt_cache_id)` returns a Pydantic dataclass with persona + global_objections list (from TreeFlow `global_objections` slot if present, else empty) + operating instructions + escalation guidance + sentinel awareness. Stable per (tenant, treeflow_version). Tests verify deterministic output + Anthropic-compatible structure.
6. **Fresh layer builder** — `build_fresh_layer(context, treeflow)` returns the per-turn dense context: current_node FULL detail + immediate next_nodes DENSE + last 15 messages + time block + (optional) correction context for retries. Tests cover branching nodes, retry context, time block format.
7. **Layered prompt assembler** — `assemble_prompt(cached, fresh)` returns a LangChain `ChatPromptTemplate`-compatible message list using `cache_control: {type: "ephemeral"}` for the cached portion (Anthropic prompt caching). Tests verify cache-control markers + message ordering (system cached → system fresh → user inbound).

### Phase 3 — LLM call + Validation + Guardrails (4 tasks)

8. **LLM client wrapper** — `flowengine/llm_client.py::main_llm_for_tenant(tenant)` returns an `init_chat_model` instance bound to `with_structured_output(TurnDecision)`. Reads `tenant.llm.default` config (provider, model, api_key_ref). Tests use FakeListChatModel returning canned `TurnDecision` JSON.
9. **Python guardrails validator** — `guardrails/validator.py::validate_response_text(text, tenant.guardrails) -> ValidationResult` runs regex against `disallowed_price_pattern` + whitelist check on detected price-like tokens. Returns `ok | violation(detail)`. Replaces the critic LLM (which stays alive for v1 path). Unit tests cover hit, miss, edge cases.
10. **TurnDecision corrective retry on guardrails violation** — `pipeline.py::handle_guardrails_violation` rebuilds the fresh layer with a CORRECTION block describing the violation, re-invokes the LLM (max 1 retry), then either commits or escalates Talk → `requires_review`. Tests cover happy path + 2x violation → escalation.
11. **Cost + token tracking** — extract token usage from the LangChain response (when available) and update `Talk.tokens_consumed` JSONB (`{input, input_cached, output, total_cost_usd}`). Tests verify tokens accumulate across turns.

### Phase 4 — Routing + Transition Validation (2 tasks)

12. **validate_transition function** — `flowengine/routing.py::validate_transition(current_node, next_node_suggestion, collected, treeflow) -> (target, reason)` per spec §7. Returns the resolved next node + None on success; current_node + reason on failure (invalid_target | condition_false | exit_not_satisfied). Pure function, fast unit tests.
13. **Corrective retry on invalid transition** — `pipeline.py::handle_invalid_transition` rebuilds fresh layer with a CORRECTION block ("you suggested advancing to X but exit_condition is not satisfied because Y"), re-invokes LLM (max 1 retry). On second failure: stay in current_node + log warning + send response_text as-is. Tests cover happy retry + max-retry fallback.

### Phase 5 — Post-processing + Adapter Send + Audit (3 tasks)

14. **Apply TurnDecision to state** — `flowengine/post_processing.py::apply_decision(context, decision)` updates `TalkFlowState.collected`, `extracted_facts`, `current_node` (after validate_transition), `objections_handled` (record entry if `detected_objection` set), `messages` (append assistant message with `Message` Pydantic), `Talk.turn_count++`, `Talk.last_message_at = now`. Closure signal (`suggest_close_talk != "no"`) is a no-op in FE-01b (FE-03 wires actual Talk closure).
15. **Send response via MessagingAdapter** — call existing `MessagingAdapter.send_text(lead, response_text)` (no chunking yet — humanization comes in FE-03). Capture send result (external_id, status) for audit. Voice path (`response_format=voice|both`) is rejected in FE-01b with a fallback to text + warning log (FE-05 implements voice).
16. **Audit OutboundMessage row** — write `OutboundMessage` row with `media_type=text`, `triggered_by="inbound"`, all FK references, `inbound_message_id` link, idempotency_key. Uses existing P10 `outbound_audit` helpers — extend signature if needed to accept Talk instead of Talkflow.

### Phase 6 — Orchestration + Integration + Cutover (5 tasks)

17. **Pipeline orchestrator** — `flowengine/pipeline.py::run_turn(tenant, inbound_message_row) -> RunTurnResult` composes the 12 steps from spec §4. Handles advisory lock, error paths (LLM timeout retry, malformed JSON retry, etc. — minimal version of §20). Idempotency: if OutboundMessage with same `(turn_index, chunk_index)` already exists, no-op early. Tests cover end-to-end with FakeListChatModel + FakeMessagingAdapter.
18. **process_lead_inbox feature flag branch** — `worker/jobs/inbound.py` gains: if `tenant.architecture_version == 2` → `await run_turn(tenant, inbound_message)`; else → existing LangGraph path unchanged. Tests verify routing decision based on tenant config.
19. **Tenant loader: architecture_version + sdr_persona slot** — `tenant_loader/loader.py` parses `architecture_version` (default 1) and accepts `sdr_persona` block (without enforcing schema yet — passed as raw dict to TreeflowLoader). Updates `TenantConfig` Pydantic. Tests cover backward compat (tenants without these fields parse fine).
20. **Avelum test fixture v2** — `tests/fixtures/avelum_treeflow_v2.yaml` (minimal 2-node TreeFlow: saudacao → qualificacao_simples) + `tests/fixtures/avelum_tenant_v2.yaml` (architecture_version=2, sdr_persona pointing at the TreeFlow). Used by integration tests.
21. **Pilot harness v2 path** — `cli/simulate.py` gains `--arch-v2` flag that runs through `run_turn` instead of the LangGraph runtime. Same REPL UX; just dispatches differently. Smoke verification: drive a 3-turn conversation manually against FakeMessagingAdapter.
22. **Smoke E2E + cutover docs** — `tests/integration/test_pipeline_smoke_end_to_end.py` runs a 3-turn happy-path conversation through `run_turn` with FakeListChatModel returning canned TurnDecisions. Verify: state mutations, OutboundMessage rows, no crashes. Also: write a 1-paragraph cutover note in `docs/superpowers/notes/2026-06-02-fe01b-cutover.md` describing how to flip a tenant from v1 to v2.

---

## Acceptance criteria

- All 22 tasks complete with one commit each (22 commits + 1 merge from FE-01a).
- `uv run pytest tests/unit/test_treeflow_loader_v2.py tests/unit/test_system_prompt_builder.py tests/unit/test_routing_validate_transition.py tests/unit/test_guardrails_validator.py tests/unit/test_post_processing_state_apply.py -v` — ALL PASS.
- `uv run pytest tests/integration/test_pipeline_smoke_end_to_end.py tests/integration/test_pipeline_corrective_retry.py tests/integration/test_pipeline_guardrails_violation.py tests/integration/test_pipeline_feature_flag_routing.py tests/integration/test_advisory_lock_serialization.py -v` — ALL PASS.
- Pilot harness `uv run ai-sdr simulate --arch-v2 --tenant avelum` drives a 3-turn happy conversation end-to-end without crashing.
- Avelum tenant in dev DB (`ai_sdr_fe01a`) can be flipped to `architecture_version=2` and an inbound message routes through `run_turn` correctly.
- Wider test suite shows no NEW regressions vs the FE-01a baseline (38 pre-existing failures should stay at 38, not grow).

## Risks / Open questions to confirm before execution

1. **LangChain `with_structured_output` quirks across providers.** The contract differs slightly between Anthropic (tool-use) and OpenAI (json_mode). Task 8's `main_llm_for_tenant` should normalize. Confirm by smoke-testing with both providers in a follow-up live_llm test (optional).
2. **Prompt cache_control marker format.** Anthropic expects `{"type": "ephemeral"}` on the content block. The exact LangChain wrapper for this is `additional_kwargs={"cache_control": {"type": "ephemeral"}}` on `SystemMessage`. Task 7 needs to verify this against the langchain-anthropic version pinned in pyproject.toml.
3. **Tenant.architecture_version reads.** The `tenants` SQLAlchemy model exposes `architecture_version` (FE-01a). `tenant_loader.loader.TenantConfig` Pydantic doesn't yet. Task 19 wires the YAML side (`tenant.yaml`), but the worker reads from the DB row — pick one source of truth. Plan: DB column is authoritative; YAML can override at load time (so a YAML change doesn't require DB update for a single-tenant dev).
4. **Idempotency key for OutboundMessage when no chunking.** FE-01b sends one message per turn (no chunks). Idempotency key = `f"{tenant_id}:{talk_id}:{turn_index}:0"`. Document this in Task 16.

---

## Next plan

**FE-02 — LangGraph removal + critic LLM deletion**. Once Avelum runs on `architecture_version=2` stably (smoke + a week of light operation), FE-02 removes the v1 path entirely: deletes `src/ai_sdr/treeflow/`, drops the LangGraph checkpointer tables (migration 0024), and deletes `guardrails/critic.py`. About 12 tasks, ~6h of work.
