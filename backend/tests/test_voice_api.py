import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import voice_api


class _RetryThenSucceedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, text: str, voice: str = "alloy", speed: float = 1.0) -> bytes:
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "http://tts.local/speak")
            raise httpx.ReadTimeout("timeout", request=request)
        return b"ID3fake-mp3"


class _PermanentBadRequestProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, text: str, voice: str = "alloy", speed: float = 1.0) -> bytes:
        self.calls += 1
        request = httpx.Request("POST", "http://tts.local/speak")
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("bad request", request=request, response=response)


class _CaptureVoiceProvider:
    def __init__(self) -> None:
        self.voice: str | None = None

    async def synthesize(self, text: str, voice: str = "alloy", speed: float = 1.0) -> bytes:
        self.voice = voice
        return b"ID3fake-mp3"


def _make_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(voice_api.router, prefix="/api/v1")
    return TestClient(app)


def test_tts_response_headers_include_runtime_details(monkeypatch) -> None:
    provider = _RetryThenSucceedProvider()
    monkeypatch.setattr(voice_api, "create_tts_provider", lambda provider_id=None: provider)

    with _make_test_client() as client:
        response = client.post(
            "/api/v1/voice/tts",
            json={
                "text": "测试响应头",
                "provider": "edge_tts",
                "voice": "zh-CN-XiaoxiaoNeural",
            },
        )

    assert response.status_code == 200
    assert response.headers["x-voice-requested"] == "zh-CN-XiaoxiaoNeural"
    assert response.headers["x-voice-used"] == "zh-CN-XiaoxiaoNeural"
    assert response.headers["x-tts-provider"] == "edge_tts"
    assert response.headers["x-tts-attempts"] == "2"
    assert float(response.headers["x-tts-elapsed-ms"]) >= 0
    assert provider.calls == 2


def test_tts_does_not_retry_non_transient_upstream_errors(monkeypatch) -> None:
    provider = _PermanentBadRequestProvider()
    monkeypatch.setattr(voice_api, "create_tts_provider", lambda provider_id=None: provider)

    with _make_test_client() as client:
        response = client.post(
            "/api/v1/voice/tts",
            json={
                "text": "测试非瞬时错误",
                "provider": "edge_tts",
                "voice": "zh-CN-XiaoxiaoNeural",
            },
        )

    assert response.status_code == 500
    assert provider.calls == 1


def test_tts_uses_provider_default_voice_when_request_voice_is_omitted(monkeypatch) -> None:
    provider = _CaptureVoiceProvider()
    monkeypatch.setattr(voice_api, "create_tts_provider", lambda provider_id=None: provider)
    monkeypatch.setattr(
        type(voice_api.settings),
        "get_tts_voice_for_provider",
        lambda self, provider_id=None: "FunAudioLLM/CosyVoice2-0.5B:alex",
    )

    with _make_test_client() as client:
        response = client.post(
            "/api/v1/voice/tts",
            json={
                "text": "测试默认音色",
                "provider": "siliconflow_tts",
            },
        )

    assert response.status_code == 200
    assert response.headers["x-voice-requested"] == ""
    assert response.headers["x-voice-used"] == "FunAudioLLM/CosyVoice2-0.5B:alex"
    assert provider.voice == "FunAudioLLM/CosyVoice2-0.5B:alex"
