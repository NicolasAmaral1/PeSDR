"""humanizer.humanize() chunks + delays (FE-03b Task 6)."""

from __future__ import annotations

from ai_sdr.flowengine.humanizer import Chunk, HumanizationConfig, humanize


def _default_config(**overrides):
    return HumanizationConfig(**overrides)


def test_paragraph_split_yields_multiple_chunks():
    cfg = _default_config()
    text = "Olá!\n\nQue legal saber.\n\nQual seu segmento?"
    chunks = humanize(text, cfg)
    assert len(chunks) == 3
    assert chunks[0].text == "Olá!"
    assert chunks[1].text == "Que legal saber."
    assert chunks[2].text == "Qual seu segmento?"


def test_first_chunk_has_zero_delay():
    cfg = _default_config()
    chunks = humanize("Olá!\n\nMundo!", cfg)
    assert chunks[0].delay_before_ms == 0


def test_subsequent_chunks_have_delay_bounded():
    cfg = _default_config(min_delay_ms=500, max_delay_ms=2000)
    chunks = humanize("a\n\nb", cfg)
    assert chunks[1].delay_before_ms >= 500
    assert chunks[1].delay_before_ms <= 2000


def test_delay_proportional_to_next_chunk_length():
    cfg = _default_config(
        chars_per_second_min=10.0,
        chars_per_second_max=10.0,  # deterministic
        min_delay_ms=0,
        max_delay_ms=10_000,
    )
    chunks = humanize("a\n\n" + "x" * 100, cfg)
    # 100 chars at 10 chars/s = 10s = 10000ms
    assert 9500 <= chunks[1].delay_before_ms <= 10500


def test_voice_mode_returns_single_chunk_no_delay():
    cfg = _default_config(apply_to_voice=False)
    text = "Olá!\n\nMundo!"
    chunks = humanize(text, cfg, is_voice=True)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].delay_before_ms == 0


def test_voice_mode_apply_to_voice_still_chunks():
    cfg = _default_config(apply_to_voice=True)
    chunks = humanize("Olá!\n\nMundo!", cfg, is_voice=True)
    assert len(chunks) == 2


def test_disabled_returns_single_chunk():
    cfg = _default_config(enabled=False)
    chunks = humanize("Olá!\n\nMundo!", cfg)
    assert len(chunks) == 1
    assert chunks[0].text == "Olá!\n\nMundo!"


def test_no_delimiter_in_text_yields_single_chunk():
    cfg = _default_config()
    chunks = humanize("Tudo numa linha só.", cfg)
    assert len(chunks) == 1
    assert chunks[0].delay_before_ms == 0


def test_empty_response_returns_empty_list():
    cfg = _default_config()
    chunks = humanize("", cfg)
    assert chunks == []


def test_only_whitespace_response_returns_empty_list():
    cfg = _default_config()
    chunks = humanize("   \n\n   \n\n  ", cfg)
    assert chunks == []
