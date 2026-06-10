"""Dynamic Pydantic model + structured-output runner for a Node's `collects` list."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, create_model

from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.schemas.treeflow_yaml import CollectField

RESPONSE_FIELD = "response_text"

_PY_TYPE: dict[str, type] = {
    "text": str,
    "number": float,
    "boolean": bool,
    "email": str,
    "phone": str,
}

_GUARDRAIL_RESERVED = {"prices_mentioned", "products_mentioned"}


def build_structured_model(
    collects: list[CollectField],
    guardrails: GuardrailsConfig | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model: { response_text, <collects>, [prices/products_mentioned] }."""

    field_defs: dict[str, Any] = {
        RESPONSE_FIELD: (str, Field(description="What the agent says to the lead next.")),
    }

    guardrails_active = guardrails is not None and guardrails.enabled
    reserved = {RESPONSE_FIELD} | (_GUARDRAIL_RESERVED if guardrails_active else set())

    for c in collects:
        if c.field in reserved:
            raise ValueError(f"{c.field!r} is a reserved collect-field name")
        py_type = _PY_TYPE[c.type]
        description = c.extraction_hint or f"Extracted {c.type} field {c.field!r}."
        field_defs[c.field] = (py_type | None, Field(default=None, description=description))

    if guardrails_active:
        field_defs["prices_mentioned"] = (
            list[int],
            Field(
                default_factory=list,
                description=(
                    "Lista TODOS os valores monetários (em reais, como int) "
                    "que você mencionou textualmente em response_text. "
                    "Exemplo: se você escreveu 'a Mentoria custa R$ 6.000', "
                    "retorne [6000]. Vazio se nenhum valor mencionado."
                ),
            ),
        )
        field_defs["products_mentioned"] = (
            list[str],
            Field(
                default_factory=list,
                description=(
                    "Lista TODOS os nomes de produtos que você mencionou em "
                    "response_text. Exemplo: ['Mentoria', 'Aceleradora']. "
                    "Vazio se nenhum produto mencionado."
                ),
            ),
        )

    return create_model("NodeOutput", **field_defs)


async def extract(
    llm: BaseChatModel,
    model: type[BaseModel],
    messages: list[BaseMessage],
    *,
    trace_metadata: dict[str, Any] | None = None,
) -> BaseModel:
    """Bind the model as structured output and invoke against `messages` (async).

    `trace_metadata`, when provided, is attached to the underlying
    ``ainvoke`` call as ``config={"metadata": ...}`` so LangSmith can
    filter sub-traces by tenant/talkflow/lead/node + trace_origin.
    """
    runnable = llm.with_structured_output(model)
    if trace_metadata:
        result = await runnable.ainvoke(messages, config={"metadata": trace_metadata})
    else:
        result = await runnable.ainvoke(messages)
    if isinstance(result, dict):
        # langchain's with_structured_output can return a dict when include_raw=False
        # and the underlying impl uses JSON mode — normalize to the pydantic model.
        return model.model_validate(result)
    return result
