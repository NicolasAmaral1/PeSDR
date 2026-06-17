# Form Ingestion + CRM Write-Only — Skeleton Demo

> **⚠️ ESTA PASTA É DEMONSTRATIVA. NÃO É CÓDIGO DE PRODUÇÃO.**
>
> Tudo aqui é stub estrutural pra Nicolas visualizar a forma final da implementação proposta na [spec 2026-06-16](../../docs/superpowers/specs/2026-06-16-form-ingestion-and-crm-write-only-design.md) (PR #20) antes dela virar plan executável.

## Propósito

A spec PR #20 propõe ~38 arquivos novos pra integrar:
- **Formulário Respondi** (entrada de leads)
- **CRM RD Station** (saída de qualificação)

A spec é densa (2.015 linhas) e tem 12 open questions. Esta pasta materializa a **estrutura proposta** em arquivos reais (sem implementação funcional) pra:

1. Validar visualmente a árvore de pastas final
2. Conferir nomes de classes, métodos, imports
3. Identificar acoplamentos não-aparentes só lendo spec
4. Permitir code review estilo "structural" antes do plan virar tarefas

## O que tem aqui

| Item | Status |
|---|---|
| **5 MDs orientadores** (este + 4 ao lado) | Completos |
| **Migration 0030** (`migrations/`) | Real e completa — pode rodar `alembic upgrade head` num clone |
| **Stubs Python** (`src/forms/`, `src/flowengine/actions/crm/`, etc) | Estruturais: assinaturas + docstrings + `raise NotImplementedError` |
| **Tenant manoela-demo** (`tenants/manoela-demo/`) | tenant.yaml + treeflow demonstrativos com `forms.respondi` + `crm.rdstation` + `on_collected: crm` |
| **Scripts auxiliares** (`scripts/`) | `oauth_flow_init.py` (obtém refresh_token RD Station 1x) + `seed_demo.py` (demonstra fluxo E2E mockado) |
| **Estratégia de testes** (`tests/README.md`) | Descrição da abordagem (sem os 16 arquivos de teste ainda) |

## O que NÃO tem aqui

- ❌ Implementação funcional (todos os métodos `raise NotImplementedError`)
- ❌ Testes que passam (são estruturais)
- ❌ Integração com `src/ai_sdr/` real (esta pasta é isolada, sem efeito no produto)
- ❌ Decisões finais sobre as 12 open questions da spec (esses pontos estão marcados `# TODO:` ou `# Q11` etc nos stubs)

## Como ler isso

Sugestão de ordem:

1. **[ARCHITECTURE.md](./ARCHITECTURE.md)** — resumo das decisões da spec + diagrama
2. **[IMPLEMENTATION_GUIDELINES.md](./IMPLEMENTATION_GUIDELINES.md)** — padrões a seguir (TDD, RLS, async, etc)
3. **[TESTING_STRATEGY.md](./TESTING_STRATEGY.md)** — como testar cada fase
4. **[AGENT_ROLES.md](./AGENT_ROLES.md)** — quem faz o quê (Pedro / Nicolas / Lana / Manoela / subagents AI)
5. **`src/`** — abrir na ordem dos imports (base → registry → factory → impl)
6. **`tenants/manoela-demo/tenant.yaml`** — ver como tudo se conecta

## Próximos passos

Quando esta demo for aprovada pelo Nicolas:

1. Spec PR #20 vira plan executável via skill `writing-plans`
2. Stubs daqui migram pra `src/ai_sdr/` real com implementação preenchida
3. Pasta `demos/2026-06-16-form-crm-skeleton/` é **deletada** (cumprido seu papel)
4. Trabalho real começa em 3 PRs separados (Fase A → B → C), conforme §8 da spec

## Referências

- Spec PR #20: [docs/superpowers/specs/2026-06-16-form-ingestion-and-crm-write-only-design.md](../../docs/superpowers/specs/2026-06-16-form-ingestion-and-crm-write-only-design.md)
- ADR CRM PR #18: [docs/superpowers/specs/2026-06-12-crm-posture-decision.md](../../docs/superpowers/specs/2026-06-12-crm-posture-decision.md)
- FE-03c Actions Framework: [docs/superpowers/specs/2026-06-12-fe03c-actions-adapter-framework-design.md](../../docs/superpowers/specs/2026-06-12-fe03c-actions-adapter-framework-design.md)
- Pattern de Adapter (4 bordas): [docs/superpowers/specs/2026-05-24-adapter-pattern-decision.md](../../docs/superpowers/specs/2026-05-24-adapter-pattern-decision.md)
