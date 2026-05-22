from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from ai_sdr.llm.extractor import build_structured_model, extract
from ai_sdr.schemas.treeflow_yaml import CollectField


def test_build_model_has_response_text_and_fields() -> None:
    collects = [
        CollectField(field="faturamento", type="number", required=True),
        CollectField(field="email", type="email"),
    ]
    model = build_structured_model(collects)
    fields = set(model.model_fields.keys())
    assert fields == {"response_text", "faturamento", "email"}

    # all extracted fields are Optional regardless of `required`
    # (the gate is exit_condition, not pydantic)
    instance = model(response_text="oi")
    assert instance.faturamento is None
    assert instance.email is None
    assert instance.response_text == "oi"


def test_build_model_rejects_field_named_response_text() -> None:
    collects = [CollectField(field="response_text", type="text")]
    with pytest.raises(ValueError, match="reserved"):
        build_structured_model(collects)


async def test_extract_with_stub_llm_returns_typed_object() -> None:
    collects = [
        CollectField(field="faturamento", type="number"),
        CollectField(field="cidade", type="text"),
    ]
    expected: dict[str, Any] = {
        "response_text": "Legal! Anotei R$ 50000 e Curitiba.",
        "faturamento": 50000,
        "cidade": "Curitiba",
    }

    class StubLLM:
        def with_structured_output(self, model: Any) -> Any:
            return RunnableLambda(lambda _msgs: model.model_validate(expected))

    model = build_structured_model(collects)
    result = await extract(
        StubLLM(),  # type: ignore[arg-type]
        model,
        messages=[SystemMessage(content="you are a SDR"), HumanMessage(content="50000, curitiba")],
    )
    assert result.response_text.startswith("Legal!")
    assert result.faturamento == 50000
    assert result.cidade == "Curitiba"


async def test_extract_smoke_with_fake_list_chat_model_does_not_crash() -> None:
    """Guards that FakeListChatModel has with_structured_output bound."""
    fake = FakeListChatModel(responses=["irrelevant"])
    assert hasattr(fake, "with_structured_output")
