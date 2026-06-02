"""Verifies migration 0021 added media fields to outbound_messages."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_outbound_messages_has_media_fields(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'outbound_messages'
              AND column_name IN (
                  'media_type', 'media_storage_key', 'audio_url',
                  'audio_duration_ms', 'synthesis_voice_id',
                  'voice_emotion'
              )
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols == {
        "media_type", "media_storage_key", "audio_url",
        "audio_duration_ms", "synthesis_voice_id", "voice_emotion",
    }
