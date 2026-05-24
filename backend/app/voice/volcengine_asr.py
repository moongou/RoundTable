"""火山引擎 ASR 提供商

通过火山引擎语音识别 HTTP API 进行语音转文字。
API 文档: https://www.volcengine.com/docs/6561/80818
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx

from app.voice.base import ASRProvider

logger = logging.getLogger(__name__)


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


class VolcengineASRProvider(ASRProvider):
    """火山引擎语音识别提供商。"""

    def __init__(
        self,
        base_url: str = "https://openspeech.bytedance.com/api/v1/asr",
        access_key: str = "",
        secret_key: str = "",
        model: str = "bigmodel",
        language: str = "zh-CN",
    ):
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.model = model
        self.language = language

    def _sign_headers(
        self, method: str, path: str, query: str, body: bytes, content_type: str
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%dT%H%M%SZ")
        nonce = uuid.uuid4().hex

        headers_to_sign = {
            "Host": "openspeech.bytedance.com",
            "Content-Type": content_type,
            "X-Date": date_str,
            "X-Content-Sha256": _sha256_hex(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else ""),
        }
        if not body:
            headers_to_sign["X-Content-Sha256"] = _sha256_hex("")

        signed_headers = ";".join(sorted(k.lower() for k in headers_to_sign))
        canonical_headers = "\n".join(
            f"{k.lower()}:{v}" for k, v in sorted(headers_to_sign.items(), key=lambda x: x[0].lower())
        )
        canonical_request = (
            f"{method}\n{path}\n{query}\n{canonical_headers}\n\n{signed_headers}\n"
            f"{headers_to_sign['X-Content-Sha256']}"
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
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        auth = (
            f"HMAC-SHA256 "
            f"Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        return {
            "Authorization": auth,
            "X-Date": date_str,
            "Content-Type": content_type,
            "Host": "openspeech.bytedance.com",
        }

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        query = f"model={self.model}&language={self.language}"
        path = "/api/v1/asr"
        content_type = f"audio/{format}" if format in ("wav", "mp3", "ogg") else "audio/wav"

        headers = self._sign_headers("POST", path, query, audio_data, content_type)
        headers["Content-Type"] = content_type

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}?{query}",
                content=audio_data,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()
            return result.get("text", "")

    async def is_available(self) -> bool:
        return bool(self.access_key) and bool(self.secret_key)
