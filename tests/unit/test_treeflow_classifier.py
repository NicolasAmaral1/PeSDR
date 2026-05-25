"""Unit tests for the Haiku-backed objection classifier (Plan 4a, spec §4.4)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection
from ai_sdr.treeflow.classifier import ClassifierResult, classify


class _StubLLM:
    """Minimal stand-in for BaseChatModel — captures call and returns a canned result."""

    def __init__(self, canned: ClassifierResult | Exception):
        self._canned = canned
        self.calls: list[Any] = []

    def with_structured_output(self, schema: Any) -> _StubLLM:  # noqa: ARG002
        return self

    async def ainvoke(self, messages: list[Any]) -> ClassifierResult:
        self.calls.append(messages)
        if isinstance(self._canned, Exception):
            raise self._canned
        return self._canned


@pytest.mark.asyncio
async def test_classify_empty_list_returns_none_without_calling_llm():
    llm = _StubLLM(ClassifierResult(objection_id="preco", confidence=0.9, quote="x"))
    result = await classify(
        llm=llm,
        objections=[],
        conversation=[HumanMessage(content="tá caro")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id is None
    assert result.confidence == 0.0
    assert llm.calls == []  # LLM never called


@pytest.mark.asyncio
async def test_classify_returns_llm_result():
    expected = ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")
    llm = _StubLLM(expected)
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    result = await classify(
        llm=llm,
        objections=[obj],
        conversation=[HumanMessage(content="tá caro")],
        previously_handled=[],
        history_window=4,
    )
    assert result == expected
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_classify_truncates_conversation_to_history_window():
    llm = _StubLLM(ClassifierResult(objection_id=None, confidence=0.0))
    conv = [
        HumanMessage(content="m1"),
        AIMessage(content="m2"),
        HumanMessage(content="m3"),
        AIMessage(content="m4"),
        HumanMessage(content="m5"),
        AIMessage(content="m6"),
    ]
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    await classify(
        llm=llm,
        objections=[obj],
        conversation=conv,
        previously_handled=[],
        history_window=3,
    )
    # The 1st call's message list ends with the last 3 conversation messages
    # (after the system message)
    sent_messages = llm.calls[0]
    human_or_ai = [m for m in sent_messages if isinstance(m, HumanMessage | AIMessage)]
    assert len(human_or_ai) == 3
    assert human_or_ai[-1].content == "m6"


@pytest.mark.asyncio
async def test_classify_propagates_llm_exception():
    llm = _StubLLM(RuntimeError("rate limit"))
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        await classify(
            llm=llm,
            objections=[obj],
            conversation=[HumanMessage(content="tá caro")],
            previously_handled=[],
            history_window=4,
        )


@pytest.mark.asyncio
async def test_classify_includes_previously_handled_in_context():
    """When previously_handled is non-empty, the prompt mentions those ids."""
    llm = _StubLLM(ClassifierResult(objection_id=None, confidence=0.0))
    obj = GlobalObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    await classify(
        llm=llm,
        objections=[obj],
        conversation=[HumanMessage(content="oi")],
        previously_handled=["preco", "falta_tempo"],
        history_window=4,
    )
    system_text = llm.calls[0][0].content
    assert isinstance(system_text, str)
    assert "preco" in system_text
    assert "falta_tempo" in system_text


def test_classifier_result_validates():
    with pytest.raises(ValidationError):
        ClassifierResult(objection_id="x", confidence=1.5)  # > 1
    with pytest.raises(ValidationError):
        ClassifierResult(objection_id="x", confidence=-0.1)
    ok = ClassifierResult(objection_id=None, confidence=0.0)
    assert ok.quote == ""
