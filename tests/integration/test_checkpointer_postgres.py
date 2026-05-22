import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from ai_sdr.treeflow.checkpointer import (
    checkpointer_from_settings,
    ensure_checkpointer_schema,
)


class S(TypedDict, total=False):
    count: int


@pytest.mark.integration
async def test_checkpointer_persists_state_across_invocations() -> None:
    await ensure_checkpointer_schema()

    async def bump(state: S) -> S:
        return {"count": (state.get("count") or 0) + 1}

    sg: StateGraph = StateGraph(S)
    sg.add_node("bump", bump)
    sg.add_edge(START, "bump")
    sg.add_edge("bump", END)

    async with checkpointer_from_settings() as saver:
        graph = sg.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "test-thread-checkpoint-roundtrip"}}

        out1 = await graph.ainvoke({"count": 0}, config=cfg)
        assert out1["count"] == 1

        # invoke again with same thread_id — state from checkpoint persists
        out2 = await graph.ainvoke({}, config=cfg)
        assert out2["count"] == 2

        # different thread starts fresh
        out_other = await graph.ainvoke(
            {"count": 0},
            config={"configurable": {"thread_id": "other"}},
        )
        assert out_other["count"] == 1
