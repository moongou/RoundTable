"""性能基准测试 API

提供 ASR、TTS 和 LLM 服务的延迟基准测试接口。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

import httpx
from fastapi import APIRouter

from app.config import (
    AVAILABLE_PROVIDERS,
    LOCAL_SERVICE_DEFAULTS,
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    VOICE_SERVICE_META,
    settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/benchmark")

# 用于测试的示例文本和音频
_TEST_TEXT_SHORT = "你好，欢迎参加圆桌讨论。"
_TEST_TEXT_LONG = "在这个充满挑战和机遇的时代，教育不仅是知识的传递，更是思辨能力的培养。让我们一起探讨如何激发孩子们的创造力和批判性思维。"
_LLM_TEST_PROMPT = "简述什么是批判性思维，不超过20个字"


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

    # 先用 TTS 生成一段测试音频
    test_audio = None
    try:
        from app.voice.factory import create_tts_provider
        tts = create_tts_provider("edge_tts")
        test_audio = await tts.synthesize(_TEST_TEXT_SHORT)
    except Exception:
        # 使用静音 WAV 作为 fallback
        import struct
        sr = 16000
        duration = 2
        samples = sr * duration
        test_audio = b'RIFF' + struct.pack('<I', 36 + samples * 2) + b'WAVEfmt '
        test_audio += struct.pack('<IHHIIHH', 16, 1, 1, sr, sr * 2, 2, 16)
        test_audio += b'data' + struct.pack('<I', samples * 2)
        test_audio += b'\x00' * (samples * 2)

    async def transcribe():
        return await provider.transcribe(test_audio)

    result = await _measure_latency(transcribe, rounds)
    return {
        "service": service_id,
        "status": "ok",
        "latency": result,
    }


async def _benchmark_one_llm(provider_id: str, rounds: int) -> dict:
    """基准测试单个 LLM 提供商的响应延迟。使用 httpx 直接调用 OpenAI-兼容 API。"""
    provider_name = PROVIDER_NAMES.get(provider_id, provider_id)

    # 获取配置
    api_key = getattr(settings, f"{provider_id}_api_key", "")
    base_url = getattr(settings, f"{provider_id}_base_url", "")
    model = getattr(settings, f"{provider_id}_model", "")

    if not model:
        defaults = PROVIDER_DEFAULTS.get(provider_id, {})
        model = defaults.get("model", "")

    # Ollama 不需要 API Key
    if provider_id == "ollama":
        api_key = api_key or "ollama"
    elif not api_key or api_key in ("sk-xxx", "your-api-key", ""):
        return {
            "provider": provider_id,
            "provider_name": provider_name,
            "model": model,
            "status": "skipped",
            "reason": "未配置 API Key",
        }

    if not base_url:
        return {
            "provider": provider_id,
            "provider_name": provider_name,
            "model": model,
            "status": "skipped",
            "reason": "未配置请求地址",
        }

    latencies = []
    first_token_latencies = []
    errors = []

    for _ in range(rounds):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": _LLM_TEST_PROMPT}],
                    "max_tokens": 50,
                    "stream": True,
                }
                url = f"{base_url.rstrip('/')}/chat/completions"

                t0 = time.perf_counter()
                first_token_time = None

                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        errors.append(f"HTTP {resp.status_code}: {error_body.decode()[:200]}")
                        continue
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            if first_token_time is None:
                                first_token_time = (time.perf_counter() - t0) * 1000

                total_ms = (time.perf_counter() - t0) * 1000
                latencies.append(total_ms)
                if first_token_time is not None:
                    first_token_latencies.append(first_token_time)
        except Exception as e:
            errors.append(str(e)[:200])

    result = {
        "provider": provider_id,
        "provider_name": provider_name,
        "model": model,
    }

    if latencies:
        result.update({
            "status": "ok",
            "total_avg_ms": round(sum(latencies) / len(latencies), 1),
            "total_min_ms": round(min(latencies), 1),
            "total_max_ms": round(max(latencies), 1),
            "rounds": len(latencies),
            "errors": len(errors),
        })
        if first_token_latencies:
            result["first_token_avg_ms"] = round(
                sum(first_token_latencies) / len(first_token_latencies), 1
            )
            result["first_token_min_ms"] = round(min(first_token_latencies), 1)
    else:
        result.update({
            "status": "error",
            "error": errors[0] if errors else "unknown",
            "errors": len(errors),
        })
    return result


@router.post("/tts")
async def benchmark_tts(rounds: int = 3, services: list[str] | None = None):
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
async def benchmark_asr(rounds: int = 3, services: list[str] | None = None):
    """基准测试所有或指定的 ASR 服务。"""
    target_services = services or ["browser", "capswriter", "vosk", "funasr"]
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
async def benchmark_llm(rounds: int = 2, providers: list[str] | None = None):
    """基准测试 LLM 提供商响应延迟。自动扫描所有已配置（API Key 非空）的提供商。"""
    if providers:
        target = providers
    else:
        # 自动扫描所有已配置 API Key 的提供商
        target = []
        for pid in AVAILABLE_PROVIDERS:
            api_key = getattr(settings, f"{pid}_api_key", "")
            if pid == "ollama":
                # Ollama 本地不需要 API Key，但检查 base_url 是否配置
                base_url = getattr(settings, f"{pid}_base_url", "")
                if base_url:
                    target.append(pid)
            elif api_key and api_key not in ("sk-xxx", "your-api-key", ""):
                target.append(pid)
        # 确保当前使用的提供商在列表中
        if settings.llm_provider not in target:
            target.insert(0, settings.llm_provider)

    results = await asyncio.gather(
        *[_benchmark_one_llm(p, rounds) for p in target]
    )
    results = list(results)

    # 排序：按首 token 延迟
    ok_results = [r for r in results if r.get("status") == "ok"]
    ok_results.sort(key=lambda r: r.get("first_token_avg_ms", r.get("total_avg_ms", 99999)))
    recommended = ok_results[0]["provider"] if ok_results else None

    return {"results": results, "recommended": recommended}


@router.get("/hardware")
async def get_hardware_info(apply_tuning: bool = True):
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
async def optimize_hardware_runtime():
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
async def test_voice_service(service_id: str, service_type: str, text: str = _TEST_TEXT_SHORT):
    """独立测试单个语音服务（设置页面用）。

    Args:
        service_id: 服务ID (capswriter/vosk/vibevoice/fireredtts 等)
        service_type: 'asr' 或 'tts'
        text: 测试文本 (TTS用)
    """
    result = {"service": service_id, "type": service_type}

    # 健康检查
    try:
        svc_info = LOCAL_SERVICE_DEFAULTS.get(service_id, {})
        url = svc_info.get("url", "")
        health = svc_info.get("health", "/health")
        if url:
            async with httpx.AsyncClient(timeout=5.0) as client:
                t0 = time.perf_counter()
                resp = await client.get(f"{url}{health}")
                result["health_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                result["health_status"] = resp.json() if resp.status_code == 200 else {"error": resp.status_code}
    except Exception as e:
        result["health_status"] = {"error": str(e)}

    # 功能测试
    if service_type == "tts":
        try:
            from app.voice.factory import create_tts_provider_with_url
            svc_url = LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get("url", "")
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
async def deep_test_voice_service(service_id: str, service_type: str):
    """深度测试语音服务，返回可人工确认的示例结果。"""
    if service_type == "asr":
        sample_text = "今天我们讨论的是如何培养批判性思维。"
        result: dict = {
            "service": service_id,
            "type": "asr",
            "expected_text": sample_text,
        }
        try:
            from app.voice.factory import create_tts_provider, create_asr_provider

            tts = create_tts_provider("edge_tts")
            audio = await tts.synthesize(sample_text)

            asr = create_asr_provider(service_id)
            t0 = time.perf_counter()
            recognized = await asr.transcribe(audio)
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

            svc_url = LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get("url", "")
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
