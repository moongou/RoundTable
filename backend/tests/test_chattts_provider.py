from __future__ import annotations

from app.voice.chattts import (
    _extract_generated_file_url,
    _extract_process_completed_value,
    _stable_audio_seed,
)


def test_stable_audio_seed_is_deterministic_and_distinct() -> None:
    first = _stable_audio_seed('zh-CN-XiaoxiaoNeural')
    second = _stable_audio_seed('zh-CN-XiaoxiaoNeural')
    third = _stable_audio_seed('zh-CN-YunyangNeural')

    assert first == second
    assert first != third
    assert 1 <= first <= 4294967295
    assert 1 <= third <= 4294967295


def test_extract_process_completed_value_reads_refined_text() -> None:
    payload = (
        'data: {"msg":"estimation"}\n\n'
        'data: {"msg":"process_completed","output":{"data":["refined text"]}}\n\n'
    )

    assert _extract_process_completed_value(payload) == 'refined text'


def test_extract_generated_file_url_reads_audio_url() -> None:
    payload = (
        'data: {"msg":"process_starts"}\n\n'
        'data: {"msg":"process_generating","output":{"data":[{"url":"http://localhost:9998/gradio_api/file=/tmp/audio.wav"}]}}\n\n'
    )

    assert (
        _extract_generated_file_url(payload)
        == 'http://localhost:9998/gradio_api/file=/tmp/audio.wav'
    )