"""性能基准测试 API

提供 ASR、TTS 和 LLM 服务的延迟基准测试接口。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from array import array
import wave
from dataclasses import asdict
from io import BytesIO

import httpx
from fastapi import APIRouter, Depends

from app.api.v1.admin_guard import require_management_token
from app.api.v1.config_api import _semantic_voice_probe
from app.config import (
    LOCAL_SERVICE_DEFAULTS,
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    VOICE_SERVICE_META,
    canonical_provider_id,
    provider_candidate_ids,
    settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/benchmark")

# 用于测试的示例文本和音频
_TEST_TEXT_SHORT = "你好，欢迎参加圆桌讨论。"
_TEST_TEXT_LONG = "在这个充满挑战和机遇的时代，教育不仅是知识的传递，更是思辨能力的培养。让我们一起探讨如何激发孩子们的创造力和批判性思维。"
_LLM_TEST_PROMPT = "简述什么是批判性思维，不超过20个字"


def _provider_config_value(provider_id: str, field_suffix: str, default: str = "") -> str:
    for pid in provider_candidate_ids(provider_id):
        value = getattr(settings, f"{pid}_{field_suffix}", "")
        if value:
            return value
    return default


def _synthesize_silent_wav(duration_sec: int = 2, sample_rate: int = 16000) -> bytes:
    import struct

    samples = sample_rate * duration_sec
    wav_data = b'RIFF' + struct.pack('<I', 36 + samples * 2) + b'WAVEfmt '
    wav_data += struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    wav_data += b'data' + struct.pack('<I', samples * 2)
    wav_data += b'\x00' * (samples * 2)
    return wav_data


def _wav_to_pcm16_mono_16k(wav_bytes: bytes) -> bytes:
    with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
        if wav_file.getcomptype() != "NONE":
            raise RuntimeError("仅支持未压缩 PCM WAV")
        sample_rate = wav_file.getframerate() or 16000
        sample_width = wav_file.getsampwidth() or 2
        channels = wav_file.getnchannels() or 1
        frames = wav_file.readframes(wav_file.getnframes())

    def _decode_interleaved_mono(raw: bytes, width: int, ch: int) -> list[float]:
        if width not in (1, 2, 3, 4):
            raise RuntimeError(f"不支持的 WAV 位深: {width * 8}bit")

        values: list[float] = []
        frame_size = width * ch
        if frame_size <= 0:
            return values

        total_frames = len(raw) // frame_size
        for frame_idx in range(total_frames):
            base = frame_idx * frame_size
            accum = 0.0
            for c in range(ch):
                offset = base + c * width
                if width == 1:
                    v = (raw[offset] - 128) / 128.0
                elif width == 2:
                    s = int.from_bytes(raw[offset : offset + 2], "little", signed=True)
                    v = s / 32768.0
                elif width == 3:
                    b0, b1, b2 = raw[offset], raw[offset + 1], raw[offset + 2]
                    s = b0 | (b1 << 8) | (b2 << 16)
                    if s & 0x800000:
                        s -= 0x1000000
                    v = s / 8388608.0
                else:  # width == 4
                    s = int.from_bytes(raw[offset : offset + 4], "little", signed=True)
                    v = s / 2147483648.0
                accum += v
            values.append(accum / ch)
        return values

    def _resample_linear(samples: list[float], src_rate: int, dst_rate: int) -> list[float]:
        if not samples or src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate:
            return samples
        dst_len = max(1, int(len(samples) * dst_rate / src_rate))
        last = len(samples) - 1
        out: list[float] = []
        for i in range(dst_len):
            src_pos = i * src_rate / dst_rate
            left = int(src_pos)
            if left >= last:
                out.append(samples[last])
                continue
            right = left + 1
            frac = src_pos - left
            out.append(samples[left] + (samples[right] - samples[left]) * frac)
        return out

    mono = _decode_interleaved_mono(frames, sample_width, channels)
    mono_16k = _resample_linear(mono, sample_rate, 16000)

    pcm16 = array("h")
    for s in mono_16k:
        clamped = max(-1.0, min(1.0, s))
        pcm16.append(int(round(clamped * 32767.0)))
    return pcm16.tobytes()


def _convert_audio_with_ffmpeg(audio_bytes: bytes, src_suffix: str, dst_suffix: str) -> bytes:
    src_path = ""
    dst_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=src_suffix, delete=False) as src_file:
            src_file.write(audio_bytes)
            src_path = src_file.name
        with tempfile.NamedTemporaryFile(suffix=dst_suffix, delete=False) as dst_file:
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
            dst_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg conversion failed")
        with open(dst_path, "rb") as audio_file:
            return audio_file.read()
    finally:
        for path in (src_path, dst_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


async def _build_asr_test_audio(text: str = _TEST_TEXT_SHORT) -> tuple[bytes, str]:
    from app.voice.factory import create_tts_provider

    async def _synthesize_edge_tts_wav(sample_text: str) -> bytes:
        base_url = settings.edge_tts_url or LOCAL_SERVICE_DEFAULTS.get("edge_tts", {}).get("url", "http://localhost:5051")
        payload = {
            "model": "tts-1",
            "input": sample_text,
            "voice": "zh-CN-XiaoxiaoNeural",
            "response_format": "wav",
        }
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/audio/speech",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            audio = resp.content
            if not audio.startswith(b"RIFF"):
                raise RuntimeError("edge_tts 未返回 WAV 数据")
            return audio

    has_ffmpeg = bool(shutil.which("ffmpeg"))

    try:
        tts = create_tts_provider("edge_tts")
        audio = b""
        last_error: Exception | None = None
        for _ in range(3):
            try:
                audio = await tts.synthesize(text)
                break
            except Exception as e:
                last_error = e
                await asyncio.sleep(0.2)
        if not audio:
            raise last_error or RuntimeError("edge_tts returned empty audio")
        if audio.startswith(b"RIFF"):
            return _wav_to_pcm16_mono_16k(audio), "pcm"
        if has_ffmpeg:
            try:
                wav_audio = _convert_audio_with_ffmpeg(audio, ".mp3", ".wav")
                return _wav_to_pcm16_mono_16k(wav_audio), "pcm"
            except Exception as e:
                logger.warning("ASR benchmark 音频转码失败，尝试直出 WAV: %s", e)

        try:
            wav_audio = await _synthesize_edge_tts_wav(text)
            return _wav_to_pcm16_mono_16k(wav_audio), "pcm"
        except Exception as e:
            logger.warning("ASR benchmark WAV 样本生成失败，回退到静音 WAV: %s", e)
            return _wav_to_pcm16_mono_16k(_synthesize_silent_wav()), "pcm"
    except Exception as e:
        logger.warning("ASR benchmark TTS 样本生成失败，回退到静音 WAV: %s", e)
        return _wav_to_pcm16_mono_16k(_synthesize_silent_wav()), "pcm"


async def _measure_latency(coro, rounds: int = 3) -> dict:
    """执行多次测量取平均值。"""
    latencies = []
    errors = []
    for i in range(rounds):
        try:
            t0 = time.perf_counter()
            result = await coro()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
        except Exception as e:
            errors.append(str(e))
    if latencies:
        return {
            "avg_ms": round(sum(latencies) / len(latencies), 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "rounds": len(latencies),
            "errors": len(errors),
        }
    return {"avg_ms": -1, "min_ms": -1, "max_ms": -1, "rounds": 0, "errors": len(errors), "error": errors[0] if errors else "unknown"}


async def _benchmark_one_tts(service_id: str, url: str, rounds: int) -> dict:
    """基准测试单个 TTS 服务。"""
    from app.voice.factory import create_tts_provider
    try:
        provider = create_tts_provider(service_id)
        available = await provider.is_available()
        if not available:
            return {
                "service": service_id,
                "status": "skipped",
                "reason": "service unavailable",
            }
    except Exception as e:
        return {"service": service_id, "status": "error", "error": str(e)}

    # 短文本测试
    async def synth_short():
        return await provider.synthesize(_TEST_TEXT_SHORT)

    # 长文本测试
    async def synth_long():
        return await provider.synthesize(_TEST_TEXT_LONG)

    short_result = await _measure_latency(synth_short, rounds)
    long_result = await _measure_latency(synth_long, rounds)

    return {
        "service": service_id,
        "status": "ok",
        "short_text": short_result,
        "long_text": long_result,
    }


async def _benchmark_one_asr(service_id: str, url: str, rounds: int) -> dict:
    """基准测试单个 ASR 服务。"""
    from app.voice.factory import create_asr_provider
    try:
        provider = create_asr_provider(service_id)
        available = await provider.is_available()
        if not available:
            return {
                "service": service_id,
                "status": "skipped",
                "reason": "service unavailable",
            }
    except Exception as e:
        return {"service": service_id, "status": "error", "error": str(e)}

    test_audio, test_format = await _build_asr_test_audio()

    async def transcribe():
        return await provider.transcribe(test_audio, format=test_format)

    result = await _measure_latency(transcribe, rounds)
    if (result.get("rounds") or 0) <= 0:
        return {
            "service": service_id,
            "status": "error",
            "latency": result,
            "error": result.get("error", "asr benchmark failed"),
        }
    return {
        "service": service_id,
        "status": "ok",
        "latency": result,
    }


async def _benchmark_one_llm(provider_id: str, rounds: int) -> dict:
    """基准测试单个 LLM 提供商的响应延迟。"""
    canonical_id = canonical_provider_id(provider_id)
    provider_name = PROVIDER_NAMES.get(canonical_id, canonical_id)

    if canonical_id in {"anthropic", "gemini"}:
        return {
            "provider": canonical_id,
            "provider_name": provider_name,
            "model": _provider_config_value(canonical_id, "model", PROVIDER_DEFAULTS.get(canonical_id, {}).get("model", "")),
            "status": "skipped",
            "reason": "当前测速仅支持 OpenAI 兼容接口，请先使用连接测试验证该供应商。",
        }

    # 获取配置
    api_key = _provider_config_value(canonical_id, "api_key", "")
    base_url = _provider_config_value(canonical_id, "base_url", "")
    model = _provider_config_value(canonical_id, "model", "")

    if not model:
        defaults = PROVIDER_DEFAULTS.get(canonical_id, {})
        model = defaults.get("model", "")

    # Ollama 不需要 API Key
    if canonical_id == "ollama":
        api_key = api_key or "ollama"
    elif not api_key or api_key in ("sk-xxx", "your-api-key", ""):
        return {
            "provider": canonical_id,
            "provider_name": provider_name,
            "model": model,
            "status": "skipped",
            "reason": "未配置 API Key",
        }

    if not base_url:
        return {
            "provider": canonical_id,
            "provider_name": provider_name,
            "model": model,
            "status": "skipped",
            "reason": "未配置请求地址",
        }

    latencies = []
    errors = []

    for _ in range(rounds):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": _LLM_TEST_PROMPT}],
                    "max_tokens": 50,
                    "stream": False,
                }
                url = f"{base_url.rstrip('/')}/chat/completions"

                t0 = time.perf_counter()
                resp = await client.post(url, json=body, headers=headers)
                total_ms = (time.perf_counter() - t0) * 1000
                if resp.status_code != 200:
                    error_body = (resp.text or "")[:200]
                    errors.append(f"HTTP {resp.status_code}: {error_body}")
                    continue

                payload = resp.json() if resp.content else {}
                if not isinstance(payload, dict) or not payload.get("choices"):
                    errors.append("响应格式异常：缺少 choices")
                    continue

                latencies.append(total_ms)
        except Exception as e:
            errors.append(str(e)[:200])

    result = {
        "provider": canonical_id,
        "provider_name": provider_name,
        "model": model,
    }

    if latencies:
        avg_ms = round(sum(latencies) / len(latencies), 1)
        result.update({
            "status": "ok",
            "avg_ms": avg_ms,
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "rounds": len(latencies),
            "errors": len(errors),
            "first_token_avg_ms": avg_ms,
        })
    else:
        result.update({
            "status": "error",
            "error": errors[0] if errors else "unknown",
            "errors": len(errors),
        })
    return result


@router.post("/tts")
async def benchmark_tts(
    rounds: int = 3,
    services: list[str] | None = None,
    _auth: None = Depends(require_management_token),
):
    """基准测试所有或指定的 TTS 服务。跳过未配置/不可用的服务。"""
    target_services = services or ["chattts", "edge_tts", "vibevoice", "fireredtts", "openvoice", "cosyvoice"]
    results = await asyncio.gather(
        *[_benchmark_one_tts(svc, LOCAL_SERVICE_DEFAULTS.get(svc, {}).get("url", ""), rounds) for svc in target_services]
    )
    results = list(results)
    # 排序：按短文本平均延迟
    ok_results = [r for r in results if r.get("status") == "ok"]
    ok_results.sort(key=lambda r: r.get("short_text", {}).get("avg_ms", 99999))
    recommended = ok_results[0]["service"] if ok_results else None
    return {
        "results": results,
        "recommended": recommended,
    }


@router.post("/asr")
async def benchmark_asr(
    rounds: int = 3,
    services: list[str] | None = None,
    _auth: None = Depends(require_management_token),
):
    """基准测试所有或指定的 ASR 服务。"""
    target_services = services or ["browser", "capswriter", "vosk", "funasr", "openai_whisper"]
    results = await asyncio.gather(
        *[_benchmark_one_asr(svc, LOCAL_SERVICE_DEFAULTS.get(svc, {}).get("url", ""), rounds) for svc in target_services]
    )
    results = list(results)
    for item in results:
        if item.get("service") == "browser":
            item.setdefault("note", "browser 为前端原生 ASR，后端基准用于服务可达性与统一对比展示")
    ok_results = [r for r in results if r.get("status") == "ok"]
    ok_results.sort(key=lambda r: r.get("latency", {}).get("avg_ms", 99999))
    recommended = ok_results[0]["service"] if ok_results else None
    return {
        "results": results,
        "recommended": recommended,
    }


@router.post("/llm")
async def benchmark_llm(
    rounds: int = 2,
    providers: list[str] | None = None,
    _auth: None = Depends(require_management_token),
):
    """基准测试 LLM 提供商响应延迟。默认仅测试当前启用供应商。"""
    if providers:
        target = list(
            dict.fromkeys(
                canonical_provider_id((provider_id or "").strip())
                for provider_id in providers
                if (provider_id or "").strip()
            )
        )
    else:
        target = [canonical_provider_id(settings.llm_provider)]

    results = await asyncio.gather(
        *[_benchmark_one_llm(p, rounds) for p in target]
    )
    results = list(results)

    # 排序：按平均耗时
    ok_results = [r for r in results if r.get("status") == "ok"]
    ok_results.sort(key=lambda r: r.get("avg_ms", 99999))
    recommended = ok_results[0]["provider"] if ok_results else None

    return {"results": results, "recommended": recommended}


@router.get("/hardware")
async def get_hardware_info(
    apply_tuning: bool = True,
    _auth: None = Depends(require_management_token),
):
    """获取硬件检测结果，并可选应用运行时优化参数。"""
    from app.core.hardware import apply_runtime_tuning, detect_hardware, get_runtime_tuning
    profile = detect_hardware()
    runtime_tuning = apply_runtime_tuning() if apply_tuning else get_runtime_tuning()

    report = {
        "summary": f"{profile.os_name} {profile.arch} · {profile.cpu_cores}C/{profile.cpu_threads}T · {profile.memory_gb}GB",
        "chip": profile.apple_chip or profile.cpu_brand,
        "gpu": "MPS" if profile.mps_available else "CUDA" if profile.cuda_available else "CPU",
        "recommendation": f"建议线程池 workers={profile.recommended_workers}",
        "notes": profile.optimization_notes,
    }

    data = asdict(profile)
    data["runtime_tuning"] = runtime_tuning
    data["hardware_report"] = report
    return data


@router.post("/hardware/optimize")
async def optimize_hardware_runtime(
    _auth: None = Depends(require_management_token),
):
    """按当前硬件重新应用运行时优化参数。"""
    from app.core.hardware import apply_runtime_tuning, detect_hardware

    profile = detect_hardware()
    runtime_tuning = apply_runtime_tuning(force_recreate_pool=True)
    return {
        "ok": True,
        "message": "已按当前硬件重新应用优化参数",
        "recommended_workers": profile.recommended_workers,
        "runtime_tuning": runtime_tuning,
        "optimization_notes": profile.optimization_notes,
    }


@router.post("/voice/test")
async def test_voice_service(
    service_id: str,
    service_type: str,
    text: str = _TEST_TEXT_SHORT,
    _auth: None = Depends(require_management_token),
):
    """独立测试单个语音服务（设置页面用）。

    Args:
        service_id: 服务ID (capswriter/vosk/vibevoice/fireredtts 等)
        service_type: 'asr' 或 'tts'
        text: 测试文本 (TTS用)
    """
    result = {"service": service_id, "type": service_type}

    # 健康检查
    try:
        url = settings.get_voice_service_url(service_id) or LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get("url", "")
        health = (
            LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get("health")
            or VOICE_SERVICE_META.get(service_id, {}).get("health_path", "/health")
        )
        if url and service_id != "openai_whisper":
            async with httpx.AsyncClient(timeout=5.0) as client:
                t0 = time.perf_counter()
                if url.startswith(("ws://", "wss://")):
                    probe = await _semantic_voice_probe(service_id, url, timeout_sec=3.0)
                    result["health_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    result["health_status"] = probe
                else:
                    resp = await client.get(f"{url}{health}")
                    result["health_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    result["health_status"] = resp.json() if resp.status_code == 200 else {"error": resp.status_code}
    except Exception as e:
        result["health_status"] = {"error": str(e)}

    # 功能测试
    if service_type == "tts":
        try:
            from app.voice.factory import create_tts_provider_with_url

            svc_url = settings.get_voice_service_url(service_id) or LOCAL_SERVICE_DEFAULTS.get(
                service_id, {}
            ).get("url", "")
            provider = create_tts_provider_with_url(service_id, base_url=svc_url)
            t0 = time.perf_counter()
            audio = await provider.synthesize(text)
            result["synth_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            result["audio_size"] = len(audio)
            result["status"] = "ok"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
    elif service_type == "asr":
        try:
            from app.voice.factory import create_asr_provider

            provider = create_asr_provider(service_id)
            available = await provider.is_available()
            result["available"] = available
            result["status"] = "ok" if available else "unavailable"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)

    return result


@router.post("/voice/deep-test")
async def deep_test_voice_service(
    service_id: str,
    service_type: str,
    _auth: None = Depends(require_management_token),
):
    """深度测试语音服务，返回可人工确认的示例结果。"""
    if service_type == "asr":
        sample_text = "今天我们讨论的是如何培养批判性思维。"
        result: dict = {
            "service": service_id,
            "type": "asr",
            "expected_text": sample_text,
        }
        try:
            from app.voice.factory import create_asr_provider

            audio, audio_format = await _build_asr_test_audio(sample_text)

            candidates = [service_id]
            if service_id == "capswriter":
                candidates.append("vosk")
            elif service_id == "vosk":
                candidates.append("capswriter")

            attempts: list[dict] = []
            last_error = ""

            for candidate in candidates:
                t0 = time.perf_counter()
                try:
                    asr = create_asr_provider(candidate)
                    recognized = await asr.transcribe(audio, format=audio_format)
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    normalized_expected = sample_text.replace("。", "").replace("，", "")
                    normalized_recognized = (recognized or "").replace("。", "").replace("，", "")
                    match = 0.0
                    if normalized_expected:
                        same = sum(1 for c in normalized_expected if c in normalized_recognized)
                        match = round(same / max(len(normalized_expected), 1) * 100, 1)

                    result.update({
                        "status": "ok",
                        "recognized_text": recognized,
                        "latency_ms": round(elapsed_ms, 1),
                        "match_percent": match,
                        "provider_used": candidate,
                        "fallback_used": candidate != service_id,
                        "attempts": attempts,
                    })
                    return result
                except Exception as e:
                    last_error = str(e)
                    attempts.append({"service": candidate, "error": last_error})

            result.update({
                "status": "error",
                "error": last_error or "ASR deep test failed",
                "attempts": attempts,
            })
        except Exception as e:
            result.update({"status": "error", "error": str(e)})
        return result

    if service_type == "tts":
        sample_text = "同学们好，欢迎来到今天的圆桌思辨课堂。"
        result: dict = {
            "service": service_id,
            "type": "tts",
            "preview_text": sample_text,
        }
        try:
            from app.voice.factory import create_tts_provider_with_url

            svc_url = settings.get_voice_service_url(service_id) or LOCAL_SERVICE_DEFAULTS.get(
                service_id, {}
            ).get("url", "")
            provider = create_tts_provider_with_url(service_id, base_url=svc_url)
            t0 = time.perf_counter()
            audio = await provider.synthesize(sample_text)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            result.update({
                "status": "ok",
                "synth_ms": round(elapsed_ms, 1),
                "audio_size": len(audio),
            })
        except Exception as e:
            result.update({"status": "error", "error": str(e)})
        return result

    return {
        "service": service_id,
        "type": service_type,
        "status": "error",
        "error": "service_type 必须是 asr 或 tts",
    }
