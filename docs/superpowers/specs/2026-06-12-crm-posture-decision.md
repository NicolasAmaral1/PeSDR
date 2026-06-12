# ADR: CRM interno operacional + sync bidirecional via adapters

**Data:** 2026-06-12
**Status:** Aceita
**Tipo:** Architectural Decision Record (ADR) — postura arquitetural, não feature.
**Autor:** Nicolas Amaral (decisão com Claude)
**Evolui:** [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) (não substitui — estende a borda CRM)
**Informa:** Plano 6 (Identity Resolver), Plano 7 (CRM adapter), roadmap item D.2

---

## Contexto

PeSDR conversa com leads e coleta dados de qualificação, mas não tem conceito de **Deal**, **Contact** (separado de Lead) nem **Organization**. O ADR de 2026-05-24 estabeleceu "PeSDR não é CRM — empurra pro CRM do cliente via adapter".

Na prática, 5 cenários mostraram que a conversa precisa **enxergar** o mundo comercial (não apenas alimentá-lo):

1. **Multi-stakeholder** — dois sócios da mesma empresa conversando em números separados; sem org-awareness, a agente trata como estranhos e coleta dados conflitantes.
2. **Upsell / cliente que volta** — lead que comprou a Aceleradora retorna meses depois; o funil certo é upsell pra Mentoria, não qualificação do zero. (Caso de maior LTV do piloto Manoela.)
3. **"E aí, aquela proposta?"** — lead pergunta sobre deal em andamento que a agente desconhece.
4. **Follow-up por estágio comercial** — "proposta enviada + 7 dias de silêncio" é gatilho melhor que "N horas sem mensagem".
5. **Atribuição de ROI** — "esse funil gerou R$X" exige feedback de deal ganho/perdido.

Além disso, o ICP real (mentoras, infoprodutores, microempresas BR) **frequentemente não tem CRM nenhum** — um requisito de "conecte seu CRM" exclui exatamente o público mais fácil de vender.

**Posturas avaliadas:**

| Postura | Descrição | Veredito |
|---|---|---|
| A. PeSDR cego | Deal/Org só no CRM externo; push-only, nunca lê | Quebra os 5 cenários |
| B. Cache de leitura | CRM externo é dono; PeSDR cacheia snapshots | Resolve cenários, mas cache "não tem dignidade" (mente, não aceita escrita local) e não funciona pra cliente sem CRM |
| C. PeSDR vira CRM completo | Pipeline próprio competindo com Pipedrive | Gravidade de produto; afunda o foco SDR |
| **C-platform (escolhida)** | **CRM interno operacional** + sync bidirecional com o CRM do cliente via adapters | Abaixo |

---

## Decisão

**PeSDR terá um CRM interno operacional (system of record local) com sincronização bidirecional opcional pro CRM do cliente, mediada por adapters. O agente e o console HITL conversam SEMPRE e SOMENTE com o CRM interno; só o sync engine fala com CRMs externos.**

```
                    ┌─────────────────────────────────────────┐
                    │              PeSDR                       │
                    │                                          │
  Conversa ────────▶│  Agente ──── CRM INTERNO (nosso)        │
  WhatsApp          │  (lê/escreve   │  source of truth        │
                    │   SEMPRE aqui) │  operacional            │
                    │                │                         │
                    │           Sync Engine                    │
                    └────────────────┼─────────────────────────┘
                                     │ adapters (bidirecional)
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
                RDStation        HubSpot        (cliente sem CRM:
               (CRM cliente A) (CRM cliente B)   sync desligado)
```

Pattern de referência: **operational system of record + anti-corruption layer**. O CRM interno é a verdade *operacional* (do agente); o CRM do cliente é a verdade *comercial* (do humano de vendas); o sync engine negocia entre os dois com regras explícitas de resolução de conflito.

### Por que não a postura B pura (cache)

- Cache mente: agente afirma estado comercial desatualizado.
- Cache não aceita escrita local com garantias — toda escrita depende da idempotência do CRM externo (que varia por vendor).
- Cliente sem CRM fica de fora — não há o que cachear.

### Por que não a postura C completa (virar CRM)

- Gravidade de produto: kanban → relatórios → automações → e-mail marketing → empresa de CRM medíocre.
- Compete com a ferramenta que o cliente já usa e quebra a integração Vialum.

### O argumento da idempotência

Com o intermediário interno, idempotência vira responsabilidade **nossa e verificável**:

1. Escrita do agente → CRM interno com UNIQUE constraints (mesmo pattern de `action_executions` do FE-03c).
2. Sync engine propaga com `sync_state` por entidade — retry seguro, upsert por `external_ref`, nunca duplica.
3. CRM externo caiu → fila local segura; conversa nunca trava; propaga depois.
4. Resolução de conflito centralizada num lugar só, com regras declaradas.

Sem o intermediário, cada retry depende da semântica de idempotência de cada CRM externo (alguns nem têm upsert).

---

## Guard-rails (pra postura não escorregar pra C completa)

**O CRM interno serve o agente e o operador HITL — não o usuário de CRM.** Escopo fechado:

- ✅ Entidades: `Contact`, `Organization`, `Deal` — canônico mínimo.
- ✅ UI: o console HITL existente evolui pra exibir/editar essas entidades.
- ✅ Sync engine bidirecional via adapters.
- ❌ Kanban de pipeline, relatórios avançados, automações de CRM, e-mail marketing, permissões granulares de CRM. **Cliente que quer isso usa o CRM dele (e o sync entrega os dados lá).**

Pedidos de feature que cruzem essa linha → resposta padrão: "conecte seu CRM".

### Canônico mínimo (fechado)

```python
Deal:    {id (UUID nosso), lead_id, product, stage: open|won|lost,
          value, currency, opened_at, closed_at, external_refs: dict, raw: dict}
Contact: {id (UUID nosso), lead_id, name, emails[], phones[],
          organization_id?, external_refs: dict, raw: dict}
Organization: {id (UUID nosso), tenant_id, name, external_refs: dict, raw: dict}
```

`raw` é escape hatch pra payload do CRM externo. **Custom fields NÃO entram no canônico** — vivem em `raw` e no CRM do cliente.

### Resolução de conflito: por campo, com dono declarado

- Campos coletados pela conversa (faturamento, demo_data, qualificação) → **PeSDR ganha**.
- Campos comerciais (valor negociado, estágio do pipeline) → **CRM externo ganha**.
- Timestamps por campo (não por entidade); nunca last-write-wins global.
- Merge de duplicatas e deletes no CRM externo → eventos tratados explicitamente pelo sync engine (nunca cascade silencioso no interno).

---

## Decisões de não-bloqueio (valem JÁ, antes do CRM interno existir)

Estas 4 decisões custam ~zero agora e evitam refactor caro depois. **Vinculantes para o Plano 7 e qualquer código que toque dados comerciais:**

1. **IDs internos próprios desde o dia 1.** Toda entidade comercial tem UUID nosso como PK; IDs de CRMs externos são atributos (`external_refs: {"rdstation": "deal_789"}`), nunca a chave.

2. **O canônico nasce como modelo de domínio, não como cache.** Mesmo na fase em que só exista `Lead.crm_refs` JSONB, os shapes seguem o vocabulário canônico acima (`stage: open|won|lost`), não o vocabulário de nenhum vendor.

3. **Toda escrita comercial passa por camada interna.** Actions (FE-03c) não falam com CRM externo "por fora" — o dispatcher/pipeline interno é o único caminho. (Já é assim; manter a disciplina.)

4. **Audit trail de mudanças comerciais desde cedo.** Histórico append-only de "quem mudou o quê quando" (mesmo que JSONB simples) — pré-requisito barato pra reconciliação bidirecional futura.

---

## Sequência evolutiva

| Fase | Entrega | Cenários resolvidos | Esforço | Gatilho |
|---|---|---|---|---|
| **1. Write-only + refs** | `on_collected` cria contact/deal no CRM externo; `external_id` gravado em `Lead.crm_refs` | ROI parcial | ~1 semana | Plano 7 (quando CRM da Manoela for conhecido) |
| **2. Refresh on re-engagement** | Talk nova busca deals do contato 1x; injeta no system prompt | Upsell (cenário 2 — o mais valioso do piloto) | +3-4 dias | Junto da fase 1 |
| **3. CRM interno mínimo** | Tabelas contact/org/deal + console HITL exibe/edita + agente lê local; push-only pro externo | 1, 2, 3 completos | ~4-6 semanas | 2-3 clientes sem CRM próprio OU dor real de sync no piloto |
| **4. Sync bidirecional** | Webhooks + conflito por campo + reconciliação noturna (1º CRM) | 3, 4, 5 completos | +4-8 semanas | CRM interno em produção + cliente com CRM ativo |
| **5. Organizations** | Tabela + FK no Lead + org-awareness na conversa | 1 (multi-stakeholder) | +1 semana | Primeiro tenant B2B real |

Fases 1-2 são a "postura B" original — **B é o caminho evolutivo pra C-platform, não uma alternativa**. O cache da fase 2 é o embrião do CRM interno da fase 3.

---

## Riscos aceitos

| Risco | Mitigação |
|---|---|
| Sync bidirecional é um dos problemas mais difíceis de integração (conflitos, deletes, merges, ordering, custom fields) | Fase 4 só começa com receita entrando; conflito por campo com dono declarado; reconciliação noturna; merge/delete tratados como eventos explícitos |
| Identity matching errado → agente vaza dados comerciais do lead errado (incidente LGPD) | Matching conservador (telefone E164 exato + tenant); ambiguidade → segue sem contexto comercial (degradar > vazar); Plano 6 formaliza |
| Gravidade de produto (CRM interno cresce sem parar) | Guard-rails acima + canônico fechado + resposta padrão "conecte seu CRM" |
| Cache/interno defasado do externo entre syncs | `last_synced_at` sempre visível; agente instruído a hedging sobre estado comercial; refresh on re-engagement |

---

## Consequências

- **Plano 7 muda de escopo:** de "push pro RDStation" para "fases 1-2 da sequência acima, com as 4 decisões de não-bloqueio embutidas".
- **Plano 6 (Identity Resolver) ganha requisito explícito:** matching conservador com threshold; é pré-requisito da fase 2 em diante.
- **Console HITL (Plano 11) é o embrião da UI do CRM interno** — evolui, não se substitui.
- **Roadmap do Pedro:** itens C.1 (auto-CRM-fields) e D.2 (Plano 7) devem ser relidos à luz desta ADR.
- O ADR de 2026-05-24 permanece válido: as 3 bordas (Messaging/Identity/HITL) não mudam; esta ADR adiciona a **4ª borda (CRM)** com postura própria.
