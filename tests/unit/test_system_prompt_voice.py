from __future__ import annotations

from ai_sdr.flowengine.system_prompt import voice_mode_instruction


def test_no_instruction_for_non_context_modes():
    assert voice_mode_instruction("always") == ""
    assert voice_mode_instruction("never") == ""
    assert voice_mode_instruction("match_lead") == ""
    assert voice_mode_instruction(None) == ""


def test_context_driven_emits_response_format_guidance():
    text = voice_mode_instruction("context_driven")
    assert "response_format" in text
    assert "voice" in text
