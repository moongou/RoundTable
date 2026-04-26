"""独立 WebSocket ASR 提供商

用于连接独立部署的流式 ASR 服务（如 CapsWriter/Vosk）。
协议约定：
- 客户端发送二进制 PCM16 单声道 16kHz 音频分片
- 客户端发送文本帧 {"type":"eof"} 结束
- 服务端返回 JSON 帧，字段至少包含 type/text
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from array import array
from io import BytesIO
from urllib.parse import urlparse

import httpx
import websockets

from app.voice.base import ASRProvider


class StandaloneWsAsrProvider(ASRProvider):
    def __init__(
        self,
        base_url: str,
        ws_path: str,
        eof_payload: str = '{"type":"eof"}',
        ws_subprotocol: str | None = None,
        protocol: str = "binary_pcm16",
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.ws_path = ws_path if ws_path.startswith("/") else f"/{ws_path}"
        self.eof_payload = eof_payload
        self.ws_subprotocol = (ws_subprotocol or "").strip() or None
        self.protocol = protocol

    def _build_ws_url(self) -> str:
        parsed = urlparse(self.base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"无效的 ASR 服务地址: {self.base_url}")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}{self.ws_path}"

    def _to_pcm16_mono_16k(self, audio_data: bytes, fmt: str) -> bytes:
        normalized = (fmt or "wav").strip().lower()

        if normalized in {"pcm", "pcm16", "raw"}:
            return audio_data

        if normalized == "wav":
            with wave.open(BytesIO(audio_data), "rb") as wav_file:
                sample_rate = wav_file.getframerate() or 16000
                sample_width = wav_file.getsampwidth()
                channels = wav_file.getnchannels()
                frames = wav_file.readframes(wav_file.getnframes())

            if sample_rate == 16000 and sample_width == 2 and channels == 1:
                return frames

            if not shutil.which("ffmpeg"):
                raise RuntimeError(
                    "收到的 WAV 不是 16kHz/16bit/单声道，且系统未安装 ffmpeg，无法转码。"
                )
            return self._convert_with_ffmpeg(audio_data, "wav")

        if normalized in {"webm", "ogg", "mp3", "m4a", "aac", "opus"}:
            if not shutil.which("ffmpeg"):
                raise RuntimeError("收到压缩音频，但系统未安装 ffmpeg，无法转码为 PCM。")
            return self._convert_with_ffmpeg(audio_data, normalized)

        raise RuntimeError(f"不支持的音频格式: {fmt}")

    def _convert_with_ffmpeg(self, audio_data: bytes, src_format: str) -> bytes:
        src_path = ""
        dst_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{src_format}", delete=False) as src_file:
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
                    "-sample_fmt",
                    "s16",
                    dst_path,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")

            with open(dst_path, "rb") as wav_file:
                wav_bytes = wav_file.read()
            return self._to_pcm16_mono_16k(wav_bytes, "wav")
        finally:
            for path in (src_path, dst_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def _convert_with_ffmpeg_to_f32le(self, audio_data: bytes, src_format: str) -> bytes:
        src_path = ""
        dst_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{src_format}", delete=False) as src_file:
                src_file.write(audio_data)
                src_path = src_file.name
            with tempfile.NamedTemporaryFile(suffix=".f32le", delete=False) as dst_file:
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
                    "-f",
                    "f32le",
                    "-acodec",
                    "pcm_f32le",
                    dst_path,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")

            with open(dst_path, "rb") as raw_file:
                return raw_file.read()
        finally:
            for path in (src_path, dst_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def _pcm16_to_float32(self, pcm_data: bytes) -> bytes:
        samples = array("h")
        samples.frombytes(pcm_data[: len(pcm_data) - (len(pcm_data) % 2)])
        if sys.byteorder == "big":
            samples.byteswap()
        floats = array("f", (max(-1.0, min(1.0, sample / 32768.0)) for sample in samples))
        return floats.tobytes()

    def _to_float32_mono_16k(self, audio_data: bytes, fmt: str) -> bytes:
        normalized = (fmt or "wav").strip().lower()

        if normalized in {"f32le", "float32"}:
            return audio_data

        if normalized in {"pcm", "pcm16", "raw"}:
            return self._pcm16_to_float32(audio_data)

        if normalized == "wav" and not shutil.which("ffmpeg"):
            with wave.open(BytesIO(audio_data), "rb") as wav_file:
                sample_rate = wav_file.getframerate() or 16000
                sample_width = wav_file.getsampwidth()
                channels = wav_file.getnchannels()
                frames = wav_file.readframes(wav_file.getnframes())
            if sample_rate == 16000 and sample_width == 2 and channels == 1:
                return self._pcm16_to_float32(frames)
            raise RuntimeError(
                "收到的 WAV 不是 16kHz/16bit/单声道，且系统未安装 ffmpeg，无法转码。"
            )

        if not shutil.which("ffmpeg"):
            raise RuntimeError("CapsWriter 需要 ffmpeg 将上传音频转为 16kHz float32。")
        return self._convert_with_ffmpeg_to_f32le(audio_data, normalized)

    async def _transcribe_capswriter_json(self, audio_data: bytes, fmt: str) -> str:
        float_data = self._to_float32_mono_16k(audio_data, fmt)
        ws_url = self._build_ws_url()

        task_id = f"roundtable-{uuid.uuid4()}"
        time_start = time.time()
        partial_text = ""
        final_text = ""
        audio_duration = len(float_data) / (16000 * 4) if float_data else 0.0

        connect_kwargs = {
            "proxy": None,
            "open_timeout": 8.0,
            "close_timeout": 2.0,
            "max_size": None,
        }
        if self.ws_subprotocol:
            connect_kwargs["subprotocols"] = [self.ws_subprotocol]

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            chunk_bytes = 16000 * 4
            for offset in range(0, len(float_data), chunk_bytes):
                chunk = float_data[offset : offset + chunk_bytes]
                await ws.send(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "seg_duration": 60,
                            "seg_overlap": 4,
                            "is_final": False,
                            "time_start": time_start,
                            "time_frame": time.time(),
                            "source": "file",
                            "data": base64.b64encode(chunk).decode("utf-8"),
                            "context": "",
                        }
                    )
                )
                await asyncio.sleep(0.002)

            await ws.send(
                json.dumps(
                    {
                        "task_id": task_id,
                        "seg_duration": 60,
                        "seg_overlap": 4,
                        "is_final": True,
                        "time_start": time_start,
                        "time_frame": time.time(),
                        "source": "file",
                        "data": "",
                        "context": "",
                    }
                )
            )

            deadline = asyncio.get_running_loop().time() + max(20.0, audio_duration * 2 + 8.0)
            while asyncio.get_running_loop().time() < deadline:
                timeout = max(0.2, deadline - asyncio.get_running_loop().time())
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 1.5))
                except TimeoutError:
                    continue

                if not isinstance(message, str):
                    continue

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue

                error = str(payload.get("error", "")).strip()
                if error:
                    raise RuntimeError(error)

                text = str(payload.get("text_accu") or payload.get("text") or "").strip()
                if text:
                    if payload.get("is_final") is True:
                        final_text = text
                    else:
                        partial_text = text

                if payload.get("is_final") is True:
                    break

        recognized = final_text or partial_text
        if not recognized:
            raise RuntimeError("CapsWriter 未返回识别文本")
        return recognized

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        if self.protocol == "capswriter_json":
            return await self._transcribe_capswriter_json(audio_data, format)

        pcm_data = self._to_pcm16_mono_16k(audio_data, format)
        ws_url = self._build_ws_url()

        partial_text = ""
        final_text = ""

        connect_kwargs = {
            "proxy": None,
            "open_timeout": 8.0,
            "close_timeout": 2.0,
            "max_size": 4 * 1024 * 1024,
        }
        if self.ws_subprotocol:
            connect_kwargs["subprotocols"] = [self.ws_subprotocol]

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            chunk_bytes = 3200
            for i in range(0, len(pcm_data), chunk_bytes):
                await ws.send(pcm_data[i : i + chunk_bytes])
                await asyncio.sleep(0.005)

            await ws.send(self.eof_payload)

            deadline = asyncio.get_running_loop().time() + 12.0
            while asyncio.get_running_loop().time() < deadline:
                timeout = max(0.2, deadline - asyncio.get_running_loop().time())
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 1.5))
                except TimeoutError:
                    continue

                if not isinstance(message, str):
                    continue

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue

                msg_type = str(payload.get("type", "")).strip().lower()
                text = str(payload.get("text", "")).strip()
                if text:
                    if msg_type in {"final", "result"}:
                        final_text = text
                    else:
                        partial_text = text

                if msg_type in {"final", "result", "error"}:
                    break

        recognized = final_text or partial_text
        if not recognized:
            raise RuntimeError("独立流式 ASR 未返回识别文本")
        return recognized

    async def is_available(self) -> bool:
        scheme = urlparse(self.base_url).scheme.lower()
        try:
            if scheme in {"ws", "wss"}:
                connect_kwargs = {
                    "proxy": None,
                    "open_timeout": 3.0,
                    "close_timeout": 1.0,
                }
                if self.ws_subprotocol:
                    connect_kwargs["subprotocols"] = [self.ws_subprotocol]
                async with websockets.connect(self._build_ws_url(), **connect_kwargs):
                    return True

            health_url = f"{self.base_url}/health"
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(health_url)
                return 200 <= resp.status_code < 300
        except Exception:
            return False
