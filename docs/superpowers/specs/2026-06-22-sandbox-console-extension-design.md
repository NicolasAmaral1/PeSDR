# Sandbox como extensão do Console HITL — Design Spec

**Data:** 2026-06-22
**Status:** Draft pra revisão (Nicolas + Pedro)
**Tipo:** Architectural design — proposta de implementação, ainda não plan executável
**Autor:** Pedro Aranda (com Claude Code)
**Relaciona-se com:**
- [Plano 11 — HITL Console](../plans/2026-05-26-hitl-console.md) (base que será estendida)
- [`2026-06-08-flow-engine-architecture-design.md`](./2026-06-08-flow-engine-architecture-design.md) (FlowEngine v2 — pipeline `run_turn`)
- [FE-03c Actions Adapter](./2026-06-12-fe03c-actions-adapter-framework-design.md) (idempotency patterns reusados)
**Não conflita com:** nenhum ADR. Adiciona feature operacional sem mexer em arquitetura core.

---

## 1. TL;DR

Construir uma **interface web simulando WhatsApp** pra testar conversações end-to-end sem depender de Meta Business Manager, Respondi ou RD Station rodando de verdade. **Estende o Console HITL existente** (FastAPI + Jinja2 + HTMX) com rotas novas sob `/console/{slug}/sandbox/...`, reaproveita:

- `FakeMessagingAdapter` (zero I/O — não toca Meta API)
- `run_turn` do FlowEngine v2 (mesmo pipeline de produção)
- RLS, `set_tenant_context`, `pg_advisory_xact_lock` (garantias de isolamento intactas)
- Seeds idempotentes (`seed_manoela_v2.py`, etc)
- Auth + RBAC do console (login via `ai-sdr users`)

**O sandbox NÃO mocka isolamento.** Usa o pipeline real — só troca o adapter de saída por `FakeMessagingAdapter`. Isso garante que comportamentos detectados em sandbox replicam em produção.

**Plano fasado:** 4 fases obrigatórias (S1–S4, ~5-8 dias dev focado) + 3 opcionais. **Antes de codar, escolher 1 das 3 opções de LLM** (real/fake/toggle — detalhadas em §6).

---

## 2. Contexto e motivação

### 2.1. Problema

Hoje o único caminho pra testar uma conversa end-to-end no PeSDR é:

- **`ai-sdr simulate` CLI** — REPL terminal, 1 talk por vez, entrada via `stdin`, output via `stdout`. Funciona pra dev/debug mas:
  - Não dá pra mostrar pra Lana/Manoela (não-devs)
  - Não testa visualmente como mensagem aparece pro usuário final
  - Difícil rodar múltiplos talks em paralelo
  - State debugging exige imprint manual no código
- **Tests integration** — automatizados, mas exigem ler código pra entender o cenário
- **Smoke em produção real** — exige Meta Business approval, número WhatsApp ativo, RD Station configurado. **Bloqueante hoje** porque a Lahna ainda não assinou RD Station da Manoela.

### 2.2. O que precisa testar (sem dependência externa)

Lista pragmática dos cenários que o sandbox deve cobrir:

| Cenário | Hoje testável? | Sandbox cobre? |
|---|---|---|
| Mensagem inicial proativa do agente (greeting) | Parcial (simulate CLI) | ✅ |
| Coleta de campos em múltiplos turnos | Parcial | ✅ |
| Objection handling (`__classifier`) | Parcial | ✅ |
| Guardrails bloqueando resposta (preço fora da whitelist) | Parcial | ✅ |
| Múltiplos talks simultâneos do MESMO tenant | ❌ | ✅ |
| Múltiplos talks de tenants DIFERENTES sem vazamento | ❌ | ✅ |
| State recovery entre turnos (TalkFlowState persiste) | ✅ | ✅ |
| Close lifecycle (FE-03b: completion rule, inactivity) | Parcial | ✅ |
| `on_collected` actions disparando (CRM adapter — Fake) | Parcial | ✅ |
| `__prefilled_fields__` (campos vindos de form/CRM via simulação) | ❌ | ✅ |
| Humanização (multi-chunk, typing indicator) | ❌ | ✅ |
| Voice I/O (FE-05) | ❌ | ✅ (com FakeVoiceAdapter) |

### 2.3. Preocupação central: "vazamento" entre talks

Definição **operacional** de vazamento (não LGPD/dados):

- **Talk A do tenant X** não pode acessar `state.collected` de **Talk B do tenant Y**
- **Talk A do lead α** não pode confundir histórico de mensagens com **Talk B do lead β** (mesmo tenant)
- **TreeFlow A v1.0** sendo executado em Talk A não pode ser afetado por mudanças em **TreeFlow A v2.0** em Talk B
- **Concorrência:** 2 mensagens chegando simultaneamente do MESMO lead não podem corromper state

Esta preocupação é **a razão principal** desta spec ser cuidadosa em "não quebrar arquitetura mais do que estruturar". §5 dedica seção inteira pras garantias.

---

## 3. Decisão central

**Estender o Console HITL existente** (`src/ai_sdr/web/`) com rotas novas sob `/console/{slug}/sandbox/...` que invocam `run_turn` real do FlowEngine v2, usando `FakeMessagingAdapter` no lugar do `WhatsAppCloudAPIAdapter`.

### 3.1. Por que estender o Console (não criar app separado)

| Critério | Estender Console (escolha) | App standalone |
|---|---|---|
| Auth + RBAC | Reaproveita (login `ai-sdr users` já existe) | Reimplementar |
| Stack | Jinja2 + HTMX (consistente) | Novo framework (React/Vue) ou mesma stack duplicada |
| Routes scoped por tenant | Natural via path `/console/{slug}/` | Implementar manualmente |
| RLS aplicada | Automática (mesmo session.begin do console) | Implementar manualmente |
| Deploy | Mesmo container | Novo container ou rota |
| Pedro/Lana já conhecem | Sim | Não |
| Código extra estimado | ~600 LOC | ~1500+ LOC |

Custo de extensão é < 50% de um app dedicado, com 100% das funcionalidades necessárias.

### 3.2. Feature flag por tenant

```yaml
# tenant.yaml
console:
  enabled: true                       # existente — habilita /console
  sandbox:                            # NOVO bloco
    enabled: true                     # gating: rotas /sandbox só se true
    llm_mode: "real"                  # 'real' | 'fake' | 'toggle' (ver §6)
    max_concurrent_talks: 10          # safety net
```

Tenants em produção real (clientes pagantes) podem desligar (`enabled: false`) → rotas `/sandbox/*` retornam 404. Tenants de dev/staging ligam (`true`).

### 3.3. Princípios não-negociáveis

Pra evitar "quebrar arquitetura":

1. **Sandbox NUNCA mocka o pipeline core.** Usa `run_turn` real do FlowEngine v2.
2. **Sandbox NUNCA mocka RLS.** Cada rota chama `set_tenant_context()`.
3. **Sandbox NUNCA bypassa o advisory lock.** Concorrência de mensagens do mesmo lead serializa.
4. **Sandbox NUNCA cria Talk sem `is_sandbox=true` flag.** Talks reais e de teste ficam distinguíveis em queries operacionais.
5. **Sandbox NUNCA toca CRMs externos reais.** `CRMActionAdapter` é configurado pro `LoggingActionAdapter` (Fake) no contexto do sandbox.
6. **Sandbox NUNCA dispara HSM Meta real.** `FakeMessagingAdapter.send_template` só loga em memória.

Estes 6 princípios são **invariantes operacionais** — testes integration verificam cada um (§7).

---

## 4. Arquitetura técnica

### 4.1. Rotas novas

```
GET  /console/{slug}/sandbox                            → dashboard de talks de teste
POST /console/{slug}/sandbox/talks/new                  → cria Lead+Talk sandbox
GET  /console/{slug}/sandbox/talks/{talk_id}            → chat UI tipo WhatsApp
POST /console/{slug}/sandbox/talks/{talk_id}/send       → envia inbound, dispara run_turn
GET  /console/{slug}/sandbox/talks/{talk_id}/messages   → HTMX partial: histórico (polling)
GET  /console/{slug}/sandbox/talks/{talk_id}/state      → HTMX partial: debugger painel
DELETE /console/{slug}/sandbox/talks/{talk_id}          → encerra Talk + soft-delete Lead
POST /console/{slug}/sandbox/talks/{talk_id}/inject-event → simula CRM event (S3+)
```

### 4.2. Estrutura de arquivos

```
src/ai_sdr/web/
├── sandbox/                                 # NOVO subpackage
│   ├── __init__.py
│   ├── routes.py                            # APIRouter pra todas as rotas /sandbox/*
│   ├── deps.py                              # Depends: sandbox-only adapter overrides
│   ├── service.py                           # SandboxService: cria Lead/Talk fake, dispatch run_turn
│   └── events.py                            # (S5) simulação de eventos externos (CRM, follow-up)
├── templates/
│   ├── sandbox_dashboard.html               # NOVO — lista talks ativos
│   ├── sandbox_chat.html                    # NOVO — chat UI tipo WhatsApp
│   └── _sandbox/                            # NOVO — partials HTMX
│       ├── _message_bubble.html
│       ├── _state_debugger.html
│       └── _new_talk_modal.html
└── routes.py                                # MODIFICADO — registra sandbox router
```

### 4.3. Schema changes

Migration nova `0032_sandbox_flag_on_talks.py`:

```sql
ALTER TABLE talks ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX ix_talks_sandbox ON talks (tenant_id, is_sandbox) WHERE is_sandbox = true;
```

**Por que `is_sandbox` flag (vs schema separado):**
- Permite reuso de TODOS os repositórios, modelos, queries existentes
- Filtro fácil em queries operacionais (excluir sandbox dos relatórios)
- Tests integration podem rodar contra mesma table com flag
- Sem migração de dados na transição
- Index parcial = custo zero pra produção

### 4.4. Fluxo de dados (sequence diagram)

```
[Operador no browser]                  [Sandbox routes]                  [run_turn pipeline]                  [DB]
        │                                    │                                    │                            │
        │ POST /sandbox/talks/new            │                                    │                            │
        │ {treeflow_id, lead_name}           │                                    │                            │
        │───────────────────────────────────▶│                                    │                            │
        │                                    │ set_tenant_context(tenant.id)      │                            │
        │                                    │───────────────────────────────────────────────────────────────▶│
        │                                    │ INSERT lead (is_sandbox derivado de talk)                       │
        │                                    │───────────────────────────────────────────────────────────────▶│
        │                                    │ INSERT talk (is_sandbox=true)                                   │
        │                                    │───────────────────────────────────────────────────────────────▶│
        │                                    │ INSERT talkflow_state (collected={}, current_node=entry_node)   │
        │                                    │───────────────────────────────────────────────────────────────▶│
        │                                    │ (opcional) await run_turn(inbound="") → greeting proativo       │
        │                                    │           usando FakeMessagingAdapter                           │
        │                                    │───────────────────────────────────────▶│                       │
        │                                    │                                        │ pg_advisory_xact_lock │
        │                                    │                                        │──────────────────────▶│
        │                                    │                                        │ LLM call (real/fake)  │
        │                                    │                                        │ apply_decision        │
        │                                    │                                        │ FakeMessaging.send    │
        │                                    │                                        │ commit checkpoint     │
        │                                    │                                        │──────────────────────▶│
        │ 200 OK {talk_id}                   │                                        │                       │
        │◀───────────────────────────────────│                                        │                       │
        │                                    │                                        │                       │
        │ GET /sandbox/talks/{id}            │                                        │                       │
        │───────────────────────────────────▶│ render template sandbox_chat.html      │                       │
        │ HTML chat UI                       │                                        │                       │
        │◀───────────────────────────────────│                                        │                       │
        │                                    │                                        │                       │
        │ HTMX poll /messages a cada 2s      │                                        │                       │
        │───────────────────────────────────▶│ SELECT messages FROM talkflow_states   │                       │
        │                                    │───────────────────────────────────────────────────────────────▶│
        │ HTML partial atualizado            │                                        │                       │
        │◀───────────────────────────────────│                                        │                       │
        │                                    │                                        │                       │
        │ POST /talks/{id}/send "oi tudo bem"│                                        │                       │
        │───────────────────────────────────▶│ run_turn(inbound="oi tudo bem")        │                       │
        │                                    │───────────────────────────────────────▶│ (mesmo fluxo acima)  │
        │ 200 OK                             │                                        │                       │
        │◀───────────────────────────────────│                                        │                       │
        │                                    │                                        │                       │
```

### 4.5. Componentes nucleares

#### `SandboxService` (orquestra criação + dispatch)

```python
class SandboxService:
    def __init__(self, session, tenant_loader, treeflow_loader):
        self.session = session
        self.tenant_loader = tenant_loader
        self.treeflow_loader = treeflow_loader

    async def create_talk(
        self,
        *,
        tenant_slug: str,
        treeflow_id: str,
        lead_name: str | None = None,
        phone_e164: str | None = None,
    ) -> Talk:
        """Cria Lead fake (sandbox-tagged) + Talk + state inicial."""

    async def send_inbound(
        self,
        *,
        talk_id: UUID,
        text: str,
        media: Any | None = None,
    ) -> RunTurnResult:
        """Dispatch pra run_turn real com FakeMessagingAdapter."""

    async def get_state(self, talk_id: UUID) -> TalkFlowState:
        """Retorna state atual pro debugger painel."""

    async def list_messages(self, talk_id: UUID) -> list[Message]:
        """Retorna histórico de mensagens (inbound + outbound)."""
```

#### Adapter overrides (sandbox-only)

```python
# src/ai_sdr/web/sandbox/deps.py
async def get_sandbox_adapter() -> MessagingAdapter:
    """Sempre retorna FakeMessagingAdapter NOVO por request.

    Cada request tem sua instância — evita leak de sent_messages entre
    talks/tenants no debugger.
    """
    return FakeMessagingAdapter()

async def get_sandbox_action_adapter() -> ActionAdapter:
    """Sempre LoggingActionAdapter pra actions on_collected."""
    return LoggingActionAdapter(...)
```

---

## 5. Garantias de isolamento (resposta direta à preocupação do Pedro)

### 5.1. Matriz de cenários de vazamento e como cada um é prevenido

| Cenário | Garantia | Mecanismo |
|---|---|---|
| **A — Tenants diferentes** | Talk tenant X não vê state de Talk tenant Y | RLS Postgres: `tenant_isolation` policy força `tenant_id = current_setting('app.current_tenant')::uuid` em TODA tabela tenant-scoped. Sandbox route chama `set_tenant_context(session, tenant.id)` antes de qualquer query. |
| **B — Leads diferentes no mesmo tenant** | Talk lead α não confunde histórico com Talk lead β | `thread_id` único = `(tenant_id, talk_id)`. LangGraph checkpointer salva state isolado por thread_id. State recovery em cada `run_turn` busca por `talkflow_state.talk_id`. |
| **C — TreeFlows diferentes no mesmo tenant** | Lógica de Talk A com TreeFlow X não vaza pra Talk B com TreeFlow Y | Cada Talk grava `treeflow_version_id` = snapshot imutável do YAML. `run_turn` resolve TreeflowVersion pelo Talk, não tenant. |
| **D — Versões diferentes do mesmo TreeFlow** | Talk em v0.3.0 não pega mudanças que vão em v0.4.0 | Snapshot imutável (`treeflow_versions.content_hash`). Mesmo se YAML em disk mudar, Talk continua na versão original até trocar manualmente. |
| **E — Concorrência: 2 inbounds simultâneos do mesmo lead** | State não corrompe | `pg_advisory_xact_lock((tenant_id, lead_id))` no topo de `run_turn`. Segundo inbound bloqueia até primeiro commitar. |
| **F — Worker do sandbox vs worker de produção** | Sandbox não dispara jobs de produção | Sandbox usa síncrono `await run_turn(...)` direto (sem arq enqueue). Worker arq de prod não é tocado. |
| **G — FakeMessagingAdapter de Talk A vs Talk B** | `.sent_messages` de Talk A não vê msgs de Talk B | `get_sandbox_adapter()` dep retorna **instância nova por request**. Persistência de outbound vai pro `outbound_messages` table (RLS aplicada). |

### 5.2. Tests de isolamento explícitos (S4)

Tests integration que DEVEM existir antes do sandbox virar produção:

```python
# tests/integration/test_sandbox_isolation.py

@pytest.mark.integration
async def test_sandbox_does_not_leak_across_tenants(...):
    """Cria Talk A no tenant X, Talk B no tenant Y.
    Manda mensagem em ambos. Verifica que state.collected é independente.
    """

@pytest.mark.integration
async def test_sandbox_does_not_leak_across_leads_same_tenant(...):
    """Cria 2 Talks no mesmo tenant com leads diferentes.
    Manda mensagens. Verifica que histórico de cada Talk é independente.
    """

@pytest.mark.integration
async def test_sandbox_serializes_concurrent_inbounds(...):
    """Manda 2 sends simultâneos pro mesmo Talk via asyncio.gather.
    Verifica que advisory_lock serializa e turn_count incrementa só 2x
    (não duas vezes pra cada).
    """

@pytest.mark.integration
async def test_sandbox_does_not_send_to_meta_api(...):
    """Cria Talk + send. Inspeciona o que FakeMessagingAdapter.sent_messages
    tem (deve ter), e MOCKA WhatsAppCloudAPIAdapter pra erro 500 — verifica
    que mesmo se chamada, a rota não usa.
    """

@pytest.mark.integration
async def test_sandbox_action_adapter_does_not_call_rdstation(...):
    """TreeFlow do sandbox com on_collected: crm. Send mensagem que coleta
    campo gatilho. Verifica que action vai pro LoggingActionAdapter (Fake),
    NÃO pro RDStationCRMBackend real.
    """

@pytest.mark.integration
async def test_sandbox_treeflow_version_isolation(...):
    """Talk começa em treeflow v0.3.0. Atualiza arquivo YAML em disco pra v0.4.0
    (sem bump de version manual). Manda nova mensagem. Verifica que Talk continua
    consumindo snapshot v0.3.0 do DB.
    """
```

### 5.3. Mecanismos de produção 100% preservados no sandbox

| Mecanismo | Onde aplica | Sandbox afeta? |
|---|---|---|
| RLS policy `tenant_isolation` em talks/talkflow_states/messages | DB | ❌ Não afeta — sandbox respeita |
| `set_tenant_context()` em cada route | Application | ❌ Não afeta |
| `pg_advisory_xact_lock` per (tenant_id, lead_id) | run_turn | ❌ Não afeta |
| LangGraph checkpointer (PostgresSaver) | Pipeline | ❌ Não afeta |
| TreeflowVersion snapshot imutável | Talk creation | ❌ Não afeta |
| Outbound audit em `outbound_messages` | Após send | ✅ Mantém audit (filtro: `triggered_by='sandbox'`) |

---

## 6. LLM no sandbox — 3 opções pra Pedro decidir

Pedro pediu pra eu levantar todas as opções. Aqui está análise técnica completa:

### 6.1. Opção L1: LLM real (Anthropic) sempre

```yaml
console:
  sandbox:
    llm_mode: "real"
```

**Como funciona:** Toda mensagem passa por `init_chat_model(tenant.llm.default)` real. Claude Sonnet/Haiku respondem usando `secrets.enc.yaml > anthropic_key`.

**Pros:**
- ✅ Testa **qualidade real** da resposta (prompt + persona + objections)
- ✅ Testa **guardrails reais** (whitelist + critic pass)
- ✅ **Custos previsíveis** (~$0.005–0.02 por turno na Manoela)
- ✅ Comportamento **idêntico a produção** — bugs detectados em sandbox ocorrem em prod
- ✅ Permite avaliar **prompt engineering** com lead-types diferentes
- ✅ Validação de **prompt caching** (cache hit/miss reais)

**Cons:**
- ❌ Custo monetário (~$0.01/turno × 100 turnos teste/dia = ~$1/dia)
- ❌ Latência real (~2-5s por turno) — não é instantâneo
- ❌ Não-determinístico (LLM dá respostas diferentes na mesma pergunta) — dificulta tests automated
- ❌ Depende de `ANTHROPIC_API_KEY` válida (downtime do provider = sandbox quebra)

**Quando usar:** Validação de qualidade pré-produção, demo pra clientes, prompt iteration.

### 6.2. Opção L2: LLM fake/scripted por padrão, real opt-in

```yaml
console:
  sandbox:
    llm_mode: "fake"
```

**Como funciona:** Usa LangChain `FakeListChatModel` que retorna respostas pré-scriptadas em ordem fixa. Operador define o script via JSON em `tests/fixtures/sandbox_scripts/{scenario}.json` ou via UI inline.

```python
# tests/fixtures/sandbox_scripts/manoela_qualificacao_happy_path.json
[
  "Oi! Que bom te conhecer 👋 Como posso te chamar?",
  "Show, {nome}! Pra entender melhor seu momento, qual seu faturamento mensal aproximado?",
  "Perfeito, com esse faturamento a Mentoria faz total sentido. Posso te apresentar?"
]
```

**Pros:**
- ✅ **Custo zero** (não chama nenhum LLM)
- ✅ **Determinístico** — mesmo input = mesma saída sempre
- ✅ Latência **instantânea** (~0ms)
- ✅ Roda em **CI sem credentials**
- ✅ Útil pra testar **infra/UI** isoladamente

**Cons:**
- ❌ NÃO testa qualidade real da resposta
- ❌ NÃO testa prompt engineering
- ❌ Guardrails ficam triviais (resposta scripted nunca viola whitelist)
- ❌ Operador precisa escrever scripts manualmente (overhead)
- ❌ Não detecta regressão de prompt/persona

**Quando usar:** CI, dev local sem internet, testar UI/infra sem variabilidade do LLM.

### 6.3. Opção L3: Toggle visual na UI (híbrido — recomendado pra primeira versão)

```yaml
console:
  sandbox:
    llm_mode: "toggle"          # permite ambos
    default: "fake"             # default ao abrir nova Talk
```

**Como funciona:** Switch visual na criação de Talk: **"🤖 Modo dev (fake — instantâneo)"** vs **"🧠 Modo prod (Anthropic — qualidade real)"**.

Estado armazenado em `talks.sandbox_llm_mode` (column nova). Mesma Talk usa o mesmo mode pra todos os turnos (não troca no meio).

**Pros:**
- ✅ **Flexibilidade total** — operador escolhe por cenário
- ✅ CI sempre usa Fake (determinístico)
- ✅ Dev local opcional (custo zero por padrão, real on-demand)
- ✅ Demo pra cliente: Real
- ✅ Tests integration podem testar **ambos os modos**

**Cons:**
- ⚠️ Mais complexo (~30% LOC extra na S2)
- ⚠️ Operador pode confundir resultado se esquecer qual mode tá usando
- ⚠️ Talks "mode fake" ficam contamînando dashboards de qualidade se não filtrar

**Quando usar:** Quase sempre. Cobre os 2 casos com toggle leve.

### 6.4. Comparação resumida

| Critério | L1 Real | L2 Fake | L3 Toggle |
|---|---|---|---|
| Custo | $$ | $0 | $-$$ |
| Qualidade real | ✅ | ❌ | ✅ on-demand |
| Determinístico (CI) | ❌ | ✅ | ✅ no fake |
| Complexidade extra | baseline | -10% | +30% |
| Recomendação | bom pra demo/prompt iteration | bom pra CI/dev offline | **mais flexível** |

### 6.5. Minha recomendação (pra Nicolas/Pedro decidirem)

**L3 (Toggle)** se aceitar +30% LOC na S2. Cobre 100% dos casos com baixo custo de complexidade.

**L1 (Real)** se quiserem máxima simplicidade — começa simples, adiciona fake depois se virar dor.

**L2 (Fake)** só se priorizarem CI/cost zero — ruim pra demo/validação de qualidade.

---

## 7. Plano de implementação fasado

### 7.1. Fases obrigatórias (~5-8 dias dev focado)

#### S1 — Foundation (1-2 dias)

| Task | Escopo | Estimativa |
|---|---|---|
| S1.1 | Migration `0032_sandbox_flag_on_talks.py` (talks.is_sandbox + partial index) | S |
| S1.2 | Schema `tenant_yaml.py` — `SandboxConfig` block (enabled, llm_mode, max_concurrent_talks) | S |
| S1.3 | Subpackage `src/ai_sdr/web/sandbox/` (routes, deps, service skeleton) | S |
| S1.4 | Rota `GET /console/{slug}/sandbox` + template dashboard | S |
| S1.5 | Rota `POST /console/{slug}/sandbox/talks/new` — cria Lead+Talk fake | M |
| S1.6 | Tests unit: SandboxConfig schema, route 404 sem feature flag | S |

#### S2 — Chat UI (2-3 dias)

| Task | Escopo | Estimativa |
|---|---|---|
| S2.1 | Template `sandbox_chat.html` (bubbles, sender, timestamp) | M |
| S2.2 | Rota `GET /talks/{id}` renderiza chat UI | S |
| S2.3 | Rota `POST /talks/{id}/send` invoca `SandboxService.send_inbound` → `run_turn` real com `FakeMessagingAdapter` | M |
| S2.4 | HTMX polling `/messages` partial (cada 2s) atualiza histórico | M |
| S2.5 | LLM toggle (se Opção L3 escolhida): coluna `talks.sandbox_llm_mode` + UI switch | M |
| S2.6 | Tests integration: send-receive 3 turnos com `FakeListChatModel` | M |

#### S3 — State debugger (1-2 dias)

| Task | Escopo | Estimativa |
|---|---|---|
| S3.1 | Template partial `_state_debugger.html` (collected, current_node, objections_handled, __prefilled_fields__, turn_count) | S |
| S3.2 | Rota `GET /talks/{id}/state` HTMX partial | S |
| S3.3 | Mostrar outbound_log (FakeMessagingAdapter.sent_messages do request atual) | S |
| S3.4 | Refresh manual + auto após cada send | S |
| S3.5 | Visualizar ações `on_collected` disparadas (action_executions table) | M |

#### S4 — Isolamento garantido (1 dia)

| Task | Escopo | Estimativa |
|---|---|---|
| S4.1 | Test integration: cross-tenant isolation (cenário A em §5.1) | S |
| S4.2 | Test integration: cross-lead isolation (cenário B) | S |
| S4.3 | Test integration: cross-treeflow isolation (cenário C) | S |
| S4.4 | Test integration: concorrência via asyncio.gather (cenário E) | M |
| S4.5 | Test integration: sandbox não toca Meta nem RDStation (cenários F, G) | M |
| S4.6 | Test integration: TreeflowVersion snapshot imutável (cenário D) | S |

### 7.2. Fases opcionais (after MVP)

#### S5 — Multi-talk dashboard

Lista todos os talks ativos do sandbox com badges (online/closed), modo LLM, tenant. Permite abrir vários em abas.

#### S6 — Recording / Replay

Salvar uma sessão de teste (sequência de inbounds + state final) como JSON em `tests/fixtures/sandbox_recordings/`. Replay reproduz a sessão com mesmo input + LLM mode escolhido.

#### S7 — Event injection (CRM webhook simulado)

Permite simular evento CRM (contact_created via RD Station) **dentro do sandbox**, disparando o caminho de criação de Talk via `CRMInboundAdapter` (quando esse for implementado conforme PR #22).

---

## 8. Não-objetivos (fora de escopo desta spec)

- ❌ Sandbox em ambiente mobile (PWA) — só web desktop por enquanto
- ❌ Suporte a múltiplos operadores simultaneamente no mesmo Talk (collaborative editing) — 1 por vez
- ❌ Substituir o `ai-sdr simulate` CLI — sandbox web COEXISTE com CLI
- ❌ Generation de massa: criar 1000 talks em paralelo pra load test — usa tests dedicados
- ❌ A/B test de prompts dentro do sandbox — fica pro plano de prompt iteration
- ❌ Recorder de áudio real (microfone web) — só upload de WAV/MP3 pré-gravado nas fases opcionais

---

## 9. Riscos e mitigações

| Risco | Severidade | Mitigação |
|---|---|---|
| Sandbox custar caro com LLM real (loop bug) | Média | `tenant.console.sandbox.max_concurrent_talks: 10` + alerta se > N turns em < M minutos |
| Operador testar em produção achando que é sandbox | Alta | Badge "🧪 SANDBOX" gigante no topo + cor diferente do background + warning em pop-up ao abrir |
| Pre-população de campos pulando turnos (`__prefilled_fields__`) confunde teste | Média | Sandbox sempre cria Talk com `collected={}` por padrão (a menos que operador queira simular form ingestion) |
| Tests integration de isolamento falham silenciosamente | Alta | CI bloqueia merge se test_sandbox_isolation.py falhar |
| `talks.is_sandbox=true` rows vazem pra relatórios de produção | Baixa | Queries operacionais sempre filtram `WHERE is_sandbox = false`. Documentar em CLAUDE.md. |
| Sandbox quebrar se schema `talks` mudar | Média | Tests integration do sandbox rodam toda PR — quebra é visível |
| Operador esquecer modo LLM (fake vs real) | Baixa | UI mostra badge persistente "🤖 fake" ou "🧠 real" |

---

## 10. Open questions

1. **Decisão LLM (§6)** — qual opção? L1 (Real) / L2 (Fake) / L3 (Toggle)?
2. **Pre-popular `collected` no sandbox?** Por exemplo, "simular lead que veio de form com nome+telefone pré-preenchidos"? Útil pra testar a integração form → CRM da PR #22 quando implementada. Vale ter um botão "criar Talk com form simulado" no S1.5?
3. **Persistência cross-restart?** Talks sandbox sobrevivem a restart do servidor? Resposta default: sim (vão pro DB). Pedro/Nicolas prefere TTL de N horas pra limpar lixo?
4. **Sandbox em prod?** Habilitar `console.sandbox.enabled: true` em tenant de produção real é seguro? Resposta default: sim, com aviso visual MASSIVE. Mas talvez melhor restringir a `is_platform_admin=true` users?
5. **Multi-tenant operator?** Operador com acesso a 2 tenants vê sandbox de cada um separado? Resposta default: sim — paths `/console/{slug}/sandbox/...` já scoped por tenant.
6. **Recorder/replay (S6) vs Hypothesis property tests** — recorder pode virar input pra property-based tests futuro. Vale planejar formato JSON estável agora?
7. **Tests integration de isolamento (§5.2)** — todos os 7 cenários devem estar no PR de S4 ou alguns podem ficar pra PR follow-up? (Recomendação minha: todos no S4 — é o coração da preocupação do Pedro.)

---

## 11. Trade-offs explícitos

### 11.1. Por que sandbox em Console (não app separado)

Ver §3.1. Resumo: ~50% menos código, reuso de auth + RBAC + stack.

### 11.2. Por que `is_sandbox` flag (não schema separado)

| Alternativa | Pros | Cons |
|---|---|---|
| **Flag `is_sandbox` (escolha)** | Zero refactor de queries/repositórios. Index parcial = custo zero | Operador pode esquecer filtro `WHERE is_sandbox = false` |
| Schema separado (sandbox_talks, sandbox_messages) | Isolamento 100% impossível de "leakar" | Duplica toda a chain de migrations + repositórios |
| Tenant separado (manoela-sandbox vs manoela-prod) | RLS já isola | Confuso operacionalmente — 2 tenants pra um cliente real |

### 11.3. Por que `run_turn` real (não mockar pipeline)

Crítico pro princípio "sandbox replica produção". Mockar pipeline = sandbox vira ficção que não detecta bugs reais.

### 11.4. Por que LangChain `FakeListChatModel` (não mock manual)

Já existe em `langchain-core`. Padrão da indústria. Pesquisado pelo Nicolas em testes FE-03c.

---

## 12. Plano de revisão

1. **Nicolas revisa esta spec** (foco em §3, §5, §6) + abre comments
2. **Pedro decide §6 (LLM mode)** com input do Nicolas
3. **Pedro responde §10 open questions**
4. **Após aprovação:** skill `writing-plans` gera plan executável com tasks numeradas
5. **Implementação começa** em PR única (dev/pedro-sandbox-console-fase-s1-s4) cobrindo S1–S4
6. **Após merge:** S5–S7 entram conforme demanda

---

## 13. Métricas de sucesso

Sandbox é "pronto pra uso" quando:

- [ ] Pedro/Lana conseguem criar Talk + mandar 5 mensagens + ver resposta do agente em < 30s
- [ ] State debugger mostra `collected` evoluindo em tempo real
- [ ] 7 tests de isolamento (§5.2) passam
- [ ] 0 chamadas a Meta API ou RD Station API durante 1h de uso intenso
- [ ] Custo (se LLM real) < $5/dia de uso normal
- [ ] Documentação operacional em CLAUDE.md sob "Sandbox (Plano 7b)"

---

## 14. Decisões finais do review do Nicolas (2026-06-23)

Nicolas revisou a spec contra o código atual e definiu as 3 questões abertas que estavam segurando o plano. Boa notícia: tudo se resolve **reusando padrões que já existem em produção**.

### 14.1. Q1 — `run_turn` na rota web: **REUSAR arq, não chamar inline**

Produção já desacopla `run_turn` da request (ver `webhooks.py:134`: `pool.enqueue_job("process_lead_inbox", ...)`). Sandbox copia esse pattern:

1. **`POST /sandbox/talks/{id}/send`** → grava a msg do operador como `inbound_messages` (status `queued`) → `pool.enqueue_job("process_sandbox_turn", tenant_id, talk_id)` → retorna **202 + "digitando…"**
2. **HTMX poll de 2s** (mesmo do console existente) chama `GET /sandbox/talks/{id}`, que renderiza conversa a partir das **linhas persistidas** (`inbound_messages` + `outbound_messages`) + state debugger de `talkflow_states`
3. **Novo job `process_sandbox_turn`** espelha `_run_v2_inbox` mas injeta `FakeMessagingAdapter` + `LoggingActionAdapter` + LLM (real ou stub conforme `sandbox_llm_mode`), e **NÃO agenda follow-ups**

**Bônus arquitetural:** isso também resolve o problema do `FakeMessagingAdapter.sent_messages` ser in-memory (inalcançável pela rota). Como o turno roda no worker e a resposta é persistida em `outbound_messages`, a UI lê do banco. **Fonte da verdade = DB.**

### 14.2. Q2 — Escopo do filtro `is_sandbox`: **isolamento-na-fonte + 4 pontos enumerados**

Conjunto de consumidores que *agem* sobre talks é pequeno e fechado:

| Local | Filtro a aplicar |
|---|---|
| `scan_talks.py:67` (`Talk.status=="active"` cross-tenant) | filtrar `is_sandbox=false` (cinto) |
| `follow_up_scanner.py:61` (FollowUpJob cross-tenant) | **na fonte:** sandbox NÃO cria `follow_up_job` + filtro cinto |
| `web/routes.py:98` (inbox console — `Lead.status=="pending_assignment"`) | excluir sandbox |
| `leads.py:85` (route REST list pending) | excluir sandbox |

**Modelo:** isolar na fonte (fake adapter, sem follow-up, lead sandbox fora de `pending_assignment`) **+ filtro nesses 4 pontos**.

### 14.3. Q3 — Toggle LLM real/stub: **coluna `sandbox_llm_mode` em `talks`, na migration 0032**

Como o `process_sandbox_turn` precisa saber o modo do Talk, `sandbox_llm_mode` entra junto do `is_sandbox` na mesma migration (já resolvido pelo desenho de Q1).

```sql
ALTER TABLE talks ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE talks ADD COLUMN sandbox_llm_mode TEXT;
ALTER TABLE talks ADD CONSTRAINT ck_sandbox_llm_mode
    CHECK (sandbox_llm_mode IS NULL OR sandbox_llm_mode IN ('real', 'fake'));
```

`sandbox_llm_mode IS NULL` quando `is_sandbox=false` (talks de produção). Valor obrigatório quando `is_sandbox=true`.

### 14.4. Pergunta do Nicolas pra Pedro — **RESPOSTA: opção (a) — `is_sandbox` também no Lead**

Nicolas perguntou: inbox do operador é chaveado em `Lead.status`, mas `is_sandbox` está no `Talk`. Pra lead de sandbox não aparecer pro operador, qual prefere:
- **(a)** `is_sandbox` também no Lead (recomendação Nicolas — explícito + crons varrem ambos)
- **(b)** lead de sandbox criado sem `pending_assignment`

**Pedro escolhe (a)** — seguindo a recomendação do Nicolas.

Justificativa:
- Mais explícito — queries que filtram leads por estado podem usar `WHERE is_sandbox = false` diretamente
- Crons que varrem leads (futuros — análises, exports, métricas) automaticamente excluem sandbox
- Symmetric com o flag no Talk (mais fácil de raciocinar)
- Custo zero (1 coluna BOOLEAN com partial index)

Migration 0032 atualizada:

```sql
-- Talks
ALTER TABLE talks ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE talks ADD COLUMN sandbox_llm_mode TEXT;
ALTER TABLE talks ADD CONSTRAINT ck_sandbox_llm_mode
    CHECK (sandbox_llm_mode IS NULL OR sandbox_llm_mode IN ('real', 'fake'));
CREATE INDEX ix_talks_sandbox ON talks (tenant_id, is_sandbox) WHERE is_sandbox = true;

-- Leads
ALTER TABLE leads ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX ix_leads_sandbox ON leads (tenant_id, is_sandbox) WHERE is_sandbox = true;
```

### 14.5. Impacto no plano fasado

Com Q1/Q2/Q3 resolvidos, a spec fica **plan-ready** (próximo passo: writing-plans gera plan executável).

Mudanças no plano fasado:
- **S1.5** muda de "POST /sandbox/talks/new — cria Lead+Talk direto" pra "POST /sandbox/talks/new — cria Lead (`is_sandbox=true`) + Talk (`is_sandbox=true`, `sandbox_llm_mode='real'|'fake'`)"
- **S2.3** muda de "rota /send invoca `SandboxService.send_inbound` → `run_turn` direto" pra "rota /send grava em `inbound_messages` (queued) + enqueue `process_sandbox_turn`"
- **S2 ganha task nova:** S2.7 — implementar `worker/jobs/process_sandbox_turn.py` espelhando `_run_v2_inbox` com fakes
- **S4 ganha tasks novas:** tests pra cada um dos 4 pontos de filtro de Q2

---

## Referências

- [Plano 11 — HITL Console](../plans/2026-05-26-hitl-console.md) (base de auth/RBAC/stack)
- [FlowEngine v2 architecture](./2026-06-08-flow-engine-architecture-design.md) (`run_turn` pipeline)
- [FE-03c Actions Adapter](./2026-06-12-fe03c-actions-adapter-framework-design.md) (idempotency reusada)
- [Pilot harness PR #11](https://github.com/NicolasAmaral1/PeSDR/pull/11) (FakeMessagingAdapter usage pattern)
- [`src/ai_sdr/cli/simulate.py`](../../../src/ai_sdr/cli/simulate.py) (REPL CLI — coexiste com sandbox)
- [`src/ai_sdr/messaging/fake.py`](../../../src/ai_sdr/messaging/fake.py) (FakeMessagingAdapter atual)
- LangChain `FakeListChatModel`: https://python.langchain.com/api_reference/community/llms/langchain_community.llms.fake.FakeListLLM.html

---

**Fim da spec.**

> **Próximo passo:** Nicolas review + decisão de Pedro nas 7 open questions + LLM mode → spec vira plan executável via skill `writing-plans` → implementação S1-S4 em PR única.
