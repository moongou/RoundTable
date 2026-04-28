"""语音服务工厂

根据配置创建对应的 TTS/ASR 提供商实例。
"""

from __future__ import annotations

import logging

from app.config import LOCAL_SERVICE_DEFAULTS, settings
from app.voice.base import ASRProvider, TTSProvider
from app.voice.chattts import ChatTTSProvider
from app.voice.cosyvoice import CosyVoiceProvider
from app.voice.edge_tts import EdgeTTSProvider
from app.voice.gateway import GatewayTTSProvider
from app.voice.openai_tts import OpenAITTSProvider
from app.voice.openai_whisper import OpenAIWhisperProvider
from app.voice.standalone_ws_asr import StandaloneWsAsrProvider

logger = logging.getLogger(__name__)


def _local_default_url(provider_id: str, fallback: str = "") -> str:
    return LOCAL_SERVICE_DEFAULTS.get(provider_id, {}).get("url", fallback)


def _cloud_tts_provider(provider_id: str, base_url: str | None = None) -> OpenAITTSProvider:
    resolved_url = base_url or settings.get_voice_service_url(provider_id)
    return OpenAITTSProvider(
        base_url=resolved_url,
        api_key=settings.get_voice_service_api_key(provider_id),
        model=settings.get_voice_service_model(provider_id),
        default_voice=settings.get_tts_voice_for_provider(provider_id),
    )


def _local_http_tts_provider(provider_id: str, base_url: str | None = None) -> GatewayTTSProvider:
    resolved_url = base_url or settings.get_voice_service_url(provider_id)
    return GatewayTTSProvider(
        service=provider_id,
        service_url=resolved_url,
        openvoice_url=resolved_url if provider_id == "openvoice" else None,
        health_path=LOCAL_SERVICE_DEFAULTS.get(provider_id, {}).get("health", "/health"),
    )


def create_tts_provider(provider_id: str | None = None) -> TTSProvider:
    """创建 TTS 提供商实例。

    Args:
        provider_id: 提供商 ID，为 None 时使用 settings.tts_provider。

    Returns:
        TTSProvider 实例。
    """
    pid = provider_id or settings.tts_provider

    if pid == "chattts":
        return ChatTTSProvider(
            base_url=settings.get_voice_service_url("chattts")
            or _local_default_url("chattts", "http://localhost:9998"),
        )
    elif pid == "edge_tts":
        return EdgeTTSProvider(
            base_url=settings.get_voice_service_url("edge_tts")
            or settings.tts_url
            or _local_default_url("edge_tts", "http://localhost:5051"),
        )
    elif pid == "cosyvoice":
        url = settings.get_voice_service_url("cosyvoice") or _local_default_url(
            "cosyvoice", "http://localhost:50000"
        )
        return CosyVoiceProvider(base_url=url)
    elif pid == "openai_tts":
        return _cloud_tts_provider(pid)
    elif pid == "siliconflow_tts":
        return _cloud_tts_provider(pid)
    elif pid in ("vibevoice", "fireredtts", "openvoice"):
        return _local_http_tts_provider(pid)
    elif pid == "browser":
        # 浏览器原生 TTS 由前端处理，后端不需要创建提供商
        # 返回 Edge TTS 作为默认后端 TTS
        logger.info("浏览器 TTS 由前端处理，后端默认使用 Edge TTS")
        return EdgeTTSProvider()
    else:
        logger.warning(f"未知的 TTS 提供商: {pid}，回退到 Edge TTS")
        return EdgeTTSProvider()


def create_tts_provider_with_url(provider_id: str, base_url: str = "") -> TTSProvider:
    """创建 TTS 提供商实例，使用指定的 base_url（独立测试用）。

    Args:
        provider_id: 提供商 ID。
        base_url: 服务地址，优先于 settings 配置。
    """
    from app.config import LOCAL_SERVICE_DEFAULTS

    url = base_url or LOCAL_SERVICE_DEFAULTS.get(provider_id, {}).get("url", "")

    if provider_id == "chattts":
        return ChatTTSProvider(base_url=url or "http://localhost:9998")
    elif provider_id == "edge_tts":
        return EdgeTTSProvider(base_url=url or "http://localhost:5051")
    elif provider_id == "cosyvoice":
        return CosyVoiceProvider(base_url=url or "http://localhost:50000")
    elif provider_id == "openai_tts":
        return _cloud_tts_provider(provider_id, base_url=url)
    elif provider_id == "siliconflow_tts":
        return _cloud_tts_provider(provider_id, base_url=url)
    elif provider_id in ("vibevoice", "fireredtts", "openvoice"):
        return _local_http_tts_provider(provider_id, base_url=url)
    else:
        logger.warning(f"未知的 TTS 提供商: {provider_id}，回退到 Edge TTS")
        return EdgeTTSProvider(base_url=url or "http://localhost:5051")


def create_asr_provider(provider_id: str | None = None) -> ASRProvider:
    """创建 ASR 提供商实例。

    Args:
        provider_id: 提供商 ID，为 None 时使用 settings.asr_provider。

    Returns:
        ASRProvider 实例。
    """
    pid = provider_id or settings.asr_provider

    if pid == "funasr":
        from app.voice.funasr import FunASRProvider

        # 优先使用 settings.funasr_url，其次是 settings.asr_url，最后回退到独立 WS 端口。
        default_url = _local_default_url("funasr", "ws://localhost:10095")
        configured_url = settings.funasr_url or settings.asr_url or default_url
        return FunASRProvider(base_url=configured_url)
    elif pid == "openai_whisper":
        return OpenAIWhisperProvider(
            base_url=settings.openai_whisper_base_url or settings.openai_base_url,
            api_key=settings.openai_whisper_api_key or settings.openai_api_key,
            model=settings.openai_whisper_model,
        )
    elif pid == "siliconflow_asr":
        return OpenAIWhisperProvider(
            base_url=settings.siliconflow_asr_base_url or settings.siliconflow_base_url,
            api_key=settings.siliconflow_asr_api_key or settings.siliconflow_api_key,
            model=settings.siliconflow_asr_model,
        )
    elif pid == "groq_whisper":
        return OpenAIWhisperProvider(
            base_url=settings.groq_whisper_base_url,
            api_key=settings.groq_whisper_api_key,
            model=settings.groq_whisper_model,
        )
    elif pid == "capswriter":
        return StandaloneWsAsrProvider(
            base_url=settings.get_voice_service_url("capswriter")
            or _local_default_url("capswriter", "ws://localhost:6016"),
            ws_path="/ws",
            eof_payload='{"type":"eof"}',
            ws_subprotocol="binary",
            protocol="capswriter_json",
        )
    elif pid == "vosk":
        return StandaloneWsAsrProvider(
            base_url=settings.get_voice_service_url("vosk")
            or _local_default_url("vosk", "http://localhost:6702"),
            ws_path="/stream",
            eof_payload="eof",
        )
    elif pid == "browser":
        # 浏览器原生 ASR 由前端处理，但若前端通过 ServerAsrService 将音频发到后端
        # （如用户在前端设置中选择了 funasr/capswriter 等），则根据配置的后端 ASR URL 自动选择。
        configured_url = settings.asr_url or settings.funasr_url or _local_default_url("funasr")
        if configured_url:
            from app.voice.funasr import FunASRProvider

            logger.info(f"浏览器 ASR 由前端处理，后端 ASR 代理使用 {configured_url}")
            return FunASRProvider(base_url=configured_url)
        logger.info("浏览器 ASR 由前端处理，无后端 ASR 配置可用")
        from app.voice.funasr import FunASRProvider

        return FunASRProvider()
    else:
        logger.warning(f"未知的 ASR 提供商: {pid}，回退到 FunASR")
        from app.voice.funasr import FunASRProvider

        return FunASRProvider()
