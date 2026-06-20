from __future__ import annotations

import pytest

from ai_sdr.voice.renderer import decide_modality


@pytest.mark.parametrize(
    "mode,fmt,last_in,expected",
    [
        ("never", "voice", "audio", "text"),
        ("always", None, "text", "voice"),
        ("match_lead", None, "audio", "voice"),
        ("match_lead", None, "text", "text"),
        ("context_driven", "voice", "text", "voice"),
        ("context_driven", "both", "text", "voice"),
        ("context_driven", "text", "audio", "text"),
        ("context_driven", None, "audio", "text"),
    ],
)
def test_decide_modality_matrix(mode, fmt, last_in, expected):
    assert decide_modality(mode, fmt, last_in) == expected
