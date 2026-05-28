"""Objection classifier — runs Haiku to detect whether the lead's latest
message raised one of the declared objections (Plan 4a, spec §4.4)."""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection


class ClassifierResult(BaseModel):
    """Structured output the classifier LLM returns."""

    objection_id: str | None = None  # None = no objection detected
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = ""  # the portion of the lead message that triggered the match


_SYSTEM_TEMPLATE = """Você é um classificador de objeções de vendas em PT-BR.

Sua tarefa: ler a conversa abaixo e identificar se a última mensagem do lead
levantou alguma das objeções listadas. Retorne `objection_id` igual ao id da
objeção detectada, ou `null` se nenhuma se aplica.

Objeções permitidas:
{objections_block}

{previously_handled_block}

Regras:
- Retorne SEMPRE um `objection_id` exatamente igual a um dos ids acima, ou `null`.
- `confidence` em [0,1] — quão certo você está. Use < 0.6 se houver dúvida real.
- `quote` é o trecho exato da última mensagem do lead que disparou a detecção.
- Se o lead apenas mencionou tema relacionado sem objeção real, retorne null.
- Se a mensagem do lead estiver vazia / só emoji / saudação, retorne null.
"""


def _format_objections(objections: list[NodeObjection | GlobalObjection]) -> str:
    lines = []
    for o in objections:
        lines.append(f"- id: {o.id}\n  description: {o.description}")
    return "\n".join(lines)


def _format_previously_handled(ids: list[str]) -> str:
    if not ids:
        return ""
    return (
        "Objeções já tratadas nesta conversa (sinalize de novo SÓ se o lead "
        "estiver claramente insistindo): " + ", ".join(ids)
    )


async def classify(
    *,
    llm: BaseChatModel,
    objections: list[NodeObjection | GlobalObjection],
    conversation: list[BaseMessage],
    previously_handled: list[str],
    history_window: int,
    trace_metadata: dict[str, Any] | None = None,
) -> ClassifierResult:
    """Single LLM call. Returns ClassifierResult(objection_id=None) if list empty.

    Raises whatever the LLM raises — callers are expected to catch and degrade.

    `trace_metadata`, when provided, is attached to the underlying
    ``ainvoke`` call as ``config={"metadata": ...}`` so LangSmith can
    filter/group sub-traces by tenant/talkflow/lead/node + trace_origin.
    """
    if not objections:
        return ClassifierResult(objection_id=None, confidence=0.0)

    system_text = _SYSTEM_TEMPLATE.format(
        objections_block=_format_objections(objections),
        previously_handled_block=_format_previously_handled(previously_handled),
    )

    history = [m for m in conversation if isinstance(m, HumanMessage | AIMessage)]
    history = history[-history_window:]

    messages: list[BaseMessage] = [SystemMessage(content=system_text), *history]
    structured = llm.with_structured_output(ClassifierResult)
    if trace_metadata:
        return cast(
            ClassifierResult,
            await structured.ainvoke(messages, config={"metadata": trace_metadata}),
        )
    return cast(ClassifierResult, await structured.ainvoke(messages))
