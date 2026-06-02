"""Verifies migration 0022 added media fields to inbound_messages."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_inbound_messages_has_media_fields(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'inbound_messages'
              AND column_name IN (
                  'media_type', 'media_storage_key', 'audio_url',
                  'transcription', 'transcription_confidence',
                  'transcription_provider'
              )
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols == {
        "media_type", "media_storage_key", "audio_url",
        "transcription", "transcription_confidence", "transcription_provider",
    }
