# Pilot Harness — Design

> **Status:** approved 2026-06-01
> **Author:** Nicolas + Claude
> **Goal:** enable end-to-end pilot testing of the PeSDR worker pipeline without a real WhatsApp Cloud API connection, so internal validation (Avelum dogfooding + customer demos) can happen against the live deployed system before any paid Meta credentials are in play.

---

## 1. Motivation

The PeSDR engine is feature-complete for v1 commercial promises (Planos 1–5, 9, 10, 11 + hardening) and deployed to VPS. Up to today (2026-06-01), the only end-to-end validation has been a synthetic smoke test that fired one `process_lead_inbox` job for one fake inbound and confirmed an `outbound_messages` row was written.

That smoke covered the wire-up but not the **experience**: how does the agent actually sound? Does the qualification flow feel natural? Does the price-objection branch trigger correctly? Does the critic catch out-of-bounds responses?

To answer those questions before spending Meta Business Manager money and registering HSM templates, we need a way to **drive multi-turn conversations through the real pipeline** using the existing `FakeMessagingAdapter`. That's what this harness is.

---

## 2. Non-goals

- **No time simulation.** The harness does not support `:fast-forward` or any manipulation of `scheduled_at`. Cold-lead reactivation and follow-up firing are excluded — those need real time to pass and would inflate scope without proportional value.
- **No failure injection.** No `:fail-next-send` or forced adapter errors. The harness exercises the happy path through the worker; failure-path code is already covered by the integration test suite in P10 (`test_outbound_audit_writes_from_send_failure.py` etc.).
- **No multi-tenant in one session.** One CLI invocation = one tenant = one lead. To switch tenants, exit and run again.
- **No persistence of "current conversation".** Each `ai-sdr pilot` invocation creates a fresh lead + talkflow. The user can still inspect history later via `ai-sdr outbound list` or psql.
- **No verbose debug panel.** No live display of classifier verdict, extracted fields, token usage, or latency per turn. Pure conversational view. (For debug, the user has `ai-sdr outbound list`, the worker logs, and LangSmith if tracing is enabled.)
- **No tenant config bootstrapping.** The harness assumes `tenants/<slug>/tenant.yaml`, `secrets.enc.yaml` (with a valid LLM key), and at least one treeflow YAML already exist. Configuring the Avelum tenant is separate work.

---

## 3. User experience

```
$ ai-sdr pilot --tenant avelum

Piloto Avelum · lead +5511990ab12cd · treeflow=qualificacao@1.0.0
(:quit ou Ctrl+C pra sair, :status pra ver estado)

> Oi, vi um anúncio
agente: Olá! Tudo bem? Posso te fazer uma pergunta rápida pra entender o que você procura?

> Pode
agente: Show. Qual é o faturamento mensal aproximado da sua empresa, em reais?

> Uns 50k por mês
agente: Legal! E qual é a maior dor hoje no seu time comercial?

> Achei caro
agente: Entendo. O investimento varia conforme o perfil, mas vale lembrar que […]

> :status
lead_id=2d40… status=active · talkflow_status=active · turns=4 · last_sent_at=2026-06-01 14:32:18

> :quit
[encerrado]
```

If the agent triggers handoff (e.g., lead asks for a human), the conversation closes with a clear notice:

```
> Pode me passar pra um humano?
agente: Claro, vou te conectar com um especialista agora.
[lead encaminhado pro operador humano — status=pending_assignment]
```

If a worker job fails (e.g., LLM auth error), the user sees a brief reason and the suggestion to inspect logs:

```
> Oi
[falha no processamento — AuthError. Verifica logs do worker: docker compose logs worker]
```

If the poll times out (worker stuck, redis disconnected, etc.):

```
> Oi
[timeout — worker não respondeu em 30s. Verifica `docker compose ps` e `docker compose logs worker`.]
```

---

## 4. Architecture

### 4.1. Components

```
src/ai_sdr/cli/
├── pilot.py                    NEW — CLI entrypoint + REPL loop + DB helpers
└── app.py                      MODIFIED — register pilot_app
```

No new modules outside `cli/`. No changes to worker, audit, registry, or any P9/P10/P11 surface. The harness is purely additive.

### 4.2. Data flow per turn

```
[user types text in terminal]
       │
       ▼
INSERT INTO inbound_messages (provider='fake', external_id='pilot_<uuid>', ...)
COMMIT
       │
       ▼
capture timestamp T = datetime.now(UTC)
       │
       ▼
pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))
       │
       ▼
poll every 500ms (max 30s):
  SELECT body_text, status, error_detail, message_type
  FROM outbound_messages
  WHERE lead_id = X AND created_at > T
  ORDER BY created_at ASC
  LIMIT 1
       │
       ▼
on row appears:
  if status='sent' and message_type='text': print agent reply
  if status='failed': print error_detail, end loop
       │
       ▼
also check:
  SELECT status FROM leads WHERE id = lead_id
  SELECT status FROM talkflows WHERE lead_id = lead_id
  if lead.status changed to 'pending_assignment' → handoff notice, end loop
  if talkflow.status changed to 'cold' → cold notice, end loop
       │
       ▼
otherwise: prompt for next input
```

Polling, not arq result wait. Reason: `process_lead_inbox` returns `None`, so the arq job result carries no signal. Querying `outbound_messages` directly is simple, robust, and doesn't require any worker change.

### 4.3. Startup sequence

1. Validate `--tenant <slug>` exists in DB (`SELECT FROM tenants WHERE slug=X`). If not, exit code 1 with the message: `tenant '<slug>' not in DB. Add it via psql before piloting: INSERT INTO tenants (slug, display_name) VALUES ('<slug>', '<name>');`. The harness does not create tenants; that's a prereq operator step (or a future CLI).
2. Resolve treeflow:
   - If `--treeflow <id>` given, use it.
   - Else: list `tenants/<slug>/treeflows/*.yaml`. If exactly 1 file, use it. If 0 or >1, exit with helpful message.
3. Load or create `TreeflowVersion` row:
   - Read the YAML file, compute `content_hash = sha256(content)`.
   - `SELECT FROM treeflow_versions WHERE tenant_id=X AND treeflow_id=Y AND content_hash=Z`.
   - If exists, reuse. If not, INSERT a new version row.
4. Generate `whatsapp_e164` (default: `+5511990` + 6 random hex chars).
5. INSERT new `Lead` (status='active', whatsapp_e164 = generated).
6. INSERT new `TalkFlow` (lead_id, treeflow_version_id, thread_id=`{tenant.id}:{uuid4()}`).
7. COMMIT.
8. Print header.

### 4.4. End signals (detection order)

Each poll iteration, in this order:

1. **Worker timeout** (no row in 30s) → print timeout message, exit code 1.
2. **Outbound row with status='failed'** → print error_detail, exit code 1.
3. **Lead status changed to 'pending_assignment'** → print handoff notice, exit code 0.
4. **TalkFlow status changed to 'cold'** → print cold notice, exit code 0.
5. **Outbound row with status='sent'** → print body_text, continue loop.

`:quit` and `Ctrl+C` exit with code 0 and a clean teardown (close arq pool, dispose engine).

### 4.5. REPL commands

| Command | Effect |
|---|---|
| `:quit` | Clean exit |
| `:status` | Print one-line summary: lead_id (short), lead.status, talkflow.status, turn count, last_sent_at |
| `Ctrl+C` | Same as `:quit` |
| empty line | Ignored — re-prompt without enqueueing anything |
| any other text | Treat as a lead message, enqueue, poll, display |

No other commands. No history scrollback (use `ai-sdr outbound list --tenant X --lead Y`). No retry. No edit-last-message.

---

## 5. Testing

### 5.1. Unit tests (`tests/unit/test_pilot_cli.py`)

| Test | What it asserts |
|---|---|
| `test_generate_whatsapp_e164_format` | Returns `+5511990` + 6 hex chars, length 13 |
| `test_resolve_treeflow_single_file` | If exactly 1 YAML, returns its id |
| `test_resolve_treeflow_multiple_files_requires_flag` | If >1, raises with helpful message listing options |
| `test_resolve_treeflow_no_files` | If 0, raises with hint |
| `test_resolve_treeflow_explicit_flag_wins` | If `--treeflow X` given, returns X even if multiple files exist |
| `test_status_command_format` | One-liner has lead_id prefix, both statuses, turn count |
| `test_poll_returns_first_new_outbound` | Mock session; row inserted after timestamp T returns from poll |
| `test_poll_ignores_older_rows` | Row with created_at <= T is not returned |

### 5.2. Integration test (`tests/integration/test_pilot_loop.py`)

Real Postgres + real Redis + a stub worker function (replaces `process_lead_inbox` in the arq registry for this test only) that:
1. Reads the most recent InboundMessage for the lead
2. Writes a hardcoded `OutboundMessage("eco: <text>", status='sent')` row

The test:
1. Sets up tenant + treeflow + lead via the harness's startup function
2. Drives 2 turns through the loop helper (no terminal — feeds inputs from a list)
3. Asserts 2 outbound rows exist with the expected bodies
4. Asserts the harness exited cleanly on a `:quit` signal in the input list

Why a stub: real worker LLM calls require a paid OpenAI key and add 2–5s per turn, making CI flaky. The stub validates the harness's contract with the worker (insert inbound → enqueue job → poll outbound) without coupling tests to LLM behavior.

### 5.3. Manual validation

After implementation, run the 3 scenarios in-scope:

1. **Lead novo**: configure Avelum tenant, run `ai-sdr pilot --tenant avelum`, type "Oi", verify agent responds with a greeting + first qualification question.
2. **Objeção de preço**: continue the conversation, say "Achei caro", verify the classifier triggers the `preco` branch and guardrails enforce the allowed-prices whitelist.
3. **Handoff humano**: say "Quero falar com vendedor", verify the harness detects `lead.status='pending_assignment'` and exits with the handoff notice.

Scenarios 4 (esfria + reativa) and 5 (saída de escopo) are NOT covered by this harness — see Non-goals.

---

## 6. Open questions

None. All decisions captured in the brainstorm.

---

## 7. Out of scope (related but separate work)

- **Avelum tenant configuration.** The `tenants/avelum/` directory, `tenant.yaml`, `secrets.enc.yaml` (with valid LLM key), and `treeflows/qualificacao.yaml` need to exist before piloting can happen. This is content work, not code work. Out of scope here.
- **Time simulation harness.** A future tool for testing follow-up + cold reactivation would be a separate spec.
- **Failure injection harness.** A future tool for chaos-testing the worker's failure paths.
- **Pilot session persistence.** Resuming a lead across CLI invocations would be a future enhancement (`ai-sdr pilot --lead <uuid>` flag).
- **Webhook signature smoke.** Validating Meta's HMAC signature path is excluded — needs a real `app_secret` and a HTTP request, not the pilot harness.
