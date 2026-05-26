import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

TENANT_YAML_TEMPLATE = """\
id: {slug}
display_name: Live Test
timezone: America/Sao_Paulo
llm:
  default:
    provider: {provider}
    model: {model}
    api_key_ref: secrets/{secret_name}
"""

DEMO_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: ask
nodes:
  - id: ask
    prompt: |
      Você é uma SDR em PT-BR. Em UMA pergunta curta, pergunte ao lead qual é
      o faturamento mensal aproximado da empresa dele, em reais. Se ele já
      respondeu com um número, agradeça em uma frase e siga.
    collects:
      - field: faturamento
        type: number
        extraction_hint: "número em R$"
        required: true
    exit_condition:
      type: rule_expression
      expression: "faturamento != None"
    next_nodes:
      - condition: "true"
        target: END
"""


def _make_fixture(tmp_path: Path, slug: str, provider: str, model: str, secret_name: str) -> Path:
    base = tmp_path / "tenants" / slug
    (base / "treeflows").mkdir(parents=True)
    (base / "tenant.yaml").write_text(
        TENANT_YAML_TEMPLATE.format(
            slug=slug, provider=provider, model=model, secret_name=secret_name
        )
    )
    (base / "treeflows" / "demo.yaml").write_text(DEMO_YAML)
    return tmp_path / "tenants"


async def _run_e2e(tenants_dir: Path, slug: str, secret_name: str, api_key: str) -> None:
    await ensure_checkpointer_schema()
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=tenants_dir),
        treeflow_loader=TreeFlowLoader(tenants_dir=tenants_dir),
        sops_loader=SopsLoader(tenants_dir=tenants_dir),
        secrets_resolver=lambda _slug: {secret_name: api_key},
    )

    async with sm() as session:
        async with session.begin():
            t = Tenant(slug=slug, display_name="Live")
            session.add(t)
            await session.flush()
            await runtime.publish_version(session, t, "demo")
            tf = await runtime.create(
                session,
                t,
                lead_id=f"lead-{uuid.uuid4().hex[:6]}",
                treeflow_id="demo",
            )
        tf_id = tf.id
        tenant_slug = t.slug

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r1 = await runtime.step(session, t, tf_id, user_input="")
        assert r1.response_text.strip() != ""
        assert r1.current_node == "ask"

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r2 = await runtime.step(session, t, tf_id, user_input="faturo cerca de 50 mil por mês")
        assert r2.collected.get("faturamento") is not None
        f = float(r2.collected["faturamento"])
        assert 40_000 <= f <= 60_000, f"unexpected faturamento extraction: {f}"
        assert r2.completed is True

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.live_llm
async def test_live_anthropic(tmp_path: Path) -> None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    slug = f"livetest-{uuid.uuid4().hex[:6]}"
    tenants_dir = _make_fixture(tmp_path, slug, "anthropic", "claude-haiku-4-5", "anthropic_key")
    await _run_e2e(tenants_dir, slug, "anthropic_key", key)


@pytest.mark.integration
@pytest.mark.live_llm
async def test_live_openai(tmp_path: Path) -> None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    slug = f"livetest-{uuid.uuid4().hex[:6]}"
    tenants_dir = _make_fixture(tmp_path, slug, "openai", "gpt-4o-mini", "openai_key")
    await _run_e2e(tenants_dir, slug, "openai_key", key)
