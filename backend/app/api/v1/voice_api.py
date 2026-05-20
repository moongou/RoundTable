"""语音 API 端点

提供 TTS（文本转语音）和 ASR（语音转文本）的 HTTP 接口。
"""

from __future__ import annotations

import array
import asyncio
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from io import BytesIO

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config import settings
from app.voice.factory import create_asr_provider, create_tts_provider
from app.voice.openvoice_profiles import openvoice_profile_for_character_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

_WAV_VOLUME_GUARD_PROVIDERS = {
    "chattts",
    "vibevoice",
    "openvoice",
    "fireredtts",
    "cosyvoice",
}


# ── 请求/响应模型 ────────────────────────────────────────────────────────────


class TTSRequest(BaseModel):
    """TTS 请求"""

    text: str
    provider: str | None = None
    voice: str | None = None
    character_id: str | None = None
    speed: float = Field(default=1.0, ge=0.8, le=1.1)


class TTSResponse(BaseModel):
    """TTS 响应元数据（音频数据直接作为二进制返回）"""

    content_type: str = "audio/mpeg"
    duration_seconds: float | None = None


class ASRResponse(BaseModel):
    """ASR 响应"""

    text: str
    confidence: float = 0.0
    language: str | None = None


class ASRRefineRequest(BaseModel):
    """ASR 文本纠错请求"""

    text: str


class ASRRefineResponse(BaseModel):
    """ASR 文本纠错响应"""

    text: str


def _detect_audio_content_type(audio_data: bytes) -> tuple[str, str]:
    if audio_data.startswith(b"RIFF") and audio_data[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if audio_data.startswith(b"ID3") or (
        len(audio_data) >= 2 and audio_data[0] == 0xFF and (audio_data[1] & 0xE0) == 0xE0
    ):
        return "audio/mpeg", "mp3"
    return "application/octet-stream", "bin"


def _should_retry_tts_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code if error.response is not None else None
        return bool(status_code and (status_code >= 500 or status_code == 429))
    return isinstance(error, httpx.RequestError)


def _normalize_wav_with_ffmpeg(audio_data: bytes) -> bytes | None:
    if not shutil.which("ffmpeg"):
        return None

    src_path = ""
    dst_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src_file:
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
                "-af",
                (
                    "acompressor=threshold=-20dB:ratio=2.6:attack=5:release=55:makeup=1.8,"
                    "loudnorm=I=-18:TP=-1.5:LRA=7"
                ),
                "-c:a",
                "pcm_s16le",
                dst_path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            logger.warning(
                "ffmpeg TTS loudness normalization failed: %s",
                result.stderr.strip() or "unknown error",
            )
            return None

        with open(dst_path, "rb") as audio_file:
            normalized_audio = audio_file.read()
        return normalized_audio or None
    except Exception as exc:
        logger.warning("ffmpeg TTS normalization exception: %s", exc)
        return None
    finally:
        for path in (src_path, dst_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def _iter_pcm_samples(frames: bytes, sample_width: int):
    if sample_width == 1:
        return [sample - 128 for sample in frames]

    if sample_width == 2:
        samples = array.array("h")
    elif sample_width == 4:
        samples = array.array("i")
    else:
        return None

    if len(frames) % sample_width != 0:
        return None

    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _measure_pcm_levels(frames: bytes, sample_width: int) -> tuple[int, int] | None:
    samples = _iter_pcm_samples(frames, sample_width)
    if not samples:
        return None

    peak = max(abs(sample) for sample in samples)
    if peak <= 0:
        return (0, 0)

    square_sum = sum(sample * sample for sample in samples)
    rms = int(math.sqrt(square_sum / len(samples)))
    return peak, rms


def _pcm_limits(sample_width: int) -> tuple[int, int] | None:
    if sample_width == 1:
        return (-128, 127)
    if sample_width == 2:
        return (-32768, 32767)
    if sample_width == 4:
        return (-2147483648, 2147483647)
    return None


def _apply_pcm_gain(frames: bytes, sample_width: int, gain: float) -> bytes | None:
    samples = _iter_pcm_samples(frames, sample_width)
    limits = _pcm_limits(sample_width)
    if not samples or limits is None:
        return None

    min_sample, max_sample = limits

    if sample_width == 1:
        adjusted = bytearray(len(samples))
        for index, sample in enumerate(samples):
            scaled = int(round(sample * gain))
            clipped = min(max(scaled, min_sample), max_sample)
            adjusted[index] = clipped + 128
        return bytes(adjusted)

    typecode = "h" if sample_width == 2 else "i"
    adjusted = array.array(
        typecode,
        (
            min(max(int(round(sample * gain)), min_sample), max_sample)
            for sample in samples
        ),
    )
    if sys.byteorder != "little":
        adjusted.byteswap()
    return adjusted.tobytes()


def _normalize_wav_rms(audio_data: bytes) -> bytes:
    try:
        with wave.open(BytesIO(audio_data), "rb") as input_wav:
            if input_wav.getcomptype() != "NONE":
                return audio_data
            params = input_wav.getparams()
            sample_width = input_wav.getsampwidth() or 2
            frames = input_wav.readframes(input_wav.getnframes())
    except (wave.Error, EOFError):
        return audio_data

    if sample_width not in (1, 2, 4) or not frames:
        return audio_data

    levels = _measure_pcm_levels(frames, sample_width)
    if levels is None:
        return audio_data
    peak, rms = levels

    if peak <= 0 or rms <= 0:
        return audio_data

    max_possible = float((1 << (sample_width * 8 - 1)) - 1)
    target_rms = max_possible * 0.19
    desired_gain = target_rms / float(rms)
    headroom_gain = (max_possible * 0.92) / float(peak)
    gain = min(1.85, headroom_gain, max(0.8, desired_gain))

    if 0.97 <= gain <= 1.03:
        return audio_data

    adjusted = _apply_pcm_gain(frames, sample_width, gain)
    if adjusted is None:
        return audio_data

    buffer = BytesIO()
    with wave.open(buffer, "wb") as output_wav:
        output_wav.setparams(params)
        output_wav.writeframes(adjusted)
    return buffer.getvalue()


def _stabilize_tts_audio(audio_data: bytes, suffix: str, provider_id: str) -> tuple[bytes, str]:
    normalized_provider = (provider_id or "").strip().lower()
    if suffix != "wav" or normalized_provider not in _WAV_VOLUME_GUARD_PROVIDERS:
        return audio_data, ""

    ffmpeg_normalized = _normalize_wav_with_ffmpeg(audio_data)
    if ffmpeg_normalized:
        return ffmpeg_normalized, "wav-ffmpeg-loudnorm"

    fallback_normalized = _normalize_wav_rms(audio_data)
    if fallback_normalized != audio_data:
        return fallback_normalized, "wav-rms-guard"

    return audio_data, ""


# ── 角色音色映射 ──────────────────────────────────────────────────────────────


def _get_voice_for_character(character_id: str, provider_id: str | None = None) -> str:
    """根据角色 ID 获取对应的 TTS 音色。

    如果角色有指定的音色，返回该音色；否则返回默认音色。
    需求14：优先级 思想家 YAML > 角色模板 YAML > 默认。

    硅基流动 TTS 专属分配：
    - 主持人李老师 (moderator) → anna
    - 思想家 (thinkers)       → benjamin
    - 小爱 (empath)           → diana
    - 其他角色                → alex
    """
    pid = (provider_id or "").strip().lower()

    if pid == "openvoice":
        profile_id = openvoice_profile_for_character_id(character_id)
        if profile_id:
            return profile_id

    if pid == "siliconflow_tts":
        return _siliconflow_voice_for_character(character_id)

    try:
        from app.core.thinkers import get_thinker

        thinker = get_thinker(character_id)
        if thinker:
            if pid == "openvoice":
                return "ov:thinker_elder"
            voice = (thinker.get("voice") or "").strip()
            if voice:
                return voice
    except Exception:
        pass
    try:
        from app.agents.character_templates import load_all_templates

        templates = load_all_templates()
        if character_id in templates:
            if pid == "openvoice":
                profile_id = openvoice_profile_for_character_id(character_id)
                if profile_id:
                    return profile_id
            tpl = templates[character_id]
            voice = getattr(tpl, "voice", "") or ""
            if voice:
                return voice
    except Exception:
        pass
    return "alloy"


def _siliconflow_voice_for_character(character_id: str) -> str:
    """硅基流动 CosyVoice2 音色分配。

    固定分配：
    - 李老师 → anna
    - 思想家 → benjamin
    - 小爱   → diana
    """
    model = "FunAudioLLM/CosyVoice2-0.5B"

    if character_id == "moderator":
        return f"{model}:anna"
    if character_id == "empath":
        return f"{model}:diana"

    try:
        from app.core.thinkers import get_thinker

        thinker = get_thinker(character_id)
        if thinker:
            return f"{model}:benjamin"
    except Exception:
        pass

    return f"{model}:alex"


def _normalize_siliconflow_voice(voice: str, character_id: str | None = None) -> str:
    """Ensure voice is valid for SiliconFlow CosyVoice2.

    If already in model:voice format, return as-is.
    Otherwise map bare OpenAI-style voice names or use character-based assignment.
    """
    model_prefix = "FunAudioLLM/CosyVoice2-0.5B:"
    if voice.startswith(model_prefix):
        return voice

    if character_id:
        return _siliconflow_voice_for_character(character_id)

    bare = voice.split(":")[-1].strip().lower()
    name_map = {
        "alloy": "alex",
        "echo": "alex",
        "fable": "benjamin",
        "onyx": "benjamin",
        "nova": "diana",
        "shimmer": "anna",
    }
    return f"{model_prefix}{name_map.get(bare, 'alex')}"


# ── TTS 端点 ──────────────────────────────────────────────────────────────────

# 后端 TTS 音频缓存：避免相同文本+音色+provider 的重复合成，减少本机 CPU 压力。
# 键: (provider_id, voice, text)  值: (audio_bytes, media_type, suffix, elapsed_ms, attempts)
_TTS_AUDIO_CACHE: dict[
    tuple[str, str, str],
    tuple[bytes, str, str, float, int],
] = {}
_TTS_AUDIO_CACHE_MAX = 40  # 最多缓存条目数（40 句 × ~150KB ≈ 6MB）
# 正在进行中的合成任务：相同 key 的并发请求共享同一个 Future，避免重复合成。
_TTS_INFLIGHT: dict[tuple[str, str, str], asyncio.Future[tuple[bytes, str, str, float, int]]] = {}


def _tts_cache_key(provider_id: str, voice: str, text: str) -> tuple[str, str, str]:
    return (provider_id, voice, text.strip())


def _tts_cache_get(key: tuple[str, str, str]):
    return _TTS_AUDIO_CACHE.get(key)


def _tts_cache_put(key: tuple[str, str, str], value: tuple[bytes, str, str, float, int]) -> None:
    if len(_TTS_AUDIO_CACHE) >= _TTS_AUDIO_CACHE_MAX:
        # 淘汰最早插入的条目（简单 FIFO，足够本场景使用）
        oldest = next(iter(_TTS_AUDIO_CACHE))
        _TTS_AUDIO_CACHE.pop(oldest, None)
    _TTS_AUDIO_CACHE[key] = value


@router.post("/tts")
async def text_to_speech(request: TTSRequest) -> Response:
    """将文本合成为语音音频。

    Args:
        request: 包含文本、音色和可选角色 ID 的请求。

    Returns:
        音频二进制数据（MP3 格式）。
    """
    provider_id = (request.provider or settings.tts_provider or "").strip()
    requested_voice = (request.voice or "").strip()

    # 确定音色：角色指定的 > 请求指定的 > provider 默认值 > alloy。
    if request.character_id:
        voice = _get_voice_for_character(request.character_id, provider_id)
    elif requested_voice:
        voice = requested_voice
    else:
        voice = settings.get_tts_voice_for_provider(provider_id) or "alloy"

    if provider_id == "siliconflow_tts":
        voice = _normalize_siliconflow_voice(voice, request.character_id)

    logger.info(
        "TTS request: provider=%s character_id=%s voice=%s text_len=%d",
        provider_id,
        request.character_id or "-",
        voice,
        len(request.text),
    )

    cache_key = _tts_cache_key(provider_id, voice, request.text)

    # 命中缓存：直接返回，无需重新合成。
    cached = _tts_cache_get(cache_key)
    if cached is not None:
        cached_audio, cached_media_type, cached_suffix, cached_elapsed_ms, cached_attempts = cached
        logger.debug("TTS cache hit: provider=%s voice=%s text_len=%d", provider_id, voice, len(request.text))
        return Response(
            content=cached_audio,
            media_type=cached_media_type,
            headers={
                "Content-Disposition": f"inline; filename=tts_output.{cached_suffix}",
                "X-Voice-Requested": requested_voice,
                "X-Voice-Used": voice,
                "X-TTS-Provider": provider_id,
                "X-TTS-Attempts": str(cached_attempts),
                "X-TTS-Elapsed-Ms": str(cached_elapsed_ms),
                "X-Audio-Normalized": "cache",
            },
        )

    # 检查是否有相同 key 的合成任务正在进行，若有则共享结果（避免重复合成）。
    loop = asyncio.get_event_loop()
    inflight = _TTS_INFLIGHT.get(cache_key)
    if inflight is not None:
        try:
            audio_data, media_type, suffix, elapsed_ms, attempts = await asyncio.shield(inflight)
            return Response(
                content=audio_data,
                media_type=media_type,
                headers={
                    "Content-Disposition": f"inline; filename=tts_output.{suffix}",
                    "X-Voice-Requested": requested_voice,
                    "X-Voice-Used": voice,
                    "X-TTS-Provider": provider_id,
                    "X-TTS-Attempts": str(attempts),
                    "X-TTS-Elapsed-Ms": str(elapsed_ms),
                    "X-Audio-Normalized": "shared",
                },
            )
        except (Exception, asyncio.CancelledError):
            pass  # 若共享失败或原始合成取消，回退到独立合成

    # 创建 Future 并注册，让并发请求可以共享本次合成结果。
    future: asyncio.Future[tuple[bytes, str, str, float, int]] = loop.create_future()
    _TTS_INFLIGHT[cache_key] = future

    try:
        provider = create_tts_provider(request.provider)
        last_error: Exception | None = None
        audio_data: bytes | None = None
        attempts = 0
        started_at = time.perf_counter()
        for attempt in range(2):
            attempts = attempt + 1
            try:
                audio_data = await provider.synthesize(
                    request.text,
                    voice=voice,
                    speed=request.speed,
                )
                break
            except Exception as e:
                last_error = e
                # 仅对瞬时网络/上游 5xx 做一次快速重试，避免确定性错误额外拖慢响应。
                if attempt == 0 and _should_retry_tts_error(e):
                    await asyncio.sleep(0.12)
                    continue
                break
        if audio_data is None:
            exc = last_error or RuntimeError("unknown tts error")
            # 用 cancel 代替 set_exception，避免 Python 在 Future 被 GC 时发出
            # "Future exception was never retrieved" 警告（cancel 不触发该警告）。
            # 并发等待方会收到 CancelledError，应当回退到各自独立合成。
            future.cancel()
            raise exc
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        media_type, suffix = _detect_audio_content_type(audio_data)
        normalized_audio, normalize_mode = _stabilize_tts_audio(
            audio_data,
            suffix,
            provider_id,
        )
        if normalize_mode:
            media_type, suffix = _detect_audio_content_type(normalized_audio)

        result_tuple = (normalized_audio, media_type, suffix, elapsed_ms, attempts)
        future.set_result(result_tuple)
        # 仅对云端/外部 TTS 服务跳过本地缓存（本地合成每次有少量差异但开销不高，不跳过）
        # speed != 1.0 时跳过缓存，避免不同语速的音频错误命中
        if request.speed == 1.0:
            _tts_cache_put(cache_key, result_tuple)

        return Response(
            content=normalized_audio,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename=tts_output.{suffix}",
                "X-Voice-Requested": requested_voice,
                "X-Voice-Used": voice,
                "X-TTS-Provider": provider_id,
                "X-TTS-Attempts": str(attempts),
                "X-TTS-Elapsed-Ms": str(elapsed_ms),
                "X-Audio-Normalized": normalize_mode,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        if not future.done():
            future.set_exception(e)
        logger.error(f"TTS 合成失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音合成失败: {str(e)}")
    finally:
        _TTS_INFLIGHT.pop(cache_key, None)


# ── ASR 端点 ─────────────────────────────────────────────────────────────────


@router.post("/asr")
async def speech_to_text(
    audio: UploadFile = File(...),
    format: str = Form("wav"),
    provider: str | None = Form(None),
) -> ASRResponse:
    """将语音音频转录为文本。

    Args:
        audio: 上传的音频文件。
        format: 音频格式（wav/mp3/webm 等）。

    Returns:
        包含转录文本和置信度的响应。
    """
    audio_data = await audio.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="音频数据为空")

    provider_id = (provider or "").strip() or None
    effective_provider = provider_id or settings.asr_provider

    try:
        provider_impl = create_asr_provider(provider_id)
        # 先检查服务是否可用，避免直接抛出 500
        if not await provider_impl.is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"语音识别服务 ({effective_provider}) 当前不可用，"
                    "请检查服务是否已启动或在设置中切换其他服务。"
                ),
            )
        text = _refine_transcript_text(await provider_impl.transcribe(audio_data, format=format))
        return ASRResponse(
            text=text,
            confidence=0.9,  # 本地服务无法提供准确置信度
            language="zh",
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        # 来自 ASR 提供商的业务错误（如服务未启动）
        logger.error(f"ASR 业务错误: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"ASR 识别失败: {e}")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {str(e)}")


def _refine_transcript_text(text: str) -> str:
    """轻量级转写后处理：去口语噪声、去重复、补标点。"""
    import re

    v = (text or "").strip()
    if not v:
        return ""

    v = re.sub(r"\s+", " ", v)
    v = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", v)
    v = re.sub(r"([，。！？；,.!?;])\1+", r"\1", v)
    v = re.sub(r"(嗯|呃|啊|那个|就是)(\s*\1)+", r"\1", v)
    v = re.sub(r"(我觉得){2,}", "我觉得", v)
    v = re.sub(r"(然后){2,}", "然后", v)

    parts = [p.strip() for p in re.split(r"[，。！？；,.!?;]+", v)]
    dedup: list[str] = []
    prev = ""
    for p in parts:
        if not p or p == prev:
            continue
        dedup.append(p)
        prev = p

    v = "，".join(dedup).strip()
    if not v:
        return ""
    if not re.search(r"[。！？!?]$", v):
        v += "。"
    return v


@router.post("/asr/refine")
async def refine_asr_text(request: ASRRefineRequest) -> ASRRefineResponse:
    """对 ASR 最终文本做后处理，提升句式可读性。"""
    refined = _refine_transcript_text(request.text)
    return ASRRefineResponse(text=refined)
