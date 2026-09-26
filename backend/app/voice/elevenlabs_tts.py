"""ElevenLabs TTS 提供商

ElevenLabs v3 目前是真人感的天花板：支持 audio tag（`[excited]`、`[whispers]`…）
做逐句情绪控制，并可用 `eleven_flash_v2_5` 换取更低延迟。

- 拟真优先：model_id = `eleven_v3`
- 速度优先：model_id = `eleven_flash_v2_5`（或用 `eleven_turbo_v2_5`）
- 端点 `/text-to-speech/{voice_id}/stream` 比普通端点首包更早。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.voice.base import TTSProvider
from app.voice.emotion import elevenlabs_voice_settings, strip_stage_hints, with_elevenlabs_tag

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsTTSProvider(TTSProvider):
    """ElevenLabs TTS API 提供商（支持情绪与低延迟档位）。"""

    supports_emotion = True
    supports_style = True
    supports_streaming = True

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = "eleven_v3",
        default_voice: str = "",
        output_format: str = "mp3_44100_128",
        use_stream_endpoint: bool = True,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or "eleven_v3"
        self.default_voice = default_voice
        self.output_format = output_format or "mp3_44100_128"
        self.use_stream_endpoint = use_stream_endpoint
        self.timeout = timeout

    # ── 内部工具 ────────────────────────────────────────────────────────────

    def _resolve_voice(self, voice: str | None) -> str:
        requested = (voice or "").strip()
        default = (self.default_voice or "").strip()
        if requested.lower() in {"alloy", "default", ""}:
            return default
        return requested

    def _headers(self, accept: str = "audio/mpeg") -> dict[str, str]:
        return {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "accept": accept,
        }

    def _build_payload(
        self,
        text: str,
        voice_id: str,
        speed: float,
        emotion: str | None,
        style: str | None,
    ) -> dict[str, Any]:
        spoken = strip_stage_hints(text)
        tagged = with_elevenlabs_tag(spoken, emotion or style)

        voice_settings: dict[str, Any] = {
            "stability": 0.5,
            "similarity_boost": 0.8,
            "use_speaker_boost": True,
        }
        # 情绪化的句子：略微放开稳定性、提高风格强度，让语气起伏更明显
        voice_settings.update(elevenlabs_voice_settings(emotion))
        if style:
            voice_settings.update(elevenlabs_voice_settings(style))
        if speed and abs(speed - 1.0) > 0.01:
            voice_settings["speed"] = round(speed, 2)

        return {
            "text": tagged,
            "model_id": self.model,
            "voice_settings": voice_settings,
        }

    # ── 接口实现 ────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        emotion: str | None = None,
        style: str | None = None,
    ) -> bytes:
        voice_id = self._resolve_voice(voice)
        if not voice_id:
            raise ValueError("ElevenLabs 未配置 voice_id，请在设置中填写音色 ID")

        endpoint = f"/text-to-speech/{voice_id}"
        if self.use_stream_endpoint:
            endpoint = f"{endpoint}/stream"
        url = f"{self.base_url}{endpoint}"

        payload = self._build_payload(text, voice_id, speed, emotion, style)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                json=payload,
                params={"output_format": self.output_format},
                headers=self._headers(),
            )
            if response.is_error:
                logger.error(
                    "ElevenLabs TTS error %s: %s | model=%s voice=%s text_len=%d",
                    response.status_code,
                    response.text[:500],
                    self.model,
                    voice_id,
                    len(text),
                )
            response.raise_for_status()
            return response.content

    async def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        emotion: str | None = None,
        style: str | None = None,
    ) -> AsyncIterator[bytes]:
        """边生成边产出 MP3 分片（走 `/stream` 端点，首包明显更早）。"""
        voice_id = self._resolve_voice(voice)
        if not voice_id:
            raise ValueError("ElevenLabs 未配置 voice_id，请在设置中填写音色 ID")

        url = f"{self.base_url}/text-to-speech/{voice_id}/stream"
        payload = self._build_payload(text, voice_id, speed, emotion, style)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                params={"output_format": self.output_format},
                headers=self._headers(),
            ) as response:
                if response.is_error:
                    await response.aread()
                    logger.error(
                        "ElevenLabs stream error %s: %s | model=%s voice=%s",
                        response.status_code,
                        response.text[:500],
                        self.model,
                        voice_id,
                    )
                    response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

    async def list_voices(self) -> list[dict[str, Any]]:
        """列出账号下可用音色（含官方预置音色）。"""
        if not self._has_key():
            return []
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/voices",
                headers={"xi-api-key": self.api_key, "accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        voices = payload.get("voices", [])
        return [
            {
                "id": item.get("voice_id", ""),
                "name": item.get("name", ""),
                "labels": item.get("labels", {}),
                "category": item.get("category", ""),
            }
            for item in voices
            if item.get("voice_id")
        ]

    def _has_key(self) -> bool:
        key = (self.api_key or "").strip()
        return bool(key) and key != "sk-xxx" and not key.startswith("sk-xxx")

    async def is_available(self) -> bool:
        return self._has_key()
