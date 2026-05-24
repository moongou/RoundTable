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

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "tts-1",
        default_voice: str = "alloy",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.default_voice = default_voice

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
    ) -> bytes:
        default_voice = (self.default_voice or "").strip() or "alloy"
        requested_voice = (voice or "").strip()
        resolved_voice = requested_voice or default_voice
        if requested_voice.lower() in {"alloy", "default"} and default_voice != requested_voice:
            resolved_voice = default_voice

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model,
            "input": text,
            "voice": resolved_voice,
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
            if response.is_error:
                logger.error(
                    "TTS API error %s: %s | payload model=%s voice=%s text_len=%d",
                    response.status_code,
                    response.text[:500],
                    self.model,
                    resolved_voice,
                    len(text),
                )
            response.raise_for_status()
            return response.content

    async def is_available(self) -> bool:
        return bool(self.api_key) and self.api_key != "sk-xxx"