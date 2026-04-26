"""本地语音服务提供商。

ASR 仍通过本地 voice-services gateway 访问；
TTS 改为按各服务各自的直连 URL 调用，不再依赖 6666 聚合链路。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.config import LOCAL_SERVICE_DEFAULTS, settings
from app.voice.base import ASRProvider, TTSProvider
from app.voice.openvoice_profiles import get_openvoice_profile

logger = logging.getLogger(__name__)

GATEWAY_URL = "http://localhost:6666"
DEFAULT_OPENVOICE_URL = LOCAL_SERVICE_DEFAULTS.get("openvoice", {}).get(
    "url", "http://localhost:6707"
)


class GatewayTTSProvider(TTSProvider):
    """通过各自直连 URL 访问本地 TTS 服务。"""

    def __init__(
        self,
        service: str = "vibevoice",
        service_url: str | None = None,
        openvoice_url: str | None = None,
        health_path: str | None = None,
    ):
        self.service = service
        resolved_service_url = (
            service_url
            or settings.get_voice_service_url(service)
            or LOCAL_SERVICE_DEFAULTS.get(service, {}).get("url", "")
        )
        self.service_url = resolved_service_url.rstrip("/")
        resolved_openvoice_url = (
            openvoice_url
            or (self.service_url if service == "openvoice" and self.service_url else "")
            or settings.get_voice_service_url("openvoice")
            or DEFAULT_OPENVOICE_URL
        )
        self.openvoice_url = resolved_openvoice_url.rstrip("/")
        self.health_path = (
            health_path
            or LOCAL_SERVICE_DEFAULTS.get(service, {}).get("health")
            or "/health"
        )

    async def _synthesize_direct(
        self,
        text: str,
        *,
        speaker: str,
        speed: float = 1.0,
    ) -> bytes:
        payload = {"text": text, "speaker": speaker}
        if speed != 1.0:
            payload["speed"] = speed
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{self.service_url}/synthesize",
                json=payload,
            )
            response.raise_for_status()
            return response.content

    def _resolve_speaker(self, voice: str) -> str:
        normalized = (voice or "default").strip()
        if self.service == "openvoice":
            if normalized in {"", "default", "alloy"} or normalized.startswith("zh-"):
                return "zh"
            return normalized
        if (
            normalized in {"", "default", "alloy"}
            or normalized.startswith("zh-")
            or normalized.startswith("ov:")
        ):
            return "zh"
        return normalized

    async def _synthesize_openvoice_profile(
        self,
        text: str,
        *,
        profile_id: str,
        speed: float = 1.0,
    ) -> bytes:
        profile = get_openvoice_profile(profile_id)
        if profile is None:
            return await self._synthesize_direct(
                text,
                speaker="zh",
                speed=speed,
            )

        reference_audio = Path(profile.reference_audio)
        if not reference_audio.is_file():
            logger.warning(
                "OpenVoice profile reference missing: %s -> %s",
                profile.profile_id,
                reference_audio,
            )
            return await self._synthesize_direct(
                text,
                speaker=profile.base_speaker,
                speed=speed,
            )

        files = {
            "ref_audio": (
                reference_audio.name,
                reference_audio.read_bytes(),
                "audio/wav",
            )
        }
        data = {
            "text": text,
            "speaker": profile.base_speaker,
            "language": "ZH",
            "speed": str(speed),
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{self.openvoice_url}/synthesize",
                data=data,
                files=files,
            )
            response.raise_for_status()
            return response.content

    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> bytes:
        if self.service == "openvoice" and voice.startswith("ov:"):
            return await self._synthesize_openvoice_profile(
                text,
                profile_id=voice,
                speed=speed,
            )

        return await self._synthesize_direct(
            text,
            speaker=self._resolve_speaker(voice),
            speed=speed,
        )

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.service_url}{self.health_path}")
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "application/json" in content_type:
                        data = resp.json()
                        if isinstance(data, dict) and "status" in data:
                            return data.get("status") == "healthy"
                    return True
            return False
        except Exception:
            return False


class GatewayASRProvider(ASRProvider):
    """通过 gateway 访问本地 ASR 服务 (CapsWriter/Vosk)。"""

    def __init__(self, service: str = "capswriter", gateway_url: str = GATEWAY_URL):
        self.service = service
        self.gateway_url = gateway_url.rstrip("/")

    def _normalize_audio_for_gateway(self, audio_data: bytes, format: str) -> tuple[bytes, str]:
        fmt = (format or "wav").lower().strip()
        if fmt in ("wav", "pcm", "raw"):
            return audio_data, "wav" if fmt == "raw" else fmt

        if fmt not in ("webm", "ogg", "mp3", "m4a", "aac", "opus"):
            return audio_data, fmt

        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "本地 ASR 收到压缩音频，但系统未安装 ffmpeg，无法转成 WAV。"
                "请安装 ffmpeg，或改用浏览器原生 ASR。"
            )

        src_path = ""
        dst_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as src_file:
                src_file.write(audio_data)
                src_path = src_file.name
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst_file:
                dst_path = dst_file.name

            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    src_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    dst_path,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")

            with open(dst_path, "rb") as audio_file:
                return audio_file.read(), "wav"
        finally:
            for path in (src_path, dst_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        normalized_audio, normalized_format = self._normalize_audio_for_gateway(audio_data, format)
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Gateway proxies multipart to backend /transcribe
            data = httpx.Request("POST", "/dummy")
            response = await client.post(
                f"{self.gateway_url}/asr/{self.service}",
                content=normalized_audio,
                headers={"Content-Type": f"audio/{normalized_format}"},
            )
            if response.status_code != 200:
                # Try multipart form
                files = {
                    "audio": (
                        f"audio.{normalized_format}",
                        normalized_audio,
                        f"audio/{normalized_format}",
                    )
                }
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
