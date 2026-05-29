# Spec: HITL Console — operator UI for lead assignment (Plano 11)

**Data:** 2026-05-26
**Status:** Aceito (brainstorm fechado, pronto pra plano)
**Autor:** Nicolas Amaral (decisão com Claude)
**Referências:**
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) — spec master
- [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) — ADR adapter pattern
- [`2026-05-24-messaging-adapter-design.md`](./2026-05-24-messaging-adapter-design.md) — Plano 5 (Messaging) — define `leads`, `inbound_messages`, REST endpoints que P11 consome

---

## 1. Contexto

Hoje a operação do PeSDR depende de CLI (`ai-sdr leads list-pending` / `assign-lead`). Funciona pra dev, é ruim pra Joana piloto operar diariamente. Plano 11 entrega a primeira interface web do projeto — um console mínimo de operação que substitui essa CLI.

Pela ADR de adapters, **o console é a impl default do "HITL surface"** pra tenants standalone (Joana, ou qualquer tenant fora do ecossistema Vialum). Tenants que rodam dentro de Vialum **substituem o console por Vialum Chat / Tasks Inbox** e desabilitam P11 via `tenant.yaml > console.enabled: false`.

**Investigação prévia:** [agente de research, 2026-05-26] confirmou que Vialum Chat ainda está mid-hardening (109 issues conhecidos incl. tenant isolation bypass, sem OpenAPI público, sem versioning) — não está estável o suficiente pra ser frontend de PeSDR hoje. P11 segue.

**Direção arquitetural de longo prazo:** quando Vialum Chat for re-arquitetado, será sobre a fundação técnica do PeSDR (RLS forçada, adapter pattern, stack Python, KB+guardrails formalizados). P11 v1 deve seguir convenções limpas o suficiente pra servir como referência implícita.

---

## 2. Decisão (síntese do brainstorm)

P11 v1 entrega:

- **Página única** `/console/{tenant_slug}/leads` (master-detail).
- **Master**: lista de leads `pending_assignment` (provider-agnostic display: WhatsApp E.164, ou `external_label`, ou ID truncado).
- **Detail** (lead selecionado): mensagens inbound em fila + dropdown de treeflows ativos do tenant + botão "Atribuir".
- **Atribuir** dispara o mesmo fluxo do REST `POST /tenants/{slug}/leads/{id}/assign` que já existe (Plano 5).
- **Polling HTMX 10s** atualiza a lista master automaticamente.
- **Auth real**: login form + session cookie signed (`itsdangerous`), users em tabela própria, RBAC com role `operator` + `tenant_admin` + flag global `is_platform_admin`.
- **Provisioning via CLI** (`ai-sdr users add/grant/revoke/passwd/list`).

Stack: **FastAPI + Jinja2 + HTMX**. Sem build step, sem novo container. Sobe junto com o app FastAPI existente.

---

## 3. Não-objetivos

- **Sem conversation viewer** (histórico de mensagens trocadas durante o talkflow) — fica pra P11b.
- **Sem manual takeover** (operador assumir conversa do agente) — fica pra P11c.
- **Sem queue de guardrails-exaustos** (UI da `_handle_exhausted` hook) — fica pra plano dedicado.
- **Sem cross-tenant UI** (admin ver conversas de todos os tenants num mesmo lugar) — schema já suporta, UI fica pra P11b.
- **Sem user management via UI** — operações administrativas (add/grant/revoke) ficam na CLI no v1.
- **Sem tenant_admin com privilegios extras** — role existe no schema, mas v1 trata `tenant_admin` igual a `operator` (mesmo acesso a tudo do tenant). Diferenciação real fica pra futuro.
- **Sem password reset por email, MFA, audit log de logins** — Plano 12.
- **Sem editor de TreeFlow YAML web-based** — segue Git-only.
- **Sem dashboard / analytics / conversion funnels** — fora de escopo.
- **Sem manage de Vialum (Chat / Hub / Tasks)** — quando tenant é Vialum, console é desabilitado por `console.enabled: false`.

---

## 4. Arquitetura

```
Browser ──HTTP──► FastAPI app (same process / same container)
                  ├── /webhooks/...                  (Plano 5, REST, JSON)
                  ├── /tenants/.../leads             (Plano 5, REST, JSON)
                  └── /console/...                   (NEW: Jinja templates, HTML+HTMX)
                       ├── /console/login            (form + session cookie)
                       ├── /console/logout
                       ├── /console/{slug}/leads     (full page, master-detail)
                       ├── /console/{slug}/leads/list           (HTMX partial, polling target)
                       ├── /console/{slug}/leads/{id}/detail    (HTMX partial, detail panel)
                       └── /console/{slug}/leads/{id}/assign    (POST, returns HTMX swap)
```

**Princípios:**

1. **Rotas console isoladas das REST** — `/console/...` retorna HTML/HTMX-partials; `/tenants/.../leads` continua REST JSON. Sem content-negotiation. Routers separados.
2. **Lógica de negócio compartilhada** — `/console/.../assign` e `/tenants/.../assign` chamam os mesmos helpers em `ai_sdr.treeflow.runtime` e `ai_sdr.messaging.ingest`. Não duplicar lógica entre HTML e REST.
3. **Templates server-rendered** — Jinja2 nativo (`fastapi.templating.Jinja2Templates`). HTMX faz partial-swaps pra ações sem reload.
4. **Auth via cookie session signed** — `itsdangerous` URLSafeTimedSerializer. Payload: `{user_id, exp}`. Cookie `HttpOnly` + `SameSite=Strict` + `Secure` (em HTTPS).
5. **RBAC no app layer** — users e user_tenant_access NÃO têm RLS (são tabelas globais que servem ao mecanismo de auth). Tenant context é setado via `set_tenant_context()` **após** validar acesso do user ao tenant.
6. **Multi-tenant URL-scoped** — slug do tenant na URL (`/console/{slug}/...`). Validado contra `user_tenant_access` em todo request.

---

## 5. Data model

### Migration `0009_users_and_access.py`

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_platform_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_users_username ON users (lower(username));
-- case-insensitive username. NO RLS, NO tenant_id.

CREATE TABLE user_tenant_access (
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role      TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tenant_id),
    CONSTRAINT ck_user_tenant_role CHECK (role IN ('operator', 'tenant_admin'))
);
CREATE INDEX ix_user_tenant_access_tenant ON user_tenant_access (tenant_id);
-- NO RLS (cross-tenant lookup table).
```

**Por que NÃO ter RLS aqui:** essas tabelas servem ao próprio mecanismo de authorization (chicken-and-egg). Authorization check feita no app layer; tenant-scoped tables (leads, talkflows, etc.) continuam protegidas por RLS no DB.

### Schema additions (`schemas/tenant_yaml.py`)

```python
class ConsoleConfig(BaseModel):
    enabled: bool = False                    # opt-in explícito; false desabilita /console/{slug}
```

Sem `username`/`password_hash` em tenant.yaml — credenciais vivem na tabela `users` agora. ConsoleConfig vira só "console habilitado pra este tenant ou não".

---

## 6. Auth flow

### Rotas

| Método | URL | Comportamento |
|---|---|---|
| GET | `/console/login` | Renderiza `login.html`. Se já autenticado, 302 → primeiro tenant acessível. |
| POST | `/console/login` | Valida `(username, password)` via bcrypt. Sucesso: signa cookie + 302 → primeiro tenant. Falha: 401 + mesma página + mensagem genérica. |
| GET | `/console/logout` | Limpa cookie + 302 → `/console/login`. |

### Cookie

- **Nome:** `pesdr_session`
- **Serializer:** `itsdangerous.URLSafeTimedSerializer(secret_key, salt="pesdr-console-v1")`
- **Payload:** `{"user_id": "<uuid>", "exp": <unix_ts>}`
- **Flags:** `HttpOnly=True`, `SameSite="Strict"`, `Secure=True` (toggle conditional em `APP_ENV=development`), `Max-Age=43200` (12h)
- **Renovação:** cookie reescrito em cada request bem-sucedido (sliding expiration de 12h após último request)

### Settings

- `CONSOLE_SECRET_KEY` (env var, 32+ chars random). Obrigatória quando alguma `tenant.yaml > console.enabled=true`. Validada no startup do app.
- Mudança de secret invalida todas as sessões ativas.

### Anti timing-attack

Erros de credencial retornam mesmo status + mesma página + mesma mensagem ("Usuário ou senha incorretos") independente de "usuário inexistente" vs "senha errada". bcrypt já é slow-by-design.

### CSRF

`SameSite=Strict` mitiga CSRF cross-site na maioria dos casos. Forms críticos (login, assign) podem opcionalmente carregar token CSRF via meta tag no `base.html` em P11b se preciso. v1 confia no SameSite.

---

## 7. RBAC

### Dep `require_console_user(request, db) → User`

1. Lê cookie `pesdr_session`. Ausente → 303 `/console/login`.
2. Decodifica + valida assinatura/expiração via `itsdangerous`. Inválido/expirado → 303 `/console/login`.
3. Carrega `User` por id. Inexistente → 303 `/console/login`.
4. Retorna `User`.

### Dep `require_tenant_access(tenant_slug, user) → tuple[Tenant, User]`

1. `require_console_user` validou user.
2. Carrega `Tenant` por slug. Inexistente → 404.
3. Carrega `ConsoleConfig` via `TenantLoader`. Se `console is None or not console.enabled` → 404 (silent disable).
4. Authorization check:
   - `user.is_platform_admin` → permite.
   - Senão, lookup em `user_tenant_access(user.id, tenant.id)`. Inexistente → 403.
5. `await set_tenant_context(db, tenant.id)` — RLS armada pro resto do request.
6. Retorna `(tenant, user)`.

### Routing pós-login

| Cenário | Comportamento |
|---|---|
| User tem 1 tenant | 302 → `/console/{slug}/leads` |
| User tem N>1 tenants | 302 → `/console/{first_slug}/leads`; header tem dropdown pra trocar de tenant |
| User tem 0 tenants e não é admin | 403 "Sem acesso a nenhum tenant. Contate o admin." |
| User é `is_platform_admin` | 302 → primeiro tenant existente; admin pode navegar pra qualquer `/console/{slug}/...` manualmente |

### Header do console (todas as páginas)

```
PeSDR Console │ tenant: [joana ▾] │ joana_assistente (operator) [Logout]
```

- Dropdown de tenant lista todos tenants acessíveis pelo user (todos do sistema se admin).
- Badge "admin" perto do nome quando is_platform_admin.

---

## 8. Routes + templates

### Routes (`src/ai_sdr/web/routes.py`)

| Método | URL | Template / Behavior |
|---|---|---|
| GET | `/console/login` | `login.html` (full page) |
| POST | `/console/login` | Validate + set cookie + 302 |
| GET | `/console/logout` | Clear cookie + 302 |
| GET | `/console/{slug}/leads` | `leads_list.html` (full page master-detail) |
| GET | `/console/{slug}/leads/list` | `_lead_card[]` partial (HTMX polling target, 10s) |
| GET | `/console/{slug}/leads/{lead_id}/detail` | `_lead_detail.html` partial (right panel) |
| POST | `/console/{slug}/leads/{lead_id}/assign` | Chama `runtime.create + enqueue` (mesmo helper do REST `/tenants/.../assign`). Retorna HTMX response que (a) atualiza master (lead some), (b) limpa detail panel. |

### Templates (`src/ai_sdr/web/templates/`)

- `base.html` — shell com header (tenant dropdown, username, logout), CSS link, HTMX script.
- `login.html` — form simples.
- `leads_list.html` — full page master-detail, estende `base.html`.
- `_lead_card.html` — partial: 1 lead no master (avatar/identifier, timestamp, msg count, preview).
- `_lead_detail.html` — partial: detail panel completo (header, queued messages, treeflow dropdown, assign button).
- `_empty_state.html` — partial: "Nenhum lead aguardando atribuição".

### Polish visual

Templates iniciam minimalistas durante implementação. **Task dedicada no plano invoca skill `frontend-design`** pra polir tipografia, espaçamento, cores e micro-interações antes da entrega. Layout master-detail (Q4 do brainstorm) e elementos por card são estruturais e fixos.

---

## 9. CLI: `ai-sdr users`

```bash
ai-sdr users add --username <str> [--admin] [--password <plain>]
    # Prompt interativo se --password ausente. bcrypt hash + INSERT.
    # --admin define is_platform_admin=true.

ai-sdr users grant --username <str> --tenant <slug> --role operator|tenant_admin
    # INSERT user_tenant_access. Falha se usuário ou tenant não existe.

ai-sdr users revoke --username <str> --tenant <slug>
    # DELETE user_tenant_access. No-op se não existe.

ai-sdr users passwd --username <str>
    # Prompt interativo nova senha. UPDATE users.password_hash.

ai-sdr users list [--tenant <slug>]
    # Sem --tenant: lista todos. Com --tenant: só users com acesso a esse tenant.

ai-sdr users set-admin --username <str> --admin true|false
    # UPDATE users.is_platform_admin.
```

Comandos batem DB direto via session async. Não vão por REST (operações administrativas, sem necessidade de endpoint público em v1).

---

## 10. Module layout

```
src/ai_sdr/
├── web/                                # NEW package
│   ├── __init__.py
│   ├── routes.py                       # APIRouter pras rotas /console/
│   ├── auth.py                         # cookie signer + require_console_user + require_tenant_access deps
│   ├── login.py                        # Login/logout handlers (separado p/ clareza)
│   ├── deps.py                         # tenant_loader_dep, etc.
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── leads_list.html
│       ├── _lead_card.html
│       ├── _lead_detail.html
│       └── _empty_state.html
│
├── models/
│   ├── user.py                         # NEW: User ORM
│   ├── user_tenant_access.py           # NEW: UserTenantAccess ORM
│   └── __init__.py                     # MODIFIED: re-export
│
├── schemas/
│   └── tenant_yaml.py                  # MODIFIED: ConsoleConfig
│
├── cli/
│   ├── users.py                        # NEW: ai-sdr users {add,grant,revoke,passwd,list,set-admin}
│   └── app.py                          # MODIFIED: register users_app
│
└── main.py                             # MODIFIED: include console_router; validate CONSOLE_SECRET_KEY at startup

migrations/versions/
└── 0009_users_and_access.py            # NEW

src/ai_sdr/settings.py                  # MODIFIED: add console_secret_key field
pyproject.toml                          # MODIFIED: add bcrypt, jinja2 (verify)
```

---

## 11. Testing

### Unit
- `tests/unit/test_console_auth.py` — cookie signing/verification, expiração, tampering.
- `tests/unit/test_console_config_schema.py` — `ConsoleConfig.enabled=false` (default) → console desabilitado.
- `tests/unit/test_users_cli.py` — typer commands (add/grant/etc.) com DB mockada.

### Integration (VPS)
- `tests/integration/test_users_models.py` — Users + UserTenantAccess CRUD, unique username constraint, FK cascades.
- `tests/integration/test_console_login.py` — login flow: form GET, POST com credencial correta/errada, cookie issued/rejected.
- `tests/integration/test_console_rbac.py` — operator-acessa-só-seu-tenant, admin-acessa-tudo, tenant_admin igual a operator em v1, console.enabled=false → 404.
- `tests/integration/test_console_leads.py` — list pending, polling endpoint retorna HTML válido, assign POST executa fluxo + retorna HTMX swap correto.
- `tests/integration/test_users_cli_integration.py` — CLI commands batem no DB real.

### Live / smoke
- Acessar via browser, fazer login, atribuir 1 lead, ver lista atualizar via polling. Manual.

---

## 12. Future: Chat-on-PeSDR

Esta seção é uma **nota de intenção arquitetural**, não escopo de implementação.

Direção declarada: Vialum Chat será eventualmente re-arquitetado em cima da fundação PeSDR (RLS forçada, adapter pattern, stack Python+SQLAlchemy, KB/guardrails formalizados). Isso significa que P11 v1 deve servir como **template de qualidade** pra qualquer UI futura no ecossistema:

- Convenções de rota (`/console/{slug}/...`)
- Patterns de auth (cookie session signed, users globais, user_tenant_access)
- Estrutura de templates (server-rendered, HTMX partials)
- RBAC no app layer (não em RLS)
- Separação rigorosa de tabelas globais (users) vs tenant-scoped (leads, talkflows)

Não cria trabalho extra. Apenas codifica a intenção de que P11 v1 não é throwaway — vai ser referência implícita pra plano "Chat-on-PeSDR" no futuro.

---

## 13. Hooks pra planos futuros

| Plano | Hook |
|---|---|
| **P11b — Conversation viewer** | Adiciona `GET /console/{slug}/leads/{id}/conversation` que renderiza histórico do talkflow (lê do LangGraph checkpointer). Sem mudanças no schema. |
| **P11c — Cross-tenant admin view** | Adiciona `/console/admin/conversations` que admin acessa. Schema (`is_platform_admin`) já permite. |
| **P11d — Manual takeover** | Pausa o agente pra um talkflow, abre interface de chat manual. Toca em `_handle_exhausted` hook e na fila de outbound. |
| **P11e — User management UI** | UI pros comandos da CLI (`/console/admin/users`). |
| **P12 — Production polish** | Rate limit em /console/login, password reset por email, MFA opcional, audit log de logins. |
| **Plano Vialum-integration** | Tenants Vialum setam `console.enabled: false` em `tenant.yaml`. Vialum Tasks Inbox vira surface de assignment. P11 fica oculto pra eles. |
| **Plano Chat-on-PeSDR (futuro distante)** | Vialum Chat é re-arquitetado usando padrões estabelecidos por P11. |

---

## 14. Open questions

Nenhuma.

Decisões marginais deferred:
- CSS approach final (Pico.css / Tailwind / vanilla / outra). `frontend-design` skill decide no momento do polish.
- Tenant picker UX exato (dropdown no header vs. dedicated route). Implementação decide; recomendação: dropdown.
- Session storage server-side (Redis) — Plano 12 se necessário.

---

**Fim do spec.**
