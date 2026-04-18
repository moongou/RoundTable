"""FunASR ASR 提供商

通过本地的 FunASR 服务（端口 10096）提供语音识别。
FunASR 支持 HTTP 接口接收音频并返回转录文本。
"""

from __future__ import annotations

import logging

import httpx

from app.voice.base import ASRProvider

logger = logging.getLogger(__name__)


class FunASRProvider(ASRProvider):
    """FunASR ASR 提供商，通过本地 FunASR 服务进行语音识别。"""

    def __init__(self, base_url: str = "http://localhost:10096"):
        self.base_url = base_url.rstrip("/")

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        # FunASR 典型接口: POST /recognize with multipart audio upload
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"audio": (f"audio.{format}", audio_data, f"audio/{format}")}
            response = await client.post(
                f"{self.base_url}/recognize",
                files=files,
            )
            response.raise_for_status()

            data = response.json()
            # FunASR 返回格式通常是 {"text": "识别结果", ...}
            return data.get("text", data.get("result", ""))

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/")
                return resp.status_code < 500
        except Exception:
            return False