# Estratégia de Testes — Skeleton

Pasta `tests/` vazia de propósito neste skeleton. A criação dos arquivos de teste é parte do plan executável (Fase A T1-T10, Fase B T1-T12, Fase C T5).

Detalhamento completo em [TESTING_STRATEGY.md](../TESTING_STRATEGY.md).

## Resumo

Quando aprovar e migrar pra implementação real:

### `tests/unit/` (~15 arquivos)

Fase A (Form ingestion):
- `test_form_provider_base.py`
- `test_form_respondi_adapter.py`
- `test_form_registry_and_factory.py`
- `test_form_ingest.py`
- `test_form_errors.py`
- `test_tenant_yaml_forms_config.py`
- `test_phone_normalization.py`

Fase B (CRM):
- `test_crm_canonical_models.py`
- `test_crm_action_adapter.py`
- `test_crm_backend_registry.py`
- `test_rdstation_oauth.py`
- `test_rdstation_client.py`
- `test_rdstation_backend_contact.py`
- `test_rdstation_backend_deal.py`
- `test_tenant_yaml_crm_config.py`

### `tests/integration/` (~7 arquivos)

- `test_inbound_form_submissions_rls.py`
- `test_form_webhook_route.py`
- `test_process_form_inbound_worker.py`
- `test_crm_action_dispatch_e2e.py`
- `test_lead_crm_refs_concurrency.py`
- `test_rdstation_oauth_refresh_e2e.py`
- `test_rdstation_token_rotation_alert.py`

### `tests/integration/test_*_live.py` (~1 arquivo, gated)

- `test_rdstation_smoke.py` (gated por `LIVE_RDSTATION=1`)

### `tests/fixtures/` (~6 arquivos JSON)

- `respondi/submission_text_form.json`
- `respondi/submission_with_utms.json`
- `respondi/submission_invalid_phone.json`
- `rdstation/create_contact_response.json`
- `rdstation/create_deal_response.json`
- `rdstation/oauth_token_response.json`
- `rdstation/oauth_refresh_response.json` (com refresh_token rotacionado)
- `rdstation/error_*.json` (401, 422, 429)
