"""顶级语音 provider 的请求构造测试。

在没有真实 API Key 的情况下，通过替换 httpx.AsyncClient 校验「我们发出去的请求」是否正确：
ElevenLabs 是否带上情绪 audio tag、MiniMax 是否映射了 emotion 字段、静音 provider
是否产出可播放音频。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.voice.elevenlabs_tts import ElevenLabsTTSProvider
from app.voice.minimax_tts import MiniMaxTTSProvider
from app.voice.silent_tts import SilentTTSProvider

RECORDED: list[dict[str, Any]] = []
NEXT_RESPONSE: dict[str, Any] = {"text": "", "content": b"ID3fake", "status_code": 200}


class _FakeResponse:
    def __init__(self) -> None:
        self.content = NEXT_RESPONSE.get("content") or b""
        self.text = NEXT_RESPONSE.get("text") or ""
        self.status_code = NEXT_RESPONSE.get("status_code", 200)
        self.request = httpx.Request("POST", "http://fake.local")

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def raise_for_status(self) -> None:
        if self.is_error:
            raise httpx.HTTPStatusError("err", request=self.request, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return json.loads(self.text or "{}")


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        RECORDED.append({"url": url, **kwargs})
        return _FakeResponse()

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        RECORDED.append({"url": url, **kwargs})
        return _FakeResponse()


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch):
    RECORDED.clear()
    NEXT_RESPONSE.update({"text": "", "content": b"ID3fake", "status_code": 200})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient())  # noqa: ARG005
    yield
    RECORDED.clear()


def test_elevenlabs_injects_emotion_tag_and_uses_stream_endpoint() -> None:
    provider = ElevenLabsTTSProvider(
        api_key="sk-test", default_voice="voice-abc", model="eleven_v3"
    )

    audio = asyncio.run(
        provider.synthesize_safe(
            "（兴奋地）太好了，我们找到答案了！", "voice-abc", 1.0, emotion="excited"
        )
    )

    assert audio == b"ID3fake"
    call = RECORDED[-1]
    assert call["url"].endswith("/text-to-speech/voice-abc/stream")
    assert call["params"]["output_format"] == "mp3_44100_128"
    payload = call["json"]
    assert payload["model_id"] == "eleven_v3"
    assert payload["text"].startswith("[excited] ")
    assert payload["text"].endswith("太好了，我们找到答案了！")  # 舞台提示不应被朗读
    assert payload["voice_settings"]["stability"] < 0.5  # 兴奋：放开稳定性
    assert payload["voice_settings"]["style"] > 0.5


def test_elevenlabs_without_emotion_keeps_text_clean() -> None:
    provider = ElevenLabsTTSProvider(api_key="sk-test", default_voice="v1")
    asyncio.run(provider.synthesize_safe("大家好。", "v1", 1.0, emotion=None))
    payload = RECORDED[-1]["json"]
    assert payload["text"] == "大家好。"
    assert payload["voice_settings"]["stability"] == 0.5


def test_minimax_maps_emotion_and_parses_hex_audio() -> None:
    NEXT_RESPONSE["text"] = json.dumps({"data": {"audio": "49443366"}})  # "ID3f"
    provider = MiniMaxTTSProvider(api_key="sk-test", default_voice="vo-1", model="speech-2.8-hd")

    audio = asyncio.run(provider.synthesize_safe("我有点难过。", "vo-1", 1.0, emotion="sad"))

    assert audio == b"ID3f"
    call = RECORDED[-1]
    assert call["url"].startswith("https://api.minimaxi.com/v1/t2a_v2")
    payload = call["json"]
    assert payload["model"] == "speech-2.8-hd"
    assert payload["text"] == "我有点难过。"
    assert payload["voice_setting"]["emotion"] == "sad"
    assert payload["voice_setting"]["voice_id"] == "vo-1"


def test_minimax_drops_whisper_for_speech_28() -> None:
    NEXT_RESPONSE["text"] = json.dumps({"data": {"audio": "49443366"}})
    provider = MiniMaxTTSProvider(api_key="sk-test", default_voice="vo-1", model="speech-2.8-hd")

    asyncio.run(provider.synthesize_safe("（轻声）别让他们听见。", "vo-1", 1.0, emotion="whisper"))

    voice_setting = RECORDED[-1]["json"]["voice_setting"]
    assert "emotion" not in voice_setting  # 2.8 系列不支持 whisper，必须丢弃


def test_minimax_includes_group_id_when_configured() -> None:
    NEXT_RESPONSE["text"] = json.dumps({"data": {"audio": "49443366"}})
    provider = MiniMaxTTSProvider(api_key="sk-test", group_id="g-123", default_voice="vo-1")
    asyncio.run(provider.synthesize_safe("你好", "vo-1", 1.0))
    assert "GroupId=g-123" in RECORDED[-1]["url"]


def test_silent_provider_returns_playable_wav() -> None:
    audio = asyncio.run(SilentTTSProvider().synthesize_safe("任意文本"))
    assert audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"
    assert len(audio) > 44
