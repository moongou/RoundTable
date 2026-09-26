"""语气/情绪映射的单元测试。"""

from __future__ import annotations

import pytest

from app.voice.emotion import (
    ANGRY,
    CURIOUS,
    EXCITED,
    GENTLE,
    SAD,
    SURPRISED,
    WHISPER,
    detect_emotion,
    elevenlabs_voice_settings,
    strip_stage_hints,
    to_minimax_emotion,
    with_elevenlabs_tag,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("太好了，我们找到答案了！", EXCITED),
        ("竟然还有这种事情？", SURPRISED),
        ("我有点难过，明明很努力了。", SAD),
        ("这太不公平了！我抗议。", ANGRY),
        ("这是为什么呢？", CURIOUS),
    ],
)
def test_keyword_detection(text: str, expected: str) -> None:
    assert detect_emotion(text) == expected


def test_stage_hint_wins() -> None:
    # 舞台提示优先于正文关键词
    assert detect_emotion("（轻声）我特别激动地告诉大家") == WHISPER
    assert strip_stage_hints("（轻声）我特别激动地告诉大家") == "我特别激动地告诉大家"


def test_role_bias_fallback() -> None:
    assert detect_emotion("今天我们讨论一个话题。", role="moderator") == GENTLE
    assert detect_emotion("今天我们讨论一个话题。", role="student") == CURIOUS


def test_minimax_mapping() -> None:
    assert to_minimax_emotion(EXCITED) == "fluent"
    assert to_minimax_emotion("happy") == "happy"
    # speech-2.8 系列不支持 whisper，必须丢弃
    assert to_minimax_emotion(WHISPER, "speech-2.8-hd") is None
    assert to_minimax_emotion(WHISPER, "speech-02-hd") == "whisper"
    assert to_minimax_emotion(None) is None
    assert to_minimax_emotion("不存在的标签") is None


def test_elevenlabs_tag() -> None:
    tagged = with_elevenlabs_tag("今天真开心", "happy")
    assert tagged.startswith("[")
    assert tagged.endswith("今天真开心")
    assert with_elevenlabs_tag("保持中立", "neutral") == "保持中立"


def test_elevenlabs_voice_settings() -> None:
    settings = elevenlabs_voice_settings(EXCITED)
    assert 0 < settings["stability"] < 1
    assert settings["style"] > 0.5  # 兴奋应当放开风格强度
    assert elevenlabs_voice_settings(None) == {}
