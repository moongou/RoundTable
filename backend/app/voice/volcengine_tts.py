"""火山引擎 TTS 提供商

通过火山引擎语音合成 HTTP API 进行文字转语音。
API 文档: https://www.volcengine.com/docs/6561/79823
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from app.voice.base import TTSProvider

logger = logging.getLogger(__name__)


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


class VolcengineTTSProvider(TTSProvider):
    """火山引擎语音合成提供商。"""

    def __init__(
        self,
        base_url: str = "https://openspeech.bytedance.com/api/v1/tts",
        access_key: str = "",
        secret_key: str = "",
        model: str = "tts-1",
        default_voice: str = "zh_female_qingxin",
    ):
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.model = model
        self.default_voice = default_voice

    def _sign_headers(
        self, method: str, path: str, query: str, body: str
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%dT%H%M%SZ")

        content_sha256 = _sha256_hex(body)
        headers_to_sign = {
            "Host": "openspeech.bytedance.com",
            "Content-Type": "application/json",
            "X-Date": date_str,
            "X-Content-Sha256": content_sha256,
        }

        signed_headers = ";".join(sorted(k.lower() for k in headers_to_sign))
        canonical_headers = "\n".join(
            f"{k.lower()}:{v}"
            for k, v in sorted(headers_to_sign.items(), key=lambda x: x[0].lower())
        )
        canonical_request = (
            f"{method}\n{path}\n{query}\n{canonical_headers}\n\n{signed_headers}\n"
            f"{content_sha256}"
        )
        credential_scope = f"{now.strftime('%Y%m%d')}/cn-north-1/speech/request"
        string_to_sign = (
            f"HMAC-SHA256\n{date_str}\n{credential_scope}\n"
            f"{_sha256_hex(canonical_request)}"
        )

        k_date = _hmac_sha256(self.secret_key.encode("utf-8"), now.strftime("%Y%m%d"))
        k_region = _hmac_sha256(k_date, "cn-north-1")
        k_service = _hmac_sha256(k_region, "speech")
        k_signing = _hmac_sha256(k_service, "request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        auth = (
            f"HMAC-SHA256 "
            f"Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        return {
            "Authorization": auth,
            "X-Date": date_str,
            "Content-Type": "application/json",
        }

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
    ) -> bytes:
        payload = {
            "text": text,
            "voice": voice or self.default_voice,
            "speed": speed,
            "format": "mp3",
        }
        body = __import__("json").dumps(payload)

        path = "/api/v1/tts"
        query = f"model={self.model}"
        headers = self._sign_headers("POST", path, query, body)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}?{query}",
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            audio_base64 = result.get("audio", "")
            if audio_base64:
                import base64

                return base64.b64decode(audio_base64)
            return b""

    async def is_available(self) -> bool:
        return bool(self.access_key) and bool(self.secret_key)
