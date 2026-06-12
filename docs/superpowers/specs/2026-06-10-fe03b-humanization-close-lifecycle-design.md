# FE-03b — Humanização + Close Lifecycle — Design

> Sub-fase 2 da refatoração FlowEngine. Cobre humanização (chunking + typing delays no sender) e close lifecycle (3 gatilhos automáticos de fechamento de Talk). FE-03a entregou objection runtime + Python validator; FE-03c entregará on_collected + adapter framework MVP.

## 1. Contexto

### 1.1. Relação com FlowEngine

Spec arquitetural macro: `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md`. FE-03b implementa:

- §13.5 (Voice integration): "Voice humanization: when voice mode, don't chunk — send 1 audio per turn. Text humanization (chunking + typing) stays for text mode only."
- §16 (Talks lifecycle): close triggers
- §27 (tenant.yaml schema additions): humanization block

### 1.2. Bundle interno

Decidido no brainstorm: FE-03b bundle = humanização + close lifecycle num único plano. ~20 tasks, 1 PR. Os dois temas são independentes mas ambos polish do runtime.

### 1.3. Posição na linha de refatoração

```
FE-01a ✅  FE-01b ✅  FE-03a ✅  FE-03b (este)  FE-03c  FE-04  FE-05  FE-06  FE-07
```

## 2. Goals

- Humanização: agente WhatsApp parece humano de verdade — quebra resposta em chunks, delay realista entre chunks, indicador "digitando…" quando disponível
- Close lifecycle: Talks fecham automaticamente por inactivity, duration ou completion rule
- Re-engagement: lead manda mensagem após Talk close → nova Talk fresca (não reopen)
- Backward-compat: tenants sem `humanization` block ou sem `talk_lifecycle` block continuam funcionando com defaults razoáveis

### 2.1. Non-goals (deferidos)

| Item | Fase |
|---|---|
| Turn limit close trigger | FE-03b' (próximo polish) |
| LLM signal close (`suggest_close_talk` na TurnDecision) | FE-03b' |
| Sentinel ban close | FE-04 |
| Operator manual close via API | Plano 11 evolution |
| Long-term memory (Lead.profile) integrado com re-engagement | FE-03c / v2 |
| Talk reopen (em vez de nova Talk) | comportamento fixado |
| Voice mode chunking diferente | FE-05 |
| Conversation summarization (>30 msgs) | reserved v2 |

## 3. Architecture overview

FE-03b toca 8 lugares; **3 são módulos novos**, o resto são extensões pontuais.

```
       ┌──────────────────────────────────────────────────────────────┐
       │                  run_turn(talk_id, inbound)                  │
       └──────────────────────────────────────────────────────────────┘
                                          │
   preprocessing → ... → apply_decision → sender → audit
        │                     │              │
        ▼                     ▼              ▼
  re-engagement      completion rule    humanizer (NEW)
   detection (NEW)    check (NEW)        + mark_as_typing

                                  ┌────────────────────────────┐
                                  │ background worker scan job │
                                  │ (NEW) — inactivity/duration │
                                  └────────────────────────────┘
```

### 3.1. Arquivos novos

- `src/ai_sdr/flowengine/humanizer.py` — pure: `humanize(response_text, config, *, is_voice) → list[Chunk]`
- `src/ai_sdr/flowengine/close_lifecycle.py` — pure: `evaluate_completion_rule(state, decision, treeflow) → CloseOutcome | None`
- `src/ai_sdr/worker/jobs/scan_talks.py` — scheduled task: scan active Talks, mark inactivity/duration closes
- `src/ai_sdr/models/talk_status.py` — `TalkStatus` Literal + `ALL_STATUSES` tuple (single source of truth)

### 3.2. Arquivos modificados

- `src/ai_sdr/flowengine/sender.py` — chama humanizer + envia chunk a chunk com delay + mark_as_typing
- `src/ai_sdr/flowengine/post_processing.py` — hook pra `evaluate_completion_rule` após apply state delta
- `src/ai_sdr/flowengine/preprocessing.py` — checa Talk antiga closed; se sim, cria nova (re-engagement)
- `src/ai_sdr/flowengine/treeflow_loader.py` — parse `talk_lifecycle` block + bounds validation (ISO-8601 + simpleeval syntax)
- `src/ai_sdr/messaging/base.py` — adiciona `mark_as_typing(to)` opcional ao protocol (default no-op)
- `src/ai_sdr/messaging/whatsapp_cloud.py` — implementa mark_as_typing via Meta typing_indicator API
- `src/ai_sdr/schemas/tenant_yaml.py` — ativa parsing do `humanization` block já stubbed
- `src/ai_sdr/models/talk.py` — `status` Mapped[TalkStatus]
- `migrations/versions/0026_talks_status_lifecycle_values.py` — adiciona 4 valores ao enum

## 4. Humanizer

### 4.1. Schemas

```python
# src/ai_sdr/flowengine/humanizer.py

from dataclasses import dataclass

@dataclass(frozen=True)
class HumanizationConfig:
    """Per-tenant config from tenant.yaml > humanization."""
    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = 8.0
    chars_per_second_max: float = 15.0
    min_delay_ms: int = 800
    max_delay_ms: int = 4000
    apply_to_voice: bool = False  # FE-05 hook


@dataclass(frozen=True)
class Chunk:
    """One outbound message in the humanized sequence."""
    text: str
    delay_before_ms: int  # 0 for first chunk
```

### 4.2. Pure function `humanize()`

```python
def humanize(
    response_text: str,
    config: HumanizationConfig,
    *,
    is_voice: bool = False,
) -> list[Chunk]:
    """Split response into chunks with typing delays.

    Voice mode bypasses chunking (per spec §13.5) unless config.apply_to_voice.
    Humanization disabled → single chunk.
    """
    if is_voice and not config.apply_to_voice:
        return [Chunk(text=response_text, delay_before_ms=0)]

    if not config.enabled:
        return [Chunk(text=response_text, delay_before_ms=0)]

    raw_chunks = [c.strip() for c in response_text.split(config.chunk_delimiter) if c.strip()]
    if not raw_chunks:
        return []

    chunks = [Chunk(text=raw_chunks[0], delay_before_ms=0)]
    for next_chunk_text in raw_chunks[1:]:
        import random
        typing_speed = random.uniform(
            config.chars_per_second_min,
            config.chars_per_second_max,
        )
        typing_ms = int(len(next_chunk_text) / typing_speed * 1000)
        delay = max(config.min_delay_ms, min(config.max_delay_ms, typing_ms))
        chunks.append(Chunk(text=next_chunk_text, delay_before_ms=delay))

    return chunks
```

### 4.3. Sender extension

```python
# src/ai_sdr/flowengine/sender.py (modified)

import asyncio

async def send_response_text(
    *,
    adapter: MessagingAdapter,
    lead: Lead,
    decision: TurnDecision,
    humanization_config: HumanizationConfig,  # NEW kwarg
) -> SendResult:
    chunks = humanize(
        decision.response_text,
        humanization_config,
        is_voice=(decision.response_format == "voice"),
    )

    last_external_id: str | None = None
    for chunk in chunks:
        if chunk.delay_before_ms > 0:
            # Optional typing indicator before delay
            try:
                await adapter.mark_as_typing(lead.whatsapp_e164)
            except (NotImplementedError, AttributeError):
                pass
            await asyncio.sleep(chunk.delay_before_ms / 1000)

        result = await adapter.send_text(lead.whatsapp_e164, chunk.text)
        last_external_id = result.external_id

    return SendResult(
        external_id=last_external_id,
        status="sent",
        error_detail=None,
    )
```

### 4.4. MessagingAdapter protocol extension

```python
# src/ai_sdr/messaging/base.py (modified)

class MessagingAdapter(ABC):
    # ... existing methods ...

    async def mark_as_typing(self, to: str) -> None:
        """Optional: signal 'typing...' indicator to the lead.

        Default no-op. Adapter implementations override if the underlying
        channel supports a typing indicator (e.g., WhatsApp Cloud's
        typing_indicator API). Failure to send is silent — typing is UX
        only and never blocks the actual message send.
        """
        return None
```

### 4.5. WhatsApp Cloud impl

```python
# src/ai_sdr/messaging/whatsapp_cloud.py (modified)

async def mark_as_typing(self, to: str) -> None:
    try:
        await self._post(
            f"/v17.0/{self._phone_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "typing_indicator": {"type": "text"},
            },
        )
    except (PolicyError, NotImplementedError):
        # Meta gates this per account; silent fallback
        return None
```

## 5. Close lifecycle

### 5.1. Gatilhos implementados

| Gatilho | Trigger location | Talk.status final | closed_by |
|---|---|---|---|
| **Inactivity** | Worker scan job (cron 5min) | `closed_inactivity` | `scan_job` |
| **Duration** | Worker scan job | `closed_duration` | `scan_job` |
| **Completion rule** | Pipeline hook em `post_processing.apply_decision` | `closed_completed_success` \| `closed_completed_failure` \| `closed_no_interest` | `pipeline_hook` |

### 5.2. Worker scan job

```python
# src/ai_sdr/worker/jobs/scan_talks.py (NEW)

from dataclasses import dataclass
from datetime import datetime

@dataclass
class ScanResult:
    inactive_closed: int
    duration_closed: int


async def scan_active_talks(session: AsyncSession, now: datetime) -> ScanResult:
    """Cross-tenant scan: close Talks that hit inactivity or duration limit.

    Uses BYPASSRLS via SET LOCAL row_security = off (ai_sdr_app has this
    privilege; same pattern as follow_up_scanner).
    """
    inactive_closed = 0
    duration_closed = 0

    await session.execute(text("SET LOCAL row_security = off"))

    rows = await session.execute(
        select(Talk, TreeflowVersion)
        .join(TreeflowVersion, Talk.treeflow_version_id == TreeflowVersion.id)
        .where(Talk.status == "active")
        .with_for_update(skip_locked=True)
    )

    for talk, tfv in rows:
        try:
            treeflow = load_treeflow_v2(tfv.content_yaml)
        except TreeflowLoadError:
            continue  # bad YAML; T28's worker path will catch this elsewhere

        lifecycle = treeflow.talk_lifecycle
        if lifecycle is None:
            continue

        if lifecycle.close_after_inactivity:
            cutoff = now - lifecycle.close_after_inactivity
            if talk.last_message_at < cutoff:
                await _close(session, talk, now, "closed_inactivity", "scan_job")
                inactive_closed += 1
                continue

        if lifecycle.close_after_duration:
            cutoff = now - lifecycle.close_after_duration
            if talk.opened_at < cutoff:
                await _close(session, talk, now, "closed_duration", "scan_job")
                duration_closed += 1

    await session.commit()
    return ScanResult(
        inactive_closed=inactive_closed,
        duration_closed=duration_closed,
    )


async def _close(
    session: AsyncSession,
    talk: Talk,
    now: datetime,
    status: str,
    closed_by: str,
) -> None:
    talk.status = status
    talk.closed_at = now
    talk.closed_reason = status
    talk.closed_by = closed_by
    flag_modified(talk, "status")
    logger.info(
        "talk.closed talk=%s status=%s by=%s", talk.id, status, closed_by,
    )
```

**Cron wiring** via arq (mesmo pattern do Plano 9 follow-up scanner):

```python
# src/ai_sdr/worker/main.py (modified)

@cron("*/5 * * * *")
async def scheduled_scan_talks(ctx):
    async with session_factory() as session:
        await scan_active_talks(session, now=datetime.now(timezone.utc))
```

Configurável via env: `WORKER_SCAN_INTERVAL_SECONDS` (default 300).

### 5.3. Completion rule (pipeline hook)

```python
# src/ai_sdr/flowengine/close_lifecycle.py (NEW)

from dataclasses import dataclass
from simpleeval import SimpleEval

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import TreeflowDef
from ai_sdr.flowengine.state import TalkFlowState

@dataclass(frozen=True)
class CloseOutcome:
    status: str  # "closed_completed_success" | "closed_completed_failure" | "closed_no_interest"
    reason: str
    closed_by: str


def evaluate_completion_rule(
    *,
    state: TalkFlowState,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> CloseOutcome | None:
    """Check if collected fields trigger a completion rule from talk_lifecycle.

    Returns None if no rule fires.
    """
    lifecycle = treeflow.talk_lifecycle
    if lifecycle is None or not lifecycle.close_when_completed:
        return None

    context = {
        **state.collected,
        **decision.collected_fields,
        "collected": {**state.collected, **decision.collected_fields},
        "extracted_facts": state.extracted_facts,
        "turn_index": state.turn_index,
    }

    for rule in lifecycle.close_when_completed:
        try:
            if bool(SimpleEval(names=context).eval(rule.expression)):
                return CloseOutcome(
                    status=f"closed_{rule.outcome}",
                    reason=f"completion_rule: {rule.expression}",
                    closed_by="pipeline_hook",
                )
        except Exception:
            continue  # invalid at runtime → skip; loader catches at load time

    return None
```

### 5.4. Wire em post_processing

Logo após a aplicação do delta de objection (step 5 do FE-03a) e antes do requires_review_reason chain:

```python
# src/ai_sdr/flowengine/post_processing.py (modified)

# ... after step 5: state delta applied ...

# NEW: completion rule check
close_outcome = evaluate_completion_rule(
    state=state, decision=decision, treeflow=treeflow,
)
if close_outcome is not None:
    talk.status = close_outcome.status
    talk.closed_at = now
    talk.closed_reason = close_outcome.reason
    talk.closed_by = close_outcome.closed_by
    # Completion close is mutually exclusive with requires_review_reason:
    # if completion fires, no review needed; skip the rest of the
    # requires_review_reason chain.
    # The objection state delta + history apply normally (we may have
    # entered/resolved a treatment in the same turn that completion fires).

# ... rest of apply_decision (requires_review_reason chain) ...
# Guard: only run if close_outcome is None
```

### 5.5. Re-engagement em preprocessing

```python
# src/ai_sdr/flowengine/preprocessing.py (modified)

async def resolve_pipeline_context(...) -> PipelineContext:
    # ... lead resolution unchanged ...

    existing_talk = await talk_repo.find_active_for_lead(tenant.id, lead.id)

    if existing_talk is None:
        # Check for most recent closed Talk for observability
        closed_talk = await talk_repo.find_most_recent_closed(tenant.id, lead.id)
        if closed_talk is not None:
            logger.info(
                "re_engagement_after_close lead=%s previous_talk=%s "
                "previous_status=%s closed_at=%s",
                lead.id, closed_talk.id, closed_talk.status, closed_talk.closed_at,
            )
        # Always create fresh Talk regardless of prior history
        talk = await _create_new_talk(
            session, tenant, lead, treeflow_version, now,
        )
        state = await _create_initial_state(session, talk, treeflow)
    else:
        talk = existing_talk
        state = await state_repo.load(talk.id)

    return PipelineContext(lead=lead, talk=talk, state=state)
```

## 6. YAML schema extensions

### 6.1. TreeFlow `talk_lifecycle` block

```yaml
# tenants/<slug>/treeflows/<id>.yaml (NEW block at top level)

talk_lifecycle:
  close_after_inactivity: P7D          # ISO-8601 duration; 1h <= X <= 1y
  close_after_duration: P30D           # 1d <= X <= 2y
  close_when_completed:
    - expression: "collected.demo_agendada == true"
      outcome: success
    - expression: "collected.compra_confirmada == true"
      outcome: success
    - expression: "collected.no_interest_flag == true"
      outcome: no_interest
```

### 6.2. Dataclasses

```python
# src/ai_sdr/flowengine/treeflow_loader.py (extended)

from datetime import timedelta

@dataclass
class TreeflowCompletionRule:
    expression: str
    outcome: str  # "success" | "failure" | "no_interest"


@dataclass
class TreeflowTalkLifecycle:
    close_after_inactivity: timedelta | None = None
    close_after_duration: timedelta | None = None
    close_when_completed: list[TreeflowCompletionRule] = field(default_factory=list)


@dataclass
class TreeflowDef:
    # ... existing fields ...
    talk_lifecycle: TreeflowTalkLifecycle | None = None
```

### 6.3. Bounds validation

```python
_ALLOWED_OUTCOMES = {"success", "failure", "no_interest"}


def _parse_talk_lifecycle(raw: dict | None) -> TreeflowTalkLifecycle | None:
    if raw is None:
        return None

    import isodate

    inactivity = raw.get("close_after_inactivity")
    inactivity_td: timedelta | None = None
    if inactivity:
        try:
            inactivity_td = isodate.parse_duration(inactivity)
        except (isodate.ISO8601Error, ValueError) as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_inactivity invalid ISO-8601: "
                f"{inactivity!r}: {e}"
            ) from e
        if not (timedelta(hours=1) <= inactivity_td <= timedelta(days=365)):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_inactivity must be in [PT1H, P365D], "
                f"got {inactivity}"
            )

    duration = raw.get("close_after_duration")
    duration_td: timedelta | None = None
    if duration:
        try:
            duration_td = isodate.parse_duration(duration)
        except (isodate.ISO8601Error, ValueError) as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_duration invalid ISO-8601: "
                f"{duration!r}: {e}"
            ) from e
        if not (timedelta(days=1) <= duration_td <= timedelta(days=730)):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_duration must be in [P1D, P730D], "
                f"got {duration}"
            )

    completion_raw = raw.get("close_when_completed") or []
    completion: list[TreeflowCompletionRule] = []
    for entry in completion_raw:
        if not isinstance(entry, dict):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed entries must be mappings, "
                f"got {entry!r}"
            )
        expr = entry.get("expression")
        outcome = entry.get("outcome")
        if not expr or outcome not in _ALLOWED_OUTCOMES:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed entry invalid: "
                f"expression and outcome ∈ {_ALLOWED_OUTCOMES} required, got {entry!r}"
            )
        # Parse-time syntax check: simpleeval must not raise on parse
        try:
            SimpleEval(names={}).parse(expr)
        except Exception as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed expression invalid "
                f"syntax: {expr!r}: {e}"
            ) from e
        completion.append(
            TreeflowCompletionRule(expression=expr, outcome=outcome)
        )

    return TreeflowTalkLifecycle(
        close_after_inactivity=inactivity_td,
        close_after_duration=duration_td,
        close_when_completed=completion,
    )
```

Wire em `load_treeflow_v2`:

```python
talk_lifecycle = _parse_talk_lifecycle(data.get("talk_lifecycle"))
# ... include in TreeflowDef(...)
```

### 6.4. tenant.yaml `humanization` block

```python
# src/ai_sdr/schemas/tenant_yaml.py (extended)

class HumanizationConfig(BaseModel):
    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = Field(default=8.0, gt=0)
    chars_per_second_max: float = Field(default=15.0, gt=0)
    min_delay_ms: int = Field(default=800, ge=0)
    max_delay_ms: int = Field(default=4000, ge=0)
    apply_to_voice: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> "HumanizationConfig":
        if self.chars_per_second_min > self.chars_per_second_max:
            raise ValueError("chars_per_second_min must be <= chars_per_second_max")
        if self.min_delay_ms > self.max_delay_ms:
            raise ValueError("min_delay_ms must be <= max_delay_ms")
        return self
```

Em `TenantConfig`:

```python
class TenantConfig(BaseModel):
    # ... existing fields ...
    humanization: HumanizationConfig = Field(default_factory=HumanizationConfig)
```

## 7. Migration 0026 — talks.status enum

`talks.status` é VARCHAR + CHECK constraint (pattern de migration 0013). Adiciona 4 valores:

```python
# migrations/versions/0026_talks_status_lifecycle_values.py

"""talks.status enum: add lifecycle close values (FlowEngine FE-03b)

Per spec §16.3. Adds closed_completed_success/failure, closed_no_interest,
closed_duration. Preserves backward-compat with closed_completed.

Revision ID: 0026_talks_status_lifecycle_values
Revises: 0025_talks_requires_review_reason
Create Date: 2026-06-10 00:00:00
"""

from alembic import op
from ai_sdr.models.talk_status import ALL_STATUSES

revision = "0026_talks_status_lifecycle_values"
down_revision = "0025_talks_requires_review_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN ('active', 'requires_review', 'closed_completed', "
        "'closed_inactivity', 'closed_optout', 'closed_banned')",
    )
```

### 7.1. Single source of truth

```python
# src/ai_sdr/models/talk_status.py (NEW)

"""Canonical Literal for talks.status (FE-03b).

Single source of truth across the migration, ORM model, scan job, and
close_lifecycle module. Keep in sync with migration 0026's enum values.
"""

from __future__ import annotations
from typing import Literal, get_args

TalkStatus = Literal[
    "active",
    "requires_review",
    "closed_completed",            # backward-compat (pre-FE-03b)
    "closed_completed_success",
    "closed_completed_failure",
    "closed_no_interest",
    "closed_duration",
    "closed_inactivity",
    "closed_optout",
    "closed_banned",
]

ALL_STATUSES: tuple[str, ...] = get_args(TalkStatus)
```

ORM column:

```python
# src/ai_sdr/models/talk.py (modified)

from ai_sdr.models.talk_status import TalkStatus

status: Mapped[TalkStatus] = mapped_column(Text(), nullable=False)
```

## 8. Matriz de brechas

| ID | Cenário | Decisão |
|---|---|---|
| **H1** | LLM responde sem `\n\n` | 1 chunk único, sem delay. Aceitável. LLM instruction reforçada no system prompt cached layer pra preferir parágrafos entre ideias diferentes. |
| **H2** | `send_text` falha no chunk 2 de 3 | Adapter retry interno via tenacity (já existe). Falha persistente: chunks 1+2 marcados sent (audit), chunk 3 marcado error em outbound_messages. Talk **não fecha**. Lead vê mensagem "cortada" — próximo turn LLM reage ao que tem. |
| **H3** | Validador retry → response diferente | Sem problema — chunking aplica na nova resposta. |
| **H4** | Adapter sem `mark_as_typing` | Default no-op no protocol. WhatsApp Cloud tenta; se Meta gates per account, fallback silencioso. |
| **H5** | `response_text` vazio após split (só whitespace/delimiters) | `humanize()` retorna `[]`. Sender skip silencioso + log warning. Talk não progride. |
| **C1** | Worker scan + pipeline hook race fechando mesma Talk | `WHERE status='active'` no UPDATE garante idempotência. Vencedor da race fecha; outro vê status changed e desiste. |
| **C2** | Lead manda inbound durante scan close | Worker já tem `pg_advisory_lock` per lead. Scan adquire lock antes do close. Ingest aguarda lock; vê status closed; preprocessing cria nova Talk (re-engagement path). |
| **C3** | YAML completion expression inválida | `TreeflowLoader._parse_talk_lifecycle` chama `SimpleEval(names={}).parse(expr)` no load. Sintaxe inválida → `TreeflowLoadError`. Tenant nem inicia. |
| **C4** | ISO-8601 duration mal formatado | Parser usa `isodate.parse_duration()`. Inválido → `TreeflowLoadError`. |
| **C5** | Scan idempotency loop | `WHERE status='active'` filter. Talk não-ativa = skip. |
| **C6** | Opt-out + completion rule no mesmo turn | Opt-out prioridade (já existe FE-01b). Completion não fira porque pipeline já fechou via opt-out. Idempotente. |
| **C7** | Scan crashou no meio de fechar 50 Talks | Transação per-Talk (commit por Talk fechada, não bulk). Crash deixa N fechadas + M ativas → próximo scan pega as M. Idempotente. |
| **C8** | Completion rule dispara mas objection ACTIVE | Completion fecha Talk com `closed_completed_*`. `active_treatment` é registrada em `objections_handled` como `deferred` (resolution: deferred) na história, depois Talk fecha. Conservador. |
| **C9** | `treeflow.talk_lifecycle` é None mas YAML tenta usar | Loader parsing devolve `talk_lifecycle=None` se bloco omisso. Scan + completion check ambos skip silencioso. TreeFlows v1 (sem o bloco) continuam funcionando. |

## 9. Idempotência + transações

### 9.1. Worker scan

Per-Talk commit (não bulk). Pattern:

```python
for talk, tfv in rows:
    try:
        # decide close
        ...
        await session.commit()  # per-row
    except Exception:
        await session.rollback()
        logger.exception("scan_talk_failed talk=%s", talk.id)
        continue
```

Resultado: crash deixa state parcial mas consistente — próximo scan re-tenta as restantes. `WHERE status='active'` garante que talks já fechadas não voltam.

### 9.2. Pipeline hook (completion rule)

Roda dentro da `session.begin()` do FE-03a `run_turn`. Se `evaluate_completion_rule` raise (não deveria — pure function), transação rolla back inteira; worker retry processa de novo.

### 9.3. Re-engagement

`find_active_for_lead` + `find_most_recent_closed` + `_create_new_talk` rodam todos dentro da mesma transação de preprocessing → atomicamente. Race entre 2 workers do mesmo lead: advisory lock per lead bloqueia (já existe).

## 10. Observabilidade

### 10.1. Eventos structlog novos

| Event | Quando | Payload |
|---|---|---|
| `talk.closed.inactivity` | scan_talks marca close_inactivity | talk_id, lead_id, last_message_at, cutoff_age_days |
| `talk.closed.duration` | scan_talks marca close_duration | talk_id, lead_id, opened_at, cutoff_age_days |
| `talk.closed.completion` | pipeline hook marca close | talk_id, outcome, expression_matched |
| `talk.re_engagement` | preprocessing detecta lead com Talk fechada anterior | lead_id, previous_talk_id, previous_status, days_since_close |
| `humanization.chunks_emitted` | sender envia múltiplos chunks | talk_id, chunk_count, total_chars, total_delay_ms |
| `humanization.skipped_voice_mode` | response_format=voice + apply_to_voice=false | talk_id |
| `mark_as_typing.unsupported` | adapter raise NotImplementedError | adapter_name |
| `mark_as_typing.failed` | adapter raise PolicyError ou similar | adapter_name, error |
| `scan_talks.completed` | scan_talks termina batch | inactive_closed, duration_closed, total_active_scanned, duration_ms |

### 10.2. Métricas (via Plano 10 LangSmith wiring se aplicável)

- Avg chunks per response (humanization usage rate)
- Avg total send delay per turn
- % Talks closed por tipo (inactivity/duration/completion)
- Re-engagement rate (% leads voltam após close)

## 11. Testing strategy

### 11.1. Unit tests (~22 arquivos)

```
tests/unit/
├── test_humanizer_paragraph_split.py
├── test_humanizer_voice_skips.py
├── test_humanizer_disabled_returns_single_chunk.py
├── test_humanizer_empty_response.py
├── test_humanizer_delay_bounded.py
├── test_humanizer_delay_proportional_to_next_chunk.py
├── test_close_lifecycle_completion_rule_success.py
├── test_close_lifecycle_completion_rule_failure.py
├── test_close_lifecycle_no_rules_returns_none.py
├── test_close_lifecycle_invalid_runtime_skipped.py
├── test_scan_inactivity_closes_active_talks.py
├── test_scan_duration_closes_active_talks.py
├── test_scan_skips_already_closed.py
├── test_scan_treeflow_no_lifecycle_block_skipped.py
├── test_treeflow_loader_talk_lifecycle.py
├── test_treeflow_loader_invalid_iso_duration.py
├── test_treeflow_loader_invalid_completion_expression.py
├── test_tenant_yaml_humanization_defaults.py
├── test_tenant_yaml_humanization_invalid_bounds.py
├── test_re_engagement_creates_new_talk.py
├── test_re_engagement_logs_previous_talk.py
└── test_talk_status_literal_source_of_truth.py
```

### 11.2. Integration tests (skip-friendly per Phase 11 pattern de FE-03a)

```
tests/integration/
├── test_humanization_e2e_3_chunks_with_delays.py
├── test_completion_rule_fires_e2e.py
├── test_scan_closes_inactive_talk_e2e.py
├── test_re_engagement_after_close_e2e.py
└── test_migration_0026_status_enum.py
```

### 11.3. Fixtures novas

```
tests/fixtures/
├── avelum_v2_with_lifecycle.yaml   # tenant com talk_lifecycle completo
└── treeflow_invalid_lifecycle_*.yaml  # bounds validation negative cases
```

## 12. Out of scope (explicit)

| Item | Onde aterrissa |
|---|---|
| Turn limit close trigger (`close_after_turns`) | FE-03b' |
| LLM signal close (`suggest_close_talk` consumption) | FE-03b' |
| Sentinel ban close (`closed_banned`) | FE-04 |
| Operator manual close via REST API | Plano 11 evolution |
| Long-term memory (Lead.profile) populated from closed Talks | FE-03c / v2 |
| Talk reopen (in lieu of new Talk) | Fixed: always new Talk |
| Voice-mode chunking different (1 audio per turn vs N text chunks) | FE-05 wire |
| Conversation summarization (>30 msgs → history_summary) | reserved v2 |
| Cross-tenant operator dashboard (close stats per tenant) | FE-06 + Plano 11 |
| Closure events to external BI sink | FE-06 |

## 13. Migration / cutover

### 13.1. Tenants v2 existentes

Avelum + future Joana — ambos devem ganhar `talk_lifecycle` block no TreeFlow YAML antes de FE-03b ser ativado pra eles. Recomendação:

```yaml
talk_lifecycle:
  close_after_inactivity: P7D
  close_after_duration: P30D
  close_when_completed:
    - expression: "collected.demo_agendada == true"
      outcome: success
```

Mínimo viável. Autores adicionam regras de business conforme funil.

### 13.2. TreeFlows sem `talk_lifecycle`

Backward-compat: `talk_lifecycle: None` → scan + completion ambos no-op. Talk fica ativa indefinidamente (comportamento pré-FE-03b). OK pra dev/test.

### 13.3. Tenants sem `humanization` block

Defaults razoáveis. Bot fica humanizado out-of-the-box.

## 14. Decisões abertas — nenhuma

Todas as decisões substantivas fechadas no brainstorm. Implementação pode prosseguir direto pra plano TDD.

---

**Autoria:** brainstorm conduzido via `superpowers:brainstorming` em 2026-06-10. Decisões registradas turn-a-turn.
