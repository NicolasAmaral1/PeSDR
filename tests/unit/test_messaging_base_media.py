from __future__ import annotations

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.fake import FakeMessagingAdapter


def test_inbound_message_defaults_to_text_modality():
    m = InboundMessage(
        external_id="x", from_address="+1", text="hi",
        received_at_iso="2026-06-19T00:00:00+00:00", raw={},
    )
    assert m.media_type == "text"
    assert m.media_ref is None


async def test_fake_send_audio_records_payload():
    a = FakeMessagingAdapter()
    r = await a.send_audio("+5511999998888", b"OGG", "audio/ogg")
    assert r.external_id
    assert a.sent_audio == [{"to": "+5511999998888", "content_type": "audio/ogg", "n_bytes": 3}]


async def test_fake_download_media_returns_staged_blob():
    a = FakeMessagingAdapter()
    a.stage_media("media-123", b"VOICEBYTES", "audio/ogg")
    data, ct = await a.download_media("media-123")
    assert data == b"VOICEBYTES"
    assert ct == "audio/ogg"
