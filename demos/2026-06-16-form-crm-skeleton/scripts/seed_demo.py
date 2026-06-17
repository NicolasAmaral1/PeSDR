"""seed_demo.py — demonstração end-to-end mockada do fluxo Form + CRM.

Usado pra mostrar pro Nicolas / operadora COMO os pedaços se conectam, sem
bater em sistemas externos. Tudo é mockado:
- FormProviderAdapter recebe payload fixture (não webhook real)
- FakeMessagingAdapter "envia" HSM (só loga)
- LoggingActionAdapter "cria" contato/deal (só loga + retorna fake external_id)

Pré-requisitos:
- docker-compose up (postgres + redis)
- alembic upgrade head (com migration 0030 aplicada)

STUB — implementação real na Fase C T5 (smoke E2E demo).
"""
from __future__ import annotations

import asyncio
import sys


async def seed_and_demo() -> int:
    """Demonstra fluxo:

    T0  insert tenant 'manoela-demo' no DB
    T1  load tenant.yaml + qualificacao_inicial.yaml
    T2  simulate inbound form (payload fixture Respondi)
        ├─ adapter.handle_submission() → IngestedFormSubmission
        ├─ find_or_create_lead_by_form → Lead criado
        └─ INSERT inbound_form_submissions
    T3  trigger process_form_inbound (sync, não enfileira)
        ├─ create_talk_with_state → Talk active, collected pré-populado
        └─ FakeMessagingAdapter.send_template → "HSM enviado" loggado
    T4  simulate inbound WhatsApp message ("oi, sou a Maria")
        ├─ run_turn → LLM extrai nome (mas já tava no collected!)
        ├─ dispatch_actions → on_collected do node 'saudacao' dispara
        ├─ CRMActionAdapter resolve → LoggingActionAdapter (não RDStation real)
        ├─ LoggingActionAdapter.execute → fake_external_id retornado
        └─ Lead.crm_refs.rdstation.contact_id atualizado (em memória)
    T5  simulate inbound message ("R$ 40 mil")
        ├─ run_turn → LLM extrai faturamento_mensal
        ├─ on_collected do node 'qualificacao' → CRMActionAdapter → fake deal
        └─ Lead.crm_refs.rdstation.deal_id_mentoria atualizado
    T6  print estado final:
        - Lead.crm_refs final
        - Talk status final
        - action_executions rows criadas
    """
    print("=" * 70)
    print("seed_demo.py — STUB demonstrativo")
    print("=" * 70)
    print()
    print(
        "Implementação real na Fase C T5. Roteiro completo no docstring.",
    )
    print()
    print("Pra rodar agora (skeleton mode):")
    print("  1. make up && make migrate     # postgres + redis + schema")
    print("  2. Copie tenants/manoela-demo/ pra tenants/ real local")
    print(
        "  3. INSERT INTO tenants(slug, display_name) "
        "VALUES('manoela-demo', 'Manoela DEMO')",
    )
    print("  4. Aguarde Fase C concluir pra rodar este script de verdade")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed_and_demo()))
