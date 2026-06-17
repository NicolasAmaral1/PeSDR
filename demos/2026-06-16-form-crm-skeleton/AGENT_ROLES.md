# Agent Roles — quem faz o quê

> Mapeia as **6 personas** envolvidas no fluxo Form ingestion + CRM e o que cada uma faz/decide. Cobre tanto pessoas (Pedro, Nicolas, Lana, Manoela) quanto subagents AI usados no processo de implementação.

## Personas humanas

### 1. Pedro Aranda (`pedrooaranda`) — Product owner + Frontend orchestrator

**Responsabilidades:**
- Levanta requisitos do cliente (Manoela)
- Mantém spec e roadmap atualizados (esta pasta, [docs/superpowers/roadmap/](../../docs/superpowers/roadmap/), specs em [docs/superpowers/specs/](../../docs/superpowers/specs/))
- Coleta credenciais externas (Meta WhatsApp, RD Station OAuth, Respondi secret)
- Abre PRs em `dev/pedro` ou branches descendentes
- Cifra secrets via SOPS no Mac local (age key já registrada em `.sops.yaml`)
- Coordena com Lahna (admin da Manoela) sobre billing de APIs (Anthropic, OpenAI)
- Mantém dialogue com Nicolas sobre decisões arquiteturais

**Não faz (delegado pra outras roles):**
- Decisões arquiteturais (Nicolas)
- Implementação core do FlowEngine (Nicolas)
- Operação do console HITL diária (Lana)
- Configurar templates HSM no Meta (Lana ou Manoela)

### 2. Nicolas Amaral (`NicolasAmaral1`) — Architect + Core implementer

**Responsabilidades:**
- Aprova specs e ADRs (review fechado obrigatório no ruleset do main)
- Decide as 12 open questions desta spec (ver §11 da spec)
- Implementa FlowEngine core (FE-01a..FE-05+)
- Mergeia PRs em `main`
- Mantém qualidade de código (lint/format/type/test)
- Faz hardening pré-produção (ex: PR #10 — 4 bugs do VPS smoke)
- Decide sobre Plano 6 (IdentityResolver), Plano 8 (Media), etc

**Hoje (estado da arte):**
- FE-03c (Actions Framework) entregue
- Manoela v2 com FlowEngine v2 conversion entregue
- ADR CRM (Fase 1) aprovado
- 12+ PRs mergeadas em main
- Atualmente trabalhando em: FE-03b refinements, multi-channel hedges

### 3. Lana — Operadora do console HITL (estrategista da Manoela)

**Responsabilidades:**
- Acessa o console em `/console/manoela-mentora/leads`
- Atribui leads pendentes ao TreeFlow correto
- Revisa Talks em `requires_review` (e.g., HSM template falhou)
- Atualiza prompts/objections via PR ou conversa com Pedro
- Fornece feedback sobre qualidade da conversa

**Não tem acesso direto a:**
- Código-fonte
- DB
- Secrets cifrados

### 4. Manoela Mentora — Tenant final / cliente piloto

**Responsabilidades:**
- Configura conta no RD Station (cria pipeline, define stages)
- Configura conta no Respondi (cria form, configura webhook)
- Aprova templates HSM no Meta Business Manager
- Define a estratégia de venda (preços, produtos, persona de comunicação)

**Sua mentora/marca pessoal é a domain expertise** — Pedro/Nicolas/Lana servem essa expertise técnica.

### 5. Lahna — Admin financeiro / billing

**Responsabilidades:**
- Cadastra cartão de crédito + adquire créditos nas APIs externas (Anthropic, OpenAI)
- Repassa as API keys pro Pedro

## Subagents AI (assistentes de implementação)

Não são agents rodando em produção. São assistentes do processo de desenvolvimento (Claude Code, skills, etc).

### A. Claude Code (orquestrador principal)

**Modelo:** Claude Opus 4.7 (atualmente)

**Responsabilidades de implementação:**
- Lê código existente pra entender contexto
- Propõe arquitetura (esta spec)
- Implementa stubs e código real
- Gera testes via TDD
- Mantém docs atualizadas (CLAUDE.md, README.md)
- Aponta riscos e trade-offs
- Cifra secrets via SOPS quando autorizado

**Limites:**
- Não faz merge em main (só com aprovação humana via ruleset)
- Não decide arquitetura sozinho (propõe, Nicolas decide)
- Não tem acesso a secrets em plaintext no chat (boa prática — Pedro confirma local)
- Não invoca skill `writing-plans` sem confirmação do Pedro

### B. Subagent `Explore` (read-only)

**Usado em:** mapeamento de codebase, análise de PRs do Nicolas, deep-dive em arquivos antes de propor arquitetura.

**Exemplos desta spec:**
- Mapeou FlowEngine v2 + FE-03c inteiro (~2000 palavras)
- Mapeou tenant Manoela pós-conversion
- Validou que `lead.crm_refs` não existe ainda

### C. Subagent `general-purpose` (multi-step)

**Usado em:** tarefas multi-step de exploração que não cabem em prompt único.

**Exemplo potencial:** "audit toda a implementação atual de Action adapters e me diz se algum desses 22 stubs precisa de ajuste de interface."

### D. Skills do Claude Code

Já usadas neste projeto:

- `superpowers:writing-plans` — converte spec em plan executável task-by-task (Nicolas usa pra cada Plano N)
- `superpowers:executing-plans` — executa um plan via subagents
- `superpowers:test-driven-development` — TDD discipline
- `superpowers:requesting-code-review` — checklist pré-PR

Skills relevantes pra esta spec depois da aprovação:

- `writing-plans` → gera 3 plans (Fase A, B, C) em `docs/superpowers/plans/`
- `executing-plans` → executa cada fase task-by-task
- Subagents de implementação rodam em paralelo onde possível (1 subagent por task A1, A2, ..., A10)

## Fluxo de decisão

```
┌─────────────────────────────────────────────────────────────────┐
│  Pedro identifica necessidade (cliente Manoela precisa do CRM)   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Claude (orquestrador) faz pesquisa, propõe arquitetura          │
│  → spec MD em docs/superpowers/specs/                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PR pro main, Nicolas revisa, decide open questions              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Spec aprovada → Claude usa skill writing-plans → gera plans     │
│  em docs/superpowers/plans/ (Fase A, B, C)                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Claude executa plans (subagents paralelos por task)             │
│  → PRs separados por fase, Nicolas mergeia em main               │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  Pedro/Lana fazem smoke E2E em produção                          │
│  Manoela começa a operar real                                    │
└─────────────────────────────────────────────────────────────────┘
```

## Decisões reservadas pra humanos (não delegáveis ao Claude)

Por filosofia do projeto:

1. **Mergear em `main`** — exige review humano via ruleset (1 approval mandatório)
2. **Aprovar specs/ADRs** — Nicolas é o único que decide arquitetura final
3. **Cifrar secrets em plaintext no chat** — política: Pedro cifra local, Claude não vê plaintext
4. **Aprovar templates HSM no Meta** — Manoela/Lana
5. **Adquirir créditos em APIs externas** — Lahna (billing)
6. **Conversar com cliente final** — Pedro orquestra, Lana opera

## Decisões delegáveis ao Claude

1. Pesquisa em docs externas (RD Station, Respondi)
2. Geração de stubs/scaffolds
3. Sugestão de testes
4. Análise de impacto em código
5. Refactor mecânico (ex: rename `joana` → `manoela`)
6. Cifragem via SOPS quando Pedro autoriza explicitamente
7. Abertura de PRs (não merge)

## Quando humano deve sobrescrever Claude

- Decisão arquitetural fundamental (escolha de pattern)
- Trade-offs onde Claude oscila ou apresenta opções equivalentes
- Quando Claude propõe "fazer agora" coisa que era pra esperar
- Quando Claude inventa nome/convenção fora do padrão estabelecido pelo Nicolas
