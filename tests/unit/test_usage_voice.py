from ai_sdr.flowengine.usage import accumulate_voice_usage


def test_accumulate_voice_usage_sums_in_place():
    running = {}
    accumulate_voice_usage(running, synthesis_chars=120)
    accumulate_voice_usage(running, synthesis_chars=30, transcription_ms=2000)
    assert running["voice_synthesis_chars"] == 150
    assert running["voice_transcription_ms"] == 2000
