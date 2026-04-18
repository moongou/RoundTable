"""Edge TTS 提供商 - OpenAI 兼容接口

通过本地的 Edge TTS 代理服务（端口 5051）提供 TTS，
该服务兼容 OpenAI /v1/audio/speech 接口格式。
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import TTSProvider

logger = logging.getLogger(__name__)


class EdgeTTSProvider(TTSProvider):
    """Edge TTS 提供商，通过 OpenAI 兼容接口访问本地 Edge TTS 服务。"""

    def __init__(self, base_url: str = "http://localhost:5051", api_key: str = "edge-tts"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        # Edge TTS 使用 Azure 语音名称，如果传入的是 OpenAI 格式（如 alloy），
        # 自动转换为默认中文语音
        tts_voice = voice
        if voice in ("alloy", "echo", "fable", "onyx", "nova", "shimmer"):
            tts_voice = "zh-CN-XiaoxiaoNeural"  # 默认中文女声

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": tts_voice,
            "response_format": "mp3",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.content

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                return resp.status_code < 500
        except Exception:
            return False