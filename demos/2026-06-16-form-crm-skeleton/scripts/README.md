# Scripts auxiliares

Scripts pra setup inicial + demonstração do fluxo E2E.

## `oauth_flow_init.py`

**Quando usar:** uma vez por tenant que vai integrar RD Station. Antes da Fase C.

**O que faz:**
1. Abre browser do operador na página de autorização do RD Station
2. Operador autoriza o app pra acessar a conta dele
3. Captura o `authorization_code` retornado via redirect
4. Troca por `refresh_token` no endpoint OAuth
5. Imprime o `refresh_token` na stdout — operador cifra via SOPS no `tenants/<slug>/secrets.enc.yaml`

**Pré-requisitos:**
- App registrado em https://app.rdstation.com.br/dashboard (vide CLAUDE.md seção "CRM (Plano 7b)" pós-Fase B)
- `client_id` e `client_secret` do app já em mãos
- Redirect URI configurado no painel: `http://localhost:8765/oauth_callback`

**Uso:**

```bash
uv run python demos/2026-06-16-form-crm-skeleton/scripts/oauth_flow_init.py \
    --client-id <client_id> \
    --client-secret <client_secret> \
    --redirect-uri http://localhost:8765/oauth_callback
```

## `seed_demo.py`

**Quando usar:** demonstração local do fluxo end-to-end pra Nicolas/operadora antes da Fase C.

**O que faz (mockando tudo):**
1. Insere tenant `manoela-demo` no DB local
2. Carrega tenant.yaml + treeflow.yaml deste demo
3. Simula submission de form Respondi (payload fixture)
4. Mostra trace do fluxo:
   - Form ingestion → Lead criado
   - Talk criada com TalkFlowState pré-populado
   - HSM enviado (mockado via FakeMessagingAdapter)
   - run_turn → on_collected dispara action CRM (mockado via LoggingActionAdapter)
   - Lead.crm_refs atualizado (mockado)
5. Imprime estado final

**NÃO bate em sistemas externos** (Meta, RD Station, Respondi). Tudo mockado.

**Uso:**

```bash
make up                          # postgres + redis
make migrate                     # inclui 0030 desta spec quando implementada
uv run python demos/2026-06-16-form-crm-skeleton/scripts/seed_demo.py
```

Por ora (skeleton): script é stub. Implementação real chega na Fase C.
