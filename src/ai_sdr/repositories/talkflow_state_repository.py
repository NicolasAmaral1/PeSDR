"""TalkFlowStateRepository — load + initialize + message append.

FE-01a ships the minimum FE-01b needs to read state, seed it on new Talk,
and grow the rolling message window. Heavier mutations (treatment
lifecycle, objection history append) live in feature-specific modules.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.state import Message
from ai_sdr.models.talkflow_state import TalkFlowState


class TalkFlowStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, talk_id: uuid.UUID) -> TalkFlowState | None:
        stmt = select(TalkFlowState).where(TalkFlowState.talk_id == talk_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def initialize(
        self,
        *,
        talk_id: uuid.UUID,
        tenant_id: uuid.UUID,
        entry_node: str,
    ) -> TalkFlowState:
        """Create the runtime state row for a freshly opened Talk."""
        state = TalkFlowState(
            talk_id=talk_id,
            tenant_id=tenant_id,
            current_node=entry_node,
            collected={},
            extracted_facts={},
            messages=[],
            objections_handled=[],
            talkflow_stack=[],
        )
        self._session.add(state)
        return state

    async def append_message(
        self,
        state: TalkFlowState,
        message: Message,
        *,
        max_window: int,
    ) -> None:
        """Append a Message to the rolling window, evicting oldest as needed.

        The TalkFlowState.messages JSONB list is mutated in-place; we mark
        it modified so SQLAlchemy flushes the change.
        """
        if max_window < 1:
            raise ValueError("max_window must be >= 1")
        payload = message.model_dump(mode="json")
        # `state.messages` is the live JSONB list; treat as mutable.
        current = list(state.messages)
        current.append(payload)
        if len(current) > max_window:
            current = current[-max_window:]
        state.messages = current
        flag_modified(state, "messages")
