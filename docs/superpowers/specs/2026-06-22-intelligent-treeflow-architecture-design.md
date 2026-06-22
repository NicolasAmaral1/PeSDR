# TreeFlow Inteligente — Arquitetura em Camadas (Design Spec)

**Data:** 2026-06-22
**Status:** Draft pra revisão e discussão (Nicolas + Pedro)
**Tipo:** Architectural design — visão de longo prazo + recorte executável da camada inicial
**Autor:** Pedro Aranda (com Claude Code, após pesquisa exaustiva da documentação existente)
**Relaciona-se com:**
- [FlowEngine v2 architecture](./2026-06-08-flow-engine-architecture-design.md) (base que será estendida)
- [FE-03a objection runtime](./2026-06-09-fe03a-objection-runtime-design.md) (precedente de detector pattern)
- [FE-03c actions framework](./2026-06-12-fe03c-actions-adapter-framework-design.md) (precedente de adapter framework — modelo a copiar)
- [FE-01a schema foundation](../plans/2026-06-02-flowengine-fe01a-schema-foundation.md) (tabelas reservadas que vão ser usadas)
**Não conflita com:** nenhum ADR. Estende FlowEngine v2 sem reescrita.

---

## 1. TL;DR

Pedro pediu uma "arquitetura de agente inteligente" dentro do FlowEngine v2 — que comporte **inúmeras condicionais e ramificações** durante a conversa, **detecte, preveja, contorne** problemas no caminho do lead, mantenha **harmonia/fluidez** mesmo quando o fluxo desvia, e seja **retroalimentado** (aprenda com casos passados).

**Pesquisa do estado atual** (32 docs + 100+ arquivos de código) revelou:

- ✅ **80% do que o Pedro quer JÁ existe** em forma de mecanismos isolados: 7 detectores ativos, TurnDecision emite 11+ sinais por turno, recovery patterns consolidados, condicionais via simpleeval, KB retrieval pgvector.
- ❌ **20% genuinamente falta** ou está hardcoded sem extensão clara: framework plugável pra detectores, recovery strategies além de escalar, **banco de abordagens comportamentais** (KB hoje é só factual), retroalimentação automática (tabelas reservadas, zero runtime), memória de longo prazo do lead.

**Proposta:** arquitetura em **7 camadas**, cada uma independente e implementável como plano dedicado. **Camadas 1-2** (Detector Framework + Recovery Strategies) são extensões naturais do que existe — formalizam patterns ad-hoc atuais em ABCs + registries seguindo o exato precedente do FE-03c. **Camada 3** (Banco de Abordagens) é o "diferencial" que Pedro mencionou — exige novo subsistema mas com estrutura previsível. **Camadas 4-7** são roadmap.

**Escopo do PR/plano executável que sairá daqui:** Camadas 1-2 + esqueleto da Camada 3. ~600-800 LOC. Camadas 4-7 entram como specs separadas conforme necessidade.

---

## 2. Contexto: a visão do Pedro

Resumo do pedido (palavras dele):

> *"Criar uma estrutura de um agente inteligente dentro da arquitetura do FlowEngine, óbvio que seguindo à risca v2. Pensar em inúmeras condicionais ao longo do caminho do lead, ramificações que podem surgir, como adequar o sistema pra se precaver e ficar responsável por esperar caso aconteça e contornar caminhos. Verificação, prevenção, previsão. Não ter conflito, não parar no meio, garantir harmonia e fluidez total. Lead vive a experiência como se fosse humano. Banco de abordagens que vai sendo retroalimentado."*

Decomposição em **9 capacidades operacionais**:

| # | Capacidade | O que significa operacionalmente |
|---|---|---|
| 2.1 | Condicionais ricas | TreeFlow expressa rotas variadas (não só "node A → node B") |
| 2.2 | Ramificações dinâmicas | Lead segue caminho diferente baseado em sinais detectados em runtime |
| 2.3 | Detecção | Sistema identifica que "algo está acontecendo" (objeção, desânimo, intenção de compra, fadiga, frustração, etc) |
| 2.4 | Prevenção | Sistema antecipa problemas (lead saindo do trilho) e ajusta antes de virar incidente |
| 2.5 | Previsão | Sistema prevê próximos passos (intenção de compra, churn, momento certo de fechar) |
| 2.6 | Recovery / contorno | Quando algo dá errado, sistema reage sem travar (não só escalação) |
| 2.7 | Harmonia / fluidez | Múltiplas detecções/respostas se compõem sem conflito |
| 2.8 | Experiência humana | Tom, ritmo, escolha de palavras alinhados a "humano que entende" |
| 2.9 | Banco de abordagens + retroalimentação | Biblioteca de táticas que cresce e melhora com base em outcomes passadas |

---

## 3. Diagnóstico: o que JÁ existe (créditos ao Nicolas + Claude)

**Antes de propor nada novo, registrar o que já está construído.** Pesquisa profunda em `docs/superpowers/specs/` (15 docs), `docs/superpowers/plans/` (13 docs), `docs/superpowers/notes/`, `CLAUDE.md`, e `src/ai_sdr/` (todo o código FlowEngine v2).

### 3.1. Mapeamento das 9 capacidades vs estado atual

| Capacidade do Pedro | Estado | Como está implementado | Gap real |
|---|---|---|---|
| **2.1 Condicionais ricas** | ✅ **Funciona** | `simpleeval` no `next_nodes[].condition` + `exit_condition` (3 tipos) + `is_set()` helper. Context: `collected`, `extracted_facts`, `objections_handled`, `turn_index`. Bloqueio dinâmico se `active_treatment` setado. | Sem funções domain-specific (`sales_stage_qualifies()`, `lead_score_high()`). Sem branching probabilístico. |
| **2.2 Ramificações dinâmicas** | ✅ **Funciona** | `next_nodes: list[Transition]` no NodeSpec. Objection classifier desvia pra subnode (BACK_TO_ORIGIN). | Sem branching paralelo (qualificação + agendamento simultâneo). Sem sub-fluxos compartilháveis. |
| **2.3 Detecção** | ✅ **Funciona** (7 detectores) | Objection (tool/inline), off-topic counter, guardrails whitelist, critic pass, request_human_escalation, suspect_injection_attempt, Sentinel (parcial). TurnDecision emite 11+ sinais por turno. | **Não há framework plugável.** Adicionar novo detector (sentiment, intent, fadiga) exige refactor do pipeline. |
| **2.4 Prevenção** | ⚠️ **Reativa, não preventiva** | Detectores reagem DEPOIS que LLM emite sinal. Guardrails validam DEPOIS que LLM responde. | Sem mecanismo de detecção preventiva (e.g., "lead já mostrou 2 sinais de desânimo — ajusta tom no próximo turn antes de virar problema"). |
| **2.5 Previsão** | ❌ **Quase ausente** | Sentinel (`risk_level`) modelo existe; triggers automáticos não implementados. Nenhuma sentiment/intent prediction. | **Major gap.** Não há sistema de inferência sobre "estado mental" do lead nem "próximo passo provável". |
| **2.6 Recovery / contorno** | ⚠️ **Existe, mas regressivo** | Fallbacks por categoria: `fallback_text`, `escalate_to_human`, `gracefully_continue`, `max_treatment_turns`. `_handle_exhausted` hook existe. | **Recovery é binário** (fallback OU escalar). Sem strategies intermediárias ("offer_alternative", "reduce_scope", "ask_clarification"). Sem framework plugável. |
| **2.7 Harmonia / fluidez** | ⚠️ **Sequencial** | Detectores são processados em ordem fixa no `post_processing.apply_decision`. Priority hardcoded: objection > offtopic > validator > escalation. | Sem sistema de **composição** quando múltiplos sinais coexistem. Ex: lead frustrado E pedindo info que não temos — qual respondemos primeiro? |
| **2.8 Experiência humana** | ⚠️ **Cosmética** | Humanização FE-03b: chunking, typing delays, `mark_as_typing`. Variação de tom 100% delegada ao LLM (via prompt). | Sem variação de tom **adaptativa** (não responde mais consultivamente se lead técnico, mais empático se lead emocional). Sem memória de longo prazo do lead. |
| **2.9 Banco de abordagens + retroalimentação** | ❌ **Não existe** | KB existe mas é **factual** (preços/garantias/features). Tabelas `experiments`, `response_reviews`, `treeflow_improvement_suggestions` reservadas em FE-01a mas zero runtime. | **Major gap.** Sem playbook comportamental, sem outcome tracking, sem aprendizado loop. |

### 3.2. Tabela de detectores existentes (referência)

| Detector | Trigger | State mutado | Recovery |
|---|---|---|---|
| Objection (tool) | `TurnDecision.detected_objection` + `treatment_mode='tool'` | `active_treatment` state machine | `max_treatment_turns` → gracefully_continue OR escalate |
| Objection (inline) | `TurnDecision.detected_objection` + `treatment_mode='inline'` | `objections_handled` log entry | Resolvido inline no `response_text` |
| Off-topic | `TurnDecision.off_topic_detected=true` | `collected['__off_topic_count__']` | Aos 3 strikes → `requires_review_reason='off_topic_exhausted'` |
| Guardrails whitelist | Python validator pós-LLM (preço/produto fora) | — (state não muda em violação) | 1 retry corretivo; 2ª falha → fallback + `requires_review_reason='validator_exhausted'` |
| Critic pass | LLM judge em nodes `critical=true` | — | Retry com `suggested_fix`; após `max_retries` → fallback |
| Request human | `TurnDecision.request_human_escalation` is HumanEscalation | — | `requires_review_reason='escalation_requested'` |
| Suspect injection | `TurnDecision.suspect_injection_attempt=true` | — (logado mas sem efeito automático) | Plan-N: Sentinel pode subir `risk_level` |
| Sentinel | `Lead.risk_level != 'normal'` (verificado pré-turn) | Lead row | Bloqueia pipeline se `banned` |

### 3.3. Conclusão do diagnóstico

A visão "TreeFlow inteligente" do Pedro é **80% formalização do que já existe** + **20% adições genuínas**. Isso é positivo porque:

- ✅ Não precisa reescrever core
- ✅ Existe **padrão claro a seguir** (FE-03c Actions Framework — ABC + registry + factory + decorator `@register`)
- ✅ Tabelas já reservadas pra retroalimentação (`experiments`, `response_reviews`, `treeflow_improvement_suggestions`)
- ⚠️ Risco: confundir "formalização" com "feature nova" — gerar over-engineering

**Princípio guia desta spec:** sempre que possível, **extrair o pattern existente** em ABC + registry. Adicionar feature genuinamente nova **só quando o gap é claro** e justifica complexidade.

---

## 4. Princípios arquiteturais

Antes da arquitetura técnica, princípios não-negociáveis:

1. **Não quebrar FlowEngine v2.** Pipeline `run_turn`, TurnDecision schema, RLS, advisory lock, checkpointer — **invariantes**. Mudanças no core são proibidas; extensões via pontos de extensão claros são bem-vindas.
2. **Reusar precedentes do projeto.** Padrão FE-03c (ABC + registry + factory + decorator) é o **modelo a copiar** pra detector framework e recovery strategies.
3. **Não duplicar conceitos.** Se algo já existe (e.g., KB retrieval, action adapter, TurnDecision signals), **estender**, não recriar.
4. **Cada camada independente.** Cada uma das 7 camadas é implementável como plano separado, em PRs separados. Não há "big bang".
5. **Feature flags por tenant.** Toda nova capacidade liga/desliga por `tenant.yaml` — Manoela usa, próximo cliente pode não usar.
6. **Observability desde o dia 1.** Cada detector/recovery emite structlog event auditável. Não criar features sem visibilidade.
7. **Backward compatibility absoluta.** TreeFlows YAML existentes continuam funcionando sem mudança. Adições são opt-in.
8. **Idempotência onde aplica.** Padrões FE-03c (UNIQUE constraint + value_hash) aplicáveis a actions; recovery strategies precisam ser safe pra reexecução.
9. **Lead.profile JSONB já existe.** Memória de longo prazo do lead usa esse slot reservado, não nova tabela.
10. **Tabelas FE-01a reservadas são santuário.** `experiments`, `response_reviews`, `treeflow_improvement_suggestions` foram pré-paveadas pelo Nicolas pra exatamente isso. Usar.

---

## 5. Arquitetura proposta — 7 camadas

### 5.1. Visão geral

```
┌────────────────────────────────────────────────────────────────────┐
│                    FlowEngine v2 run_turn (existente)              │
│  preprocessing → LLM call → apply_decision → post_processing       │
└────────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│ Camada 1        │ │ Camada 2         │ │ Camada 3                 │
│ Detector        │ │ Recovery         │ │ Approach Library         │
│ Framework       │ │ Strategies       │ │ (Banco de Abordagens)    │
│ ABC + registry  │ │ ABC + registry   │ │ KB-like + outcome track  │
└─────────────────┘ └──────────────────┘ └──────────────────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌─────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│ Signal[]        │ │ Strategy[]       │ │ ApproachSnippet[]        │
│ (sinais         │ │ (estratégias     │ │ (táticas pré-escritas    │
│  emitidos)      │ │  aplicáveis)     │ │  com outcome rate)       │
└─────────────────┘ └──────────────────┘ └──────────────────────────┘

        ╔═══════════════════════════════════════════════════════╗
        ║   Camadas 4-7 (roadmap futuro)                         ║
        ╠═══════════════════════════════════════════════════════╣
        ║ 4. Long-term lead memory (leads.profile JSONB usage)  ║
        ║ 5. Retroalimentação loop (experiments + reviews)      ║
        ║ 6. Decision tracing (reasoning estruturado)           ║
        ║ 7. Adaptive routing (LLM-assisted next_node)          ║
        ╚═══════════════════════════════════════════════════════╝
```

### 5.2. Resumo das 7 camadas

| # | Camada | Status hoje | Esforço | Prioridade |
|---|---|---|---|---|
| **1** | **Detector Framework** plugável | Ad-hoc — 7 detectores hardcoded | M (~3-4 dias) | **Alta** — bloqueia adicionar novos detectores |
| **2** | **Recovery Strategies** plugáveis | Binário (fallback OR escalar) | M (~3-4 dias) | **Alta** — destrava "contornar caminhos" |
| **3** | **Approach Library** (banco de abordagens) | Não existe | L (~7-10 dias) | **Média-Alta** — diferencial mas exige discussão |
| **4** | **Long-term lead memory** | `leads.profile` JSONB vazio | M (~4-5 dias) | Média — depende de uso real |
| **5** | **Retroalimentação loop** | Tabelas reservadas, zero runtime | L (~10-15 dias) | Baixa — só faz sentido com volume de talks fechadas |
| **6** | **Decision tracing** estruturado | `reasoning` é texto solto | S (~2-3 dias) | Baixa — útil mas não bloqueador |
| **7** | **Adaptive routing** LLM-assisted | YAML estático determinístico | L (~7-10 dias) | Baixa — só faz sentido após Camadas 1-3 amadurecerem |

**Recomendação:** este PR/spec foca em **Camadas 1-2 detalhadas + Camada 3 esboçada**. 4-7 são roadmap.

---

## 6. Camada 1 — Detector Framework plugável

### 6.1. Motivação

Hoje, 7 detectores estão **hardcoded** em pontos diferentes do pipeline:
- 4 sinais vêm direto do `TurnDecision` (LLM emite)
- Off-topic counter vive em `flowengine/offtopic.py`
- Objection state machine vive em `flowengine/objection_runtime.py`
- Guardrails whitelist vive em `guardrails/validator.py` + `runner.py`

Adicionar um 8º detector (e.g., "sentiment shift", "buying intent", "fatigue") exige:
1. Adicionar campo novo no `TurnDecision` (mudança intrusiva no schema do LLM)
2. Adicionar handler no `post_processing.apply_decision` (mudança no pipeline core)
3. Adicionar enum value em `RequiresReviewReason` se escalar

**Custo de cada novo detector hoje: alto.**

### 6.2. Padrão proposto (idêntico ao FE-03c)

ABC + registry + factory + decorator `@register`. Mesma estrutura do framework de actions.

```python
# src/ai_sdr/flowengine/detectors/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

DetectorSeverity = Literal["info", "warning", "critical"]

@dataclass(frozen=True)
class DetectorSignal:
    """Output normalizado de um Detector. Consumido pelo pipeline pós-LLM."""
    detector_name: str
    severity: DetectorSeverity
    payload: dict  # detector-specific data
    suggested_recovery: list[str]  # nomes de RecoveryStrategy aplicáveis (Camada 2)


class Detector(ABC):
    """Contract pra detectores plugáveis no pipeline pós-LLM.

    Detectores são chamados em ordem fixa após apply_decision. Cada um inspeciona
    o estado (TalkFlowState + TurnDecision + recent_history) e decide se emite
    Signal ou não.
    """
    name: str  # class attribute — registry key

    def __init__(self, tenant_config, secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def inspect(
        self,
        *,
        state: TalkFlowState,
        decision: TurnDecision,
        recent_history: list[Message],
    ) -> DetectorSignal | None:
        """Inspeciona estado pós-LLM e retorna Signal se detectou algo.

        Returns:
            DetectorSignal com severity + payload + suggested_recovery, ou None.
        """
```

### 6.3. Registry + factory (mesma pattern do FE-03c)

```python
# src/ai_sdr/flowengine/detectors/registry.py
DETECTORS: dict[str, type[Detector]] = {}

def register(cls: type[Detector]) -> type[Detector]:
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} missing `name` attribute")
    if cls.name in DETECTORS:
        raise ValueError(f"detector {cls.name!r} already registered")
    DETECTORS[cls.name] = cls
    return cls
```

### 6.4. Detectores migrados (não-quebrantes)

Os 7 detectores existentes ficam como impls do framework:

```python
# src/ai_sdr/flowengine/detectors/builtin.py

@register
class ObjectionDetector(Detector):
    name = "objection"
    async def inspect(self, *, state, decision, recent_history):
        if decision.detected_objection:
            return DetectorSignal(
                detector_name="objection",
                severity="warning",
                payload={"objection_id": decision.detected_objection, ...},
                suggested_recovery=["objection_treatment_tool", "objection_inline"],
            )
        return None


@register
class OffTopicDetector(Detector):
    name = "off_topic"
    async def inspect(self, *, state, decision, recent_history):
        if not decision.off_topic_detected:
            return None
        count = state.collected.get("__off_topic_count__", 0) + 1
        if count >= 3:
            return DetectorSignal(
                detector_name="off_topic",
                severity="critical",
                payload={"count": count},
                suggested_recovery=["escalate_to_human"],
            )
        return DetectorSignal(
            detector_name="off_topic",
            severity="info",
            payload={"count": count},
            suggested_recovery=["redirect_to_topic"],
        )


@register
class RequestHumanDetector(Detector):
    name = "request_human"
    # ... outros

@register
class GuardrailsWhitelistDetector(Detector):
    name = "guardrails_whitelist"
    # ... outros

@register
class SuspectInjectionDetector(Detector):
    name = "suspect_injection"
    # ... outros
```

### 6.5. Novos detectores possíveis (registrados via `@register`)

Sem mudança no pipeline core — só adicionar arquivo + import side-effect.

| Detector novo | Sinal | Suggested recovery |
|---|---|---|
| `sentiment_shift` | Lead passou de positivo pra negativo (LLM emite via TurnDecision extension) | `acknowledge_concern`, `slow_down`, `offer_pause` |
| `buying_intent` | Lead manifestou intenção clara (perguntou preço, prazo, próximo passo) | `accelerate_to_close`, `propose_next_step` |
| `fatigue` | Mensagens cada vez mais curtas / monossilábicas | `wrap_up_gracefully`, `schedule_followup` |
| `out_of_window` | Lead respondeu próximo do limite de 24h Meta | `send_hsm_followup`, `mark_at_risk` |
| `repeating_question` | Lead fez a mesma pergunta 2+ vezes | `clarify_misunderstanding`, `change_explanation` |
| `silence_pattern` | Lead pausou > N minutos no meio do turno | `gentle_nudge`, `passive_check_in` |

Cada detector é ~50-80 LOC. **Adicionar 1 novo = 1 arquivo, 0 mudanças no core.**

### 6.6. Integração no pipeline pós-LLM

`flowengine/post_processing.apply_decision` ganha 1 chamada nova:

```python
# Pseudo-código (post_processing.py)
async def apply_decision(state, decision, talk, ...):
    # ... merges collected/extracted_facts ...

    # NOVO: invoca todos os detectores em ordem registrada
    signals = await run_detectors(
        detectors=DETECTORS.values(),
        state=state,
        decision=decision,
        recent_history=state.messages[-4:],
    )

    # NOVO: dispatch pra recovery strategies (Camada 2)
    if signals:
        await dispatch_recoveries(signals, talk, state, ...)

    # ... resto do post_processing existente ...
```

`run_detectors` itera `DETECTORS` em ordem topológica (priority configurável). Cada Signal é loggado (structlog) + persistido em tabela nova `detector_signals` (audit).

### 6.7. Schema novo

Migration `0033_detector_signals.py`:

```sql
CREATE TABLE detector_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    talk_id UUID NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    detector_name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    payload JSONB NOT NULL,
    suggested_recovery JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_detector_signals_talk ON detector_signals (talk_id, turn_index);
CREATE INDEX ix_detector_signals_tenant_severity ON detector_signals (tenant_id, severity)
    WHERE severity IN ('warning', 'critical');

ALTER TABLE detector_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE detector_signals FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON detector_signals
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

### 6.8. Config no tenant.yaml

```yaml
flow_engine:
  detectors:
    enabled:
      - objection
      - off_topic
      - request_human
      - guardrails_whitelist
      # novos detectores adicionados conforme registrados:
      # - sentiment_shift
      # - buying_intent
      # - fatigue
    priority_order:
      - guardrails_whitelist  # crítico — sempre primeiro
      - objection
      - request_human
      - off_topic
      - sentiment_shift
      - buying_intent
      - fatigue
```

Tenant que não tem o bloco usa lista default (compatibility).

### 6.9. Observability

Eventos structlog novos:

| Event | Quando | Payload chave |
|---|---|---|
| `detector.signal.emitted` | Detector emite Signal | `detector_name`, `severity`, `talk_id`, `turn_index` |
| `detector.batch.completed` | Todos detectores rodaram pro turno | `total_signals`, `critical_count`, `duration_ms` |
| `detector.error` | Detector levantou exception | `detector_name`, `error_type`, `error_message` |

Detector falhando NÃO derruba o turno — log + skip + segue. Igual ao pattern de FE-03a (falha do classifier não derruba).

---

## 7. Camada 2 — Recovery Strategies plugáveis

### 7.1. Motivação

Hoje, recovery é **binário**:
- "Continue conversation normally" (default)
- "Escalate to human" (`requires_review_reason='...'`)

Pedro pediu **"contornar caminhos"** — estratégias intermediárias entre "tudo bem" e "passa pra humano":
- "Reduce scope" — agente reconhece que pediu demais, simplifica pergunta
- "Offer alternative" — agente propõe caminho diferente
- "Acknowledge concern" — pausa, valida sentimento do lead
- "Schedule followup" — agente sugere conversar depois
- "Change explanation" — usa outra abordagem da mesma KB

### 7.2. Padrão proposto

Idêntico ao detector framework. ABC + registry + factory.

```python
# src/ai_sdr/flowengine/recovery/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class RecoveryAction:
    """Output de uma RecoveryStrategy."""
    strategy_name: str
    next_response_text: str | None  # se setado, substitui response_text do LLM
    state_mutation: dict  # campos a setar em TalkFlowState
    requires_review_reason: str | None  # se setado, escala
    halt_pipeline: bool  # se True, não chama mais nenhuma strategy nesse turn


class RecoveryStrategy(ABC):
    """Contract pra estratégias plugáveis de recovery."""
    name: str

    def __init__(self, tenant_config, secrets: dict[str, str]) -> None: ...

    @abstractmethod
    async def applicable(self, signal: DetectorSignal, state, talk) -> bool:
        """Decide se essa strategy se aplica a esse signal."""

    @abstractmethod
    async def execute(self, signal: DetectorSignal, state, talk) -> RecoveryAction:
        """Aplica a strategy. Retorna RecoveryAction (mutação a aplicar)."""
```

### 7.3. Strategies built-in (migram patterns existentes)

```python
@register_strategy
class EscalateToHumanStrategy(RecoveryStrategy):
    name = "escalate_to_human"
    async def applicable(self, signal, state, talk):
        return signal.severity == "critical"
    async def execute(self, signal, state, talk):
        return RecoveryAction(
            strategy_name=self.name,
            next_response_text=None,  # mantém resposta do LLM
            state_mutation={},
            requires_review_reason=signal.payload.get("reason", "auto_escalation"),
            halt_pipeline=True,
        )


@register_strategy
class GracefullyContinueStrategy(RecoveryStrategy):
    name = "gracefully_continue"
    async def applicable(self, signal, state, talk):
        return signal.detector_name == "objection" and ...
    async def execute(self, signal, state, talk):
        return RecoveryAction(
            strategy_name=self.name,
            next_response_text=None,
            state_mutation={"active_treatment": None},  # limpa treatment
            requires_review_reason=None,
            halt_pipeline=False,
        )


@register_strategy
class AcknowledgeConcernStrategy(RecoveryStrategy):
    name = "acknowledge_concern"
    async def applicable(self, signal, state, talk):
        return signal.detector_name in ("sentiment_shift", "fatigue")
    async def execute(self, signal, state, talk):
        # Estratégia que PODE chamar LLM secundário ou pegar snippet do Approach Library (Camada 3)
        ...
```

### 7.4. Schema novo

Migration `0034_recovery_executions.py` (similar a `action_executions` de FE-03c):

```sql
CREATE TABLE recovery_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    talk_id UUID NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
    turn_index INT NOT NULL,
    signal_id UUID REFERENCES detector_signals(id),
    strategy_name TEXT NOT NULL,
    action_taken JSONB NOT NULL,  -- snapshot do RecoveryAction
    halted_pipeline BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_recovery_executions_talk ON recovery_executions (talk_id, turn_index);
ALTER TABLE recovery_executions ENABLE ROW LEVEL SECURITY;
-- ... policy idêntica ao padrão ...
```

### 7.5. Dispatcher de recovery

```python
# pseudo-code
async def dispatch_recoveries(signals, state, talk):
    # Ordena signals por severity (critical first)
    sorted_signals = sorted(signals, key=lambda s: severity_rank(s.severity), reverse=True)

    for signal in sorted_signals:
        for strategy_name in signal.suggested_recovery:
            strategy = STRATEGIES.get(strategy_name)
            if strategy is None:
                log.warning("recovery.strategy.not_registered", name=strategy_name)
                continue

            if not await strategy.applicable(signal, state, talk):
                continue

            action = await strategy.execute(signal, state, talk)
            await record_recovery_execution(signal, action)
            apply_action_to_state(action, state)

            if action.halt_pipeline:
                return  # halt all further recoveries
```

### 7.6. Config no tenant.yaml

```yaml
flow_engine:
  recovery:
    enabled:
      - escalate_to_human
      - gracefully_continue
      # adicionar conforme implementadas:
      # - acknowledge_concern
      # - offer_alternative
      # - schedule_followup
      # - change_explanation
    max_recoveries_per_turn: 2
    halt_on_critical: true
```

### 7.7. Observability

| Event | Quando |
|---|---|
| `recovery.strategy.applied` | Strategy.execute roda |
| `recovery.strategy.skipped` | applicable() retornou False |
| `recovery.strategy.not_registered` | Suggested recovery não existe no registry |
| `recovery.batch.completed` | Todas as recoveries do turno rodaram |

---

## 8. Camada 3 — Approach Library (banco de abordagens)

> Esta camada é o **diferencial** que o Pedro mencionou. É a única camada que requer subsistema novo significativo. Esboçada aqui — detalhes finais em spec dedicada após aprovação conceitual.

### 8.1. Motivação

KB atual é **factual** (preços, garantias, especificações). É a "fonte da verdade" sobre dados imutáveis.

Approach Library é **comportamental**. Coleção de táticas de venda/comunicação que o agente pode "puxar" baseado em contexto. Exemplos:

| Contexto | Approach snippet |
|---|---|
| Lead hesita ao saber preço alto | "Reframe: vamos pensar no custo de NÃO investir. Em quanto tempo você recupera com X clientes a mais por mês?" |
| Lead diz "vou pensar" | "Faz total sentido pensar. Posso te enviar 2-3 cases de quem teve seu perfil de faturamento e investiu? Sem compromisso." |
| Lead pergunta sobre concorrente direto | "Não comparo com concorrente — todo programa tem seu fit. Posso te explicar pra qual perfil de empreendedora a Mentoria foi desenhada?" |
| Lead técnico (engenheiro) | Tom: dados, estrutura, ROI. Evitar emoção. |
| Lead emocional (mãe empreendedora) | Tom: empatia, casos similares, conforto. |

### 8.2. Estrutura proposta

```yaml
# tenants/<slug>/approaches/<approach_id>.yaml
id: reframe_price_high
version: 1.0.0
context_tags:
  - stage: oferta_premium
  - signal: price_objection
  - lead_profile: high_revenue
  - persona: consultive

content:
  template: |
    Faz sentido pensar no investimento. Mas deixa eu te perguntar uma coisa:
    com {{ collected.faturamento_mensal }} mensais, quanto você gastaria pra
    captar mais 3-5 clientes pagantes desse mesmo ticket? A Mentoria foi
    pensada exatamente pra desbloquear essa escala.

  followup_questions:
    - "Faz sentido pra você?"
    - "Quer que eu mostre um caso similar?"

outcome_stats:  # populado pelo loop de retroalimentação (Camada 5)
  times_used: 0
  positive_outcome: 0
  negative_outcome: 0
  conversion_rate: null
```

### 8.3. Como o agente "puxa" approach

3 caminhos possíveis (decidir na spec dedicada):

**Opção A — Approach como input pro LLM**
O LLM principal recebe approach selecionado no system prompt como "sugestão". LLM decide se usa.

```
SystemMessage:
  ... persona ...
  <approach_suggestion>
  Caso o lead apresente objeção de preço, considere usar este reframe:
  "Faz sentido pensar no investimento..."
  </approach_suggestion>
```

**Opção B — Approach substitui response_text**
Quando um Detector emite Signal com `suggested_recovery`, RecoveryStrategy busca approach apropriado e usa **diretamente** como response.

**Opção C — Híbrido**
LLM gera resposta. Approach Library re-rankeia se sugestão tem stats melhores. Operador no Console aprova/edita.

Recomendação: **Opção C** como design final, mas começar com **Opção A** (menor risco arquitetural).

### 8.4. Retrieval

Reusa KB pgvector retrieval pattern, mas com filtros adicionais por `context_tags`. Schema separado pra não misturar com KB factual.

```sql
CREATE TABLE approach_snippets (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    approach_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_template TEXT NOT NULL,
    context_tags JSONB NOT NULL,  -- pra filtering
    embedding vector(1536),  -- pra semantic retrieval
    outcome_stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, approach_id, version)
);
```

### 8.5. Status nesta spec

Camada 3 fica **esboçada**, não detalhada. Implementação requer:
- Decisão A/B/C
- Schema final
- CLI pra autorar approaches (`ai-sdr approach add ...`)
- Retrieval com context-aware ranking
- Integração com Camada 5 (outcome tracking)

**Spec dedicada** após Camadas 1-2 estabilizarem.

---

## 9. Camadas 4-7 (roadmap)

### 9.1. Camada 4 — Long-term lead memory

`leads.profile` JSONB já existe vazio. Estrutura proposta:

```python
lead.profile = {
    "tone_preference": "consultive | empathetic | direct",
    "objection_history": [
        {"objection_id": "preco", "resolved": True, "tactic_used": "reframe_value"},
        ...
    ],
    "buying_signals_history": [...],
    "communication_pattern": {
        "avg_response_time_seconds": 120,
        "avg_message_length": 45,
        "preferred_language": "informal",
    },
    "last_outcome": "won" | "lost" | "no_interest",
    "interaction_count": 3,
}
```

Atualizada por job pós-close: analisa Talk → extrai padrões → enriquece profile.

Injetada no system prompt de Talks futuras (lead que voltou).

### 9.2. Camada 5 — Retroalimentação loop

Job pós-close (extends `scan_talks`):
1. Talk fechada com outcome (`closed_completed_success`, `closed_completed_failure`, etc)
2. LLM (Haiku) analisa Talk → extrai padrões: "qual approach foi usada", "lead respondeu bem a X tática", etc
3. Atualiza `approach_snippets.outcome_stats`
4. Atualiza `leads.profile`
5. Insere sugestão em `treeflow_improvement_suggestions` (tabela já reservada FE-01a)

### 9.3. Camada 6 — Decision tracing estruturado

`TurnDecision.reasoning` é texto solto. Propor estruturar:

```python
class DecisionTrace(BaseModel):
    chosen_path: str  # e.g., "answer_objection_price"
    alternatives_considered: list[str]  # paths não escolhidos
    detectors_fired: list[str]  # Signals emitidos neste turn
    recoveries_applied: list[str]
    kb_chunks_used: list[str]
    approach_used: str | None  # ID do approach (se Camada 3 ativa)
    confidence: float
    rationale: str  # texto livre (reasoning humano)
```

Persistido em `talkflow_states.decision_trace` JSONB. Útil pra debugging + Camada 5 (loop de aprendizado).

### 9.4. Camada 7 — Adaptive routing LLM-assisted

Hoje `next_nodes[].condition` é `simpleeval` determinístico. Propor:

```yaml
nodes:
  - id: qualificacao
    next_nodes:
      - condition: "faturamento_mensal >= 30000"
        target: oferta_premium
      - condition: "faturamento_mensal < 30000"
        target: oferta_basica

      # NOVO: routing assistido por LLM (opt-in)
      - condition: "llm_routing(question='qual oferta?')"
        target: dynamic
        llm_choices:
          oferta_premium: "se lead demonstrou interesse em volume + estrutura"
          oferta_basica: "se lead quer começar pequeno + validar"
          downsell: "se lead hesitou em investimento alto"
```

LLM (Haiku) escolhe `target` baseado em descrição. Mais flexível mas custa mais $.

---

## 10. Plano de implementação fasado (Camadas 1-2 + esboço da 3)

**Recorte recomendado pra esta spec virar plano executável:** Camadas 1 e 2 (Detector Framework + Recovery Strategies) + Camada 3 esboçada.

### 10.1. Fase I-1 — Detector Framework (~3-4 dias)

| Task | Escopo |
|---|---|
| I1.1 | Migration `0033_detector_signals.py` + RLS |
| I1.2 | Subsistema `src/ai_sdr/flowengine/detectors/` (base, registry, factory, errors) |
| I1.3 | Migrar 7 detectores existentes pra `detectors/builtin.py` com `@register` |
| I1.4 | Integrar `run_detectors` no `post_processing.apply_decision` |
| I1.5 | Tenant.yaml schema (`flow_engine.detectors`) |
| I1.6 | Tests unit (registry, factory) + integration (cada detector built-in continua funcionando) |
| I1.7 | CLAUDE.md seção "Detector Framework (Fase I-1)" |

**Critério de aceitação:** Todos os tests existentes continuam passando (0 regression) + tabela `detector_signals` ganha 1 row por turno por detector ativo.

### 10.2. Fase I-2 — Recovery Strategies (~3-4 dias)

| Task | Escopo |
|---|---|
| I2.1 | Migration `0034_recovery_executions.py` + RLS |
| I2.2 | Subsistema `src/ai_sdr/flowengine/recovery/` (base, registry, factory, errors) |
| I2.3 | Migrar patterns existentes (`escalate_to_human`, `gracefully_continue`) como strategies built-in |
| I2.4 | Integrar `dispatch_recoveries` no `post_processing.apply_decision` (depois de detectors) |
| I2.5 | 2-3 strategies novas como exemplo: `redirect_to_topic`, `acknowledge_concern`, `wrap_up_gracefully` |
| I2.6 | Tenant.yaml schema (`flow_engine.recovery`) |
| I2.7 | Tests unit + integration |
| I2.8 | CLAUDE.md seção "Recovery Strategies (Fase I-2)" |

**Critério de aceitação:** Strategies registráveis funcionam end-to-end + ao menos 1 cenário onde uma strategy nova (não-built-in) é acionada.

### 10.3. Fase I-3 — Approach Library esqueleto (~2-3 dias)

| Task | Escopo |
|---|---|
| I3.1 | Schema YAML pra approach snippet + Pydantic models |
| I3.2 | Migration `0035_approach_snippets.py` (tabela com embeddings) |
| I3.3 | CLI `ai-sdr approach add/list/edit/index` |
| I3.4 | Indexação inicial (reusa retriever KB) |
| I3.5 | Sem integração com pipeline ainda — apenas autoria + storage |

**Critério de aceitação:** Operador consegue criar 1 approach, indexar, e fazer query manual via CLI retornar match.

### 10.4. Não fazem parte desta fase

- ❌ Integração de Approach Library no LLM (decisão A/B/C pendente — spec dedicada)
- ❌ Camadas 4-7 inteiras

---

## 11. Não-objetivos (fora de escopo)

- ❌ Reescrever pipeline `run_turn` ou TurnDecision schema
- ❌ Substituir LLM por sistema rule-based — LLM continua sendo o cérebro
- ❌ Eliminar simpleeval em transitions (mantém como path default)
- ❌ Sentiment analysis ML (usar LLM via TurnDecision como detector)
- ❌ Multi-turn parallel conversations (out of scope v1)
- ❌ Autonomous self-modifying TreeFlow YAML — Camada 5 sugere, operador aprova
- ❌ Implementação de Camadas 4-7 nesta spec — só roadmap

---

## 12. Riscos e mitigações

| Risco | Severidade | Mitigação |
|---|---|---|
| Over-engineering — formalizar demais o que já funciona | Alta | Princípio §4 #3 ("não duplicar conceitos"). Camada 1 começa migrando os 7 detectores EXISTENTES, sem adicionar novo no PR inicial. |
| Performance — N detectores por turno pode lentificar | Média | Detectors devem ser `async` (não bloquear). Most são puros (sem I/O). Budget: ~50ms total. |
| Detector mal-implementado quebra pipeline | Média | Pattern FE-03a: detector falha = log + skip + segue. Nunca derruba turn. |
| Recovery strategies se compõem mal (loops) | Alta | `max_recoveries_per_turn: 2` no tenant.yaml. `halt_pipeline=true` em strategies críticas. |
| Approach Library vira "overengineering" se sub-utilizado | Média | Camada 3 só esqueleto neste PR. Integração com LLM em spec dedicada após validar valor. |
| Migrações destrutivas em produção | Alta | 3 migrations novas (0033, 0034, 0035) — todas idempotentes, com downgrade. Schema novo, zero alteração em tables existentes. |
| Confusão com FE-03a (objection runtime) | Média | Camada 1 ENGLOBA FE-03a como detector built-in. Documentado claramente. |

---

## 13. Open questions

1. **Camada 3 — opção A/B/C:** approach como suggestion no prompt (A), substitui response_text (B), ou híbrido com re-ranking (C)? Discussão dedicada.
2. **Priority dos detectores:** ordenação fixa em config OR LLM-decided? (Hoje hardcoded.)
3. **Detectores rodam em paralelo ou sequencial?** Asyncio.gather seria mais rápido mas pode disparar recoveries inconsistentes.
4. **Pra Camadas 4-7, em que ordem entrar?** Sentinel triggers (4) vs decision tracing (6) vs retroalimentação (5)?
5. **Approach Library — global por tenant ou também por TreeFlow?** Mentoria e Aceleradora têm approaches distintas?
6. **Tracing estruturado (Camada 6):** persistir EM CADA turno OU apenas em talks que escalaram?
7. **Compatibility com TreeFlows existentes:** detector/recovery activation é opt-in (default off) OR opt-out (default on)?

---

## 14. Métricas de sucesso

Após Camadas 1-2 em produção:

- [ ] **0 regressões** — testes existentes do FE-03a/03b/03c continuam passando
- [ ] **`detector_signals` table populated** — 1+ row por turn em sandbox/staging
- [ ] **`recovery_executions` populated** — pelo menos 2 strategies não-trivials acionadas
- [ ] **Tempo médio do `post_processing.apply_decision`** mantém-se < 200ms (vs baseline atual)
- [ ] **Adicionar novo detector** (e.g., `sentiment_shift`) = 1 arquivo novo, 0 mudanças no core
- [ ] **CLAUDE.md atualizado** com docs operacionais das 2 fases

---

## 15. Referências

- [`2026-06-12-fe03c-actions-adapter-framework-design.md`](./2026-06-12-fe03c-actions-adapter-framework-design.md) — **Modelo arquitetural copiado** (ABC + registry + factory + `@register`)
- [`2026-06-09-fe03a-objection-runtime-design.md`](./2026-06-09-fe03a-objection-runtime-design.md) — precedente de detector pattern (objection runtime vira detector built-in)
- [`2026-06-08-flow-engine-architecture-design.md`](./2026-06-08-flow-engine-architecture-design.md) — pipeline `run_turn` que é estendido
- [`2026-06-02-flowengine-fe01a-schema-foundation.md`](../plans/2026-06-02-flowengine-fe01a-schema-foundation.md) — tabelas reservadas (`experiments`, `response_reviews`, `treeflow_improvement_suggestions`) consumidas em Camada 5
- Código atual:
  - [`src/ai_sdr/flowengine/post_processing.py`](../../../src/ai_sdr/flowengine/post_processing.py) (ponto de integração)
  - [`src/ai_sdr/flowengine/offtopic.py`](../../../src/ai_sdr/flowengine/offtopic.py) (detector que vira built-in)
  - [`src/ai_sdr/flowengine/objection_runtime.py`](../../../src/ai_sdr/flowengine/objection_runtime.py) (idem)
  - [`src/ai_sdr/flowengine/escalation.py`](../../../src/ai_sdr/flowengine/escalation.py) (idem)
  - [`src/ai_sdr/flowengine/decision.py`](../../../src/ai_sdr/flowengine/decision.py) (TurnDecision schema — não muda)

---

**Fim da spec.**

> **Próximo passo após review do Nicolas:** spec aprovada → skill `writing-plans` gera 2 plans executáveis (Fase I-1 e Fase I-2 em PRs separados; Fase I-3 esqueleto pode ir junto ou separada). Camada 3 plena, Camadas 4-7 entram como specs dedicadas conforme demanda.
