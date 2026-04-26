"""OpenAI Whisper ASR 提供商

通过 OpenAI /v1/audio/transcriptions 接口提供语音识别。
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import ASRProvider

logger = logging.getLogger(__name__)


class OpenAIWhisperProvider(ASRProvider):
    """OpenAI Whisper ASR 提供商。"""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "whisper-1",
        language: str = "zh",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language = language

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"file": (f"audio.{format}", audio_data, f"audio/{format}")}
            data = {"model": self.model}
            if self.language:
                data["language"] = self.language

            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                files=files,
                data=data,
                headers=headers,
            )
            response.raise_for_status()

            result = response.json()
            return result.get("text", "")

    async def is_available(self) -> bool:
        return bool(self.api_key) and self.api_key != "sk-xxx"