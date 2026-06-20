"""Outbound modality renderer — replaces the voice fallback slot in
flowengine.sender. decide_modality picks text vs voice per tenant policy;
render_and_send (Task 9) performs the synthesis + send.
"""

from __future__ import annotations

from typing import Literal


def decide_modality(
    response_mode: str,
    response_format: str | None,
    last_inbound_media_type: str,
) -> Literal["text", "voice"]:
    if response_mode == "always":
        return "voice"
    if response_mode == "never":
        return "text"
    if response_mode == "match_lead":
        return "voice" if last_inbound_media_type == "audio" else "text"
    if response_mode == "context_driven":
        return "voice" if response_format in ("voice", "both") else "text"
    return "text"
