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

    async def synthesize(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
    ) -> bytes:
        # 如果已经是 Azure 神经语音名称（含 Neural 或以 zh- 开头），直接使用；
        # 否则从 OpenAI 格式映射到默认中文语音。
        _openai_to_azure = {
            "alloy":   "zh-CN-XiaoxiaoNeural",
            "echo":    "zh-CN-YunyangNeural",
            "fable":   "zh-CN-YunxiNeural",
            "onyx":    "zh-CN-YunzeNeural",
            "nova":    "zh-CN-XiaoyiNeural",
            "shimmer": "zh-CN-XiaohanNeural",
        }
        if "Neural" in voice or voice.startswith("zh-") or voice.startswith("en-"):
            tts_voice = voice
        else:
            tts_voice = _openai_to_azure.get(voice, "zh-CN-XiaoxiaoNeural")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": tts_voice,
            "response_format": "mp3",
        }
        if speed != 1.0:
            payload["speed"] = speed

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