"""critic_pass — second LLM (Haiku by default) reviews proposed response.

Spec §4.6. Returns Verdict; never blocks on its own — caller (run_with_guardrails)
decides what to do with passed=False.
"""

from __future__ import annotations

from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message

LLMFactory = Any  # mirrors compiler's LLMFactory typedef; loose to avoid import cycle


_CRITIC_SYSTEM_PROMPT = """\
Você é um revisor de qualidade de respostas de um SDR (assistente comercial via WhatsApp).
Recebe a RESPOSTA proposta pelo agente, o CONTEXTO FACTUAL recuperado da base de
conhecimento da empresa, e as REGRAS COMERCIAIS (valores e produtos permitidos).

Rejeite a resposta se ela:
1. Mencionar valor (R$) ou produto NÃO listado nas REGRAS COMERCIAIS
2. Fizer promessa não suportada pelo CONTEXTO FACTUAL (e.g. "garantia vitalícia"
   se essa garantia não consta na KB)
3. Inventar dado factual (data, prazo, condição) não citado no CONTEXTO FACTUAL

Caso contrário, aprove.

Retorne o Verdict estruturado: { passed: bool, reason: str|null, suggested_fix: str|null }.
Quando rejeitar, o suggested_fix deve ser uma mensagem CURTA e DIRETA pro agente
refazer a resposta corrigindo o problema específico.
"""


def _render_kb_block(kb_chunks: list[RetrievedChunk]) -> str:
    if not kb_chunks:
        return "(nenhum chunk recuperado)"
    parts = []
    for i, c in enumerate(kb_chunks, 1):
        header = f"[{i}] {c.heading_path or '(sem heading)'} (score {c.score:.2f}) [{c.kb_id}]"
        parts.append(f"{header}\n{c.content}")
    return "\n\n".join(parts)


def _render_history(history: list[Message], limit: int = 4) -> str:
    tail = history[-limit:]
    if not tail:
        return "(sem histórico)"
    return "\n".join(f"- {m['role']}: {m['content']}" for m in tail)


async def critic_pass(
    llm_factory: LLMFactory,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    response_text: str,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],
    guardrails: GuardrailsConfig,
    trace_metadata: dict[str, Any] | None = None,
) -> Verdict:
    """Run the critic. Uses tenant_llm.classifier (Haiku by design); raises
    ValueError if not configured.

    `trace_metadata`, when provided, is attached to the underlying
    ``ainvoke`` call as ``config={"metadata": ...}`` so LangSmith can
    filter sub-traces by tenant/talkflow/lead/node + trace_origin.
    """
    if tenant_llm.classifier is None:
        raise ValueError("guardrails critic pass requires tenant.llm.classifier to be configured")

    llm_cfg: LLMConfig = tenant_llm.classifier
    llm = llm_factory(llm_cfg, secrets, "guardrails_critic")
    runnable = llm.with_structured_output(Verdict)

    rules_yaml = yaml.safe_dump(
        {
            "allowed_prices": guardrails.allowed_prices,
            "allowed_products": guardrails.allowed_products,
        },
        allow_unicode=True,
    )
    user_block = (
        f"REGRAS COMERCIAIS:\n```yaml\n{rules_yaml}```\n\n"
        f"CONTEXTO FACTUAL:\n{_render_kb_block(kb_chunks)}\n\n"
        f"HISTÓRICO RECENTE:\n{_render_history(recent_history)}\n\n"
        f"RESPOSTA PROPOSTA:\n{response_text}"
    )

    messages = [
        SystemMessage(content=_CRITIC_SYSTEM_PROMPT),
        HumanMessage(content=user_block),
    ]
    if trace_metadata:
        result: Verdict = await runnable.ainvoke(messages, config={"metadata": trace_metadata})
    else:
        result = await runnable.ainvoke(messages)
    return result
