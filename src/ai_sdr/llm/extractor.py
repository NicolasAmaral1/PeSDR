"""Dynamic Pydantic model + structured-output runner for a Node's `collects` list."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, create_model

from ai_sdr.schemas.treeflow_yaml import CollectField

RESPONSE_FIELD = "response_text"

_PY_TYPE: dict[str, type] = {
    "text": str,
    "number": float,
    "boolean": bool,
    "email": str,
    "phone": str,
}


def build_structured_model(collects: list[CollectField]) -> type[BaseModel]:
    """Create a Pydantic model: { response_text: str, <each collected field as Optional> }."""
    field_defs: dict[str, Any] = {
        RESPONSE_FIELD: (str, Field(description="What the agent says to the lead next.")),
    }

    for c in collects:
        if c.field == RESPONSE_FIELD:
            raise ValueError(f"{RESPONSE_FIELD!r} is a reserved collect-field name")
        py_type = _PY_TYPE[c.type]
        description = c.extraction_hint or f"Extracted {c.type} field {c.field!r}."
        field_defs[c.field] = (py_type | None, Field(default=None, description=description))

    return create_model("NodeOutput", **field_defs)


async def extract(
    llm: BaseChatModel,
    model: type[BaseModel],
    messages: list[BaseMessage],
) -> BaseModel:
    """Bind the model as structured output and invoke against `messages` (async)."""
    runnable = llm.with_structured_output(model)
    result = await runnable.ainvoke(messages)
    if isinstance(result, dict):
        # langchain's with_structured_output can return a dict when include_raw=False
        # and the underlying impl uses JSON mode — normalize to the pydantic model.
        return model.model_validate(result)
    return result
