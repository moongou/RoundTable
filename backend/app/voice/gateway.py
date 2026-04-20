"""Gateway 语音服务提供商

通过本地 voice-services gateway (端口 6666) 代理访问多种本地语音服务：
- ASR: CapsWriter, Vosk
- TTS: VibeVoice, FireRedTTS, OpenVoice
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import ASRProvider, TTSProvider

logger = logging.getLogger(__name__)

GATEWAY_URL = "http://localhost:6666"


class GatewayTTSProvider(TTSProvider):
    """通过 gateway 访问本地 TTS 服务。"""

    def __init__(self, service: str = "vibevoice", gateway_url: str = GATEWAY_URL):
        self.service = service
        self.gateway_url = gateway_url.rstrip("/")

    async def synthesize(self, text: str, voice: str = "default") -> bytes:
        payload = {"text": text, "speaker": voice}
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.gateway_url}/tts/{self.service}",
                json=payload,
            )
            response.raise_for_status()
            return response.content

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.gateway_url}/health/{self.service}")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") == "healthy"
            return False
        except Exception:
            return False


class GatewayASRProvider(ASRProvider):
    """通过 gateway 访问本地 ASR 服务 (CapsWriter/Vosk)。"""

    def __init__(self, service: str = "capswriter", gateway_url: str = GATEWAY_URL):
        self.service = service
        self.gateway_url = gateway_url.rstrip("/")

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Gateway proxies multipart to backend /transcribe
            data = httpx.Request("POST", "/dummy")
            response = await client.post(
                f"{self.gateway_url}/asr/{self.service}",
                content=audio_data,
                headers={"Content-Type": f"audio/{format}"},
            )
            if response.status_code != 200:
                # Try multipart form
                files = {"audio": (f"audio.{format}", audio_data, f"audio/{format}")}
                response = await client.post(
                    f"{self.gateway_url}/asr/{self.service}",
                    files=files,
                )
            response.raise_for_status()
            data = response.json()
            return data.get("text", data.get("result", ""))

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.gateway_url}/health/{self.service}")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") == "healthy"
            return False
        except Exception:
            return False
