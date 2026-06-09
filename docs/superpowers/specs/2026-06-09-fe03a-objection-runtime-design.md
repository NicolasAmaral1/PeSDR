# FE-03a — Objection Runtime + Python Validator — Design

> Sub-fase da refatoração FlowEngine. Cobre tratamento stateful multi-turno de objeções (`tool` mode) e substituição do critic LLM por validador Python. **Não cobre** humanização, close lifecycle, on_collected actions, adapter framework genérico, Sentinel ou voice — esses ficam em FE-03b/03c/04/05 respectivamente.

## 1. Contexto

### 1.1. Relação com a refatoração FlowEngine

A arquitetura macro vive em `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md`. FE-03a implementa especificamente:

- §10 (TreeFlow YAML schema): porção de `global_objections`, `handles_objections`, `tool_payload`
- §15 (Critic removal + replacement): substituir critic LLM por validador Python
- §16.2 (`Talk.handling_mode`): seta `requires_review` em múltiplos caminhos
- §20 (Failure modes): caminho `python_validator → corrective_retry → escalate`

### 1.2. Relação com Plano 4a (v1)

Plano 4a entregou objection classifier com 2 LLM calls/turn (Haiku classifier + main response). FE-03a **substitui esse mecanismo** mantendo a expressividade (objeções declaradas, handled inline ou via subnode), mas:

- Detection passa pro main LLM (`TurnDecision.objection_detected`) — zero LLM calls extras
- `subnode` é descartado em v2 (autor migra pra `tool` ou `inline`)
- Adiciona modo `tool` (multi-turno stateful via `ActiveTreatment`)

### 1.3. Recorte de FE-03

```
FE-03a (este doc)  →  objection runtime + python validator
FE-03b             →  humanização (chunking + typing) + close lifecycle
FE-03c             →  on_collected actions + adapter framework MVP
```

## 2. Goals

- Single LLM call per turn (incluindo detecção + tratamento de objeção)
- Tratamento stateful: agente argumenta por N turnos contra a mesma objeção, sabe quando parou
- Resolução clara em 3 outcomes: `accepted`, `deferred`, `exhausted`
- Cross-objection handling: lead troca de objeção → comportamento previsível
- Validador Python substitui critic LLM (−1 LLM call/turn)
- Fallback explícito quando coisas dão errado (sem silêncio)

### 2.1. Non-goals (deferidos)

- Sentinel anti-prompt-injection → **FE-04**
- Voice/mídia processing → **FE-05**
- Humanização (chunking + typing) → **FE-03b**
- Close lifecycle (inactivity, turn limit, etc.) → **FE-03b**
- on_collected actions runtime → **FE-03c**
- Adapter framework genérico → **FE-03c**
- HITL operator console consuming requires_review queue → **FE-07**

## 3. Architecture overview

FE-03a toca o pipeline em **5 pontos**, sem alterar o esqueleto do `run_turn`:

```
       ┌──────────────────────────────────────────────────────────────┐
       │                  run_turn(talk_id, inbound)                  │
       └──────────────────────────────────────────────────────────────┘
                │            │              │              │           │
                ▼            ▼              ▼              ▼           ▼
        ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌─────────┐
        │ treeflow_   │ │ system_  │ │ llm_client │ │ objection│ │ python_ │
        │ loader.py   │ │ prompt.py│ │  (existing)│ │_runtime  │ │validator│
        │ (EXTEND)    │ │ (EXTEND) │ │ extended   │ │  (NEW)   │ │  (NEW)  │
        │             │ │          │ │ TurnDecision│ │  ←→ state│ │ replaces│
        │ + parse:    │ │ + fresh: │ │ schema     │ │  apply   │ │ critic  │
        │ global_obj  │ │ active_  │ │            │ │          │ │         │
        │ handles_obj │ │ treatment│ │            │ │          │ │         │
        │ tool_payload│ │ node_obj │ │            │ │          │ │         │
        └─────────────┘ └──────────┘ └────────────┘ └──────────┘ └─────────┘
```

Sequência dentro de `run_turn` (acréscimos em **negrito**):

```
preprocessing → system_prompt → llm_client (main LLM call) → TurnDecision
                                                              │
                                                              ▼
                                                       **python_validator.check()**
                                                              │
                                                       (se violou → corrective retry 1x; se exauriu → fallback)
                                                              │
                                                              ▼
                                                       **objection_runtime.apply()**
                                                              │
                                                              ▼
                                                       post_processing.apply
                                                              │
                                                              ▼
                                                       sender.send → audit → usage
```

**Deletado/desativado no pipeline v2:**
- `guardrails/critic.py` — **não invocado** pelo pipeline FlowEngine (continua existindo enquanto algum tenant rodar `architecture_version=1`; cleanup definitivo fica pra plano de decommission de v1)
- `guardrails/runner.py` — pipeline v2 chama diretamente `python_validator`; o runner original continua ativo no caminho LangGraph v1

## 4. Active treatment state machine

Estado vive em `TalkFlowState.active_treatment: ActiveTreatment | None` (Pydantic schema já existente em `src/ai_sdr/flowengine/state.py` desde FE-01a).

**Importante — `inline` mode não entra na máquina de estado:** uma objeção declarada com `treatment_mode: inline` é apenas *visível* ao LLM via system prompt (em `global_objections` ou `handles_objections`). Quando o LLM detecta uma objeção inline, ele responde no mesmo turn dentro do `response_text` e o runtime **não** seta `active_treatment`. A máquina de estado abaixo só rege objeções com `treatment_mode: tool`.

### 4.1. Estados

```
┌─────────┐                                         ┌─────────────────┐
│  IDLE   │  ◄──── (start) (default a cada Talk)    │     ACTIVE      │
│         │                                         │ objection_id    │
│ nenhum  │                                         │ started_at_turn │
│ tratame.│                                         │ current_turn    │
│ ativo   │                                         │ max_turns       │
└─────────┘                                         └─────────────────┘
```

### 4.2. Transições

```
                    ▲ 1. Enter (objeção detectada, mode=tool)
                    │
        IDLE ───────┴─────────▶ ACTIVE
                                  │
                                  │   2. Continue (treatment_status=in_progress)
                                  ├─► ACTIVE(turn+1)
                                  │
                                  │   3. Resolved accepted
                                  ├─► IDLE  (history: accepted)
                                  │
                                  │   4. Resolved deferred
                                  ├─► IDLE  (history: deferred)
                                  │
                                  │   5. Max turns exhausted
                                  ├─► IDLE  (history: exhausted)
                                  │       └─ if action=escalate_to_human:
                                  │             Talk.status=requires_review
                                  │             requires_review_reason=
                                  │               objection_treatment_exhausted
                                  │
                                  │   6. Nova objeção (Y ≠ atual X)
                                  └─► ACTIVE(Y, turn=1)
                                          └─ history: X marcada deferred
```

### 4.3. Regras de prioridade (avaliadas no fim de cada turn)

Quando `state.active_treatment is None`:
- Se `decision.objection_detected` e treatment_mode=tool → entra ACTIVE

Quando `state.active_treatment` está setado:
1. **Cross-objection:** `decision.objection_detected != active.objection_id` → defer atual, entra ACTIVE no novo
2. **Max turns:** `current_treatment_turn >= max_treatment_turns` → exhausted (consulta `on_max_turns_no_resolution.action`)
3. **Resolved accepted:** `decision.treatment_status == "resolved_accepted"` → IDLE, history.accepted
4. **Resolved deferred:** `decision.treatment_status == "resolved_deferred"` → IDLE, history.deferred
5. **Default:** `current_treatment_turn += 1` (continua)

### 4.4. Pseudo-código `apply()`

```python
def apply(
    state: TalkFlowState,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> TalkFlowStateUpdate:
    active = state.active_treatment
    
    if active is None:
        if decision.objection_detected and _is_tool_mode(decision.objection_detected, treeflow):
            return _enter_treatment(decision.objection_detected, treeflow)
        return TalkFlowStateUpdate.noop()
    
    # 1. cross-objection
    if (
        decision.objection_detected
        and decision.objection_detected != active.objection_id
        and _is_tool_mode(decision.objection_detected, treeflow)
    ):
        return _defer_and_enter(active, decision.objection_detected, treeflow)
    
    # 2. max turns
    if active.current_treatment_turn >= active.max_treatment_turns:
        action = _lookup_on_max_turns(active.objection_id, treeflow)
        return _exhausted(active, action)
    
    # 3. resolved accepted
    if decision.treatment_status == "resolved_accepted":
        return _resolve(active, ObjectionResolution.accepted)
    
    # 4. resolved deferred
    if decision.treatment_status == "resolved_deferred":
        return _resolve(active, ObjectionResolution.deferred)
    
    # 5. default
    return _continue_treatment(active)
```

Função pura — recebe estado e decisão, devolve **um delta** (não muta in-place). `post_processing.apply()` persiste.

### 4.5. O que o LLM vê no system prompt quando ACTIVE

A fresh layer ganha bloco condicional:

```
=== TRATAMENTO DE OBJEÇÃO ATIVA ===
Você está argumentando contra: {objection_id}
Descrição: {objection.description}
Turno {current} de {max} (resta {max - current} turno(s) antes de aceitar/desistir)

Argumentos canônicos:
{tool_payload.canonical_arguments_summary}

Conhecimento adicional (do KB):
{chunks recuperados via tool_payload.kb_ref — sistema KB do Plano 3}

Critério de resolução:
{tool_payload.resolution_criteria}

INSTRUÇÕES:
- Argumente até a objeção parecer resolvida
- Em dúvida entre resolved_accepted e resolved_deferred, prefira deferred
- Sinais de deferred: mensagem curta sem entusiasmo, palavras como "tá bom"/"tanto faz"/"sei lá", pontuação seca
- resolved_accepted exige sinal positivo claro: "fechou!", "maravilha", pergunta sobre próximo passo
- Se lead ainda está resistindo: in_progress
- NÃO sugira mudar de node enquanto tratamento estiver ativo
- Se nova objeção surgir, emit objection_detected com novo id (o tratamento atual será diferido)
```

## 5. TurnDecision schema extensions

Em `src/ai_sdr/flowengine/decision.py`, `TurnDecision` ganha 3 campos opcionais:

```python
class TurnDecision(BaseModel):
    # ... campos existentes de FE-01b ...
    
    # NEW (FE-03a)
    objection_detected: str | None = Field(
        default=None,
        description="id da objeção detectada no inbound, ou null. "
                    "Deve corresponder a um id declarado em global_objections ou node.handles_objections."
    )
    treatment_status: Literal["in_progress", "resolved_accepted", "resolved_deferred"] | None = Field(
        default=None,
        description="Status do tratamento ativo. Só válido se active_treatment está setado."
    )
    escalate_requested: bool = Field(
        default=False,
        description="True se o lead pediu explicitamente humano OU LLM decidiu escalar."
    )
```

Schema continua sendo serializado via structured output do LangChain → tools cacheável.

## 6. YAML schema extensions

`TreeFlowLoader` em `src/ai_sdr/flowengine/treeflow_loader.py` ganha parsing de:

### 6.1. Bloco `global_objections`

```yaml
global_objections:
  - id: preco
    description: "lead questiona valor, acha caro"
    treatment_mode: tool             # tool | inline (subnode/subflow não suportados em FE-03a)
    tool_payload:                    # obrigatório se treatment_mode=tool
      canonical_arguments_summary: |
        ROI calculation, parcelamento, comparação com SDR humano
      kb_ref: argumentos_preco       # referência ao sistema KB (Plano 3)
      max_treatment_turns: 3         # 1..10
      expected_turns: 2              # informativo, não usado pelo runtime
      resolution_criteria: |
        Lead demonstrou abertura, aceitou parcelamento, ou pediu pra continuar
      on_max_turns_no_resolution:
        action: gracefully_continue  # gracefully_continue | escalate_to_human
        message_hint: "Reconheça hesitação, ofereça material, retome funil"
```

### 6.2. Bloco `nodes[].handles_objections`

```yaml
nodes:
  - id: qualificacao_economica
    # ...
    handles_objections:
      - id: ja_tentei_curso_online   # objeção escopo de node (não global)
        description: "lead diz que cursos online não funcionam pra ele"
        treatment_mode: tool
        tool_payload: { ... }
```

Resolução: objeção declarada em `handles_objections` é visível só quando o agente está naquele node. Tem precedência sobre `global_objections` em caso de id duplicado (warning no loader).

### 6.3. Bounds validation no loader

`TreeflowLoadError` é levantado se:
- `treatment_mode` ∉ {`tool`, `inline`}
- `treatment_mode=tool` sem `tool_payload`
- `tool_payload.max_treatment_turns` fora de [1, 10]
- `tool_payload.canonical_arguments_summary` vazio (<10 chars)
- `tool_payload.resolution_criteria` vazio (<10 chars)
- `on_max_turns_no_resolution.action` ∉ {`gracefully_continue`, `escalate_to_human`}
- `id` duplicado entre `global_objections` (warning se duplicado com `handles_objections`)
- `description` < 10 chars

Erro fatal: tenant nem inicia.

## 7. Python validator

### 7.1. Regras (port verbatim de v1)

Validador Python em `src/ai_sdr/guardrails/python_validator.py`:

```python
class ValidationViolation(BaseModel):
    rule: Literal["disallowed_price", "unknown_price", "unknown_product"]
    detail: str
    matched_text: str

class ValidationResult(BaseModel):
    ok: bool
    violations: list[ValidationViolation]

def check(
    response_text: str,
    tenant_guardrails: TenantGuardrailsConfig,
) -> ValidationResult: ...
```

Regras aplicadas:
- **`disallowed_price`**: regex de `tenant.guardrails.disallowed_price_pattern` (default: detecta `R$ \d+` ou `\d+ reais`)
- **`unknown_price`**: cada preço encontrado tem que estar em `tenant.guardrails.allowed_prices: list[int]`
- **`unknown_product`**: cada produto mencionado tem que estar em `tenant.guardrails.allowed_products: list[str]`. Match via normalização: `lowercase + collapse internal whitespace + strip leading/trailing whitespace`. Sem unicode normalization, sem stripping de pontuação. Ex: `"Mentoria  Premium"` matches `"mentoria premium"` mas não `"Mentoria-Premium"`.

Config herda integralmente do `tenant.yaml > guardrails` da v1 — zero schema novo do tenant.

### 7.2. Retry + exhausted

```
response_text gerado
   │
   ▼
python_validator.check(response_text, tenant.guardrails)
   │
   ├─► OK → segue pra objection_runtime
   │
   └─► VIOLATION
        │
        ▼
       Retry corretivo:
         system_message = (
           "Sua última resposta violou: {violation.detail}. "
           "Preços permitidos: {allowed_prices}. "
           "Produtos permitidos: {allowed_products}. "
           "Refaça respeitando estritamente."
         )
         + chamar LLM main de novo (1 call extra)
         │
         ├─► OK → segue pra objection_runtime
         │
         └─► VIOLATION 2ª vez:
              sender.send(tenant.guardrails.fallback_text)
              Talk.status = requires_review
              Talk.requires_review_reason = "validator_exhausted"
              pipeline para — não roda objection_runtime nem post_processing
              evento: validator.exhausted
```

`tenant.guardrails.fallback_text` — string obrigatória ≥10 chars no tenant.yaml. Texto de referência sugerido pra novos tenants: `"Deixa eu confirmar isso com a equipe, te retorno em alguns minutos."` (autor pode customizar; validator só exige presença + tamanho mínimo, não conteúdo).

## 8. Matriz de brechas — decisões consolidadas

| ID | Cenário | Decisão |
|---|---|---|
| **A1** | Off-topic puro (lead pergunta fora do funil) | Instrução system prompt redireciona + contador `TalkFlowState.off_topic_count` + escalation aos 3 |
| **A2** | Lead pede humano direto | `TurnDecision.escalate_requested=true` + response_text avisa lead + Talk.status=requires_review |
| **A3** | Prompt injection / manipulação | **NÃO TRATADO em FE-03a** — deferido pra Sentinel (FE-04). Gap explícito no spec. |
| **A4** | Aceitação fake (lead aceita por exaustão) | Instrução conservadora no system prompt cached layer (preferir deferred em dúvida) |
| **A5** | Mídia em vez de texto (áudio/imagem) | **NÃO TRATADO em FE-03a** — depende VoiceAdapter (FE-05). Gap explícito. FE-03a só pode ir a produção depois de FE-05, ou com workaround Meta Business Manager (desabilitar mídia). |
| **A6** | Lead pede troca de produto/funil | Tenant declara `pediu_downsell`, `nao_quer_mentoria` etc como global_objections; mecanismo cross-objection cuida |
| **A7** | Spam de objeções idênticas | Coberto naturalmente por max_treatment_turns; padrão repetitivo entre Talks fica pra Sentinel (FE-04) |
| **A8** | Lead hostil/ofensivo | LLM responde com guidance (system prompt); banimento automático fica pra Sentinel (FE-04) |
| **B1** | Race condition: múltiplas mensagens simultâneas | Worker concatena inbounds pendentes dentro do advisory lock; janela de 2s configurável |
| **B2** | Versionamento de TreeFlow no meio de Talk ativa | `talks.treeflow_version_snapshot` registra versão na abertura; Talk usa snapshot. Versão sumiu → `requires_review` com reason=`treeflow_version_missing` |
| **B3** | Validador falha 2x | Sender envia `tenant.guardrails.fallback_text`; Talk.status=requires_review com reason=`validator_exhausted` |
| **B4** | Contradição interna da TurnDecision | Heurísticas pós-LLM (~30 LOC) corrigem accepted→deferred quando texto contradiz; eventos `decision.contradiction_corrected` emitidos |
| **B5** | Re-engagement após close (lead volta dias depois) | Não tratado — depende close lifecycle (FE-03b); cada Talk nova começa com state limpo |

## 9. Idempotência e consistência transacional

### 9.1. Garantia

Cada `run_turn` executa dentro de **uma única transaction async SQLAlchemy**, incluindo:
- (a) leitura de `TalkFlowState`
- (b) chamada LLM (fora do DB lock; tempo gasto não bloqueia outras transactions)
- (c) `python_validator.check()`
- (d) corrective retry LLM se necessário
- (e) `objection_runtime.apply()` (calcula delta)
- (f) `post_processing.apply()` (persiste delta)
- (g) `sender.send()` (vai pro WhatsApp adapter)
- (h) `audit.outbound_row` (insere em `outbound_messages`)
- (i) `usage.accumulate`

Commit ao final. Rollback em qualquer exception levanta — estado não fica meio-aplicado.

### 9.2. Idempotency key do inbound

`inbound_messages.idempotency_key = sha256(tenant_id + provider + external_id)` (já existe FE-01b). Worker que processa o mesmo job 2x detecta na inserção via UNIQUE constraint → second wins é descartado silenciosamente.

### 9.3. Janela de concatenação (B1)

Dentro do lock, antes da LLM call:

```sql
SELECT id, text FROM inbound_messages
WHERE lead_id = $lead_id
  AND processed_at IS NULL
  AND received_at >= now() - interval '2 seconds'
ORDER BY received_at ASC
FOR UPDATE;
```

Todas marcadas como `processed_at = now()` + concatenadas com `\n` no payload da LLM call. 1 LLM call para N inbounds.

Janela default 2s; configurável via env `WORKER_INBOUND_CONCAT_WINDOW_SECONDS`.

## 10. State migration policy

### 10.1. Defaults Pydantic

Todo campo novo em `TalkFlowState` e suas subentidades **DEVE** ter default Python — never required, never raises. Talks abertas com payload JSONB serializado anterior deserializam sem erro.

Campos adicionados em FE-03a:

```python
class TalkFlowState(BaseModel):
    # ... existentes ...
    off_topic_count: int = Field(
        default=0,
        ge=0,
        # Sem upper bound. A regra de escalation aos 3 strikes vive no runtime
        # (não no schema); contador continua incrementando depois disso pra
        # debug/auditoria, mas só o primeiro cruzamento do threshold dispara
        # a escalation.
    )
    # (sem novo campo escalation; escalation é via Talk.status, não TalkFlowState)
```

### 10.2. ActiveTreatment

Schema existente. Sem mudanças. Tudo já tinha defaults em FE-01a.

### 10.3. ObjectionHistoryEntry

Schema existente. `resolution: ObjectionResolution | None = None` permite registros legados pré-FE-03a (nenhum existe, mas precaução).

## 11. `Talk.requires_review_reason`

### 11.1. Nova coluna

Migration **0025** (próxima disponível após 0024):

```python
op.add_column(
    "talks",
    sa.Column(
        "requires_review_reason",
        sa.String(64),
        nullable=True,
    ),
)
```

Valores enum (string livre, validado em código):

```python
RequiresReviewReason = Literal[
    "escalation_requested",            # lead pediu humano (A2)
    "off_topic_exhausted",             # 3 strikes off-topic (A1)
    "validator_exhausted",             # validador exhauriu retries (B3)
    "treeflow_version_missing",        # snapshot sumiu do disco (B2)
    "objection_treatment_exhausted",   # max turns + action=escalate
]
```

### 11.2. UI Console (referência)

Plano 11 (HITL Console) consome essa coluna no futuro — coluna `Razão` na lista de leads `requires_review`. Por enquanto fica gravada e queryable via SQL.

## 12. Observabilidade

### 12.1. Eventos structlog emitidos (vão pra LangSmith via wiring do Plano 10)

| Event | Quando | Payload |
|---|---|---|
| `objection.treatment.entered` | IDLE → ACTIVE | objection_id, max_turns, mode (global/node-scoped) |
| `objection.treatment.continued` | ACTIVE → ACTIVE | objection_id, current_turn |
| `objection.treatment.resolved` | ACTIVE → IDLE | objection_id, status (accepted/deferred), total_turns |
| `objection.treatment.cross_swap` | ACTIVE(X) → ACTIVE(Y) | from_id, to_id |
| `objection.treatment.exhausted` | max turns hit | objection_id, action_taken (gracefully_continue / escalate_to_human) |
| `objection.hallucinated_id` | LLM emitiu id inexistente | id_received |
| `decision.contradiction_corrected` | heurística aplicada | field, original, corrected |
| `offtopic.detected` | LLM marcou off-topic | count |
| `offtopic.escalated` | count atingiu threshold | final_count |
| `escalation.requested` | TurnDecision.escalate_requested=true | (vazio) |
| `validator.violation` | 1ª violação | rule, matched_text |
| `validator.violation_retry` | retry corretivo | rule |
| `validator.exhausted` | 2ª violação | rule, fallback_sent |
| `treeflow.version_missing` | snapshot não está no disco | version_requested |

Todos os eventos incluem context implícito: `tenant_id`, `talk_id`, `lead_id`, `turn_index`.

### 12.2. Dependência observabilidade

Sem mudança em LangSmith ou Plano 10 — só usa o wiring existente. Eventos novos vão pra mesma pipeline.

## 13. Testing strategy

### 13.1. Unit tests (`tests/unit/flowengine/`)

20 arquivos novos:

```
test_objection_runtime_idle_to_active.py
test_objection_runtime_continue.py
test_objection_runtime_resolved_accepted.py
test_objection_runtime_resolved_deferred.py
test_objection_runtime_exhausted_graceful.py
test_objection_runtime_exhausted_escalate.py
test_objection_runtime_cross_objection.py
test_objection_runtime_hallucinated_id.py
test_objection_runtime_contradiction_correction.py
test_objection_runtime_treatment_status_when_idle.py
test_python_validator_price_whitelist.py
test_python_validator_product_whitelist.py
test_python_validator_disallowed_pattern.py
test_python_validator_retry_loop.py
test_python_validator_exhausted_fallback.py
test_python_validator_fallback_text_required.py
test_offtopic_counter_and_escalate.py
test_escalation_request_via_turndecision.py
test_treeflow_loader_global_objections.py
test_treeflow_loader_handles_objections.py
test_treeflow_loader_bounds_validation.py
```

### 13.2. Integration tests (`tests/integration/flowengine/`)

6 arquivos novos (com `FakeListChatModel` controlando TurnDecision):

```
test_treatment_3_turn_resolve_accepted.py
test_treatment_3_turn_exhausted_escalate.py
test_treatment_cross_objection_swap.py
test_multi_message_concat.py
test_validator_fallback_e2e.py
test_versioning_snapshot_missing.py
```

### 13.3. Fixtures necessárias

Avelum fixture YAML existente em `tests/fixtures/` é minimal. FE-03a precisa:

```
tests/fixtures/avelum_v2_with_objections.yaml      # tenant com global_objections completas
tests/fixtures/avelum_v2_node_objections.yaml      # tenant com handles_objections por node
tests/fixtures/invalid_max_turns.yaml              # pra testar bounds validation
tests/fixtures/invalid_treatment_mode.yaml         # pra testar bounds validation
```

### 13.4. Não testado por unit/integration (limitações)

- Qualidade do LLM em seguir instrução conservadora (testável só com live LLM)
- Comportamento real do WhatsApp Cloud com mídia (delegado FE-05)

## 14. Migration / cutover

### 14.1. Tenants v1 → v2

Cada tenant existente que pular `architecture_version` de 1 pra 2 **precisa reescrever seu TreeFlow YAML**:

- Bloco `objections` (Plano 4a) → `global_objections` com `treatment_mode` + `tool_payload`
- Bloco `objections_per_node` → `nodes[].handles_objections`
- Critic LLM config → não existe em v2 (`tenant.llm.critic` é ignorado)

Sem migration automática — autor faz à mão. Documentar conversão em `CLAUDE.md` quando FE-03a for mergeada.

### 14.2. Coexistência v1/v2

A flag `tenants.architecture_version` (já existe desde FE-01a, migration 0023) controla qual pipeline o worker invoca. Tenants em `architecture_version=1` continuam usando LangGraph + classifier + critic. Tenants em `architecture_version=2` usam FlowEngine + python_validator + objection_runtime.

Big-bang flip por tenant — quando autor migra YAML, opera flag e tenant passa pra v2.

### 14.3. Decommission v1

Quando o último tenant migrar pra v2:
- `langgraph/` tables podem ser dropadas
- `guardrails/critic.py` e referências viram dead code → cleanup commit
- `objection_classifier/` (Plano 4a) similar

Esse cleanup é **plano dedicado pós-FE-03a/b/c** (e provavelmente FE-04+). Não escopo de FE-03a.

## 15. Out of scope (defer to FE-XX)

| Item | Fase |
|---|---|
| Sentinel anti-prompt-injection | FE-04 |
| Voice in (Whisper) / out (ElevenLabs) | FE-05 |
| Humanização (chunking + typing) | FE-03b |
| Close lifecycle (inactivity, turn limit, etc.) | FE-03b |
| Re-engagement após Talk close | FE-03b |
| `on_collected` actions runtime | FE-03c |
| Adapter framework genérico (CRM/Calendar/etc.) | FE-03c |
| Event bus (Postgres LISTEN/NOTIFY) | FE-06 |
| Pricing table + cost tracking | FE-06 |
| Experiments + HITL approval workflow | FE-07 |
| Treeflow improvement suggestions batch | FE-07 |
| Console HITL consumindo `requires_review` queue | FE-07 (integração com Plano 11 console) |

## 16. Decisões abertas — nenhuma

Todas as decisões substantivas do escopo FE-03a foram travadas no brainstorm. Implementação pode prosseguir direto pra `superpowers:writing-plans` sem precisar voltar pra perguntas.

---

**Autoria:** brainstorm conduzido via `superpowers:brainstorming` em 2026-06-09. Decisões registradas turn-a-turn com user.
