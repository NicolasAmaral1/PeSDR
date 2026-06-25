# RD Station — Guia de configuração da Manoela (v2 — 2026-06-24)

**Data:** 2026-06-23 (criado) · 2026-06-24 (revisão crítica após Pedro testar)
**Status:** Guia operacional pra Pedro
**Contexto:** Lana assinou RD Station CRM (PRO). Esta é a lista exata pra destravar Fases A e B do CRM.

> ⚠️ **CORREÇÃO IMPORTANTE da v1:** o `pipeline_id`, `stage_id` e `custom_field_id` **NÃO aparecem na URL** do navegador (a URL é genérica `crm.rdstation.com/app/deals/pipeline`). A forma correta é via **API** (`curl`) com um **Token de Instância** que você gera no painel.

---

## 🎯 Resumo executivo: 6 ações + 3 descobertas críticas

| # | Ação | Tempo | Bloqueia |
|---|---|---|---|
| 1 | Gerar **Token de Instância** RD Station (uma vez) | 2 min | Tudo |
| 2 | Criar Pipeline + 3 stages na UI + pegar IDs via API | 15 min | Fase B (deals) |
| 3 | Criar Custom Fields na UI + pegar IDs via API | 15 min | Fase B (mapping) |
| 4 | Criar App OAuth (Marketplace Dev) | 15 min | Fase B (auth contínua) |
| 5 | Conectar Respondi → RD Station na UI do Respondi | 5 min | Fase A (entrada lead) |
| 6 | Configurar Webhook RD Station → PeSDR | 10 min | Fase A (gatilho Talk) |
| — | **+ 3 descobertas críticas** (durante config) | — | Ligar produção |

**Total:** ~1h de cliques. Meta/WhatsApp HSM **NÃO precisa de novo setup** (reusa o app que você já tem — só registra 1 template novo no Business Manager, ver §7).

---

## 📋 Bloco de notas — template pra anotar tudo

Copia pro Apple Notes (com lock), 1Password ou bloco seguro:

```
═══════════════════════════════════════════════════════
RD STATION — Manoela Mentora
Data da config: 2026-06-24
═══════════════════════════════════════════════════════

TOKEN DE INSTÂNCIA (passo 1)
  RD_TOKEN: _______________________________________

PIPELINE (passo 2)
  pipeline_id: ___________________________
  stage_id_open:  ___________________________  ("Lead Novo" / similar)
  stage_id_won:   ___________________________  ("Cliente Ganho")
  stage_id_lost:  ___________________________  ("Lead Perdido")

CUSTOM FIELDS (passo 3)
  cf_faturamento_mensal_id: _______________  (para deal)
  cf_origem_lead_id:         _______________  (para contact) ⭐ CRÍTICO
  cf_momento_profissional_id: _____________   (para contact, opcional)

APP OAUTH (passo 4)
  client_id:     _______________________________
  client_secret: _______________________________
  refresh_token: (vem depois via script Python)

INTEGRAÇÃO RESPONDI → RD STATION (passo 5)
  Conectado? Sim/Não
  cf_origem_lead será preenchido com: "respondi:mentoria_iconica" (passo 5.3)

WEBHOOK RD → PESDR (passo 6)
  HMAC ou URL secret? ___________________
  Token usado: ___________________
═══════════════════════════════════════════════════════
```

---

## Passo 1 — Gerar Token de Instância (2 min)

**Por que:** todas as calls de leitura (`curl`) precisam dele. É um token simples (não OAuth — esse vem no passo 4 só pra produção).

1. Logado no RD Station CRM, vai em: **Configurações → Marketplace → Tokens de instância** (ou similar; alguns layouts mostram em "API")
2. Clica em **Criar token de instância** (ou "Gerar")
3. Copia o token longo (algo tipo `abc123xyz...`)

🚨 **Anota no bloco de notas como `RD_TOKEN`.**

---

## Passo 2 — Pipeline + 3 stages (15 min)

### 2.1. Cria na UI

1. **Funil de Vendas** (menu lateral)
2. Botão **"+ Criar"** (canto superior direito) → **Funil**
3. Nome: `Manoela — Mentoria Icônica`
4. Cria pelo menos **3 stages**:
   - "Lead Novo" (será `open` no PeSDR)
   - "Cliente Ganho" (será `won`)
   - "Lead Perdido" (será `lost`)

Salva. Pode adicionar mais stages depois (não muda o mapping).

### 2.2. Pega os IDs via API

No terminal do seu Mac:

```bash
RD_TOKEN="<cole o token do passo 1>"

curl -s "https://crm.rdstation.com/api/v1/deal_pipelines?token=${RD_TOKEN}" \
  | python3 -m json.tool
```

Vai retornar JSON tipo:

```json
[
  {
    "id": "5f0f576f08c0c768b2dd4759",            ← ESSE É O pipeline_id
    "name": "Manoela — Mentoria Icônica",
    "deal_stages": [
      { "id": "54480f523f64f90155000034", "name": "Lead Novo" },
      { "id": "54480f523f64f90155000035", "name": "Cliente Ganho" },
      { "id": "54480f523f64f90155000036", "name": "Lead Perdido" }
    ]
  }
]
```

🚨 **Anota:**
- `pipeline_id` = o `"id"` do pipeline da Manoela
- `stage_id_open` = `id` do stage "Lead Novo"
- `stage_id_won` = `id` do stage "Cliente Ganho"
- `stage_id_lost` = `id` do stage "Lead Perdido"

---

## Passo 3 — Custom Fields (15 min)

### 3.1. Cria 3 custom fields na UI

**Configurações → Campos personalizados** (ou similar — varia por versão)

| Nome | Tipo | Vinculado a (`for`) |
|---|---|---|
| **Faturamento Mensal Estimado** | Número | `deal` |
| **Origem do Lead** ⭐ | Texto | `contact` |
| Momento Profissional (opcional) | Texto | `contact` |

> ⭐ O campo **Origem do Lead** é CRÍTICO — vai ser preenchido automaticamente pelo Respondi (passo 5.3) com `respondi:mentoria_iconica`. É o que o PeSDR vai usar pra filtrar "contact criado via integração" vs "contact criado manualmente".

### 3.2. Pega os IDs via API

```bash
# Pega custom fields de contact
curl -s "https://crm.rdstation.com/api/v1/custom_fields?token=${RD_TOKEN}&for=contact" \
  | python3 -m json.tool

# Pega custom fields de deal
curl -s "https://crm.rdstation.com/api/v1/custom_fields?token=${RD_TOKEN}&for=deal" \
  | python3 -m json.tool
```

Resposta:

```json
[
  {
    "id": "62684cbc6caff8000c8b6b00",         ← ESSE É O custom_field_id
    "label": "Origem do Lead",
    "for": "contact",
    "type": "text"
  }
]
```

🚨 **Anota** os IDs no bloco de notas.

---

## Passo 4 — App OAuth (15 min)

> Diferente do token do passo 1 (que é "instant" e fica só pro setup). Esse OAuth é o que o PeSDR vai usar em **produção** pra escrever no CRM continuamente.

1. Abre **https://app.rdstation.com.br/marketplace/dev**
2. **+ Nova aplicação** (ou "Custom App")
3. Tipo: **Aplicação privada**
4. Nome: `PeSDR — Manoela`
5. **Redirect URI:** `http://localhost:8765/oauth_callback`
6. **Escopos:** marcar tudo de CRM (contacts read/write, deals read/write)

🚨 **Anota** `client_id` e `client_secret` (clica em "Mostrar").

> O `refresh_token` vem depois — quando eu implementar a Fase B, te entrego um script `scripts/oauth_flow_init.py` que abre o browser e faz o flow OAuth de uma vez só.

---

## Passo 5 — Conectar Respondi → RD Station (5 min)

Esse passo é **no painel do Respondi** (não do RD).

1. Abre o form **Mentoria Icônica** (`QWHmKbnx`)
2. **Configurações → Integrações → CRMs → RD Station CRM** → clica em **Conectar**
3. Autoriza com login da Lana
4. **Mapeamento de campos** (Respondi → RD Station):

| Pergunta Respondi | Campo RD Station |
|---|---|
| Nome | `primeiro_nome` |
| WhatsApp | `telefone_principal` (type `cellphone`) |
| Faturamento | Custom field `Faturamento Mensal Estimado` (criado no passo 3) |

### 5.3. ⭐ CONFIGURAÇÃO CRÍTICA — preencher `cf_origem_lead`

No mapping, procura se o Respondi permite mandar **valor fixo** pra um custom field (não conectado a uma pergunta do form). Geralmente é "Valor fixo" ou "Constante".

**Configura:**
- Campo destino: **Origem do Lead** (custom field criado no passo 3)
- Valor fixo: `respondi:mentoria_iconica`

> Isso resolve a OQ-A1 (gate de produção). Sem esse campo, o webhook do RD Station **não distingue** "criado via integração" de "criado manualmente" — e o PeSDR poderia disparar Talk + HSM proativo pra contact criado à mão pela Lana.

> **Se o Respondi não permitir valor fixo aqui** → me avisa. Plano B: configurar no RD Station uma "regra de automação" que adiciona uma **tag** específica quando contact vem da integração Respondi. (Webhooks RD entregam tags no payload.)

---

## Passo 6 — Webhook RD Station → PeSDR (10 min)

### 6.1. Cria webhook na UI

**Configurações → Webhooks** (ou Integrações → Webhooks)

- **URL:** `https://<sua-url-ngrok>/webhooks/manoela-mentora/crm/rdstation`
  - Quando o código estiver pronto, eu te passo a URL definitiva
  - Por enquanto deixa em branco ou usa placeholder (a gente preenche depois)
- **Eventos:** marcar **mínimo** `crm_contact_created`
  - Opcionais futuros: `crm_contact_updated`, `crm_deal_stage_changed`

### 6.2. ⭐ DESCOBERTAS CRÍTICAS — anotar enquanto configura

**OQ-A2 — Auth do webhook:**
- O RD tem opção de **HMAC** (header `X-RD-Signature` ou similar)?
- Ou só **URL secreta** (`?secret=...`)?

> **Anota** o que vê na tela de criação do webhook. Determina o contract do `RDStationCRMInboundAdapter._validate_signature`.

**OQ-A3 — Retry policy:**
- Procura na config se mostra **retry on failure**
- Anota: retenta? Quantas vezes? Em quanto tempo?

---

## Passo 7 — Meta / Templates HSM (REUSA o que você já tem)

> **Importante (sua pergunta):** **NÃO precisa criar app Meta novo nem configurar API.** Você já tem o app + API Cloud da Manoela funcionando (foi configurado em sessões anteriores — credenciais em `tenants/manoela-mentora/secrets.enc.yaml`).
>
> **O que precisa fazer:** registrar **1 template HSM** dentro do **Business Manager** da conta Meta que já está conectada.

### Por que precisa registrar template

WhatsApp Cloud API (que você já usa) **bloqueia** o envio da **primeira mensagem** se ela não for um **template aprovado pela Meta** (a janela de 24h se aplica só **depois** que o lead falar primeiro). Como nosso fluxo é:

```
Respondi → RD Station → PeSDR cria Talk → MANDA mensagem proativa
```

Essa mensagem proativa **tem que ser** template HSM aprovado. Sem isso, Meta retorna `WindowExpiredError` e o lead nunca recebe nada.

### Como registrar (10 min ação + 24-72h espera Meta aprovar)

1. **Meta Business Manager** → conta da Manoela → **WhatsApp** → **Templates de Mensagem** (ou "Message Templates")
2. **+ Novo Template** (ou "Create Template")
3. **Nome:** `saudacao_mentoria_v1`
4. **Categoria:** `Marketing` (mais flexível) ou `Utility` (se Meta exigir)
5. **Idioma:** `Português (BR)`
6. **Body** (com 1 variável `{{1}}` pro nome do lead):

   ```
   Oi {{1}}! 👋

   Aqui é a SDR digital da Manoela Mentora. Vi que você acabou
   de se cadastrar pra Mentoria Icônica — posso te fazer 1 ou 2
   perguntas rápidas pra entender se faz sentido conversarmos?
   ```

7. (Opcional) **Botões Quick Reply:** "Pode sim! 👍" / "Agora não"

### Submeter e aguardar

- Submeter pra aprovação
- Meta leva **24-72h** pra aprovar (geralmente < 24h se seguir regras)
- **Regras:** sem preço explícito, sem promessa exagerada, sem CAPS LOCK, sem urgência forçada
- Se rejeitar: ajusta copy e re-submete

> **Faça primeiro pra ganhar tempo enquanto configura RD Station.** Quando o template estiver aprovado, o `template_ref: saudacao_mentoria_v1` vai pro `tenant.yaml` da Manoela.

---

## 🚦 Ordem sugerida pra fazer (1h ativo + 24-72h espera Meta)

1. **Passo 7** (Meta template) — **PRIMEIRO**, pra ganhar 24-72h em paralelo
2. **Passo 1** (Token RD)
3. **Passo 2** (Pipeline) → me passa pipeline_id + 3 stage_ids
4. **Passo 3** (Custom Fields) → me passa 3 custom_field_ids
5. **Passo 4** (OAuth) → me passa client_id + client_secret
6. **Passo 5** (Conectar Respondi) → confirma se conseguiu preencher `cf_origem_lead` com valor fixo
7. **Passo 6** (Webhook) → me responde OQ-A2 (HMAC?) + OQ-A3 (retry?)

---

## ❓ Resposta às 3 perguntas que você levantou (2026-06-24)

### 1. "Pipeline ID não está na URL — como pegar?"
**Resposta:** corrigido neste guia. Via **API REST** com token de instância (passo 1 + 2.2). A URL do navegador é genérica.

### 2. "Por que precisa do Meta sendo que já tenho a API e o app?"
**Resposta:** você não precisa criar **nada** novo no Meta. Só **registrar 1 template** dentro do app que já existe (passo 7). É um clique de cadastro de copy, não setup de API. A app/credenciais já estão cifradas no `secrets.enc.yaml`. O template é só um "conteúdo aprovado pela Meta" que o app pode enviar como primeira mensagem fora da janela 24h.

### 3. "OQ-A1 (origem do contact) — como o RD distingue?"
**Resposta (descoberta crítica):** o webhook do RD Station **NÃO TEM** campo `source`/`origin` nativo no payload (confirmado em https://developers.rdstation.com/reference/webhooks-payload-crm — exemplo de payload `crm_contact_created` mostra só `document.{id, name, emails, phones, custom_fields, ...}`).

**Solução:** usar o **custom field `Origem do Lead`** (criado no passo 3) preenchido com valor fixo `respondi:mentoria_iconica` pela integração nativa (passo 5.3). O `RDStationCRMInboundAdapter` valida `payload.document.custom_fields.cf_origem_lead == "respondi:mentoria_iconica"` antes de criar Talk.

**Plano B se Respondi não permitir valor fixo:** configurar regra de automação no RD que **aplica uma tag** (`respondi-mentoria`) ao contact recém-criado. Webhooks entregam tags. Filtro vira `"respondi-mentoria" in payload.document.tags`.

---

## 🔗 Referências oficiais (consultadas em 2026-06-24)

- [RD Station Developers Portal](https://developers.rdstation.com/)
- [API: List pipelines](https://developers.rdstation.com/reference/crm-v1-list-pipelines)
- [API: List custom fields](https://developers.rdstation.com/reference/crm-v1-list-custom-fields)
- [Webhooks payload structure](https://developers.rdstation.com/reference/webhooks-payload-crm)
- [API: Get pipeline (single)](https://developers.rdstation.com/reference/crm-v1-get-pipeline)
- [llms.txt — índice em Markdown](https://developers.rdstation.com/llms.txt)

---

**Fim do guia v2. Boa config! 🚀**
