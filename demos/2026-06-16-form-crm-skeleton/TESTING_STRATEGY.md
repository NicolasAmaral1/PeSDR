# Testing Strategy

> Como testar cada uma das 3 fases da spec. Padrão: TDD por task — teste failing primeiro, impl mínima, refactor.

## Camadas de teste

| Camada | Onde | Quando | Mock vs Real |
|---|---|---|---|
| **Unit** | `tests/unit/` | Por task A/B (rápidos) | HTTP mockado, DB mockado quando viável (Pydantic, parsers, classifiers) |
| **Integration** | `tests/integration/` | Por task quando toca DB/Redis | Postgres + Redis reais (docker-compose), HTTP mockado (`respx`) |
| **Live** | `tests/integration/test_*_live.py` ou `tests/live/` | Smoke pré-deploy | API real (RD Station sandbox, Anthropic real). Gated por env var. |

## Fase A — Form ingestion (Respondi)

### Unit tests (~7 arquivos)

| Arquivo | Cobre | Mocks |
|---|---|---|
| `test_form_provider_base.py` | `FormProviderAdapter` ABC contract, `IngestedFormSubmission` dataclass, `LeadIdentifier` validators | — |
| `test_form_registry_and_factory.py` | `@register` decorator (dup-name falha), `build_form_adapter` (unknown provider) | — |
| `test_form_errors.py` | Hierarchy de exceptions | — |
| `test_form_respondi_adapter.py` | `RespondiFormAdapter.handle_submission` — parsing, phone normalization, signature validation, malformed payload | Pydantic only |
| `test_form_ingest.py` | `find_or_create_lead_by_form` — find existing lead OR create new, `create_talk_with_state` | DB mockado via SQLAlchemy AsyncSession mock |
| `test_tenant_yaml_forms_config.py` | `FormProviderConfig`, `ProactiveFirstMessageConfig` Pydantic validation | — |
| `test_phone_normalization.py` | Helpers de normalização E.164 com `phonenumbers` | — |

### Integration tests (~3 arquivos)

| Arquivo | Cobre | Setup |
|---|---|---|
| `test_inbound_form_submissions_rls.py` | RLS isola cross-tenant, UNIQUE constraint, ON CONFLICT DO NOTHING | docker-compose up |
| `test_form_webhook_route.py` | POST `/webhooks/{slug}/form/{provider}` — 200 happy, 404 unknown tenant, 401 bad secret, 200 dedup | TestClient |
| `test_process_form_inbound_worker.py` | Worker job E2E: submission → Talk criada → HSM enviado (via `FakeMessagingAdapter`) | arq worker + Fake messaging |

### Fixtures (`tests/fixtures/respondi/`)

- `submission_text_form.json` — payload típico (form simples, raw_answers com question_id + question_type + answer)
- `submission_with_utms.json` — payload com `respondent_utms` populado
- `submission_invalid_phone.json` — phone que falha normalização

### Critério de pronto pra mergear Fase A

- [ ] Todos unit tests passam (`make test-unit`)
- [ ] Todos integration tests passam (`make test-integration` com docker-compose up)
- [ ] CLAUDE.md tem seção "Form ingestion (Plano 7a)" com URL shape, config, troubleshooting
- [ ] Manual smoke: submeter payload Respondi real via curl → Lead criado → ver em DB

## Fase B — CRM action adapter (RD Station)

### Unit tests (~8 arquivos)

| Arquivo | Cobre | Mocks |
|---|---|---|
| `test_crm_canonical_models.py` | `ContactCanonical`, `DealCanonical`, `DealStage` Pydantic | — |
| `test_crm_backend_registry.py` | `@register_crm_backend`, dup-name falha, unknown levanta | — |
| `test_crm_action_adapter.py` | `CRMActionAdapter` despacha pro backend correto baseado em handler | Backend mockado |
| `test_rdstation_oauth.py` | Token cache, refresh quando expira, refresh falha → `AuthError` | `respx` mock HTTP |
| `test_rdstation_client.py` | HTTP layer, tenacity retry, error classification (401/403/422/429/5xx) | `respx` |
| `test_rdstation_backend_contact.py` | 3 cenários create_or_update_contact (já em refs / encontra remoto / cria) | `respx` + DB mock |
| `test_rdstation_backend_deal.py` | 2 cenários create_or_update_deal | `respx` + DB mock |
| `test_tenant_yaml_crm_config.py` | `CRMConfig`, `RDStationCRMConfig` Pydantic | — |

### Integration tests (~4 arquivos)

| Arquivo | Cobre | Setup |
|---|---|---|
| `test_crm_action_dispatch_e2e.py` | TreeFlow node com `on_collected: crm` → dispatcher INSERT → worker execute → Lead.crm_refs atualizado | docker-compose, RD Station mockado |
| `test_lead_crm_refs_concurrency.py` | 2 actions concorrentes pro mesmo lead não corrompem refs (advisory lock) | docker-compose |
| `test_rdstation_oauth_refresh_e2e.py` | Token expirado → backend refresh transparente → retry mesmo job | docker-compose |
| `test_rdstation_token_rotation_alert.py` | Refresh retorna novo refresh_token → log alert + worker fail terminal | docker-compose |

### Live tests (~1 arquivo, gated)

| Arquivo | Cobre | Como rodar |
|---|---|---|
| `test_rdstation_smoke.py` | Hit RD Station sandbox real: create contact + update + create deal + verify in panel | `LIVE_RDSTATION=1 pytest tests/integration/test_rdstation_smoke.py` |

### Fixtures (`tests/fixtures/rdstation/`)

- `create_contact_response.json` — resposta sucesso da API
- `create_deal_response.json`
- `oauth_token_response.json`
- `oauth_refresh_response.json` (com novo refresh_token rotacionado)
- `error_401_token_expired.json`
- `error_429_rate_limit.json`
- `error_422_invalid_phone.json`

### Critério de pronto pra mergear Fase B

- [ ] Todos unit/integration passam
- [ ] Live smoke roda (com sandbox) e dá verde
- [ ] CLAUDE.md tem seção "CRM (Plano 7b)" com OAuth setup, troubleshooting, rotação de refresh_token

## Fase C — Wiring na Manoela

### Smoke test manual E2E

**Pré-requisitos:**
- Fase A + B mergeadas em `main`
- Tenant `manoela-mentora` registrado no DB
- Secrets cifrados em `tenants/manoela-mentora/secrets.enc.yaml`
- Template HSM `saudacao_proativa_v1` aprovado no Meta
- App RD Station criado, refresh_token via `scripts/oauth_flow_init.py`
- Pipeline RD Station configurado, stage IDs anotados no tenant.yaml
- Form Respondi configurado, webhook URL = `https://sdr.luminai.ia.br/webhooks/manoela-mentora/form/respondi?secret=<...>`
- Docker compose subido em VPS, worker rodando

**Roteiro do smoke:**

1. Preencher form Respondi com dados de teste (telefone real do Pedro ou Lana pra simular)
2. Verificar nos logs: `form.submission.parsed`, `form.lead.created`, `form.proactive_sent`
3. Receber mensagem HSM no WhatsApp do número de teste
4. Responder mensagem
5. Verificar nos logs: `process_lead_inbox`, `run_turn`, `action.enqueued`, `action.crm.executed`
6. Conferir em `painel.rdstation.com`: contact criado, deal criado vinculado, qualification_notes preenchidas
7. Conferir em `localhost:8200/console/manoela-mentora/leads`: Lead aparece com link pro Talk

**Não-bloqueante (anotar issues e abrir bugs):**
- LLM responde estranhamente porque `collected` veio pré-populado (pode precisar prompt tuning)
- Custom field não bate ID exato (ajustar `custom_field_mapping`)
- Token de RD Station expira durante teste (recarregar manual + investigar refresh)

### Critério de pronto pra "produção piloto"

- [ ] Smoke E2E manual passa com lead de teste
- [ ] Lana consegue operar: ver Lead pendente no console, atribuir Talk, ver histórico de actions executadas
- [ ] 1 semana de operação real sem incidente crítico (LGPD, runaway cost, erro silencioso)

## Cobertura mínima esperada

- Unit: > 80% pra cada módulo novo
- Integration: cada path do fluxo E2E coberto pelo menos 1x
- Smoke: manual, sem expectativa de cobertura % (validação humana)

`make test-unit` no CI; integration roda em pre-merge manual ou em runner mais pesado.
