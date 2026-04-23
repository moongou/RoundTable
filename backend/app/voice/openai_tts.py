"""OpenAI TTS API 提供商

通过 OpenAI /v1/audio/speech 接口提供 TTS，
也可用于任何 OpenAI 兼容的 TTS 服务。
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import TTSProvider

logger = logging.getLogger(__name__)


class OpenAITTSProvider(TTSProvider):
    """OpenAI TTS API 提供商。"""

    def __init__(self, base_url: str = "https://api.openai.com/v1", api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def synthesize(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
    ) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3",
        }
        if speed != 1.0:
            payload["speed"] = speed

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/audio/speech",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.content

    async def is_available(self) -> bool:
        return bool(self.api_key) and self.api_key != "sk-xxx"