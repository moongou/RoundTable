"""MiniMax Speech TTS 提供商

MiniMax Speech 是中文文本转语音的顶级梯队：支持 9 种情绪字段、音色混合、
参考音色复刻，国内直连延迟低。

- 音质优先：model = `speech-2.8-hd`
- 速度优先：model = `speech-2.8-turbo`
- emotion 取值：happy / sad / angry / fearful / disgusted / surprised /
  calm / fluent / whisper（speech-2.8 系列不支持 whisper）
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx

from app.voice.base import TTSProvider
from app.voice.emotion import strip_stage_hints, to_minimax_emotion

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"


class MiniMaxTTSProvider(TTSProvider):
    """MiniMax 语音合成提供商（支持情绪参数）。"""

    supports_emotion = True
    supports_streaming = True

    def __init__(
        self,
        api_key: str = "",
        group_id: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = "speech-2.8-hd",
        default_voice: str = "",
        sample_rate: int = 32000,
        bitrate: int = 128000,
        audio_format: str = "mp3",
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.group_id = group_id
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or "speech-2.8-hd"
        self.default_voice = default_voice
        self.sample_rate = sample_rate
        self.bitrate = bitrate
        self.audio_format = audio_format or "mp3"
        self.timeout = timeout

    # ── 内部工具 ────────────────────────────────────────────────────────────

    def _resolve_voice(self, voice: str | None) -> str:
        requested = (voice or "").strip()
        default = (self.default_voice or "").strip()
        if requested.lower() in {"alloy", "default", ""}:
            return default
        return requested

    def _endpoint(self, path: str, extra_params: dict[str, str] | None = None) -> str:
        params: dict[str, str] = {}
        if self.group_id:
            params["GroupId"] = self.group_id
        if extra_params:
            params.update(extra_params)
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _build_payload(
        self,
        text: str,
        voice_id: str,
        speed: float,
        emotion: str | None,
    ) -> dict[str, Any]:
        spoken = strip_stage_hints(text)
        voice_setting: dict[str, Any] = {
            "voice_id": voice_id,
            "speed": round(speed, 2) if speed else 1.0,
            "vol": 1.0,
            "pitch": 0,
        }
        mapped = to_minimax_emotion(emotion, self.model)
        if mapped:
            voice_setting["emotion"] = mapped

        return {
            "model": self.model,
            "text": spoken,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "bitrate": self.bitrate,
                "format": self.audio_format,
                "channel": 1,
            },
        }

    @staticmethod
    def _extract_audio(payload_text: str) -> bytes:
        """从响应体取出音频：兼容 JSON（hex）与 SSE 分片两种返回。"""
        body = (payload_text or "").strip()
        if not body:
            return b""
        if body.startswith("{"):
            data = json.loads(body)
            inner = data.get("data") or {}
            hex_audio = inner.get("audio") or ""
            if hex_audio:
                return bytes.fromhex(hex_audio)
            # 有些兼容网关直接给 base64
            b64_audio = inner.get("audio_base64") or data.get("audio_base64") or ""
            if b64_audio:
                import base64

                return base64.b64decode(b64_audio)
            raise ValueError(f"MiniMax 返回异常: {body[:300]}")

        chunks: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                item = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            hex_audio = (item.get("data") or {}).get("audio") or ""
            if hex_audio:
                chunks.append(hex_audio)
        if not chunks:
            raise ValueError(f"MiniMax 返回异常: {body[:300]}")
        return bytes.fromhex("".join(chunks))

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
            raise ValueError("MiniMax 未配置 voice_id，请在设置中填写音色 ID")

        payload = self._build_payload(text, voice_id, speed, emotion or style)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._endpoint("/t2a_v2"),
                json=payload,
                headers=headers,
            )
            if response.is_error:
                logger.error(
                    "MiniMax TTS error %s: %s | model=%s voice=%s text_len=%d",
                    response.status_code,
                    response.text[:500],
                    self.model,
                    voice_id,
                    len(text),
                )
            response.raise_for_status()
            return self._extract_audio(response.text)

    async def synthesize_stream(
        self,
        text: str,
        voice: str | None = None,
        speed: float = 1.0,
        emotion: str | None = None,
        style: str | None = None,
    ) -> AsyncIterator[bytes]:
        """边生成边产出音频分片（MiniMax 的 SSE 流，逐块 hex 音频）。"""
        voice_id = self._resolve_voice(voice)
        if not voice_id:
            raise ValueError("MiniMax 未配置 voice_id，请在设置中填写音色 ID")

        payload = self._build_payload(text, voice_id, speed, emotion or style)
        payload["stream"] = True
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "accept": "text/event-stream",
        }

        buffer = ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", self._endpoint("/t2a_v2"), json=payload, headers=headers
            ) as response:
                if response.is_error:
                    await response.aread()
                    logger.error(
                        "MiniMax stream error %s: %s | model=%s voice=%s",
                        response.status_code,
                        response.text[:500],
                        self.model,
                        voice_id,
                    )
                    response.raise_for_status()

                async for chunk in response.aiter_text():
                    buffer += chunk
                    while True:
                        line, separator, rest = buffer.partition("\n")
                        if not separator:
                            break
                        buffer = rest
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            item = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        hex_audio = (item.get("data") or {}).get("audio") or ""
                        if hex_audio:
                            yield bytes.fromhex(hex_audio)

    async def list_voices(self) -> list[dict[str, Any]]:
        """列出系统预置音色与用户复刻音色。"""
        if not self._has_key():
            return []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            for voice_type in ("system", "cloned"):
                try:
                    url = self._endpoint("/get_voice", {"voice_type": voice_type})
                    response = await client.get(url, headers=headers)
                    if response.is_error:
                        continue
                    payload = response.json()
                except Exception as exc:  # noqa: BLE001 - 音色列表失败不影响主流程
                    logger.debug("MiniMax list_voices %s failed: %s", voice_type, exc)
                    continue
                items = payload.get("system_voice") or payload.get("cloned_voice") or []
                for item in items:
                    voice_id = item.get("voice_id", "")
                    if voice_id:
                        results.append(
                            {
                                "id": voice_id,
                                "name": item.get("display_name") or item.get("name") or voice_id,
                                "type": voice_type,
                            }
                        )
        return results

    def _has_key(self) -> bool:
        key = (self.api_key or "").strip()
        return bool(key) and not key.startswith("sk-xxx")

    async def is_available(self) -> bool:
        return self._has_key()
