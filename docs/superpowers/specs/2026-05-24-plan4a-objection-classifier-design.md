# Objection Classifier — Design Spec (Plano 4a)

**Data:** 2026-05-24
**Status:** Draft para revisão (pós-brainstorm)
**Autor:** Nicolas Amaral (brainstorm com Claude)
**Parent spec:** [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) (§4.4 Objection Classifier, §5.2 Anatomia de Node, §12 LLM e providers)
**Plano antecessor:** [Plano 3 — KB + Guardrails](../plans/2026-05-23-kb-and-guardrails.md)
**Plano sucessor:** Plano 4b — Multi-provider LLM validation matrix (será brainstormado depois que 4a fechar)

---

## 1. Resumo executivo

Implementa o **Objection Classifier** descrito no spec §4.4. Adiciona ao runtime a capacidade de, antes de cada call principal de LLM em um Node, executar um classificador barato (Haiku) que detecta se a mensagem do lead levantou alguma das objeções declaradas no TreeFlow (`handles_objections` por Node + `global_objections` do funil). Se detectado, o sistema desvia para uma **resposta inline** (default — reusa persona do Node + KB da objeção + instrução "não avance") ou para um **sub-node referenciado** (opt-in via `as_subnode: <node_id>`, quando a objeção justifica coletar dados ou ter fluxo próprio). Em ambos casos, após responder, a conversa retorna ao Node original sem mexer em `collects`/`exit_condition` desse turn.

A arquitetura usa **classifier-as-edge-router**: cada NodeSpec do TreeFlow vira até 3 nodes LangGraph (`N_classifier`, opcionalmente `N_inline_response`, e o `N_main` existente do Plan 2/3), com conditional edges. TreeFlows sem objections continuam funcionando sem custo extra (classifier vira passthrough).

Falha do classifier nunca derruba o turn — degrada graciosamente pro caminho main, garantindo que o lead sempre recebe resposta.

---

## 2. Escopo

### 2.1 Dentro do Plano 4a

- Schema tighten: `NodeObjection` + `GlobalObjection` ganham `description: str` (obrigatório) e `as_subnode: str | None` (opt-in).
- Validação de grafo: `as_subnode` deve referenciar `node_id` declarado no mesmo TreeFlow; sentinel `BACK_TO_ORIGIN` reconhecido como target válido em transitions.
- Tenant config: novo bloco `objections:` em `tenant.yaml` (`enabled`, `min_confidence`, `max_handled_per_lead`, `history_window`).
- Reuso do `tenant.llm.classifier` já existente (Haiku no example) — sem mudança em `LLMDefaults`.
- Novo módulo `ai_sdr.treeflow.classifier`: função `classify()` com `with_structured_output(ClassifierResult)`.
- Novo módulo `ai_sdr.treeflow.objection_response`: builder do `SystemMessage` inline (persona herdada + bloco objection + KB), com cache breakpoint próprio.
- Compiler changes: emit `N_classifier` + `N_inline_response_{node_id}` + conditional edges; resolver de `BACK_TO_ORIGIN` via `state._origin_node_id`.
- State extension: `objections_handled: list[ObjectionRecord]` e `_origin_node_id: str | None`.
- Telemetria estruturada: `objection.classifier.{skipped,detected,no_match,error,invalid_output,hallucinated_id}`, `objection.inline.responded`, `objection.subnode.{entered,exited,orphan_return}`, `objection.kb.{empty,missing}`, `objection.threshold.exceeded`.
- Example tenant scaffolding: `global_objections` + `handles_objections` no TreeFlow `example`; bloco `objections:` no `tenant.yaml`; KBs `kb_obj_tempo`, `kb_obj_pensar` (kb_obj_preco já existe).
- Cobertura de testes: unit (schemas, classifier mockado, response builder, compiler, state), integration (runtime real + checkpointer + RLS), live (Haiku real, analog T19 Plan 3).

### 2.2 Fora do Plano 4a (non-goals explícitos)

| Item | Por quê | Quando |
|---|---|---|
| Auto-escalation pra humano após N objections recorrentes | Depende de HITL (UI de review + LangGraph `interrupt`); 4a apenas loga warning quando `max_handled_per_lead` excedido | Plano HITL (após WhatsApp + CRM) |
| Multi-objection per turn (lead levanta 2 objeções no mesmo message) | Classifier retorna 1 objection_id; segunda vem na próxima turn. Suficiente pro pilot; complexidade marginal não justificada | Se medirmos miss rate alto em produção |
| LLM-judge cross-validation (segundo LLM revisa a classificação) | Critic pass já cobre o response final via Plan 3; classifier mal calibrado é resolvido tunando description/threshold | Se accuracy do classifier ficar abaixo do tolerável |
| Confidence-threshold tunável por objection (não só global) | YAGNI; tenant-level default cobre o pilot | Conforme demanda |
| Métricas Prometheus dedicadas (taxa de detection, distribuição de confidence) | `structlog` events permitem agregação posterior; dashboard fica pro plano de observabilidade | Plano de observabilidade |
| Sub-node aninhado (objection sub-node tendo suas próprias objections) | Suportado por construção (mesmo pipeline roda em todo NodeSpec) mas não exercitado em testes nem documentado como pattern | Quando algum tenant pedir |
| A/B accuracy do classifier vs baselines | Research, não MVP | N/A |
| Cost analytics do classifier (custo extra de Haiku por turn) | Telemetria já emite eventos; agregação fica pro plano de observabilidade | Plano de observabilidade |

---

## 3. Arquitetura

### 3.1 File layout (delta sobre o estado pós-Plano 3)

```
src/ai_sdr/
├── treeflow/
│   ├── classifier.py            # NEW — classify() + ClassifierResult
│   ├── objection_response.py    # NEW — build_inline_objection_messages()
│   ├── compiler.py              # MODIFIED — emite N_classifier + N_inline_response + conditional edges + BACK_TO_ORIGIN resolver
│   ├── state.py                 # MODIFIED — adiciona objections_handled + _origin_node_id + ObjectionRecord
│   └── ...                      # (loader, runtime, retriever, guardrails do Plan 3 inalterados)
├── schemas/
│   ├── treeflow_yaml.py         # MODIFIED — NodeObjection (substitui dict opaco), GlobalObjection com description, validador de BACK_TO_ORIGIN e as_subnode
│   └── tenant_yaml.py           # MODIFIED — adiciona ObjectionsConfig
└── observability/
    └── events.py                # MODIFIED — registra novos event_types

tenants/example/
├── tenant.yaml                  # MODIFIED — adiciona bloco objections:
└── treeflows/example.yaml       # MODIFIED — adiciona global_objections + handles_objections em node de qualificação

kb/example/
├── kb_obj_preco/precos.md       # já existe
├── kb_obj_tempo.md              # NEW
└── kb_obj_pensar.md             # NEW

tests/
├── unit/schemas/test_treeflow_objections.py     # NEW
├── unit/treeflow/test_classifier.py             # NEW
├── unit/treeflow/test_objection_response.py     # NEW
├── unit/treeflow/test_compiler_objections.py    # NEW
├── unit/treeflow/test_state_objections.py       # NEW
├── integration/test_objection_runtime.py        # NEW
├── integration/test_objection_isolation.py      # NEW
├── integration/test_objection_live.py           # NEW (live_llm marker)
└── integration/test_simulate_with_objections.py # NEW
```

### 3.2 Topologia do grafo LangGraph

Cada `NodeSpec` N do TreeFlow vira até 3 nodes LangGraph após compilação:

```
                  ┌─ (no objection / empty list)
                  │
N_classifier ────┼─ (detected, inline)    →  N_inline_response  ─┐
                  │                                                │
                  └─ (detected, as_subnode)  →  N_subnode_X  ─────┤
                                                                   │
                                                                   ↓
                                                            N_main (existing
                                                            Plan 2/3 node:
                                                            collects + LLM +
                                                            exit_condition)
```

- `N_classifier` é sempre compilado, mas vira passthrough quando o Node não tem `handles_objections` nem `global_objections` aplicáveis (skip da call ao Haiku — zero custo).
- `N_inline_response` reusa o prompt-base de N (mesma persona), injeta KB da objeção detectada, instrui "não avance"; envolto em `run_with_guardrails` do Plan 3.
- `N_subnode_X` é resolvido em runtime pra um `NodeSpec` existente no TreeFlow (referenciado por id em `as_subnode`); ao sair, transita via sentinel `BACK_TO_ORIGIN` que o compiler resolve em `N_main` do nó originário usando `state._origin_node_id`.
- Após `N_inline_response` ou conclusão de `N_subnode_X`, o **active_node persistido no checkpoint continua sendo N** (não N_inline_response). Isso garante que o próximo turn re-entra em `N_classifier` da mesma forma.
- `N_main` permanece exatamente o que é hoje (Plan 2/3) — zero refactor no caminho feliz.

### 3.3 Invariantes

- 1 call LLM principal por turn no caminho feliz. Caminho com objection detectada: 2 calls (Haiku classifier + Sonnet inline ou main do subnode).
- `active_node` no checkpoint = sempre um `N_main` do TreeFlow original — nunca um `N_classifier`/`N_inline_response`. Reentry no próximo turn é determinístico.
- `state.objections_handled` cresce monotonicamente, nunca reset (cross-turn).
- `_origin_node_id` é set/clear estritamente na transição subnode (escopo curto, sem leak entre turns).
- Falha do classifier nunca propaga — toda exception cai em fallback "no match" → `Command(goto=N_main)`.

### 3.4 Compatibilidade com TreeFlows existentes

TreeFlows sem `handles_objections` nem `global_objections`:
- Schema continua aceitando (defaults: lista vazia).
- `N_classifier` compilado mas vira passthrough no primeiro check — zero custo.
- Comportamento idêntico ao pós-Plan 3.

TreeFlows que tinham `handles_objections: list[dict[str, Any]]` como forward-compat blob (status quo do schema atual):
- **Breaking change controlado:** YAMLs precisam ganhar `description` (obrigatório). Como nenhum TreeFlow em produção exercitava o classifier (era forward-compat), o impacto é só nos fixtures de teste e no example tenant — ambos atualizados como parte de 4a.

---

## 4. Componentes

### 4.1 Schema changes (`src/ai_sdr/schemas/treeflow_yaml.py`)

```python
class NodeObjection(BaseModel):
    """Per-Node objection ref (substitui dict[str, Any] opaco do Plan 2/3)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = None  # node_id no mesmo TreeFlow

class GlobalObjection(BaseModel):
    """TreeFlow-level objection (tighten do schema atual)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = None

class NodeSpec(BaseModel):
    ...
    handles_objections: list[NodeObjection] = Field(default_factory=list)
```

Sentinel adicional em transitions: `BACK_TO_ORIGIN` (string literal) reconhecido como valid target em `_validate_graph_consistency` quando o node em questão é referenciado por algum `as_subnode`.

Validações novas no `_validate_graph_consistency`:
- Todo `as_subnode: X` em qualquer objection (global ou node-local) requer X em `nodes[]`.
- IDs de objection únicos por escopo (global e node-local podem colidir; node-local vence em merge).
- `BACK_TO_ORIGIN` em transition de Node N que NÃO é referenciado por nenhum `as_subnode` → warning (não erro: node pode ser referenciado em versão futura).
- KB referenciada por objection não é validada no schema (delegado ao retriever; warn se vazio no runtime).

### 4.2 Tenant config (`src/ai_sdr/schemas/tenant_yaml.py`)

```yaml
# tenant.yaml
objections:
  enabled: true                    # kill switch (analog ao guardrails.enabled)
  min_confidence: 0.6              # threshold pra deflect
  max_handled_per_lead: 10         # safety net (log warning, não bloqueia)
  history_window: 4                # quantas mensagens o classifier vê
```

```python
class ObjectionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    max_handled_per_lead: int = Field(default=10, ge=1, le=100)
    history_window: int = Field(default=4, ge=1, le=20)
```

Kill switch: `enabled=false` → `N_classifier` sempre passthrough (mesmo com objections declaradas), zero call ao Haiku. Plan 3 já estabeleceu esse padrão pra guardrails.

### 4.3 Classifier (`src/ai_sdr/treeflow/classifier.py`)

```python
class ClassifierResult(BaseModel):
    objection_id: str | None       # None = no detection
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = ""                # trecho do lead que disparou (debug)

async def classify(
    llm: BaseChatModel,
    objections: list[NodeObjection | GlobalObjection],
    conversation: list[BaseMessage],
    previously_handled: list[str],
) -> ClassifierResult:
    """Single Haiku call via with_structured_output. Returns None objection_id se lista vazia."""
```

Prompt do classifier (fixo, em portugues do BR):
- System: explica role, lista `[(id, description)]` permitidos, recebe `previously_handled` como contexto ("se o lead estiver insistindo numa dessas, sinalize de novo"), instrui "retorne objection_id=null se nenhuma se aplica OU se confiança baixa".
- Human: janela das últimas `history_window` mensagens.
- Threshold `min_confidence` aplicado **em código** após a call (não no prompt), pra permitir tuning sem invalidar prompt cache.

Validação pós-call em código:
- Se `objection_id` retornado não está na lista permitida → trata como `None` + emit `objection.classifier.hallucinated_id`.
- Se Pydantic falha → trata como `None` + emit `objection.classifier.invalid_output`.
- Se exception (rede/auth/rate-limit) → trata como `None` + emit `objection.classifier.error`.

### 4.4 Inline response builder (`src/ai_sdr/treeflow/objection_response.py`)

```python
def build_inline_objection_messages(
    node: NodeSpec,
    objection: NodeObjection | GlobalObjection,
    kb_content: str,
    conversation: list[BaseMessage],
    cache_enabled: bool,
    provider: str,
) -> list[BaseMessage]:
    """Returns SystemMessage(persona herdada de N + bloco objection-prefix + KB) + conversation."""
```

System message structure:
- Block 1 (cacheable): `node.prompt` (persona-base, igual ao Plan 3) com `cache_control: ephemeral` se Anthropic.
- Block 2 (cacheable por `(node_id, objection_id)`): "O lead levantou uma objeção identificada como '{objection.id}' ({objection.description}). Use o conhecimento abaixo. Não tente avançar a conversa nem coletar campos — apenas resolva a preocupação e convide a continuar." com `cache_control: ephemeral` se Anthropic.
- Block 3 (dinâmico): KB content. Se vazio: anexa "se faltar info suficiente, peça mais detalhes ao lead em vez de inventar".

Reusa o pattern do `build_system_messages` (Plan 3, T6).

### 4.5 Compiler changes (`src/ai_sdr/treeflow/compiler.py`)

Pra cada `NodeSpec` N do TreeFlow:

1. Emite `N_classifier_{node_id}`:
   - Recebe state.
   - Merge `global_objections + node.handles_objections` (dedupe por id, node-local vence).
   - Se merge vazio OR `tenant.objections.enabled=false`: emit event `skipped`, return `Command(goto="N_main_{node_id}")`.
   - Else: call `classify(...)`. Aplica threshold. Se result.objection_id is None: emit `no_match`, return `Command(goto="N_main_{node_id}")`.
   - Se detectado:
     - Se objection.as_subnode is None: emit `detected`, return `Command(goto="N_inline_response_{node_id}", update={"_classifier_result": result, "_active_objection": objection})`.
     - Else: emit `detected`, return `Command(goto="N_classifier_{as_subnode}", update={"_origin_node_id": node.id, "objections_handled": [...existing, new_record]})`.

2. Emite `N_inline_response_{node_id}` (só se N tem alguma objection inline declarada):
   - Pega `_active_objection` do state.
   - Retrieve KB chunks (Plan 3 retriever).
   - `build_inline_objection_messages(...)`.
   - Call LLM principal de N (mesma config que `N_main` usaria), envolto em `run_with_guardrails` do Plan 3.
   - Append `ObjectionRecord` em `state.objections_handled`.
   - Emit `inline.responded`.
   - Return `Command(goto=END)` — turn termina, resposta enviada.

3. `N_main_{node_id}` é exatamente o node Plan 2/3 atual (sem mudança).

4. **Entry-point routing:** o conditional edge a partir de `START` que hoje (Plan 2/3) faz goto `N_{state.active_node}` passa a fazer goto `N_classifier_{state.active_node}`. Compiler patch surgical, sem mexer em como `active_node` é mantido no state (continua sendo gerenciado pelo `N_main` ao avançar via transitions).

5. Resolver de `BACK_TO_ORIGIN`: ao compilar transitions de qualquer Node, se target == `"BACK_TO_ORIGIN"`, conditional edge resolve em runtime via `state._origin_node_id`:
   - Se set: goto `N_main_{state._origin_node_id}`, clear `_origin_node_id`, emit `subnode.exited`.
   - Se None: warning + emit `subnode.orphan_return`, fallback pro `N_classifier_{entry_node}` do TreeFlow.

### 4.6 State extension (`src/ai_sdr/treeflow/state.py`)

```python
class ObjectionRecord(TypedDict):
    objection_id: str
    detected_at_node: str
    turn_index: int
    quote: str

# Adicionado ao TalkFlowState:
objections_handled: Annotated[list[ObjectionRecord], operator.add]
_origin_node_id: str | None       # interno, gerenciado pelo compiler
_active_objection: dict | None    # interno, passado de classifier pra inline_response no mesmo turn
_classifier_result: dict | None   # interno, idem (pra observability/debug)
```

Reducer pra `objections_handled`: append-only via `operator.add` (LangGraph standard).

`_origin_node_id`, `_active_objection`, `_classifier_result` são intra-turn — não persistem entre turns; o compiler clear no return correspondente.

### 4.7 Telemetria (`src/ai_sdr/observability/events.py`)

Novos event_types (todos `structlog`):

| Event | Quando | Campos |
|---|---|---|
| `objection.classifier.skipped` | Node sem objections aplicáveis OU `enabled=false` | `tenant_id`, `talkflow_id`, `node_id`, `reason` |
| `objection.classifier.detected` | Objection identificada acima do threshold | `..., objection_id, confidence, quote, scope (global/local)` |
| `objection.classifier.no_match` | LLM retornou null OR confidence < threshold | `..., max_confidence_seen` |
| `objection.classifier.error` | Exception não tratada | `..., error_type, error_message` |
| `objection.classifier.invalid_output` | Pydantic validation falhou | `..., raw_output` |
| `objection.classifier.hallucinated_id` | LLM retornou id fora da lista | `..., returned_id, allowed_ids` |
| `objection.inline.responded` | Resposta inline emitida com sucesso | `..., objection_id` |
| `objection.subnode.entered` | Goto sub-node | `..., objection_id, subnode_id, origin_node_id` |
| `objection.subnode.exited` | BACK_TO_ORIGIN resolvido | `..., subnode_id, returned_to_node_id` |
| `objection.subnode.orphan_return` | BACK_TO_ORIGIN sem origin (should-never-happen) | `..., fallback_target` |
| `objection.kb.empty` | Retriever retornou 0 chunks | `..., kb_id, query` |
| `objection.kb.missing` | KB id não existe | `..., kb_id` |
| `objection.threshold.exceeded` | `max_handled_per_lead` atingido | `..., count, threshold` |

---

## 5. Data flow (por turn)

```
Lead manda mensagem
    │
    ▼
TalkFlowRuntime.step(talkflow_id, lead_message)
    │
    ▼
LangGraph reentra em active_node N → entry = N_classifier
    │
    ├── (a) handles_objections + global_objections aplicáveis = []
    │       └─ N_classifier emite event "skipped" → Command(goto=N_main)
    │
    ├── (b) há objeções aplicáveis
    │       1. Monta lista [(id, description)] (global + node-local merge, dedupe por id; node-local vence)
    │       2. Pega últimas `history_window` mensagens do checkpointer state
    │       3. Inclui state.objections_handled como contexto "já tratadas"
    │       4. Call Haiku via classifier.classify() → ClassifierResult
    │       5a. objection_id is None  OR  confidence < min_confidence
    │            └─ event "no_match" → Command(goto=N_main)
    │       5b. objection_id detectado E confidence ≥ threshold
    │            ├─ resolve objection: olha se tem as_subnode
    │            │
    │            ├── (i) inline (as_subnode = None)
    │            │      ├─ retrieve KB chunks da objeção (mesmo retriever Plan 3)
    │            │      ├─ build_inline_objection_messages(N, obj, kb, conv)
    │            │      ├─ wrapped em run_with_guardrails (Plan 3)
    │            │      ├─ emit event "inline.responded"
    │            │      ├─ append ObjectionRecord em state.objections_handled
    │            │      ├─ active_node permanece = N (não muda)
    │            │      └─ Command(goto=END) — turn termina, resposta enviada
    │            │
    │            └── (ii) subnode (as_subnode = "obj_preco_node")
    │                   ├─ state._origin_node_id = N.id
    │                   ├─ append ObjectionRecord em state.objections_handled
    │                   ├─ emit event "subnode.entered"
    │                   └─ Command(goto="obj_preco_node_classifier")
    │                      └─ (sub-node tem seu próprio N_classifier → N_main flow normal,
    │                          inclusive collects/exit_condition; quando bater transition
    │                          target=BACK_TO_ORIGIN, conditional edge volta pra N_main
    │                          do origin via state._origin_node_id; emit "subnode.exited")
    │
    ▼
N_main (caminho feliz, idêntico a Plan 2/3) executa só quando classifier não desviou
    │
    ▼
Persist checkpoint, retorna resposta
```

### Edge cases tratados

- **Lead manda mensagem vazia / só emoji** → classifier roda, Haiku retorna null robustamente, fluxo segue pra N_main.
- **Lead muda de assunto E levanta objeção no mesmo message** → classifier prioriza objeção (instrução explícita), main LLM pega o resto na próxima rodada (multi-objection-per-turn é V2).
- **Mesma objeção detectada N vezes** → cada turn é tratada igual, contador em state, warning quando atinge `max_handled_per_lead`. Sem auto-escalation no 4a.
- **Sub-node de objeção tem suas próprias objections** → suportado por construção (cada NodeSpec roda o mesmo pipeline). Autor não deve aninhar, mas não bloqueamos.
- **Sub-node sem `BACK_TO_ORIGIN` em transitions** → validator não bloqueia (sub-node pode ter exit pra END ou outro node), mas se a conversa nunca voltar pro origin, é problema de autoria (warn no load).
- **Cross-tenant isolation** → garantido por (a) `tenant.objections` carregado via `TenantLoader`, (b) RLS em `talkflows` (Plan 2), (c) `thread_id` prefixado com `tenant_id` (Plan 2).

---

## 6. Error handling

**Princípio:** falha do classifier nunca derruba o turn. Lead sempre recebe uma resposta. Pior caso degrada pra "fluxo Plan 3 normal" (skipa o classifier, vai direto pro main).

| Falha | Detecção | Tratamento | Event |
|---|---|---|---|
| Haiku raise (rate-limit, network, auth) | `try/except` em `classifier.classify()` | Log, fallback: tratar como "no match" → `Command(goto=N_main)` | `objection.classifier.error` com `error_type`, `error_message` |
| Structured output mal-formado | Pydantic `ValidationError` no `with_structured_output` | Idem acima (no match → main) | `objection.classifier.invalid_output` |
| `objection_id` retornado que não está na lista permitida | Validação pós-call em código | Idem (no match → main) — não confiar no LLM cego | `objection.classifier.hallucinated_id` com o id inválido |
| KB da objeção não retorna chunks (vazio) | Plan 3 retriever retorna `[]` | Inline response roda com `kb_content=""` + instrução defensiva extra; guardrails pegam alucinações | `objection.kb.empty` |
| KB id referenciado não existe | Plan 3 retriever raise `KBNotFound` | Capturado por `N_inline_response`; fallback pro main com event de erro (não falha o turn) | `objection.kb.missing` |
| Inline LLM falha após N retries do guardrails | `run_with_guardrails` retorna fallback_text (Plan 3) | Já tratado pelo guardrails runner; nada novo | reusa eventos Plan 3 |
| Sub-node referenciado em `as_subnode` foi removido do TreeFlow (versão nova) | Schema validator no `TreeFlowLoader.load()` | Bloqueia publish → erro de carregamento; não chega no runtime | `treeflow.publish.invalid` |
| `state._origin_node_id` ausente quando bate `BACK_TO_ORIGIN` (state corrompido ou TreeFlow trocado mid-conversa) | Conditional edge resolver | Loga warning, transita pro `entry_node` como fallback de último recurso | `objection.subnode.orphan_return` |
| `max_handled_per_lead` excedido | Contador em state | Warning event, classifier continua rodando normalmente (não bloqueia) | `objection.threshold.exceeded` |
| LangGraph node retornou exception não-tratada | LangGraph propaga | Já tratado pelo lifespan / TalkFlowRuntime do Plan 2 | reusa tratamento Plan 2 |

### Decisões críticas

- **Classifier nunca é "fail-hard".** Toda falha cai pro caminho main. Razão: classifier é otimização de qualidade, não feature crítica. Lead conversando vale mais do que detectar objeção. Memória `project_guardrails_hitl_direction` aplica filosofia análoga.
- **Hallucinated id é tratado como bug do LLM, não bug do tenant.** Não retornamos erro, só logamos com o id inválido pra triagem posterior.
- **KB vazio NÃO bloqueia o turn.** Inline response roda mesmo sem chunks, com instrução defensiva. Guardrails Plan 3 (whitelist + critic) continuam aplicáveis e pegam riscos de fato.
- **BACK_TO_ORIGIN sem origin é "should never happen" mas tem fallback.** Se acontecer, vai pro `entry_node` e loga warning — usuário entra numa conversa estranha mas não trava.
- **Estado nunca é mutado em caso de erro.** `objections_handled.append()` só roda se a resposta inline saiu com sucesso. Idempotência preservada (re-rodar o turn não duplica).

---

## 7. Testing

Filosofia: TDD por task (per CLAUDE.md). Unit tests sem rede (mocks LLM), integration com docker, live tests com `live_llm` marker pra rodar contra Haiku real (analog ao T19 do Plan 3).

### 7.1 Unit tests (sem LLM real, sem DB)

**`tests/unit/schemas/test_treeflow_objections.py`**
- `NodeObjection` valida required fields (id, kb, description ≥ 10 chars).
- `description` ausente → `ValidationError` clara.
- `as_subnode` referenciando node id inexistente → `_validate_graph_consistency` rejeita.
- `BACK_TO_ORIGIN` em transition target de node NÃO-referenciado por nenhum `as_subnode` → warning (não erro — node pode ser referenciado em versão futura).
- TreeFlow legacy sem objections → carrega sem erro (backward-compat).
- Validador de `tenant.yaml > objections` (min_confidence em [0,1], history_window > 0, max_handled_per_lead ≥ 1).

**`tests/unit/treeflow/test_classifier.py`** (mocka LLM)
- `classify()` com lista vazia → retorna `ClassifierResult(objection_id=None, confidence=0.0)` sem chamar LLM.
- `classify()` retornando id válido + confidence ≥ threshold → caller deflecta.
- `classify()` retornando id válido + confidence < threshold → caller trata como no-match.
- `classify()` retornando id inválido (hallucinated) → caller trata como no-match + event registrado.
- Janela de história: passa últimas N mensagens, não mais.
- Exception path: LLM raise → `classify()` re-raise; caller captura e emite event.

**`tests/unit/treeflow/test_objection_response.py`**
- `build_inline_objection_messages` produz SystemMessage com persona de N + bloco objection + KB.
- Cache breakpoints aplicados no bloco objection-prefix (quando `cache_enabled=true` e provider=Anthropic).
- KB content vazio → mensagem tem instrução defensiva extra.
- Provider != Anthropic → sem `cache_control` (concatena blocks).

**`tests/unit/treeflow/test_compiler_objections.py`**
- Node sem objections → grafo compilado tem `N_classifier` como passthrough (Command(goto=N_main) sem call).
- Node com objections inline → compilado adiciona `N_inline_response_{node}`.
- Node com `as_subnode` → conditional edge para `N_classifier_{subnode_id}`; subnode existente em outro lugar do grafo é reusado, não duplicado.
- `BACK_TO_ORIGIN` resolve via `state._origin_node_id`; sem origin → fallback pro entry_node + warning event.
- `objections_handled` cresce monotonicamente entre invocações; idempotente em re-run do mesmo turn.

**`tests/unit/treeflow/test_state_objections.py`**
- `ObjectionRecord` typed dict shape.
- Reducer append-only em `objections_handled`.
- `_origin_node_id` set/clear scope correto (set ao entrar subnode, clear ao retornar).

### 7.2 Integration tests (DB + checkpointer real, LLM mockado)

**`tests/integration/test_objection_runtime.py`** — usa fixtures de tenant + treeflow + checkpointer Postgres
- 1 turn, lead manda mensagem que dispara objection_id="preco" → mock classifier retorna preco@0.85 → checkpoint mostra `objections_handled=[preco]`, `active_node` inalterado.
- Próximo turn, lead muda assunto → mock classifier retorna null → main LLM (mockado) roda; active_node avança per exit_condition.
- Sub-node mode: lead dispara objection com `as_subnode=obj_node` → checkpoint mostra trajetória classifier→subnode→back; `_origin_node_id` set then clear.
- `max_handled_per_lead` excedido → warning event logado, classifier continua rodando.

**`tests/integration/test_objection_isolation.py`**
- 2 tenants concorrentes, ambos com objections → cada um vê só seus `objections_handled` (RLS via talkflows + thread_id prefix do Plan 2).
- TreeFlow upgrade de v1 (sem objections) → v2 (com) → conversas em andamento em v1 não afetadas; novas em v2 ganham classifier.

### 7.3 Live tests (`live_llm` marker, requires real Haiku key)

**`tests/integration/test_objection_live.py`** — analog T19 do Plan 3
- Tenant example, classifier real Haiku, prompt com 3 objections globais + 1 node-local.
- Mensagem "tá muito caro pra mim" → assert classifier retorna `preco`, confidence ≥ 0.6.
- Mensagem "não sei se é a hora certa" → assert retorna `falta_tempo` OR `preciso_pensar` (ambos aceitáveis).
- Mensagem "qual o whatsapp de vocês?" (no-objection) → assert retorna null.
- Mensagem "tá muito caro E preciso pensar" → assert retorna um dos dois (não exigimos qual — multi-objection-per-turn é V2).
- Round-trip end-to-end: lead message → classifier real → inline response real (Sonnet) → assert response menciona conteúdo do KB.

### 7.4 Acceptance / smoke

**`tests/integration/test_simulate_with_objections.py`**
- Roda `ai-sdr simulate --tenant example --treeflow example --lead test-1` em modo headless.
- Injeta sequência scriptada: saudação → qualif → objection "tá caro" → confirma extracted fields preservados → não-objection → avanço de node.
- Assert: conversa convergiu sem loops, `objections_handled` aparece em `--show-extracted` output.

### 7.5 Fixtures novas necessárias

- `tenants/example/treeflows/example.yaml` ganha `global_objections` (3 entries) + `handles_objections` no node de qualificação (1-2 entries).
- `kb/example/kb_obj_preco/precos.md` (já existe — reusa).
- `kb/example/kb_obj_tempo.md` (novo).
- `kb/example/kb_obj_pensar.md` (novo).
- `tenants/example/tenant.yaml` ganha block `objections:`.

### 7.6 Anti-tests (NÃO escrevemos)

- Performance / load tests do classifier.
- A/B accuracy do classifier vs baselines.
- Sub-node aninhamento (objection-de-objection) — suportado por construção mas não exercitado.

---

## 8. Migrations e backward-compat

- **Sem migration de banco.** Schema do checkpointer LangGraph não muda (state evolui via TypedDict; LangGraph aceita campos novos em runs existentes).
- **Sem migration de tenant.yaml.** Bloco `objections:` é opcional — TreeFlow loader aplica defaults se ausente.
- **Schema break em `handles_objections`:** era `list[dict[str, Any]] | None`, vira `list[NodeObjection]`. TreeFlows que tinham entries (todas eram forward-compat blobs no-op) precisam ganhar `description`. Como nenhum TreeFlow em produção exercitava esse campo, o único impacto é nos fixtures de teste e no example tenant — ambos atualizados como parte de 4a. Documentar em `CLAUDE.md`.
- **Sem version bump obrigatório de TreeFlow YAML:** mas se o autor ADICIONA objections a um TreeFlow existente, o `version: x.y.z` precisa subir (regra Plan 2 já em vigor — runtime recusa re-publicar mesma versão com hash diferente).

---

## 9. Open questions / decisões adiadas

| Questão | Resposta provisória | Quando revisitar |
|---|---|---|
| Tunning de prompt do classifier (poucos vs muitos exemplos) | Começar com prompt simples; medir accuracy em live tests; iterar | Após primeira semana de pilot |
| Cache breakpoint do classifier prompt | Sim por default (classifier prompt + lista de objections é estático por TreeFlow version) | Implementado em 4a |
| Cost guard pro classifier (limitar calls/turno) | Não necessário — 1 call Haiku por turn é barato. Se virar problema, kill switch já existe | Conforme demanda |
| `--no-classifier` flag no `ai-sdr simulate` (debug) | Útil pra dev; baixo custo. Incluir em 4a | Sim, em 4a |
| Como o autor vê accuracy do classifier durante dev | `simulate` mostra event log; `--show-extracted` poderia incluir classifier result | Sim, expandir em 4a |
| Objection priority/ordering (se múltiplas igual confiança) | Por ordem de declaração no YAML; node-local vence global em colisão de id | Implementado em 4a |
| TTL pra `objections_handled` (limpar se conversa pausa por dias) | Sem TTL no 4a — checkpointer já tem retention policy do LangGraph | Plano de retention futuro |

---

## 10. Roadmap pós-4a

- **Plano 4b:** Multi-provider LLM validation matrix (Gemini, DeepSeek, Ollama, Bedrock end-to-end). Testes live por provider, tuning de caching por provider, multi-provider embeddings (widen `EmbeddingsConfig`).
- **Plano HITL:** Substituir `max_handled_per_lead` warning por escalação real via LangGraph `interrupt()`. Mesmo hook do guardrails fallback (memória `project_guardrails_hitl_direction`).
- **Plano de observabilidade:** Dashboards Prometheus/Grafana dos events emitidos por 4a (taxa detection, distribuição confidence, top objections por tenant).
- **Multi-objection per turn:** Se medirmos miss rate alto em produção. Classifier retorna `list[ClassifierResult]`, agent encadeia respostas.

---

## 11. Referências

- Spec macro: [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) §4.4, §5.2, §12.
- ADR adapters: [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) (PeSDR standalone-first).
- Plano antecessor: Plano 3 (`docs/superpowers/plans/2026-05-23-kb-and-guardrails.md`) — retriever, guardrails runner, build_system_messages, run_with_guardrails são reusados intactos.
- Plano 2 (`docs/superpowers/plans/2026-05-22-treeflow-engine-langgraph.md`) — compiler, runtime, state, checkpointer; ponto de extensão.
