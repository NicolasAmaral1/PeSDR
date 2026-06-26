# Chat Frontend — Operator Inbox (v1) Design

> **Status:** design em refinamento no brainstorming (rev. 2 — 2026-06-24, modelo **contact-based** + instância + dados/invariantes pós red-team). Aguardando review da spec.
> **Branch:** `dev/nicolas-chat-frontend` (off `main`).
> **Escopo:** inbox estilo WhatsApp pra operadores **verem, assumirem e responderem** conversas — a WhatsApp Cloud API oficial não fornece frontend.

## 1. Motivação

A WhatsApp Cloud API (Meta) não tem inbox/UI. As conversas vivem no Postgres, conduzidas pela IA; o operador não tem onde ver, assumir nem responder. Isso também deixa o gap de escalação (`requires_review`) e o de leads novos (`pending_assignment`) **invisíveis**. Este projeto entrega o inbox do operador e, de quebra, destrava o HITL.

## 2. Decisões batidas (log)

| # | Decisão |
|---|---|
| D1 | **Stack B:** SPA React/TS (Vite + Tailwind + shadcn/ui + `@chatscope` + TanStack Query), servida pelo FastAPI (mesma imagem, StaticFiles). Não forkar (Chatwoot é acoplado; Tercela/Erxes AGPL). |
| D2 | **Escopo v1 interativo:** ver + **assumir** + **responder** (HITL real). |
| D3 | **Realtime:** WebSocket + Redis pub/sub. |
| D4 | **Auth:** reusa o cookie de sessão do console (`web/auth.py` + `user_tenant_access`); RLS via `set_tenant_context`. Sem auth server novo. |
| D5 | **Marca Avelum Labs** (avelumia.com): `#0057FF` primary, `#0038FF` action, `#44CDCE` teal; gradiente `linear-gradient(52deg,#0057FF,#1A63F7,#F9C5B4)`; fontes **Syne** + **Madefor Text**. Aplicação **leve**, cara de WhatsApp Web. (NÃO confundir com Genesis Laudos: lime #9FEC14/Sora.) |
| **D6** | **Instância = (tenant + número/`channel_label`).** É o escopo do inbox e a chave do canal WS. **Funil ≠ parte da instância** — é **filtro ortogonal** (`Talk.treeflow_id`), com opções vindas dos funis do tenant. |
| **D7** | **Inbox contact-based** (não talk-based). Lista = **um row por Contato (Lead)** dentro da instância. Conversa = **fluxo contínuo de todas as mensagens** com o contato (igual WhatsApp). **Talks = delimitadores inline** no fluxo (a unidade de funil/status/HITL, renderizada como marcador de sessão). |
| **D8** | **Lista ancorada no Lead.** Contato **sem Talk renderiza** (estado de 1ª classe): `pending_assignment`/novo = "aguardando"; histórico todo encerrado = "encerrada". |
| D9 | **Escopo de tenant:** v1 = single-tenant-context por sessão (troca de instância dentro do tenant; cross-tenant só `platform_admin`, depois). |
| D10 | **Hierarquia/time = fase 2.** v1 = visão conjunta por tenant (acesso+RLS já são por-tenant) + já criar `assigned_operator_id`. |

## 3. Modelo de domínio → UI (contact-based)

Hierarquia: **Tenant → Instância (número) → Contato (Lead) → mensagens (com Talks como delimitadores).**

| Conceito | UI |
|---|---|
| **Instância** = (tenant + número) | seletor no topo da lista (dropdown); escopo + chave do WS `inst:{instance_id}` |
| **Contato** = Lead (whatsapp_e164, profile) | **row da lista** + a conversa aberta |
| **mensagens** (inbound/outbound) | **fluxo contínuo** por `lead_id`, ordenado por tempo |
| **Talk** (status, handling_mode, treeflow) | **delimitador inline** (`— nova conversa · Funil X · data —` … `— encerrada: <desfecho> —`); unidade de funil/status/HITL/takeover |
| **Funil** (`treeflow_id`) | **filtro** (contatos cujo Talk **ativo** está no funil X) |
| **operador** (`user_tenant_access`) | logado / "assigned to" |

### 3.1 Estado do Contato (o badge da lista)

Derivado do **Talk ativo** do contato, se houver:
- 🟢 **IA ativa** — Talk ativo, `handling_mode=ai`
- 🟠 **Precisa de revisão** — Talk ativo, `requires_review`
- 🔵 **Humano** — Talk ativo, `handling_mode=human`
- 🆕 **Aguardando** — **sem Talk** (novo / `pending_assignment`) ou Talk ainda não criado pelo primeiro turno
- ⚪ **Encerrada** — sem Talk ativo, mas com histórico (mostra o desfecho do último Talk)

Filtro `pending_assignment`/novo é um filtro visível (operador vê leads chegando antes da IA pegar).

### 3.2 Bolhas e delimitadores

- Lado do agente distingue **IA vs Você (operador)** via `outbound_messages.triggered_by` (`inbound`=IA, `operator`=HITL).
- **Talks são separadores** desenhados sobre o fluxo, a partir das faixas de tempo dos Talks (`created_at`→`closed_at`). Talks não se sobrepõem (um ativo por vez), então os limites são inequívocos sem precisar de `talk_id` por mensagem.
- Áudio (FE-05): bolha com play + transcrição; transcrição com `confidence < 0.7` (ou nula) ganha selo "confiança baixa".

## 4. Arquitetura

```
┌──────────── Browser (operador) ────────────┐
│ SPA React/TS · Tailwind+shadcn · @chatscope │
│ TanStack Query · cliente WS                 │
└─────────┬───────────────────────┬───────────┘
  REST/JSON│  cookie de sessão     │ WS (ao vivo)
┌──────────▼───────────────────────▼───────────┐
│ FastAPI (mesmo app)                           │
│  /api/console/*   ·   /ws/instances/{id}      │
│  StaticFiles → build da SPA                   │
│  set_tenant_context (RLS) em toda request     │
└──────────┬───────────────────────┬───────────┘
   Postgres (RLS)                   │ Redis pub/sub
┌──────────────────────────────────▼───────────┐
│ Webhook Meta + worker arq → ao persistir      │
│  inbound/outbound/status, PUBLICA em Redis     │
│  `inst:{instance_id}` (com seq) → hub WS empurra│
└────────────────────────────────────────────────┘
```

- Deploy: `vite build` na mesma imagem Docker. Um serviço só.
- Único componente novo no backend: **hub WS + `publish`** nos pontos de persistência.

## 5. Layout / UX (contact-based, aprovado nos mockups)

3 painéis + sidebar colapsável:
- **Coluna 1 — Contatos:** no topo o **seletor de instância** (dropdown escuro c/ logo + wordmark Syne; abre as instâncias do(s) tenant(s) do operador). Abaixo: busca, **filtros** (Todas / 🆕 Aguardando / 🟢 IA / 🟠 Revisão / 🔵 Humano), e **filtro de funil** separado. Cada row: avatar, nome, telefone, **prévia da última mensagem**, hora, **badge de estado** + **tag de etapa do funil do Talk ativo** + contador de **não-lidas (por contato)**.
- **Coluna 2 — Conversa:** header (contato, estado, **Assumir / Devolver pra IA**), **fluxo contínuo** com **delimitadores de Talk**, bolhas IA/Você, áudio com play+transcrição, e **composer** com indicador da **janela de 24h**.
- **Coluna 3 — Sidebar:** contato, etapa do funil (Talk ativo), **contexto da IA** (intenção/última razão), ações (devolver / resolver / reatribuir).

**Marca (leve):** fio de gradiente Avelum; seletor escuro Syne; accent **azul `#0057FF`** na seleção/header/badge/send, **teal `#44CDCE`** na etapa do funil. Resto = WhatsApp Web. **Pendência:** logo Avelum real (SVG) — hoje placeholder.

## 6. Modelo de dados & invariantes (pós red-team)

Quase tudo **aditivo** (colunas/tabelas/índices). O contact-based **rebaixou o `talk_id` de bloqueador a opcional**.

### 6.1 Tabelas / colunas novas
- **`instances`** (materializa D6): `(id, tenant_id, channel_label, phone_e164, display_name, created_at)`, `UNIQUE(tenant_id, channel_label)`. WS e escopo chaveiam em `instance_id`.
- **Resumo de contato denormalizado no `leads`** (pro list, ancorado no Lead, independe de Talk): `last_message_at`, `last_message_preview`, `active_talk_id` (FK nullable), e cache do estado/funil do Talk ativo. Escrito pelo worker e pelo send do operador.
- **`operator_read_markers`** `(user_id, lead_id, last_read_at, last_read_message_id)` PK `(user_id, lead_id)` — **read-state POR CONTATO** (não por Talk).
- **`Talk.assigned_operator_id`** (FK→users, nullable) + semântica `claimed_by` pro takeover atômico.
- **`outbound_messages`:** adicionar `'operator'` ao CHECK `triggered_by`; `client_message_id` (UUID do browser, idempotência); `delivery_status` (sent/delivered/read/failed) + `delivered_at` + `read_at`.
- **`whatsapp_templates`** `(tenant_id, name, language, status, last_synced_at)` — registry pro picker fora da janela 24h.
- **`talk_id` em inbound/outbound_messages = OPCIONAL** (útil pra atribuição fina/analytics; **não bloqueia** — a timeline é por `lead_id` e os delimitadores vêm das faixas de tempo dos Talks). Se/quando o FE-02 adicionar, melhora a precisão de borda.

### 6.2 Índices
- `leads (tenant_id, instance/channel, last_message_at DESC)` — lista de contatos.
- `inbound_messages (lead_id, received_at DESC)` + `outbound_messages (lead_id, sent_at DESC)` — fluxo da conversa.
- `talks (tenant_id, lead_id, created_at)` — delimitadores.

## 7. Máquina HITL & invariantes de concorrência

- **Takeover = check-and-set atômico:** `UPDATE talks SET handling_mode='human', assigned_operator_id=:op WHERE id=:active_talk AND handling_mode='ai' RETURNING id`; 0 linhas → **409**.
- **`run_turn` re-lê `handling_mode`** depois de pegar o advisory lock e **antes de enviar**; se `human`, aborta (não responde por cima do operador).
- **`POST send` exige `handling_mode=human`** (ou faz takeover-then-send atômico). Send em `ai` sem assumir → 409.
- **Send do operador:** `adapter.send_text` direto → grava `outbound` `triggered_by="operator"` + `client_message_id` → publica. Não roda IA.
- **Opt-out roda mesmo em `human`:** pré-check de keyword no inbound em modo humano → fecha `closed_optout` + avisa o operador (risco LGPD).
- **`scan_talks` pula `handling_mode != 'ai'`** (não auto-fecha conversa sob operador) e **publica `talk.updated`** ao fechar.
- **Todo mudança de Talk (status/handling_mode/close) publica `talk.updated`.**
- **Send quando não há Talk ativo** (contato `pending_assignment`/frio): **abre um Talk em `handling_mode=human`** e envia (dentro da janela 24h ou via template).

## 8. Contrato realtime (WS)

- Canal Redis `inst:{instance_id}`; cada evento carrega **`seq` monotônico** (Redis `INCR` por instância).
- **Envelope:** `{ seq, type, instance_id, lead_id, payload }`. Tipos: `message.created`, `message.status_updated` (entrega/leitura), `talk.updated` (status/handling_mode/etapa/close), `contact.updated` (resumo/unread), `talk.window_expired` (opcional; cliente também faz countdown local).
- **Reconexão sem buraco:** cliente manda `{type:"subscribe", instance_id, last_seq}`; servidor faz **catch-up** (relê dos message-tables desde `last_seq`) e entra em modo live; cliente **deduplica por `seq`**.
- **Backpressure:** fila por-cliente no hub (`maxsize`), overflow → drop + `{type:"overflow"}` → cliente refaz fetch. `PUBLISH` fire-and-forget no webhook.

## 9. WhatsApp honesto (não mentir pro operador)

- **Entrega/leitura (✓✓):** processar o array **`statuses`** do webhook da Meta (hoje `handle_inbound` descarta) → atualizar `delivery_status` → `message.status_updated`. Inclui falha por número bloqueado (131026).
- **Janela 24h:** `window_expires_at` = `max(inbound.received_at)+24h`, no payload do contato; countdown no cliente; fora da janela → bloqueia texto livre e abre **picker de template** (do `whatsapp_templates`); send expirado → 422 com mensagem clara.
- **Tipos não suportados** (localização, contato, etc.): ingest fallback grava `media_type="unsupported"` + `raw` + bolha "mensagem não suportada (tipo: X)". Nunca sumir silenciosamente.
- **Race do audit-lost:** persistir `outbound` `status="pending_send"` ANTES da chamada Meta; atualizar pra `sent`/`failed` depois; dedup por `client_message_id` no retry.

## 10. Auth, RLS & escopo

- Reusa cookie de sessão (`user_tenant_access`). Toda `/api/console/*` e o WS sob `set_tenant_context`.
- v1 **single-tenant-context**: troca de instância dentro do tenant; **cross-tenant** (ver instâncias de vários tenants juntas) só `platform_admin`, depois.
- Capability `can_send` (observador vs operador). v1 simples; gancho modelado.

## 11. Superfície de API + componentes

```
GET  /api/console/instances                          (instâncias acessíveis)
GET  /api/console/instances/{id}/contacts?status=&funnel=&q=&cursor=
GET  /api/console/contacts/{lead_id}                 (contato + Talk ativo + contexto IA + janela)
GET  /api/console/contacts/{lead_id}/messages?before=<cursor>
POST /api/console/contacts/{lead_id}/send            { text, client_message_id } | { template_ref, params, client_message_id }
POST /api/console/contacts/{lead_id}/takeover · /release · /resolve
POST /api/console/contacts/{lead_id}/read            { last_read_message_id }
WS   /ws/instances/{id}                              (subscribe c/ last_seq)
```

**Componentes React:** AppShell · InstanceSelector · FunnelFilter · ContactList (filtros+busca) · ConversationView (Header · MessageStream c/ TalkDelimiters · Composer c/ gate 24h + template picker) · DetailsSidebar (contato · funil · contexto IA · ações) · `useWebSocket` (seq/catch-up) · apiClient + hooks TanStack.

**Tokens de marca:** `--accent:#0057FF; --accent-action:#0038FF; --teal:#44CDCE; --brand-gradient:linear-gradient(52deg,#0057FF,#1A63F7,#F9C5B4); --font-display:'Syne'; --font-body:'Madefor Text',system-ui;` (chat mantém paleta WhatsApp Web).

## 12. Testes

- **Front:** ContactList (incl. estado "sem Talk"), Composer + gate 24h + template picker, delimitadores de Talk, badge de estado, reconexão WS (seq/dedup), envio otimista + falha.
- **Back:** endpoints (send/takeover/release respeitam RLS + gate `handling_mode` + janela + `client_message_id` idempotente); gate do worker (`human` → sem `run_turn`); opt-out em human; `scan_talks` pula human; processamento de `statuses` (delivery); publish WS com seq.

## 13. Não-objetivos (v1)

Kanban do funil (só a tag) · dashboards de analytics · ações em massa · self-service de tenant · app mobile nativo (responsivo, desktop-first) · edição de treeflow pela UI · cross-tenant inbox p/ operador comum · hierarquia/time.

## 14. Decisões em aberto / defaults aplicados (vete se discordar)

- **Filtro de funil** = pelo Talk **ativo** do contato *(default)*; alternativa "qualquer Talk no funil" não recomendada.
- **`talk_id` por mensagem** = não implementar agora (opcional) *(default)* — delimitadores via faixas de tempo.
- **Templates:** v1 com registry simples + picker; **sync automático** com a Meta = fase 2 *(default)*.
- **Logo Avelum / fonte Madefor Text:** pendência de asset/licença (placeholder até lá).
- **Contexto da IA na sidebar:** exige persistir `TurnDecision.reasoning`/intenção (hoje não-gravado) — campo no `outbound` ou linha de turn; pode pedir migration.

## 15. Paredes semânticas (decisões de produto)

- **C1 — mesmo lead em 2 instâncias:** no contact-based **dissolvido** — cada número é sua própria caixa de contato (à la WhatsApp Business); o contato é `(instância, lead)`. (A `UniqueConstraint(tenant,lead)` do TalkFlow segue; revisar só se um lead precisar de 2 Talks ATIVOS simultâneos em números diferentes — adiado.)
- **C2 — trocar funil no meio:** Talk é preso à versão do treeflow → **"fechar-e-reabrir"** (novo Talk = novo delimitador). Não é ação do v1.
