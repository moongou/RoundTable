"""FunASR ASR 提供商

通过本地的 FunASR 服务提供语音识别：
  - HTTP API:    http://localhost:8000/recognition  (默认)
  - WebSocket:   ws://localhost:10095              (将 base_url 设为 ws:// 前缀启用)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from io import BytesIO

import httpx
import websockets
from websockets.exceptions import InvalidStatus

from app.voice.base import ASRProvider

logger = logging.getLogger(__name__)


class FunASRProvider(ASRProvider):
    """FunASR ASR 提供商，通过本地 FunASR 服务进行语音识别。"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        if not audio_data:
            logger.warning("FunASR 收到空音频数据")
            return ""

        if self.base_url.startswith("ws://") or self.base_url.startswith("wss://"):
            return await self._transcribe_via_websocket(audio_data, format=format)

        # FunASR HTTP API: POST /recognition with multipart audio upload
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                files = {"audio": (f"audio.{format}", audio_data, f"audio/{format}")}
                response = await client.post(
                    f"{self.base_url}/recognition",
                    files=files,
                )
                response.raise_for_status()

                data = response.json()
                # FunASR HTTP 响应格式有多种，按优先级依次尝试：
                # 1. {"result": "text"}  (runtime/python/http/server.py 常见)
                # 2. {"text": "text"}
                # 3. [{"text": "...", ...}, ...]  (列表格式)
                # 4. {"code": 0, "data": {"result": "text"}}
                if isinstance(data, list):
                    texts = [item.get("text", "") for item in data if isinstance(item, dict)]
                    return " ".join(t.strip() for t in texts if t.strip())
                if isinstance(data, dict):
                    for key in ("result", "text"):
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                    # {"code": 0, "data": {"result": "..."}}
                    nested = data.get("data", {})
                    if isinstance(nested, dict):
                        for key in ("result", "text"):
                            val = nested.get(key)
                            if isinstance(val, str) and val.strip():
                                return val.strip()
                return ""
        except httpx.ConnectError as e:
            logger.error(f"FunASR 服务连接失败 ({self.base_url}): {e}")
            raise RuntimeError(
                f"FunASR 服务未启动或无法连接 ({self.base_url})。"
                f"请确认 FunASR 已在运行，或将 asr_provider 切换为其他可用服务。"
            ) from e
        except httpx.HTTPStatusError as e:
            logger.error(f"FunASR HTTP 错误: {e.response.status_code} - {e.response.text[:200]}")
            raise RuntimeError(
                f"FunASR 服务返回错误 {e.response.status_code}。"
                f"请检查 FunASR 日志确认服务状态。"
            ) from e
        except Exception as e:
            logger.error(f"FunASR 识别异常: {e}")
            raise RuntimeError(f"FunASR 识别失败: {e}") from e

    async def _transcribe_via_websocket(self, audio_data: bytes, format: str = "wav") -> str:
        pcm_data, sample_rate = self._to_pcm_stream(audio_data, format)
        request = {
            "chunk_size": [5, 10, 5],
            "wav_name": "backend",
            "is_speaking": True,
            "chunk_interval": 10,
            "itn": True,
            "mode": "2pass",
            "wav_format": "PCM",
            "audio_fs": sample_rate,
        }

        texts: list[str] = []
        async with websockets.connect(
            self.base_url,
            subprotocols=["binary"],
            open_timeout=8.0,
            close_timeout=2.0,
        ) as ws:
            await ws.send(json.dumps(request, ensure_ascii=False))

            chunk_bytes = max(sample_rate // 10 * 2, 1600)
            for i in range(0, len(pcm_data), chunk_bytes):
                await ws.send(pcm_data[i : i + chunk_bytes])
                await asyncio.sleep(0.01)

            await ws.send(json.dumps({"is_speaking": False}))

            deadline = asyncio.get_running_loop().time() + 10.0
            while asyncio.get_running_loop().time() < deadline:
                timeout = max(0.2, deadline - asyncio.get_running_loop().time())
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 1.5))
                except asyncio.TimeoutError:
                    continue

                if not isinstance(message, str):
                    continue

                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue

                for key in ("text", "text_offline", "text_online", "result"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        texts.append(value.strip())

                is_final = payload.get("is_final") is True or payload.get("is_speaking") is False
                if is_final and texts:
                    break

        if not texts:
            raise RuntimeError("FunASR WebSocket 未返回识别文本")

        return max(texts, key=len)

    def _to_pcm_stream(self, audio_data: bytes, format: str) -> tuple[bytes, int]:
        fmt = (format or "").lower().strip()
        if fmt in ("pcm", "raw"):
            return audio_data, 16000

        if fmt == "wav":
            with wave.open(BytesIO(audio_data), "rb") as wav_file:
                sample_rate = wav_file.getframerate() or 16000
                sample_width = wav_file.getsampwidth()
                channels = wav_file.getnchannels()
                frames = wav_file.readframes(wav_file.getnframes())

            if sample_width != 2:
                raise ValueError("FunASR WebSocket 仅支持 16-bit PCM WAV")
            if channels != 1:
                raise ValueError("FunASR WebSocket 仅支持单声道 WAV")
            return frames, sample_rate

        # Browser media recorder commonly uploads webm/ogg; convert to mono PCM WAV then feed WS protocol.
        if fmt in ("webm", "ogg", "mp3", "m4a", "aac", "opus"):
            if not shutil.which("ffmpeg"):
                raise ValueError(
                    "FunASR WebSocket 收到压缩音频，且未安装 ffmpeg，无法转码。"
                    "请安装 ffmpeg 或将 funasr_url 设为 HTTP 地址（http://localhost:8000）使用批量转录模式。"
                )
            return self._convert_to_pcm_with_ffmpeg(audio_data, fmt)

        raise ValueError(
            "FunASR WebSocket 模式仅支持 PCM/WAV 输入。"
            "当前为非 PCM 音频，请改用 HTTP FunASR (http://localhost:8000) 或安装 ffmpeg 进行自动转码。"
        )

    def _convert_to_pcm_with_ffmpeg(self, audio_data: bytes, src_format: str) -> tuple[bytes, int]:
        src_path = ""
        dst_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{src_format}", delete=False) as src_file:
                src_file.write(audio_data)
                src_path = src_file.name

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dst_file:
                dst_path = dst_file.name

            cmd = [
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
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")

            with open(dst_path, "rb") as f:
                wav_data = f.read()
            return self._to_pcm_stream(wav_data, "wav")
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("音频转码超时") from e
        except Exception as e:
            raise RuntimeError(f"音频转码失败: {e}") from e
        finally:
            for p in (src_path, dst_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    async def is_available(self) -> bool:
        try:
            if self.base_url.startswith("ws://") or self.base_url.startswith("wss://"):
                async with websockets.connect(
                    self.base_url,
                    subprotocols=["binary"],
                    open_timeout=3.0,
                    close_timeout=1.0,
                ):
                    return True

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/")
                return resp.status_code < 500
        except InvalidStatus as e:
            status_code = getattr(e.response, "status_code", None)
            return status_code is not None and status_code < 500
        except Exception:
            return False