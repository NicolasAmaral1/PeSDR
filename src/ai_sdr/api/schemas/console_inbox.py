"""Response schemas for the contact-based operator inbox API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ContactState = Literal["ai", "requires_review", "human", "awaiting", "closed"]


class InstanceOut(BaseModel):
    id: uuid.UUID
    channel_label: str
    display_name: str | None
    phone_e164: str | None


class ContactOut(BaseModel):
    lead_id: uuid.UUID
    display_name: str | None
    whatsapp_e164: str | None
    last_message_at: datetime | None
    last_message_preview: str | None
    state: ContactState
    funnel_node: str | None
    unread: int


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: Literal["in", "out"]
    origin: Literal["lead", "ai", "operator"]
    text: str | None
    media_type: str
    audio_url: str | None = None
    transcription: str | None = None
    at: datetime


class ContactDetailOut(BaseModel):
    lead_id: uuid.UUID
    display_name: str | None
    whatsapp_e164: str | None
    state: ContactState
    funnel_node: str | None
    active_talk_id: uuid.UUID | None
    ai_reasoning: str | None
    window_open: bool
    window_expires_at: datetime | None


class ReadBody(BaseModel):
    last_read_message_at: datetime


class SendBody(BaseModel):
    text: str
    client_message_id: uuid.UUID


class TalkBandOut(BaseModel):
    talk_id: uuid.UUID
    status: str
    funnel_node: str | None
    created_at: datetime
