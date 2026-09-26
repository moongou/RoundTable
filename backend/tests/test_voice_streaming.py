"""流式语音合成测试。

校验两件事：
1. provider 层：支持流式的服务能逐块产出音频；不支持的自动退回一次性产出。
2. API 层：`GET /voice/tts/stream` 真的分片下发，并带上可诊断的响应头。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.voice.base import TTSProvider
from app.voice.elevenlabs_tts import ElevenLabsTTSProvider
from app.voice.minimax_tts import MiniMaxTTSProvider

# ── 假流式 HTTP 客户端 ───────────────────────────────────────────────────────


class _FakeStreamResponse:
    def __init__(self, chunks: list[Any], status_code: int = 200):
        self._chunks = chunks
        self.status_code = status_code
        self.text = ""
        self.request = httpx.Request("POST", "http://fake.local")

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def raise_for_status(self) -> None:
        if self.is_error:
            raise httpx.HTTPStatusError("err", request=self.request, response=self)  # type: ignore[arg-type]

    async def aread(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk


class _StreamContext:
    def __init__(self, response: _FakeStreamResponse):
        self.response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        return self.response

    async def __aexit__(self, *_args: Any) -> bool:
        return False


class _FakeStreamClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    async def __aenter__(self) -> _FakeStreamClient:
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    def stream(self, method: str, url: str, **kwargs: Any) -> _StreamContext:  # noqa: ARG002
        return _StreamContext(_FakeStreamResponse(NEXT_CHUNKS))


NEXT_CHUNKS: list[Any] = [b"ID3", b"chunk-a", b"chunk-b"]


@pytest.fixture
def _fake_httpx(monkeypatch: pytest.MonkeyPatch):
    NEXT_CHUNKS[:] = [b"ID3", b"chunk-a", b"chunk-b"]
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeStreamClient())  # noqa: ARG005
    yield


async def _collect(provider: TTSProvider, **kwargs: Any) -> list[bytes]:
    chunks = []
    async for chunk in provider.synthesize_stream("测试文本", "v1", 1.0, **kwargs):
        chunks.append(chunk)
    return chunks


@pytest.mark.usefixtures("_fake_httpx")
def test_elevenlabs_streams_chunks() -> None:
    provider = ElevenLabsTTSProvider(api_key="sk-test", default_voice="v1")
    chunks = asyncio.run(_collect(provider))
    assert chunks == [b"ID3", b"chunk-a", b"chunk-b"]
    assert provider.supports_streaming is True


@pytest.mark.usefixtures("_fake_httpx")
def test_minimax_stream_parses_sse_chunks() -> None:
    NEXT_CHUNKS[:] = [
        f"data: {json.dumps({'data': {'audio': '494433'}})}\n\n".encode(),
        f"data: {json.dumps({'data': {'audio': '66'}})}\n\n".encode(),
    ]
    provider = MiniMaxTTSProvider(api_key="sk-test", default_voice="vo-1")
    chunks = asyncio.run(_collect(provider))
    assert b"".join(chunks) == bytes.fromhex("49443366")
    assert provider.supports_streaming is True


def test_provider_without_streaming_yields_once() -> None:
    class _Plain(TTSProvider):
        async def synthesize(self, text, voice="alloy", speed=1.0) -> bytes:  # noqa: ARG002
            return b"ID3whole"

        async def is_available(self) -> bool:
            return True

    provider = _Plain()
    assert provider.supports_streaming is False
    chunks = asyncio.run(_collect(provider))
    assert chunks == [b"ID3whole"]


# ── API 层 ──────────────────────────────────────────────────────────────────


class _StreamingProvider(TTSProvider):
    supports_streaming = True

    async def synthesize(self, text, voice="alloy", speed=1.0) -> bytes:  # noqa: ARG002
        return b"ID3whole"

    async def synthesize_stream(self, text, voice="alloy", speed=1.0, emotion=None, style=None):  # noqa: ARG002
        for part in (b"ID3", b"aaa", b"bbb"):
            yield part

    async def is_available(self) -> bool:
        return True


def test_stream_endpoint_emits_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.api.v1 import voice_api
    from app.main import app

    monkeypatch.setattr(
        voice_api, "create_tts_provider", lambda provider_id=None: _StreamingProvider()
    )
    client = TestClient(app)
    response = client.get(
        "/api/v1/voice/tts/stream",
        params={"text": "太好了，我们做到了！", "provider": "elevenlabs_tts", "voice": "v1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/")
    assert response.headers["x-streaming"] == "1"
    assert response.headers["x-voice-used"] == "v1"
    # 情绪由后端本地规则推导：感叹句 + 兴奋关键词 → excited
    assert response.headers["x-emotion-used"] == "excited"
    assert response.content == b"ID3aaabbb"
