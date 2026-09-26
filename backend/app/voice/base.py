"""语音服务抽象基类

定义 TTS 和 ASR 提供商的接口契约。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class TTSProvider(ABC):
    """文本转语音（TTS）提供商抽象基类。"""

    # 能力声明：支持情绪/语气的子类覆写为 True，synthesize_safe 才会透传对应参数。
    # 这样老 provider 无需改签名即可继续工作。
    supports_emotion: bool = False
    supports_style: bool = False
    # 是否支持上游流式返回（边生成边下发给客户端）
    supports_streaming: bool = False

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

    async def synthesize_safe(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
        emotion: str | None = None,
        style: str | None = None,
    ) -> bytes:
        """带语气降级能力的合成入口。

        只有声明了对应能力的 provider 才会收到 emotion / style 参数，
        其余情况自动退回纯文本合成，避免老实现因签名不兼容而报错。
        """
        extra: dict[str, str] = {}
        if emotion is not None and getattr(self, "supports_emotion", False):
            extra["emotion"] = emotion
        if style is not None and getattr(self, "supports_style", False):
            extra["style"] = style
        return await self.synthesize(text, voice, speed, **extra)

    async def synthesize_stream(
        self,
        text: str,
        voice: str = "alloy",
        speed: float = 1.0,
        emotion: str | None = None,
        style: str | None = None,
    ) -> AsyncIterator[bytes]:
        """流式合成：边生成边产出音频分片。

        默认实现直接复用一次性合成并在末尾整体产出，保证所有 provider 都能接入
        流式端点；支持上游流式的 provider 应覆写为真正的分片输出。
        """
        if not getattr(self, "supports_streaming", False):
            extra: dict[str, str] = {}
            if emotion is not None and self.supports_emotion:
                extra["emotion"] = emotion
            if style is not None and self.supports_style:
                extra["style"] = style
            audio = await self.synthesize(text, voice, speed, **extra)
            if audio:
                yield audio
            return

        # 声明支持流式却未覆写时，退回一次性合成，避免空音频。
        audio = await self.synthesize_safe(
            text, voice=voice, speed=speed, emotion=emotion, style=style
        )
        if audio:
            yield audio

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
