"""静音 TTS 提供商

用于「禁用语音合成」选项：此前选择 disabled 时后端会静默回退到 Edge TTS 继续发声，
这里明确返回一个极短的静音片段，让前端播放链路不至于拿到空数据而报错。
"""

from __future__ import annotations

import struct
from typing import Any

from app.voice.base import TTSProvider


def silent_wav(seconds: float = 0.25, sample_rate: int = 24000) -> bytes:
    """生成一段无声的 16bit 单声道 WAV。

    Args:
        seconds: 时长，默认 0.25 秒，足够触发「播放结束」事件又不产生噪声。
        sample_rate: 采样率。

    Returns:
        WAV 容器字节。
    """
    frames = int(max(0.05, seconds) * sample_rate)
    silence = b"\x00\x00" * frames

    data_size = len(silence)
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16),
            b"data",
            struct.pack("<I", data_size),
        ]
    )
    return header + silence


class SilentTTSProvider(TTSProvider):
    """不发声的 TTS 实现。"""

    supports_emotion = False
    supports_style = False

    async def synthesize(
        self,
        text: str,  # noqa: ARG002 - 保持接口一致
        voice: str | None = None,  # noqa: ARG002
        speed: float = 1.0,  # noqa: ARG002
        emotion: str | None = None,  # noqa: ARG002
        style: str | None = None,  # noqa: ARG002
    ) -> bytes:
        return silent_wav()

    async def list_voices(self) -> list[dict[str, Any]]:
        return []

    async def is_available(self) -> bool:
        return True
