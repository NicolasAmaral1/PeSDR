import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import LLMConfig
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

DEMO_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: saudacao
nodes:
  - id: saudacao
    prompt: Cumprimente.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: qualificacao
  - id: qualificacao
    prompt: Pergunte faturamento.
    collects:
      - field: faturamento
        type: number
        required: true
    exit_condition:
      type: rule_expression
      expression: "faturamento != None"
    next_nodes:
      - condition: "faturamento >= 30000"
        target: premium
      - condition: "faturamento < 30000"
        target: basica
  - id: premium
    prompt: Oferta premium.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
  - id: basica
    prompt: Oferta básica.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
"""

TENANT_YAML_TEMPLATE = """\
id: {slug}
display_name: RT Test
timezone: America/Sao_Paulo
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
"""


def _write_tenant_fixture(tmp_path: Path, slug: str) -> Path:
    base = tmp_path / "tenants" / slug
    (base / "treeflows").mkdir(parents=True)
    (base / "tenant.yaml").write_text(TENANT_YAML_TEMPLATE.format(slug=slug))
    (base / "treeflows" / "demo.yaml").write_text(DEMO_YAML)
    return tmp_path / "tenants"


def _stub_factory(per_node_payloads: dict[str, dict[str, Any]]) -> Any:
    class _Stub:
        def __init__(self, nid: str) -> None:
            self._nid = nid

        def with_structured_output(self, model: type[BaseModel]) -> Any:
            return RunnableLambda(lambda _msgs: model.model_validate(per_node_payloads[self._nid]))

    def factory(cfg: LLMConfig, secrets: dict[str, str], current_node: str) -> BaseChatModel:
        return _Stub(current_node)  # type: ignore[return-value]

    return factory


@pytest.mark.integration
async def test_publish_create_step_end_to_end(tmp_path: Path) -> None:
    await ensure_checkpointer_schema()

    tenant_slug_base = f"rttest-{uuid.uuid4().hex[:6]}"
    fake_tenants = _write_tenant_fixture(tmp_path, tenant_slug_base)

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    sops = SopsLoader(tenants_dir=fake_tenants)
    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=fake_tenants),
        treeflow_loader=TreeFlowLoader(tenants_dir=fake_tenants),
        sops_loader=sops,
        secrets_resolver=lambda _slug: {"anthropic_key": "fake"},
        llm_factory=_stub_factory(
            {
                "saudacao": {"response_text": "Oi!"},
                "qualificacao": {"response_text": "50k anotado.", "faturamento": 50000},
                "premium": {"response_text": "Mentoria pra você."},
            }
        ),
    )

    async with sm() as session:
        async with session.begin():
            t = Tenant(slug=tenant_slug_base, display_name="RT")
            session.add(t)
            await session.flush()
            await runtime.publish_version(session, t, "demo")
            tf = await runtime.create(session, t, lead_id="lead-A", treeflow_id="demo")
        tf_id = tf.id
        tenant_slug = t.slug

    async with sm() as session:
        t = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one()
        r1 = await runtime.step(session, t, tf_id, user_input="")
        assert r1.response_text == "Oi!"
        assert r1.current_node == "qualificacao"
        assert r1.completed is False

    async with sm() as session:
        t = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one()
        r2 = await runtime.step(session, t, tf_id, user_input="faturo 50k")
        assert "anotado" in r2.response_text.lower() or "50k" in r2.response_text.lower()
        assert r2.collected.get("faturamento") == 50000
        assert r2.current_node == "premium"

    async with sm() as session:
        t = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one()
        r3 = await runtime.step(session, t, tf_id, user_input="manda")
        assert r3.completed is True
        assert r3.current_node == "END"

    await engine.dispose()
