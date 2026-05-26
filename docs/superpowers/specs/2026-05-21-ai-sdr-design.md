# AI SDR — Design Spec

**Data:** 2026-05-21
**Status:** Draft para revisão
**Autor:** Nicolas Amaral (brainstorm com Claude)

---

## 1. Resumo executivo

Plataforma de **AI SDR (Sales Development Representative)** multi-tenant que automatiza qualificação e nutrição de leads inbound via WhatsApp, integrada bidirecionalmente a CRMs externos. O sistema é **agnóstico de CRM e de provider de LLM**, com fluxos de conversa **declarativos** (YAML versionado em Git) que podem ser editados sem mudar código.

**Caso de uso piloto:** mentora de marca pessoal com 2 funis (Mentoria R$ 6.000 / Aceleradora R$ 1.497–2.000) + downsell automático (R$ 247). Volume inicial: ~1 cliente. Arquitetura projetada para escalar pra N clientes desde o dia 1.

**Stack:** Python · LangGraph · LangChain · FastAPI · PostgreSQL + pgvector · Redis · Docker Compose · VPS (Hostinger).

---

## 2. Conceitos centrais (modelo de domínio)

| Conceito | O que é |
|---|---|
| **Tenant** | Cliente da plataforma (ex: "Joana Mentora"). Tudo é isolado por `tenant_id`. |
| **TreeFlow** | Definição estática de um funil de conversa. Mapa de todas as rotas possíveis. Versionado, imutável após publicar. Declarado em YAML. |
| **TalkFlow** | Instância viva de uma conversa percorrendo um TreeFlow. Tem state persistido, checkpoint, histórico. Um por lead em conversa. |
| **Node** (estágio) | Nó do grafo no TreeFlow. Tem prompt próprio, KB própria, condições de saída, ações. |
| **Transição** | Edge condicional entre Nodes. Decide próximo Node baseado no State da conversa. |
| **Objeção** | Side-handler que dispara quando classificador detecta no input do lead. Declarado por Node ou global no funil. |
| **KB (Knowledge Base)** | Conjunto de documentos indexados por embedding (pgvector). Cada Node referencia uma KB específica. |
| **CRMAdapter** | Implementação concreta da interface comum de CRM. Um por CRM suportado (RD Station, Pipedrive, etc.). |
| **MessagingAdapter** | Implementação concreta da interface de canal (WhatsApp Cloud API hoje; futuros: Instagram, Email). |

---

## 3. Arquitetura macro

```
┌─────────────────────────────────────────────────────────────┐
│  ENTRY LAYER                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ RDStation   │  │ Pipedrive   │  │ HubSpot     │  ...     │
│  │ Adapter     │  │ Adapter     │  │ Adapter     │          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
│         └────────────────┼────────────────┘                 │
│                          │  (interface: CRMAdapter)         │
└──────────────────────────┼──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  CORE — agnóstico, multi-tenant                              │
│  • Conversation Engine (LangGraph)                           │
│  • TreeFlow loader (YAML → StateGraph)                       │
│  • TalkFlow runtime (checkpoints no Postgres)                │
│  • Objection Classifier                                      │
│  • KB Retrievers (pgvector por Node)                         │
│  • Field Extractor (Pydantic structured output)              │
│  • Guardrails (anti-alucinação)                              │
│  • Follow-up Scheduler                                       │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  MESSAGING LAYER                                             │
│  WhatsApp Cloud API  │  ElevenLabs (TTS)  │  Whisper (STT)  │
└─────────────────────────────────────────────────────────────┘

        ┌──────────────────────────┐
        │  Postgres + pgvector     │  ← tenant_id em todas as tabelas
        │  (RLS — Row-Level Sec.)  │
        ├──────────────────────────┤
        │  Redis (filas + cache)   │
        └──────────────────────────┘
```

### Princípios

1. **Adapter pattern em 2 fronteiras** (CRM + Messaging). Core nunca importa SDKs específicos.
2. **Core agnóstico** — usa interfaces; trocar de CRM = nova classe + config, zero mudança no core.
3. **Multi-tenant por `tenant_id`** com Row-Level Security do Postgres (isolamento garantido no banco).
4. **State na conversa** via checkpoint nativo do LangGraph (não em RAM).
5. **YAML como source of truth** dos TreeFlows. Git é o sistema de versionamento.

---

## 4. Componentes principais

### 4.1 Conversation Engine (LangGraph)

| TreeFlow concept | LangGraph |
|---|---|
| TreeFlow compilado | `StateGraph` |
| TalkFlow (instância) | `thread_id` + checkpoint |
| Node | `add_node()` |
| Transição condicional | `add_conditional_edge()` |
| Pausa pra humano | `interrupt()` |
| State da conversa | `TypedDict` persistido |

### 4.2 TreeFlow Loader

Lê `tenants/<tenant>/treeflows/<flow>.yaml`, valida com Pydantic schema, compila em `StateGraph` LangGraph. Cache em memória (invalida quando arquivo muda — watch em dev, restart em prod).

### 4.3 Field Extractor

LLM gera structured output (Pydantic) declarado no Node:

```python
class QualificacaoOutput(BaseModel):
    response_text: str                         # o que o agente diz
    faturamento_mensal: Optional[int] = None   # extraído da conversa
    tempo_mercado: Optional[str] = None
```

Campos extraídos vão pro State; texto vai pro WhatsApp.

### 4.4 Objection Classifier

Roda por Node ativo. Recebe a `handles_objections` list do Node (+ globais do funil). Para cada mensagem do lead:

1. LLM (Haiku, barato) classifica: qual objeção foi detectada (se alguma)?
2. Se detectou → desvia pro Node "objeção:X"
3. Trata, registra em `state.objections_handled`
4. Volta pro Node original
5. Reavalia: ainda tem objeção pendente? (re-detecção em loop)

### 4.5 Guardrails (anti-alucinação)

**4 camadas:**

| # | Camada | Onde aplica | Custo |
|---|---|---|---|
| 1 | System prompt rígido + KB injetada | Todos os nodes | Zero extra |
| 2 | KB-grounding obrigatória pra fatos comerciais (preço, prazo, garantia) | Nodes que falam de oferta | Custo do retrieval |
| 3 | Structured output Pydantic + whitelist validator | Todos os nodes que mencionam valores | Zero extra |
| 4 | Critic pass (segundo LLM revisa antes de enviar) | Nodes marcados `critical: true` | 2x no node |

Camada 3 (whitelist):
```yaml
# tenant.yaml
guardrails:
  allowed_prices: [247, 1497, 1997, 2000, 6000]
  allowed_products: ["Mentoria", "Aceleradora", "Downsell"]
```

Se LLM responder mencionando `R$ 5.000` → validator bloqueia, re-roda LLM com feedback.

### 4.6 Follow-up Scheduler

Worker assíncrono que percorre TalkFlows pausados por inatividade e dispara follow-up conforme `treeflow.follow_up.sequence` (config por funil, ver 5.1). Cada TalkFlow herda a cadência do TreeFlow que está rodando — funis diferentes têm follow-ups diferentes. Lib específica: ver seção 23.

### 4.7 KB Retrievers (RAG por Node)

- Cada Node referencia 1+ KBs por id
- KBs são arquivos `.md` em `kb/<tenant>/<kb_id>/` indexados em pgvector
- Reindex automático em CI quando arquivo muda
- Retrieval: top-k chunks por similaridade + filtro de threshold mínimo (0.7 default)

---

## 5. Anatomia de um Node e do TreeFlow

### 5.0 Estrutura do TreeFlow (alto nível)

```yaml
# tenants/joana-mentora/treeflows/mentoria.yaml
id: "mentoria"
version: "1.0.0"                          # imutável após publicado
display_name: "Funil Mentoria"

# Follow-up — por funil (ver 5.1)
follow_up:
  enabled: true
  max_attempts: 3
  sequence:
    - after: "24h"
      template: "Oi {{nome}}, ainda quer conversar sobre a mentoria?"
    - after: "72h"
      template: "{{nome}}, posso te ajudar a..."
    - after: "7d"
      template: "Última tentativa: ..."

# Objeções globais do funil (ver seção sobre objeções)
global_objections:
  - id: "preciso_pensar"
    kb: "kb_obj_pensar_mentoria"
  - id: "falta_tempo"
    kb: "kb_obj_tempo"

# Node inicial
entry_node: "saudacao"

# Lista de Nodes
nodes:
  - id: "saudacao"
    ...
  - id: "qualificacao"
    ...
```

### 5.1 Follow-up (por TreeFlow)

Cada TreeFlow declara sua própria cadência de follow-up. Funis distintos têm comportamentos distintos:

| Funil | Cadência típica | Por que |
|---|---|---|
| Aceleradora (entrada, ticket médio) | 3 tentativas em 24h / 72h / 7d | Volume, urgência |
| Mentoria (premium) | 2 tentativas em 48h / 7d | Lead premium, sem desespero |
| Downsell | 1 tentativa em 24h | Última cartada |

**Comportamento:**
- TalkFlow herda `follow_up` do TreeFlow que está rodando
- Se TalkFlow trocar de TreeFlow (migração ou redirect), passa a usar o follow-up do novo
- Lead responde → reset do contador
- `max_attempts` alcançado → marca TalkFlow como "frio", para de tentar

**Override por Node (opcional, V2):**
Algum estágio específico pode override (ex: Node "fechamento" usa follow-up mais agressivo). Não implementar no MVP — config a nível de TreeFlow é suficiente.

### 5.2 Anatomia de um Node

```yaml
node:
  id: "qualificacao"
  
  prompt: |
    Você é uma SDR conversando no WhatsApp em nome da Joana.
    Estágio: qualificação. Pergunte sobre {{questions}}.
    Tom: amigável, sem formalidade, frases curtas.
    
  llm:                                  # opcional — herda do default se omitido
    provider: "anthropic"
    model: "claude-sonnet-4-6"
    temperature: 0.7
  
  knowledge_base:                       # opcional
    - id: "kb_qualificacao_mentoria"
      top_k: 3
      min_score: 0.7
  
  collects:                             # campos a extrair
    - field: faturamento_mensal
      type: number
      extraction_hint: "valor mensal em R$"
      required: true
      validation: { min: 0 }
    - field: tempo_mercado
      type: text
      required: true
  
  exit_condition:                       # gate pra avançar
    type: "all_fields_filled"           # | "rule_expression" | "llm_judge" | "combined"
    fallback: "llm_judge"
  
  handles_objections:                   # objeções escutadas neste node
    - id: "preco"
      kb: "kb_obj_preco_qualif"
  
  sync_to_crm: "on_node_exit"           # | "immediate" | "on_handoff" | "manual"
  
  critical: false                       # se true, ativa critic pass
  
  next_nodes:                           # transições
    - condition: "faturamento_mensal >= 30000"
      target: "oferta_premium"
    - condition: "faturamento_mensal < 30000"
      target: "oferta_aceleradora"
    - condition: "lead_disse_nao"
      target: "downsell"
```

---

## 6. Configuração por Tenant

### 6.1 `tenants/<tenant>/tenant.yaml`

```yaml
id: "joana-mentora"
display_name: "Joana Mentora"
timezone: "America/Sao_Paulo"

# Horário de atendimento
schedule:
  mon-fri: "08:00-22:00"
  sat: "09:00-18:00"
  sun: "off"
  off_hours_behavior: "queue"          # | "respond_with_notice"

# Conversação
conversation:
  debounce_ms: 5000                    # agrupa msgs do lead em janela de 5s
  optout_stop_words: ["para", "pare", "parar", "stop", "sair"]
  optout_action: "end_conversation_silent"

# (Follow-up é configurado por TreeFlow — ver seção 5.1)

# LLM default (Nodes herdam se não overridarem)
llm:
  default:
    provider: "anthropic"
    model: "claude-sonnet-4-6"
    temperature: 0.7
    api_key_ref: "secrets/anthropic_key"
  classifier:                          # usado em Objection Classifier
    provider: "anthropic"
    model: "claude-haiku-4-5"
  
# Limites de custo
limits:
  max_usd_per_day: 50.0
  alert_at_pct: 80

# CRM
crm:
  provider: "rd_station"
  credentials_ref: "secrets/rd_station"
  webhook_secret_ref: "secrets/rd_webhook_secret"
  field_mapping:
    faturamento_mensal:
      crm_field: "cf_faturamento_mensal"
      crm_type: "number"
    tempo_mercado:
      crm_field: "cf_tempo_mercado"
      crm_type: "string"
  stage_mapping:
    qualificacao: "Lead - Qualificação"
    handoff_humano: "Sales - Negociação"
    downsell: "Lead - Downsell"

# Messaging
messaging:
  provider: "whatsapp_cloud"
  phone_number_id_ref: "secrets/wa_phone_id"
  access_token_ref: "secrets/wa_token"
  webhook_verify_token_ref: "secrets/wa_verify"

# Mídia
media:
  inbound_audio: { enabled: true, provider: "whisper" }
  inbound_image: { enabled: true, provider: "anthropic_vision" }
  outbound_audio: { enabled: true, provider: "elevenlabs", voice_id_ref: "secrets/eleven_voice" }

# Guardrails
guardrails:
  allowed_prices: [247, 1497, 1997, 2000, 6000]
  allowed_products: ["Mentoria", "Aceleradora", "Downsell"]

# Funis ativos
treeflows:
  - id: mentoria
    entry_trigger:
      source: "rd_station"
      form_id: "form_mentoria_xyz"
  - id: aceleradora
    entry_trigger:
      source: "rd_station"
      form_id: "form_aceleradora_abc"
```

### 6.2 `tenants/<tenant>/secrets.enc.yaml`

Criptografado com SOPS + age (commitado). Decryp em runtime via `sops.decrypt()`.

```yaml
rd_station: ENC[AES256_GCM,data:...]
wa_token: ENC[AES256_GCM,data:...]
anthropic_key: ENC[AES256_GCM,data:...]
eleven_voice: ENC[AES256_GCM,data:...]
```

---

## 7. Fluxos de execução

### 7.1 Entrada de lead

```
Lead preenche Typeform
  ▼
Typeform → CRM (RD Station) cria contato
  ▼
RD dispara webhook → SDR /webhook/crm/rd_station
  ▼
SDR identifica tenant pelo webhook (auth via secret)
  ▼
SDR identifica TreeFlow pelo form_id (entry_trigger)
  ▼
SDR cria TalkFlow (thread_id = "tenant:joana-mentora:lead:42")
  ▼
SDR inicia conversa via WhatsApp Cloud API
```

### 7.2 Mensagem do lead

```
WhatsApp → SDR /webhook/messaging/whatsapp
  ▼
SDR identifica tenant+lead, busca TalkFlow ativo
  ▼
Se áudio → Whisper transcreve → texto
Se imagem → Vision descreve → texto
  ▼
Debounce: aguarda janela de N segundos (tenant.conversation.debounce_ms)
  ▼
Concatena mensagens recebidas na janela
  ▼
Roda Objection Classifier (paralelo)
  ▼
Se objeção detectada → ativa Node "objeção:X" (carrega KB do tratamento)
Senão → continua Node ativo
  ▼
Node executa: extrai campos (structured output) + gera response_text
  ▼
Guardrails validam response_text
  ▼
Se sync_to_crm = immediate → empurra campos pro CRM
  ▼
Avalia exit_condition do Node → se passou, transição pro próximo
  ▼
Se sync_to_crm = on_node_exit → empurra campos pro CRM
  ▼
Envia response_text via WhatsApp (e/ou áudio via ElevenLabs)
```

### 7.3 Handoff pro humano

```
Node atingiu transição → "handoff_humano"
  ▼
SDR atualiza CRM: stage = mapped("handoff_humano"), adiciona nota com resumo
  ▼
SDR notifica vendedor (canal interno: Slack/WhatsApp interno — config futura)
  ▼
SDR entra em "modo silencioso" pra esta TalkFlow
  ▼
LangGraph.interrupt() — TalkFlow pausada, aguarda human action
  ▼
Vendedor assume conversa no WhatsApp (mesmo número)
  ▼
SDR detecta msg de outgoing source = human → mantém silêncio
```

### 7.4 Downsell automático

```
Node "fechamento" detectou "lead não fechou" via exit_condition
  ▼
Transição → Node "downsell"
  ▼
Node "downsell" usa KB própria, oferece R$ 247
  ▼
Se lead aceita → handoff pro humano fechar
Se lead não aceita → finaliza TalkFlow com tag "downsell_rejeitado" no CRM
```

---

## 8. Sincronização bidirecional CRM ↔ SDR

### 8.1 Divisão de ownership

| Recurso | Dono | Justificativa |
|---|---|---|
| Dados de contato (nome, tel, email) | CRM | Vendedor edita lá |
| Estágio de funil de vendas | CRM | Visão de negócio |
| Custom fields do deal | CRM | Configuração comercial |
| **Estado da conversa** (TreeFlow node atual, objeções tratadas) | **SDR** | Específico do agente |
| **Histórico de mensagens WhatsApp** | **SDR** (com resumo como nota no CRM) | SDR já consome |
| **Qualificação coletada na conversa** | **SDR escreve, CRM lê** | Enriquecimento |

### 8.2 Anti-loop

Toda call do SDR → CRM carrega metadata `source: "sdr-<tenant_id>"`. Webhooks que chegam com esse source são ignorados.

Adicionalmente: cada evento processado tem `event_id` cacheado em Redis (TTL 24h) — idempotency.

### 8.3 Reconciliação periódica

Worker roda a cada 15min:
1. Lista deals modificados nas últimas 24h em cada CRM (via API do adapter)
2. Compara com TalkFlows ativos
3. Resolve divergências (ex: deal foi "perdido" no CRM → fecha TalkFlow)

### 8.4 Mapping de estágios (CRM-specific)

Definido em `tenant.crm.stage_mapping`. SDR internamente trabalha com nomes lógicos; adapter traduz pra IDs/labels do CRM específico.

---

## 9. Coleta de dados e envio pro CRM

### Estratégias de sync

| Estratégia | Quando dispara | Uso |
|---|---|---|
| `immediate` | Após extração de cada campo | Campos críticos (telefone correto, email) |
| `on_node_exit` (default) | Quando Node passa exit_condition | Padrão pra qualificação |
| `on_handoff` | Quando entra em handoff | Resumo final pro vendedor |
| `manual` | Trigger explícito no flow | Casos especiais |

### Auto-creation de custom fields

**Fora do MVP.** Onboarding manual: você cria os custom fields no CRM antes de ativar o tenant.

---

## 10. Versionamento de TreeFlows

### Regras

1. TreeFlow versionado: `mentoria.yaml`, `mentoria.v2.yaml`, etc. (ou usar Git tags)
2. TreeFlow imutável após publicado (rev hash gravado em cada TalkFlow)
3. TalkFlow guarda `tree_flow_version_id`

### Migração quando publica v2

**Modo Soft** (default):
- Só aplica se mudança for *additive* (perguntas/nodes adicionais ao final, sem remover/renomear)
- Detecta automaticamente via diff de schema
- Migra TalkFlows ativos in-place

**Modo Hard reset:**
- Força todos os TalkFlows ativos pro estágio inicial da v2
- Perde progresso, mas é simples e seguro
- Útil quando estrutura mudou demais

**Mapping rules declarativas** (V2 do produto, fora do MVP inicial):
```yaml
migrations:
  - from_node: "qualificacao"
    to_node: "qualificacao_v2"
```

---

## 11. Mídia

| Direção | Tipo | Provider | Pipeline |
|---|---|---|---|
| Inbound | Áudio | Whisper (OpenAI API) | WhatsApp → download → STT → texto entra como msg normal |
| Inbound | Imagem | Anthropic Vision | WhatsApp → download → descrição via Vision → texto |
| Inbound | Documento PDF | (fora do MVP) | — |
| Outbound | Texto | WhatsApp Cloud API | Direto |
| Outbound | Áudio | ElevenLabs TTS | Texto → ElevenLabs → mp3 → WhatsApp Cloud API |

**Decisão de quando enviar áudio:** flag por Node (`response_format: "text" | "audio" | "both"`) ou por tenant default. Lead que mandou áudio recebe áudio? — configurável (`tenant.media.mirror_audio: true/false`).

---

## 12. LLM e providers

LangChain padroniza interface (`BaseChatModel`). Suporte nativo a:

- Anthropic (`langchain-anthropic`)
- OpenAI (`langchain-openai`)
- Google Gemini (`langchain-google-genai`)
- DeepSeek (`langchain-deepseek`)
- Mistral (`langchain-mistralai`)
- Local via Ollama (`langchain-ollama`)

### Seleção por Node

```yaml
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-6"
  temperature: 0.7
```

### Defaults sugeridos

| Tipo de Node | Modelo sugerido | Por quê |
|---|---|---|
| Saudação, despedida | Claude Haiku 4.5 | Rápido, barato |
| Qualificação | Claude Sonnet 4.6 | Boa nuance em PT-BR |
| Tratamento de objeção | Claude Sonnet 4.6 ou GPT-4o | Nuance e tom |
| Fechamento (`critical: true`) | Claude Sonnet 4.6 + critic pass | Crítico, vale gastar |
| Classifier de objeção | Claude Haiku 4.5 | Many calls, precisa ser barato |

### Otimizações de custo

- **Prompt caching** (Anthropic) — system prompt + KB caching = -50% a -90% no custo
- **Modelo por Node** (Haiku onde dá)
- **Limite por tenant** (`max_usd_per_day`) com alerta

---

## 13. Observabilidade

### 13.1 Logs estruturados

Todo log em JSON. Campos mandatórios:
- `timestamp`, `level`, `tenant_id`, `talkflow_id`, `node_id`, `event_type`
- `lead_id` (quando aplicável), `objection_type` (quando aplicável)

Eventos principais:
- `message.received`, `message.sent`, `message.audio_transcribed`
- `node.entered`, `node.exited`, `node.transition`
- `llm.called` (com `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`)
- `objection.detected`, `objection.treated`
- `crm.synced`, `crm.webhook_received`
- `handoff.triggered`, `optout.triggered`
- `guardrail.blocked` (com `reason`)
- `error.*`

### 13.2 Métricas

Expostas via Prometheus endpoint (`/metrics`):

- `talkflows_active` (gauge, por tenant)
- `node_transitions_total` (counter, por tenant+from+to)
- `node_drop_off_rate` (derivado)
- `llm_cost_usd_total` (counter, por tenant+model)
- `llm_latency_seconds` (histogram)
- `crm_sync_errors_total` (counter, por tenant+adapter)
- `messages_received_total`, `messages_sent_total`
- `conversion_rate` (qualified / total, por tenant+treeflow)

### 13.3 Tracing

OpenTelemetry com export pra Grafana Tempo ou Jaeger (escolher 1 no MVP). Trace cobre: webhook → engine → LLM → adapter → resposta. Permite debug end-to-end de uma conversa específica.

### 13.4 Dashboards

Grafana (rodando na VPS) com dashboards default:
- **Overview**: conversas ativas, mensagens/min, custo LLM/dia
- **Por tenant**: conversão por TreeFlow, drop-off por Node, custo
- **Errors**: rate de erros de CRM/Messaging, guardrails bloqueados

---

## 14. Secrets management

**Stack: SOPS + age + Git**

- Cada `tenants/<tenant>/secrets.enc.yaml` é criptografado com chave `age`
- Devs têm chave privada em `~/.config/sops/age/keys.txt`
- VPS prod tem chave em `/etc/sops/age/keys.txt` (root-only)
- CI tem chave como secret no GitHub Actions
- Rotação: gera nova chave, re-criptografa, distribui

**Sem servidor extra, sem vendor-lock.** Migração futura pra Infisical/Vault sem mudar interface.

---

## 15. LGPD

**Fora do MVP por decisão do usuário.** A ser implementado em fase posterior:
- Política de retenção configurável
- Endpoint de "esquecimento" (`DELETE /tenants/{id}/leads/{lead_id}`)
- Disclaimer "você está conversando com IA" no início

---

## 16. Modo simulação (testes)

Comando CLI:

```bash
python -m ai_sdr simulate --tenant joana-mentora --treeflow mentoria
```

- Roda TreeFlow em terminal interativo
- LLM real, mas não conecta WhatsApp nem CRM
- Você digita as respostas "como se fosse o lead"
- Exibe transições, objections detectadas, campos extraídos
- Permite testar TreeFlow novo antes de publicar

---

## 17. Stack técnico

| Camada | Tech | Por que |
|---|---|---|
| Linguagem | Python 3.12 | Ecossistema LangChain/LangGraph |
| Orquestração de agente | LangGraph | Grafos de estado nativos, checkpoint, interrupt |
| Componentes LLM | LangChain | Abstração multi-provider |
| API HTTP | FastAPI | Async, performance, typing |
| Workers | arq (Redis-based) | Leve, async, sem ZooKeeper |
| Banco | PostgreSQL 16 + pgvector | Estrutura + RAG no mesmo banco |
| Cache/fila | Redis 7 | Idempotency, debounce buffer, queue |
| Validation | Pydantic v2 | Schema + structured output do LLM |
| Migrations | Alembic | Padrão SQLAlchemy |
| Logs | structlog | JSON estruturado nativo |
| Métricas | prometheus-client | Export `/metrics` |
| Tracing | OpenTelemetry | Padrão da indústria |
| Secrets | SOPS + age | Criptografia em repo, sem vendor-lock |
| Containerização | Docker + Compose | Deploy na VPS |
| Reverse proxy | Caddy | HTTPS automático (Let's Encrypt) |
| CI/CD | GitHub Actions | Lint, test, deploy via SSH |

---

## 18. Estrutura do repositório

```
ai-sdr/
├── packages/
│   ├── core/
│   │   ├── engine/              # LangGraph runtime
│   │   ├── treeflow/            # Loader, schema, compiler
│   │   ├── extractor/           # Structured output
│   │   ├── guardrails/          # Whitelist, critic
│   │   ├── classifier/          # Objection detection
│   │   ├── retriever/           # RAG via pgvector
│   │   └── scheduler/           # Follow-up worker
│   ├── adapters/
│   │   ├── crm/
│   │   │   ├── base.py          # CRMAdapter interface
│   │   │   ├── rd_station.py
│   │   │   └── pipedrive.py     # placeholder pra futuro
│   │   └── messaging/
│   │       ├── base.py          # MessagingAdapter interface
│   │       └── whatsapp_cloud.py
│   ├── media/
│   │   ├── whisper.py           # STT
│   │   ├── vision.py            # imagem → texto
│   │   └── elevenlabs.py        # TTS
│   ├── api/
│   │   ├── webhooks/            # /webhook/crm/*, /webhook/messaging/*
│   │   ├── admin/               # endpoints internos (V2 terá UI)
│   │   └── health.py
│   ├── workers/
│   │   ├── follow_up.py
│   │   ├── reconciliation.py    # sync CRM 15min
│   │   └── kb_indexer.py        # reindex KB quando arquivos mudam
│   └── observability/
│       ├── logging.py
│       ├── metrics.py
│       └── tracing.py
├── tenants/
│   └── joana-mentora/
│       ├── tenant.yaml
│       ├── secrets.enc.yaml
│       └── treeflows/
│           ├── mentoria.yaml
│           └── aceleradora.yaml
├── kb/
│   └── joana-mentora/
│       ├── kb_qualificacao_mentoria/
│       │   └── *.md
│       ├── kb_obj_preco/
│       │   └── *.md
│       └── ...
├── migrations/                  # Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   └── treeflow_simulations/    # cenários de TreeFlow rodando
├── scripts/
│   ├── simulate.py
│   └── reindex_kb.py
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-05-21-ai-sdr-design.md   # este arquivo
├── docker-compose.yml
├── Dockerfile
├── Caddyfile
├── pyproject.toml
├── .sops.yaml                   # config SOPS
├── .gitignore
├── CLAUDE.md                    # instruções pro Claude Code
└── README.md
```

---

## 19. Schema de banco (visão de alto nível)

Tabelas principais (todas com `tenant_id` + RLS):

| Tabela | Conteúdo |
|---|---|
| `tenants` | Cadastro de clientes |
| `treeflow_versions` | Snapshot imutável de cada versão publicada |
| `talkflows` | Conversa em andamento (1 por lead) |
| `talkflow_state` | State JSON (checkpoint do LangGraph) — gerenciado pelo LangGraph |
| `messages` | Histórico WhatsApp (in/out, com source) |
| `events` | Log de eventos de domínio (objection.detected, node.transition, etc.) |
| `crm_sync_log` | Cada chamada SDR → CRM (idempotency + audit) |
| `kb_documents` | Documentos da KB |
| `kb_chunks` | Chunks indexados (vetor pgvector) |
| `llm_usage` | Custo/token por call (telemetria de custo) |
| `follow_up_jobs` | Jobs de follow-up agendados |

**RLS (Row-Level Security):**
```sql
ALTER TABLE talkflows ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON talkflows
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

Conexão de cada request seta `SET app.current_tenant = '<id>'` no início — Postgres garante isolamento.

---

## 20. Deploy

**VPS: `vps-nova` (Hostinger KVM4, 4 vCPU / 16GB RAM / 200GB NVMe).**

Compose:
```
services:
  postgres:    # pg16 + pgvector
  redis:       # cache + queue
  api:         # FastAPI
  workers:     # arq workers (follow_up, reconciliation, kb_indexer)
  caddy:       # reverse proxy + HTTPS
  prometheus:  # métricas
  grafana:     # dashboards
```

**CI/CD:**
- PR → roda lint + tests + valida YAMLs (schema Pydantic)
- Merge na `main` → GitHub Actions builda imagem Docker → push pra registry → SSH na VPS → `docker compose pull && up -d`

**Branches:**
- `main` — produção (protegida, só merge via PR)
- `dev/nicolas` — branch do Nicolas
- `dev/<amigo>` — branch do parceiro
- `feature/<nome>` — features

---

## 21. Roadmap

### MVP (esta spec)
- Tudo descrito acima até seção 20
- 1 tenant em produção (Joana Mentora)
- 2 TreeFlows (Mentoria, Aceleradora) + downsell
- RDStationAdapter + WhatsAppCloudAPIAdapter
- Áudio bidirecional (Whisper IN, ElevenLabs OUT)
- Vision pra imagem
- Modo simulação
- Observabilidade total

### V2 (próximos passos)
- UI admin web (editar TreeFlows visualmente)
- Auto-criação de custom fields no CRM
- Migration rules declarativas (mapping fino)
- Multi-idioma
- **A/B test de TreeFlows** — rodar variantes simultâneas do mesmo funil (nós diferentes, prompts diferentes, ordens diferentes), distribuir % dos leads entre variantes, comparar métricas de conversão por variante. Precisa: variant assignment determinístico por lead, métricas segmentadas por variant_id, UI pra comparar resultados.
- Compliance LGPD completo
- Billing
- Mais CRM adapters (Pipedrive, HubSpot, Kommo)
- Mais canais (Instagram DM, Email)
- Reengajamento via ML (timing ótimo)
- White-label

### Fora de escopo (provavelmente nunca)
- Outbound (este produto é inbound puro)
- Cold messaging
- Sistema de pagamento próprio

---

## 22. Glossário

- **SDR** — Sales Development Representative (qualificador de leads)
- **TreeFlow** — Definição estática de funil de conversa
- **TalkFlow** — Instância viva de uma conversa
- **Node** — Estágio dentro de um TreeFlow
- **KB** — Knowledge Base (base de conhecimento RAG)
- **RLS** — Row-Level Security (PostgreSQL)
- **Adapter** — Implementação concreta de uma interface (CRM ou Messaging)
- **Handoff** — Passagem da conversa pro vendedor humano
- **Critic pass** — Segundo LLM que revisa a resposta antes de enviar
- **SOPS** — Secrets OPerationS (Mozilla, criptografia de arquivos)

---

## 23. Decisões abertas (a refinar na fase de plano)

- Estratégia exata de chunking da KB (semântico vs fixo)
- Tracing backend: Grafana Tempo (gratuito, self-host) vs Honeycomb (SaaS)
- Notificação de handoff: Slack? WhatsApp interno? Email? (precisa input do tenant piloto)
- Lib de structured output: Instructor vs Pydantic puro com LangChain `with_structured_output`

---

**Fim do spec.**
