# Handoff → Pedro (2026-06-28)

Escopo, pendências e pontos técnicos pro Pedro tocar a parte dele do PeSDR. Base: o diagnóstico completo de 2026-06-27 (5 frentes) + a divisão de trabalho combinada.

## TL;DR — o que é seu, o que está pronto

- **Seu:** Fase **A** (funil real da Manoela + KB no v2 + smoke ao vivo) · **B3/2B-ii** (delivery ✓✓ + templates WhatsApp) · **C** (deploy + HTTPS + Meta + segurança operacional).
- **Nosso (FEITO):** a **inbox do operador** — backend de leitura + HITL + realtime (Planos 1/2A/2B-i, PR #27) **e** a SPA React (3A read-only + **3B interativo**: o operador já assume + responde). Você constrói por cima disso.
- **1º tenant = Manoela** (já tem credenciais WhatsApp reais em sops + `console.enabled`).

---

## Fase A — fazer a IA rodar de verdade (sua prioridade)

Nada disso nunca rodou ao vivo. A engenharia do `run_turn` está completa e limpa; falta **conteúdo** e **prova**.

### A1 — Autorar o funil real da Manoela (treeflow v2)
- **Não existe funil real.** Os "tenants reais" são stubs de 2 nós:
  - `tenants/manoela-mentora/treeflows/qualificacao_inicial.yaml` — header diz literalmente **"STUB DE VALIDAÇÃO v2"**.
  - `tenants/avelum/treeflows/avelum_sdr.yaml` — 2 nós, sem objeções/KB.
  - ⚠️ `tenants/example/treeflows/example.yaml` é rico (9 nós) **mas é schema v1 velho** (`prompt:`/`kb:`/`critical:`) — **não carrega** no `load_treeflow_v2` (que exige `sdr_persona` + por nó `objetivo`/`bridge_instruction`). Não dá pra reusar direto.
- **DoD:** um treeflow v2 completo da Manoela (persona + nós + objeções + refs de KB + lifecycle) que passa em `load_treeflow_v2` e está publicado como `TreeflowVersion`.
- Loader/validador: `src/ai_sdr/flowengine/treeflow_loader.py`. Schema/exemplos de validação: `tests/fixtures/*.yaml`.

### A2 — Ligar o KB no v2
- **Gap:** `run_turn`/`system_prompt` **nunca** recuperam nem injetam KB. A objeção do treeflow tem `tool_payload.kb_ref` que é **parseado mas não usado** no turno. O design `docs/superpowers/specs/2026-05-23-kb-and-guardrails-design.md` (+ plano) é da **arquitetura v1 (critic)**, não do v2.
- **Por que importa:** funil que fala de preço/produto **vai alucinar** sem KB.
- Arquivos: `src/ai_sdr/flowengine/pipeline.py` (`run_turn`), `src/ai_sdr/flowengine/system_prompt.py`. Precisa de plano novo (o v1 não serve).

### A3 — Smoke ao vivo (AI-only)
- **`run_turn` nunca fez 1 chamada real à Anthropic + envio real ao WhatsApp.** Todo teste "e2e"/"smoke" mocka o LLM (`_StubLLM`/`AsyncMock`). Os `*_live.py` batem no LLM real mas miram o **runtime v1 LangGraph**, não o `run_turn`. O único smoke ao vivo verde nesta branch (`.superpowers/sdd/smoke-realtime-report.md`) prova só a camada **realtime/WS**.
- **DoD:** 1 lead real → número da Manoela → `run_turn` (Anthropic real, structured output) → resposta real no WhatsApp. **Depende de C (deploy+HTTPS+Meta).** Pode ser supervisionado pela inbox read-only que já existe.

---

## B3 / 2B-ii — WhatsApp honesto (seu, casa com sua Meta)

- **Delivery ✓✓:** o array **`statuses`** do webhook da Meta é **descartado hoje** (`webhooks.py` só itera `messages`). Processar → atualizar `delivery_status` no outbound → emitir evento WS `message.status_updated`. Inclui falha por número bloqueado (131026/131047).
- **Templates (janela 24h fechada):** registry `whatsapp_templates` + picker. Hoje o send fora da janela retorna **422** sem saída. O adapter já levanta `WindowExpiredError` e o worker tem um `reengagement_template` (HSM) comentado no tenant.yaml da Manoela.
- **Fronteira com a inbox (nossa):** a UI já trata janela fechada como composer desabilitado + aviso "template em breve"; quando você entregar o registry/✓✓, a gente estende o front (B2/3C consome o `message.status_updated`).
- Arquivos: `src/ai_sdr/api/routes/webhooks.py`, `src/ai_sdr/messaging/ingest.py`, `src/ai_sdr/messaging/whatsapp_cloud.py`, `src/ai_sdr/worker/jobs/inbound.py`.

---

## C — Deploy + HTTPS + Meta + segurança (seu)

Hoje roda no **laptop + tunnel SSH** pro Postgres/Redis da VPS. Pra ir ao vivo:
1. **Deploy real** na VPS: API + worker arq + Postgres + Redis co-residentes (não laptop+tunnel), atrás de **Traefik com HTTPS** num domínio estável (ex.: `sdr.luminai.ia.br`). **Chave age no host** pro `sops` decriptar em runtime.
2. **Meta** pra Manoela: webhook inscrito no Business Manager, verify token, número validado, **1 envio real testado** (graph.facebook.com 200 — nunca foi feito).
3. **Segurança operacional MVP:** teto de custo por tenant (gasto LLM ilimitado hoje), rate-limit no webhook, backup do banco. *CI opcional mas recomendado (não existe `.github/workflows`).*
- Mecanismo pronto: inbound parser + verificação HMAC (constant-time), adapter de envio real com retry + taxonomia de erro Meta, sops por tenant. Roadmap: `docs/superpowers/roadmap/2026-05-26-production-readiness.md` (Ondas 0/1/2).
- **LGPD:** parada por decisão do Nicolas. É **gate antes de leads reais em volume** (não pra um smoke controlado). Núcleo: base legal, endpoint de delete, retenção/purga, aviso de privacidade, base do art. 33 p/ transferência (Anthropic/Meta nos EUA). **Sem aviso de "é IA"** (decisão do Nicolas).

---

## ⚠️ Colisão de migration (resolver no merge)

- **Nós (#27):** chain `0032_instances` → `0033` → `0034` → `0035` → `0036_outbound_operator_send`, saindo de `0031_add_voice_synthesis_failed_reason`.
- **Você (#26):** `0032_sandbox_flags`, também com `down_revision = "0031_add_voice_synthesis_failed_reason"`.
- **Sintoma:** revisions diferentes, mas ambos saem do `0031` → **dois heads** (alembic `upgrade head` falha com "multiple heads"). Não é duplicata de id.
- **Fix (limpo):** mergear **#27 primeiro**; depois, no #26, mudar o `down_revision` do seu `0032_sandbox_flags.py` de `"0031_add_voice_synthesis_failed_reason"` → **`"0036_outbound_operator_send"`** (renomear o arquivo p/ `0037_sandbox_flags.py` é cosmético, mas ajuda a ordenar). Isso lineariza: `…0031 → 0032..0036 (nosso) → sandbox (seu)`.

---

## PRs abertos — status + ordem sugerida (decisão do Nicolas)

Todos **MERGEABLE** (sem conflito de código).

1. **#27** (nosso, chat inbox) — pronto, 4 reviews opus internos aprovados. **Mergear primeiro** (vira base + claim das migrations 0032-0036).
2. **#24** (design do sandbox como extensão do Console) — seu review do Nicolas já aplicado; mergeável.
3. **#22** (CRM proxy — Respondi→RD Station→webhook) — docs; **supera** o design do #20 (já mergeado).
4. **#25** (spec TreeFlow inteligente) — docs; aguarda review do Nicolas.
5. **#26** (impl do sandbox MVP) — **depois** de (1) + renumerar a migration; aguarda decisão do Nicolas sobre o **atalho do `run_turn` MVP** (o sandbox usa um caminho LLM simplificado, não o `run_turn` completo).
- Órfão: `dev/pedro` tem 1 commit de notas (RD Station setup) **sem PR** — abre um PR ou perde.

---

## Ver a inbox rodando (o que já está pronto)

Demo local contra o banco da VPS (precisa do tunnel aberto):
```
# tunnel (cai a cada poucas horas):
pkill -f "ssh.*15432"; ssh -fN -L 15432:localhost:15432 -L 16379:localhost:16379 vps-nova
# servidor (serve a SPA buildada em /inbox):
cd PeSDR && uv run uvicorn ai_sdr.main:app --port 8099
```
- Login: `http://localhost:8099/console/login` → **operador / demo1234** (tenant `demo-inbox`).
- Inbox: `http://localhost:8099/inbox/` — 4 contatos (IA / Revisão / Humano / Aguardando-sem-Talk), conversa com delimitadores de Talk, **Assumir + responder funcionam** (3B). Seed: `seed_demo_inbox.py`.
- "Internal Server Error" = tunnel caiu (rode o comando do tunnel de novo).

## O que a inbox já entrega (você constrói por cima)
- **Leitura:** rotas `/api/console/tenants/{slug}/...` (instances, contacts c/ filtros, contact-detail, messages, talks, read) + `/api/console/me`.
- **HITL:** takeover / release / send (idempotente por `client_message_id`, exige `human`, 24h→422, lock de supressão da IA).
- **Realtime:** WS `/ws/instances/{id}` + Redis pub/sub; eventos `message.created`/`talk.updated`/`contact.updated` (o `message.status_updated` é seu, no 2B-ii).
- **Front:** SPA Vite+React em `frontend/`, build→`/inbox` (StaticFiles). Read-only (3A) + interativo (3B). Falta nosso **B2 (3C — WS client, updates ao vivo)**.
