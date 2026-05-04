import json
import struct

import pytest
import websockets
import httpx

from app.config import settings
from app.voice.factory import create_asr_provider, create_tts_provider
from app.voice.gateway import GatewayTTSProvider
from app.voice.openai_tts import OpenAITTSProvider
from app.voice.openai_whisper import OpenAIWhisperProvider
from app.voice.standalone_ws_asr import StandaloneWsAsrProvider


class _OpenAITtsClientStub:
    def __init__(self, *, captured_payloads: list[dict], response: httpx.Response | None = None, **_: object) -> None:
        self._captured_payloads = captured_payloads
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict, headers: dict) -> httpx.Response:
        request = httpx.Request("POST", url)
        self._captured_payloads.append(
            {
                "url": url,
                "json": dict(json),
                "headers": dict(headers),
            }
        )
        if self._response is not None:
            return self._response
        return httpx.Response(200, request=request, content=b"ID3fake-mp3")


def test_create_siliconflow_asr_provider_uses_openai_compatible_client() -> None:
    object.__setattr__(settings, "siliconflow_asr_api_key", "sf-asr-key")
    object.__setattr__(settings, "siliconflow_asr_base_url", "https://api.siliconflow.cn/v1")
    object.__setattr__(settings, "siliconflow_asr_model", "TeleAI/TeleSpeechASR")

    provider = create_asr_provider("siliconflow_asr")

    assert isinstance(provider, OpenAIWhisperProvider)
    assert provider.base_url == "https://api.siliconflow.cn/v1"
    assert provider.api_key == "sf-asr-key"
    assert provider.model == "TeleAI/TeleSpeechASR"


def test_create_siliconflow_tts_provider_uses_openai_compatible_client() -> None:
    object.__setattr__(settings, "siliconflow_tts_api_key", "sf-tts-key")
    object.__setattr__(settings, "siliconflow_tts_base_url", "https://api.siliconflow.cn/v1")
    object.__setattr__(settings, "siliconflow_tts_model", "FunAudioLLM/CosyVoice2-0.5B")
    object.__setattr__(settings, "siliconflow_tts_voice", "FunAudioLLM/CosyVoice2-0.5B:alex")

    provider = create_tts_provider("siliconflow_tts")

    assert isinstance(provider, OpenAITTSProvider)
    assert provider.base_url == "https://api.siliconflow.cn/v1"
    assert provider.api_key == "sf-tts-key"
    assert provider.model == "FunAudioLLM/CosyVoice2-0.5B"
    assert provider.default_voice == "FunAudioLLM/CosyVoice2-0.5B:alex"


@pytest.mark.asyncio
async def test_openai_tts_provider_uses_configured_default_voice_when_voice_is_omitted(monkeypatch) -> None:
    captured_payloads: list[dict] = []

    monkeypatch.setattr(
        "app.voice.openai_tts.httpx.AsyncClient",
        lambda *args, **kwargs: _OpenAITtsClientStub(
            captured_payloads=captured_payloads,
            **kwargs,
        ),
    )

    provider = OpenAITTSProvider(
        base_url="https://api.siliconflow.cn/v1",
        api_key="sf-tts-key",
        model="FunAudioLLM/CosyVoice2-0.5B",
        default_voice="FunAudioLLM/CosyVoice2-0.5B:alex",
    )

    await provider.synthesize("你好")

    assert captured_payloads[-1]["json"]["voice"] == "FunAudioLLM/CosyVoice2-0.5B:alex"


@pytest.mark.asyncio
async def test_openai_tts_provider_replaces_alloy_with_provider_default_voice(monkeypatch) -> None:
    captured_payloads: list[dict] = []

    monkeypatch.setattr(
        "app.voice.openai_tts.httpx.AsyncClient",
        lambda *args, **kwargs: _OpenAITtsClientStub(
            captured_payloads=captured_payloads,
            **kwargs,
        ),
    )

    provider = OpenAITTSProvider(
        base_url="https://api.siliconflow.cn/v1",
        api_key="sf-tts-key",
        model="FunAudioLLM/CosyVoice2-0.5B",
        default_voice="FunAudioLLM/CosyVoice2-0.5B:alex",
    )

    await provider.synthesize("你好", voice="alloy")

    assert captured_payloads[-1]["json"]["voice"] == "FunAudioLLM/CosyVoice2-0.5B:alex"


def test_create_openvoice_provider_uses_runtime_openvoice_url() -> None:
    previous_url = settings.openvoice_url
    object.__setattr__(settings, "openvoice_url", "http://localhost:7799")

    try:
        provider = create_tts_provider("openvoice")

        assert isinstance(provider, GatewayTTSProvider)
        assert provider.service_url == "http://localhost:7799"
        assert provider.openvoice_url == "http://localhost:7799"
    finally:
        object.__setattr__(settings, "openvoice_url", previous_url)


@pytest.mark.parametrize(
    ("provider_id", "field_name", "expected_url"),
    [
        ("vibevoice", "vibevoice_url", "http://localhost:7704"),
        ("fireredtts", "fireredtts_url", "http://localhost:7706"),
    ],
)
def test_create_local_tts_provider_uses_runtime_service_url(
    provider_id: str,
    field_name: str,
    expected_url: str,
) -> None:
    previous_url = getattr(settings, field_name)
    object.__setattr__(settings, field_name, expected_url)

    try:
        provider = create_tts_provider(provider_id)

        assert isinstance(provider, GatewayTTSProvider)
        assert provider.service_url == expected_url
    finally:
        object.__setattr__(settings, field_name, previous_url)


def test_create_capswriter_provider_uses_runtime_url_and_json_protocol() -> None:
    previous_url = settings.capswriter_url
    object.__setattr__(settings, "capswriter_url", "ws://localhost:7616")

    try:
        provider = create_asr_provider("capswriter")

        assert isinstance(provider, StandaloneWsAsrProvider)
        assert provider.base_url == "ws://localhost:7616"
        assert provider.ws_path == "/ws"
        assert provider.ws_subprotocol == "binary"
        assert provider.protocol == "capswriter_json"
    finally:
        object.__setattr__(settings, "capswriter_url", previous_url)


def test_create_vosk_provider_uses_runtime_url_and_binary_protocol() -> None:
    previous_url = settings.vosk_url
    object.__setattr__(settings, "vosk_url", "http://localhost:7702")

    try:
        provider = create_asr_provider("vosk")

        assert isinstance(provider, StandaloneWsAsrProvider)
        assert provider.base_url == "http://localhost:7702"
        assert provider.ws_path == "/stream"
        assert provider.ws_subprotocol is None
        assert provider.protocol == "binary_pcm16"
    finally:
        object.__setattr__(settings, "vosk_url", previous_url)


@pytest.mark.asyncio
async def test_capswriter_json_provider_sends_capswriter_messages() -> None:
    received: list[dict] = []

    async def handler(websocket) -> None:
        async for message in websocket:
            payload = json.loads(message)
            received.append(payload)
            if payload["is_final"]:
                await websocket.send(
                    json.dumps(
                        {
                            "task_id": payload["task_id"],
                            "is_final": True,
                            "text": "粗识别",
                            "text_accu": "最终识别",
                        }
                    )
                )
                break

    server = await websockets.serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=["binary"],
    )
    port = server.sockets[0].getsockname()[1]

    try:
        provider = StandaloneWsAsrProvider(
            base_url=f"ws://127.0.0.1:{port}",
            ws_path="/",
            ws_subprotocol="binary",
            protocol="capswriter_json",
        )
        audio = struct.pack("<4f", 0.0, 0.1, -0.1, 0.0)

        result = await provider.transcribe(audio, format="f32le")

        assert result == "最终识别"
        assert received[0]["source"] == "file"
        assert received[0]["is_final"] is False
        assert received[0]["data"]
        assert received[-1]["is_final"] is True
        assert received[-1]["data"] == ""
    finally:
        server.close()
        await server.wait_closed()
