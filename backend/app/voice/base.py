"""语音服务抽象基类

定义 TTS 和 ASR 提供商的接口契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TTSProvider(ABC):
    """文本转语音（TTS）提供商抽象基类。"""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
    ) -> bytes:
        """将文本合成为语音音频。

        Args:
            text: 要合成的文本。
            voice: 语音标识（如角色音色名、alloy 等）。
            speed: 语速倍率，默认 1.0。

        Returns:
            音频字节数据（MP3 格式）。
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """检查服务是否可用。"""
        ...


class ASRProvider(ABC):
    """语音转文本（ASR）提供商抽象基类。"""

    @abstractmethod
    async def transcribe(self, audio_data: bytes, format: str = "wav") -> str:
        """将音频转录为文本。

        Args:
            audio_data: 音频字节数据。
            format: 音频格式（wav/mp3/webm 等）。

        Returns:
            转录文本。
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """检查服务是否可用。"""
        ...