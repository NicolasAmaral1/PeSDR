# RD Station — Guia de configuração da Manoela

**Data:** 2026-06-23
**Status:** Guia operacional pra Pedro
**Contexto:** Lana acabou de assinar o RD Station CRM (PRO) da Manoela. Esta é a lista exata do que configurar pra destravar implementação da Fase A (CRM inbound) + Fase B (CRM outbound) das PRs #22 e seus dois descendentes de implementação.

> ⚠️ **IMPORTANTE:** Algumas configurações dependem do app OAuth + acesso de desenvolvedor. Se a Lana não te der admin do RD Station, **você precisa do nível "Gestor"** pelo menos pra criar app de integração.

---

## 🎯 Resumo executivo: 7 ações + 7 descobertas

| # | Ação | Tempo estimado | Bloqueia |
|---|---|---|---|
| 1 | Criar Pipeline da Manoela + 3 stages | 10 min | Fase B (criar deals) |
| 2 | Criar Custom Fields | 10 min | Fase B (mapear campos coletados) |
| 3 | Criar App OAuth (Marketplace Dev) | 15 min | Fase B (autenticar API calls) |
| 4 | Configurar Integração Respondi → RD Station | 5 min | Fase A (entrada de leads) |
| 5 | Configurar Webhook RD Station → PeSDR | 10 min | Fase A (gatilho do Talk) |
| 6 | Aprovar Template HSM no Meta | **24-72h espera** | Fase A (mensagem proativa) |
| 7 | Cifrar 4 secrets novos no `secrets.enc.yaml` | 5 min | Tudo |
| — | **+ 7 descobertas críticas** (durante config) | — | Ligar em produção |

**Total ativo:** ~1h de cliques no painel + 24-72h aguardando aprovação do Meta.

---

## 1. Pipeline + 3 Stages

### Onde ir
**Menu lateral → Funil de Vendas** (ou "Pipelines")

### O que fazer
**1a. Criar pipeline:**
- Botão `+ Novo Pipeline`
- Nome sugerido: `Manoela — Mentoria Icônica`
- Salvar

**1b. Anotar `pipeline_id`:**
- Abre o pipeline criado
- URL no navegador: `https://crm.rdstation.com/pipelines/abc123xyz/...`
- A string `abc123xyz` é o `pipeline_id`. **Anota num bloco seguro.**

**1c. Criar 3 stages mínimos:**

| Stage canônico PeSDR | Sugestão de nome no RD | Você anota |
|---|---|---|
| `open` | "Lead Novo" ou "Em Qualificação" | `stage_id_open` = ___ |
| `won` | "Cliente Ganho" | `stage_id_won` = ___ |
| `lost` | "Lead Perdido" | `stage_id_lost` = ___ |

Pra cada stage, clica em editar → pega o ID na URL.

---

## 2. Custom Fields

### Onde ir
**Configurações → Campos personalizados** (ou similar — varia por versão)

### O que criar
| Campo | Tipo | Tag | Anota |
|---|---|---|---|
| **Faturamento Mensal Estimado** | Número | `cf_faturamento_mensal` | `custom_field_id` = ___ |
| **Momento Profissional** (opcional MVP) | Texto | `cf_momento_profissional` | `custom_field_id` = ___ |
| **Origem do Lead** | Texto | `cf_origem_lead` | `custom_field_id` = ___ |

Pra cada um, anota o `custom_field_id` (aparece na URL ou ao editar).

> 💡 **Por que "Origem do Lead":** vai ajudar a distinguir lead vindo do form (`respondi:mentoria_iconica`) vs lead criado manualmente. Pode ser **o campo que resolve a OQ-A1** abaixo.

---

## 3. App OAuth

### Onde ir
**https://app.rdstation.com.br/marketplace/dev** (Marketplace → Desenvolvedores)

### O que fazer
- Botão `+ Nova aplicação` (ou similar)
- **Tipo:** Aplicação privada / Custom App
- **Nome:** `PeSDR — Manoela`
- **Descrição:** "Integração interna SDR digital"
- **Redirect URI:** `http://localhost:8765/oauth_callback` (vamos usar 1x só pra obter refresh_token)
- **Permissões/Escopos:** marcar tudo de CRM:
  - `crm.contacts.read` + `crm.contacts.write`
  - `crm.deals.read` + `crm.deals.write`
  - `crm.activities.write` (se tiver — pra notas)

### O que anotar
- `client_id`
- `client_secret` (clica em "Mostrar")

> 💡 **Refresh token vem depois** — quando o código estiver pronto, rodaremos `scripts/oauth_flow_init.py` uma única vez pra obter via Authorization Code flow. Não precisa anotar agora.

---

## 4. Integração Respondi → RD Station

### Onde ir
**No painel RESPONDI** (não no RD), no form **Mentoria Icônica** (`QWHmKbnx`):
- **Configurações do form → Integrações → CRMs → RD Station CRM**

### O que fazer
- Clica em **"Conectar"** (o botão que você me mostrou no screenshot)
- Autoriza com a conta RD Station da Manoela (login dela)
- Configura o mapeamento Respondi → RD Station:

| Pergunta Respondi | Campo RD Station |
|---|---|
| Nome | `primeiro_nome` (ou `nome_completo`) |
| WhatsApp | `telefone_principal` |
| Faturamento | Custom field `cf_faturamento_mensal` (criado no passo 2) |

### **🚨 DESCOBERTA CRÍTICA (OQ-A1) — fazer enquanto configura:**

> A pergunta mais importante de toda a integração: **como o RD Station marca, no payload do webhook, que o contact foi criado VIA INTEGRAÇÃO RESPONDI (e não criado manualmente pela Lana)?**

**Possibilidades a investigar:**
1. Tem campo `source: "respondi"` ou `source: "integration"` no payload?
2. Tem **tag** que aparece no contact tipo `respondi`, `webform`, ou `mentoria_iconica`?
3. Pode-se configurar o **custom field `cf_origem_lead`** pra ser preenchido com `respondi:mentoria_iconica` automaticamente?

**Recomendação minha:** se nenhum dos 3 acima for nativo, **manualmente configura no Respondi pra preencher o `cf_origem_lead`** com o valor `respondi:mentoria_iconica`. Isso vira nosso filtro de origem.

> 🔴 **SEM RESOLVER ISSO, NÃO PODEMOS LIGAR EM PRODUÇÃO** — qualquer contact criado manualmente pela Lana disparava Talk + WhatsApp HSM proativo. Gate obrigatório do Nicolas.

**O que anotar:** qual estratégia de origem escolheu + valor exato (ex: `cf_origem_lead = "respondi:mentoria_iconica"`).

---

## 5. Webhook RD Station → PeSDR

### Onde ir
**Configurações → Webhooks** (ou "Integrações → Webhooks")

### O que fazer
- Botão `+ Novo webhook`
- **URL:** `https://sdr.luminai.ia.br/webhooks/manoela-mentora/crm/rdstation` — **mas como não temos o domínio configurado ainda, usa por enquanto:**
  ```
  https://<sua-url-ngrok>/webhooks/manoela-mentora/crm/rdstation
  ```
  (Quando o código estiver pronto, te passo a URL exata do ngrok que você cola aqui pra teste local)
- **Eventos a escutar (mínimo):**
  - `contact_created` ⭐ obrigatório
  - Opcionais futuros: `contact_updated`, `deal_stage_changed`

### **🚨 DESCOBERTAS CRÍTICAS (OQ-A2, A3) — fazer agora:**

**OQ-A2 — Auth do webhook:**
- O RD Station tem opção de **HMAC** (header `X-RD-Signature` ou similar)?
- Ou só **URL secreta** (você inventa um token que vai no querystring tipo `?secret=...`)?

**O que fazer:**
1. Olha na config do webhook se tem campo "Secret" ou "Signing key" ou "HMAC token"
2. Se SIM → ativa HMAC, anota a chave
3. Se NÃO → vai funcionar via URL secret. Gera você mesmo:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Anota essa string. Vai cifrar no `secrets.enc.yaml` no passo 7.

**OQ-A3 — Retry policy:**
- Olha se há config de "retry on failure" no webhook do RD Station
- Anota: retry automático? quantas vezes? backoff?
- Se não documentado, **manda 1 webhook de teste** (botão "Testar" se existir) e vê o que acontece

---

## 6. Template HSM no Meta Business Manager

### Onde ir
**business.facebook.com → conta da Manoela → WhatsApp → Templates de Mensagem**

### O que fazer
- Botão `+ Novo template`
- **Nome:** `saudacao_mentoria_v1`
- **Categoria:** `Marketing` (ou `Utility` — depende do que Meta exigir)
- **Idioma:** `Português (BR)`
- **Body** (com 1 variável `{{1}}` pro nome):
  ```
  Oi {{1}}! 👋

  Aqui é a SDR digital da Manoela Mentora. Vi que você acabou de se cadastrar pra Mentoria Icônica — posso te fazer 1 ou 2 perguntas rápidas pra entender se faz sentido conversarmos?
  ```
- **Botões (opcional):**
  - Quick reply: "Pode sim! 👍"
  - Quick reply: "Agora não, depois"

### Atenção
- **Submeter pra aprovação Meta** = 24-72h de espera
- **Não pode ter:** preço explícito, promessa de resultado, gatilho urgente forçado
- **Não pode começar com:** "OFERTA", "PROMOÇÃO", emoji exagerado

> 💡 Se Meta rejeitar, ajusta copy e re-submete. Geralmente aprovam em < 24h se seguir as regras.

---

## 7. Cifrar secrets no `secrets.enc.yaml`

### Quando fazer
Depois de ter todos os valores anotados dos passos 1-5.

### Como fazer
No terminal:

```bash
cd "/Users/usuario/Documents/Pedro Aranda/Pedro Aranda IA/PeSDR"
sops tenants/manoela-mentora/secrets.enc.yaml
```

Adiciona as 4 chaves novas (mantém as existentes intactas):

```yaml
# ... existentes ...
anthropic_key: ...
openai_key: ...
wa_phone_id: ...
wa_token: ...
wa_verify: ...
wa_app_secret: ...

# NOVAS — Fase A + B do CRM
rdstation_webhook_secret: "<a string aleatória que você gerou no passo 5>"
rdstation_client_id: "<do passo 3>"
rdstation_client_secret: "<do passo 3>"
# rdstation_refresh_token: vai ser preenchido depois que rodarmos scripts/oauth_flow_init.py
```

Salva e fecha. SOPS recifra automaticamente.

---

## 📋 Bloco de notas — template pra você anotar tudo

Copia pro Apple Notes (com lock), 1Password, ou bloco seguro:

```
═══════════════════════════════════════════════════════
RD STATION — Manoela Mentora
Data da config: 2026-06-23
═══════════════════════════════════════════════════════

PIPELINE
  pipeline_id: ___________________

STAGES (do pipeline acima)
  stage_id_open:  ___________________
  stage_id_won:   ___________________
  stage_id_lost:  ___________________

CUSTOM FIELDS
  cf_faturamento_mensal_id: ___________________
  cf_origem_lead_id:        ___________________
  cf_momento_profissional_id: ___________________ (opcional)

APP OAUTH
  client_id:     ___________________
  client_secret: ___________________
  refresh_token: (vem depois via script)

INTEGRAÇÃO RESPONDI → RD STATION
  Conectado? Sim/Não
  Mapeamento configurado: Sim/Não

WEBHOOK RD → PESDR
  HMAC ou URL secret? ___________________
  Token usado: ___________________
  Retry policy: ___________________

TEMPLATE HSM META
  Nome: saudacao_mentoria_v1
  Status: Submetido / Aprovado / Rejeitado / Pendente
  Data submissão: ___________________

═══════════════════════════════════════════════════════
🔴 DESCOBERTAS CRÍTICAS PRA O CÓDIGO FUNCIONAR
═══════════════════════════════════════════════════════

OQ-A1 — Como RD marca origem (manual vs integração)?
  Estratégia escolhida: ___________________________
  Campo/valor exato: _____________________________
  Ex: cf_origem_lead = "respondi:mentoria_iconica"

OQ-A2 — Auth do webhook (HMAC ou URL secret)?
  Resposta: _________________________

OQ-A3 — Retry policy do webhook?
  Resposta: _________________________

OQ-A4 — RD diferencia múltiplos forms Respondi no contact?
  (Quando subir Aceleradora também) _________________________

OQ-A5 — Tem ambiente sandbox?
  (Pra testar sem afetar prod da Manoela) _________________________

OQ-A6 — refresh_token rotaciona a cada uso?
  (Investiga só quando rodarmos OAuth flow) _________________________

═══════════════════════════════════════════════════════
```

---

## 🚦 Ordem sugerida pra você fazer (1h30 ativo)

1. **Passos 1, 2, 3** (Pipeline + Custom Fields + App OAuth) — 35 min — **podem ser feitos em qualquer ordem**
2. **Passo 6** (Template HSM Meta) — 10 min de submissão + espera 24-72h pra aprovação. **Faz primeiro pra ganhar tempo na espera.**
3. **Passo 4** (Integração Respondi → RD) — 5 min — **CRÍTICO descobrir OQ-A1 aqui**
4. **Passo 5** (Webhook RD → PeSDR) — 10 min — **CRÍTICO descobrir OQ-A2 e OQ-A3 aqui**
5. **Passo 7** (Cifrar secrets) — 5 min — **só depois que todos os outros estiverem anotados**

---

## ❓ Quando me chamar

- Depois de **resolver OQ-A1** (origem do lead) — **DESTRAVA implementação produção da Fase A**
- Depois de **resolver OQ-A2** (auth webhook) — **DESTRAVA implementação do RDStationCRMInboundAdapter**
- Quando **template HSM for aprovado** — destrava smoke test E2E real
- Quando **passos 1-3 estiverem feitos** — eu posso preencher tudo do tenant.yaml automaticamente

Enquanto isso, eu já estou implementando:
- ✅ Sandbox web (não depende de RD Station — testes E2E sem CRM)
- ✅ Fase B (CRM outbound) — código pronto, só falta secrets do passo 7
- ✅ Fase A (CRM inbound) **skeleton** — código pronto mas `enabled: false` no tenant.yaml até OQ-A1/A2 resolvidos

---

**Fim do guia. Boa sorte na config! 🚀**
