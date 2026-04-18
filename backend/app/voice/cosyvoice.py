"""CosyVoice TTS 提供商

通过本地的 CosyVoice 服务（端口 50000）提供 TTS。
CosyVoice 使用自定义 API 接口。
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import TTSProvider

logger = logging.getLogger(__name__)


class CosyVoiceProvider(TTSProvider):
    """CosyVoice TTS 提供商，通过本地 CosyVoice 服务合成语音。"""

    def __init__(self, base_url: str = "http://localhost:50000"):
        self.base_url = base_url.rstrip("/")

    async def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        # CosyVoice 典型接口: POST /tts with {"text": ..., "speaker": ...}
        # 如果该接口格式不对，需要根据实际服务调整
        payload = {
            "text": text,
            "speaker": voice,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/tts",
                json=payload,
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "audio" in content_type or "octet-stream" in content_type:
                return response.content

            # 某些 CosyVoice 实现返回 JSON 包含 base64 编码的音频
            if "json" in content_type:
                data = response.json()
                import base64
                audio_b64 = data.get("audio") or data.get("data", "")
                if audio_b64:
                    return base64.b64decode(audio_b64)

            return response.content

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code < 500
        except Exception:
            return False