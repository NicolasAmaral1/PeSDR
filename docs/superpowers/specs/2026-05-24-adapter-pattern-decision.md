# ADR: Standalone-first + Vialum-optional via adapter pattern

**Data:** 2026-05-24
**Status:** Aceita
**Tipo:** Architectural Decision Record (ADR) — postura arquitetural, não feature.
**Autor:** Nicolas Amaral (decisão com Claude)
**Referencia:** [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) (spec master)

---

## Contexto

PeSDR foi desenhado pra Joana (mentora de marca pessoal — cliente piloto que NÃO usa Vialum). Mas o Nicolas tem o ecossistema [Vialum](../../../../Vialum-Intelligence/) (Foundation: Hub + Chat + Media+Switch + Tasks) e quer poder rodar PeSDR **junto com Vialum** quando faz sentido (e.g., qualificação de lead inbound dentro de um workflow Tasks; WhatsApp chega via Chat em vez de webhook direto).

A questão arquitetural: PeSDR deve ser construído **acoplado ao Vialum** (assume Hub/Chat/Tasks rodando), **standalone puro** (nunca integra), ou **standalone-first com integração opcional**?

**Conversa de decisão:** 2026-05-24 entre o Nicolas e o Claude, durante a conclusão do Plano 3 (KB + Guardrails). Antes desta ADR, os Planos 5-N estavam desenhados assumindo standalone (RDStationAdapter, WhatsAppCloudAPIAdapter, UI HITL próprio).

---

## Decisão

**Standalone-first + Vialum-opcional via adapter pattern em 3 bordas.**

PeSDR core (TreeFlow engine, KB, guardrails, runtime, compiler) permanece puro — nunca importa código Vialum. As 3 bordas que cruzam com Vialum viram **interfaces com 2+ implementações**, escolhidas via `tenant.yaml`:

| Borda | Interface | Default impl (standalone) | Impl Vialum |
|---|---|---|---|
| **Messaging** (recebe/envia mensagens do lead) | `MessagingAdapter` | `WhatsAppCloudAPIAdapter` — webhook + send direto pra WhatsApp Cloud API | `VialumChatAdapter` — consome eventos de mensagens do Vialum Chat |
| **Identity** (resolve lead → contact estável) | `IdentityResolver` | `InternalLead` — modelo `leads` próprio do PeSDR (UUID) | `VialumHubAdapter` — lookup/create de Contact via Hub HTTP API |
| **HITL** (escalation quando guardrails esgotam retries) | `HitlSink` | `GenericWebhookSink` ou página web mínima de review | `VialumTasksInboxAdapter` — POST cria InboxItem em Vialum Tasks |

Pattern já provado no Plano 3 T2b (LLM factory via `init_chat_model("<provider>:<model>")`). Mesmo approach: schema declara `provider: str` livre, factory dispatcha por dict de implementações registradas.

**Config exemplo:**

```yaml
# tenant.yaml — Joana (standalone)
messaging:
  provider: whatsapp_cloud
  phone_number_id_ref: secrets/wa_phone_id
  access_token_ref: secrets/wa_token
identity:
  provider: internal
hitl:
  provider: generic_webhook
  webhook_url: https://joana.com.br/sdr-review-callback

# tenant.yaml — cliente Vialum
messaging:
  provider: vialum_chat
  endpoint: https://chat.luminai.ia.br
  api_key_ref: secrets/vialum_chat_key
identity:
  provider: vialum_hub
  endpoint: https://hub.luminai.ia.br
  api_key_ref: secrets/vialum_hub_key
hitl:
  provider: vialum_tasks
  endpoint: https://tasks.luminai.ia.br/inbox/items
  api_key_ref: secrets/vialum_tasks_key
```

---

## Não-objetivos (escopo limitado a propósito)

- **NÃO** criar adapter abstraction pra outras bordas (KB providers, embedding providers, observability backends, CRM além das 3 acima). YAGNI — só essas 3 têm 2+ impls realmente previstas.
- **NÃO** depender de Vialum rodar na mesma rede que PeSDR. Adapters Vialum são HTTP-over-internet (autenticados via API key/HMAC), não RPC interno.
- **NÃO** sistema de plugins / entry points / dynamic loading. Adapters são módulos Python regulares, escolhidos por string em `tenant.yaml` (mesmo pattern do `LLMConfig.provider`).
- **NÃO** PeSDR substitui Chat ou Tasks. PeSDR é um **sibling primitive** que opcionalmente consome esses serviços.

---

## Consequências

### Positivas

1. **Piloto Joana entrega valor sem dependência de Vialum.** PeSDR pode ser deployado sozinho, sem nenhum serviço Vialum rodando.
2. **PeSDR é comercializável standalone.** Alguém fora do ecossistema Vialum pode comprar/deployar PeSDR independente.
3. **Vialum integração não bloqueia roadmap.** Quando o ecossistema Vialum amadurecer, plugar PeSDR é trabalho focado de 1 plano (adapters), não refactor do core.
4. **Reuso de capacidade Vialum.** Cliente Vialum que ativa PeSDR pula Plano 5 (WhatsApp direto) e Plano 11 (HITL UI próprio) — Chat e Tasks Inbox já fazem.
5. **Filosoficamente alinhado com Vialum.** Vialum tem mantra "Foundation = primitives, Products = business logic" — PeSDR é primitive (engine genérica de conversa estruturada com KB+guardrails).

### Negativas / trade-offs

1. **Pequeno overhead de design.** Cada plano que toca uma das 3 bordas gasta ~10-20% extra definindo interface antes de implementar. ROI fica positivo já na 2ª implementação (Vialum adapter).
2. **Tentação de over-engineering.** Disciplina necessária pra NÃO criar interface pra bordas com 1 impl só. As 3 bordas acima são exaustivas pro horizonte previsível.
3. **Documentação importa mais.** Cada interface precisa contrato explícito (input/output shapes, error semantics, idempotência) + adapter-compliance test suite (mesmo conjunto de testes rodando contra cada impl).
4. **Coordenação inter-projeto.** Adapters Vialum acoplam PeSDR a contratos HTTP de Hub/Chat/Tasks. Mudança breaking nessas APIs requer atualizar PeSDR adapters. Mitigação: versionar APIs Vialum (e.g., `/v1/contacts`).

---

## Como isso afeta os planos futuros

| Plano | Antes desta ADR | Depois desta ADR |
|---|---|---|
| **4** — Objection Classifier + multi-provider validation | inalterado | inalterado (não toca borda Vialum) |
| **5** — Messaging | "WhatsApp Cloud Adapter" direto | "**MessagingAdapter abstraction + WhatsAppCloudAPIAdapter (default)**". `VialumChatAdapter` como task extra dentro do mesmo plano OU plano separado "Vialum-integration". |
| **6** — Identity | implícito no modelo Lead atual | "**IdentityResolver abstraction + InternalLead (default)**". `VialumHubAdapter` como task extra OU plano Vialum-integration. |
| **7** — CRM (RDStation) | inalterado | inalterado (CRM é útil em ambos modos; não é uma borda Vialum) |
| **8-10** — Media, Follow-up, Observability | inalterado | inalterado (todos agnostic) |
| **11** — HITL | "UI de review próprio" | "**HitlSink abstraction + GenericWebhookSink ou minimal page (default)**". `VialumTasksInboxAdapter` como task extra OU plano Vialum-integration. |
| **12** — Production polish | inalterado | inalterado |
| **Plano Vialum-integration (novo, opcional)** | n/a | Bundle os 3 adapters Vialum (VialumChat + VialumHub + VialumTasksInbox) num plano dedicado. Pode rodar em paralelo aos outros. Pode ficar pro fim do MVP. |

---

## Alternativas consideradas e rejeitadas

### A) Acoplar ao Vialum desde o início (PeSDR ≡ feature do Vialum)
- **Pró:** menos código (não precisa WhatsApp direto, não precisa HITL UI, etc).
- **Contra fatal:** Joana piloto não tem Vialum. PeSDR não funcionaria pra ela. Quebra premissa básica.

### B) Standalone puro (sem integração Vialum nunca)
- **Pró:** zero overhead arquitetural.
- **Contra fatal:** Nicolas tem ecossistema Vialum e PeSDR fica útil pra clientes Vialum. Rejeitar integração desperdiça leverage.

### C) Standalone + Vialum via single "compatibility module" / plugin externo
- **Pró:** keep core ainda mais puro.
- **Contra:** plugin systems Python adicionam complexidade (entry points, registration, version compatibility). Adapter pattern in-tree é mais simples e direto. Talvez no futuro extrair pra plugin separado se valer; por enquanto in-tree.

### D) PeSDR like-for-like com TreeFlow experimental do Chat (substituir)
- **Pró:** elimina ambiguidade entre os 2 sistemas no Vialum.
- **Contra:** Chat tem clientes usando o TreeFlow experimental hoje. Migração é trabalho separado. PeSDR pode coexistir como produto novo enquanto Chat depreca gradualmente o seu.

---

## Próximos passos

1. **Plano 4** (objection classifier + multi-provider validation) — segue como planejado, independente desta ADR.
2. **Plano 5** (Messaging) — abrir brainstorm referenciando esta ADR. Definir interface `MessagingAdapter` (assinatura, semântica de delivery, error shapes) antes de implementar WhatsApp Cloud.
3. **Plano 6** (Identity) — idem pra `IdentityResolver`.
4. **Plano 11** (HITL) — idem pra `HitlSink`. Pode ser bem mais leve (default = webhook simples) e adapter Vialum entra junto se Vialum tiver Inbox API estável até lá.
5. **Plano Vialum-integration** — opcional, pode rodar paralelo. Implementa os 3 adapters Vialum. Requer contratos HTTP estáveis em Hub/Chat/Tasks.

---

## Referências cruzadas

- Memória Claude: `project_standalone_plus_vialum_adapters.md` (auto-loaded em sessões futuras pra contextualizar brainstorms)
- Memória Vialum: `project_vialum_architecture.md` ("Foundation = primitives, Products = business logic")
- Memória HITL: `project_guardrails_hitl_direction.md` (já apontava `_handle_exhausted` como swap point — agora explícito: swap pra `HitlSink`)
- Pattern de referência: `src/ai_sdr/llm/factory.py` (init_chat_model dispatch) — Plano 3 T2b

---

**Fim da ADR.**
