"""配置管理 API

提供 LLM 提供商列表、语音服务状态、本地服务健康检查等接口。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets
from fastapi import APIRouter
from websockets.exceptions import InvalidStatus

from app.config import (
    ASR_PROVIDERS,
    LOCAL_SERVICE_DEFAULTS,
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    TTS_PROVIDERS,
    VOICE_SERVICE_META,
    settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])


async def _probe_service(url: str, health_path: str, timeout_sec: float = 3.0) -> dict:
    """探测单个服务，返回可用性、状态码和耗时。"""
    if url.startswith("ws://") or url.startswith("wss://"):
        return await _probe_websocket_service(url, timeout_sec=timeout_sec)

    target = f"{url.rstrip('/')}{health_path}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(target)
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            ok = 200 <= resp.status_code < 300
            return {
                "url": url,
                "target": target,
                "reachable": ok,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "detail": f"HTTP {resp.status_code}, {latency_ms}ms",
            }
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "url": url,
            "target": target,
            "reachable": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "detail": str(e),
        }


async def _semantic_voice_probe(service_id: str, url: str, timeout_sec: float = 4.0) -> dict:
    """语音服务语义探测：不仅测连通性，也测关键能力是否可用。"""
    if service_id == "funasr" and (url.startswith("ws://") or url.startswith("wss://")):
        t0 = time.perf_counter()
        try:
            async with websockets.connect(
                url,
                subprotocols=["binary"],
                open_timeout=timeout_sec,
                close_timeout=timeout_sec,
            ) as ws:
                await ws.send('{"chunk_size":[5,10,5],"wav_name":"health","is_speaking":true,"chunk_interval":10,"itn":true,"mode":"2pass","wav_format":"PCM","audio_fs":16000}')
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                return {
                    "url": url,
                    "target": url,
                    "reachable": True,
                    "status_code": 101,
                    "latency_ms": latency_ms,
                    "probe_type": "semantic",
                    "category": "asr",
                    "detail": f"WS protocol OK, {latency_ms}ms",
                }
        except Exception as e:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "url": url,
                "target": url,
                "reachable": False,
                "status_code": None,
                "latency_ms": latency_ms,
                "probe_type": "semantic",
                "category": "asr",
                "detail": f"WS protocol failed: {e}",
            }

    # Default semantic probe for HTTP-based services
    base = await _probe_service(
        url,
        LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get("health")
        or VOICE_SERVICE_META.get(service_id, {}).get("health_path", "/"),
        timeout_sec=timeout_sec,
    )
    base["probe_type"] = "semantic"
    base["category"] = "asr" if service_id in ASR_PROVIDERS else "tts"

    if not base.get("reachable"):
        return base

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            if service_id == "edge_tts":
                resp = await client.get(f"{url.rstrip('/')}/v1/models")
                payload = resp.json() if resp.status_code == 200 else []
                if isinstance(payload, list):
                    count = len(payload)
                elif isinstance(payload, dict):
                    count = len(payload.get("data", []) or payload.get("models", []))
                else:
                    count = 0
                base["reachable"] = resp.status_code == 200 and count > 0
                base["detail"] = f"models={count}, HTTP {resp.status_code}"
                base["status_code"] = resp.status_code
            elif service_id == "cosyvoice":
                resp = await client.get(f"{url.rstrip('/')}/speakers")
                payload = resp.json() if resp.status_code == 200 else []
                if isinstance(payload, list):
                    count = len(payload)
                elif isinstance(payload, dict):
                    count = len(payload.get("speakers", []))
                else:
                    count = 0
                base["reachable"] = resp.status_code == 200 and count > 0
                base["detail"] = f"speakers={count}, HTTP {resp.status_code}"
                base["status_code"] = resp.status_code
            elif service_id == "chattts":
                resp = await client.get(f"{url.rstrip('/')}/gradio_api/info")
                payload = resp.json() if resp.status_code == 200 else {}
                named_endpoints = payload.get("named_endpoints", {}) if isinstance(payload, dict) else {}
                has_seed_endpoint = "/on_audio_seed_change" in named_endpoints
                base["reachable"] = resp.status_code == 200 and has_seed_endpoint
                base["detail"] = (
                    f"gradio endpoints={'ok' if has_seed_endpoint else 'missing'}, HTTP {resp.status_code}"
                )
                base["status_code"] = resp.status_code
            elif service_id == "openai_tts":
                # OpenAI TTS has no dedicated voice-list endpoint; /models probe is a practical semantic check.
                headers = {}
                key = settings.openai_api_key
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                resp = await client.get(f"{(settings.openai_base_url or 'https://api.openai.com/v1').rstrip('/')}/models", headers=headers)
                base["reachable"] = 200 <= resp.status_code < 300
                base["detail"] = f"OpenAI models HTTP {resp.status_code}"
                base["status_code"] = resp.status_code
            elif service_id == "openai_whisper":
                key = settings.openai_whisper_api_key or settings.openai_api_key
                if key:
                    base["detail"] = f"API key configured, {base['detail']}"
                else:
                    base["reachable"] = False
                    base["detail"] = "OpenAI Whisper 缺少 API Key"
    except Exception as e:
        base["reachable"] = False
        base["detail"] = f"semantic probe failed: {e}"

    return base


async def _probe_websocket_service(url: str, timeout_sec: float = 3.0) -> dict:
    """探测 WebSocket 服务可用性。"""
    t0 = time.perf_counter()
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("无效的 WebSocket URL")

        async with websockets.connect(
            url,
            subprotocols=["binary"],
            open_timeout=timeout_sec,
            close_timeout=timeout_sec,
        ):
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "url": url,
                "target": url,
                "reachable": True,
                "status_code": 101,
                "latency_ms": latency_ms,
                "detail": f"WS connected, {latency_ms}ms",
            }
    except InvalidStatus as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        status_code = getattr(e.response, "status_code", None)
        body = (getattr(e.response, "body", b"") or b"").decode("utf-8", "ignore").strip()
        # 服务端返回握手错误通常表示服务在线但请求参数不匹配
        reachable = status_code is not None and status_code < 500
        return {
            "url": url,
            "target": url,
            "reachable": reachable,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "detail": body or f"WS handshake HTTP {status_code}",
        }
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "url": url,
            "target": url,
            "reachable": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "detail": str(e),
        }


@router.get("/providers")
async def list_providers():
    """列出所有可用的 LLM 提供商及其默认配置。"""
    providers = []
    for pid, defaults in PROVIDER_DEFAULTS.items():
        # 读取当前配置值
        api_key = getattr(settings, f"{pid}_api_key", "")
        base_url = getattr(settings, f"{pid}_base_url", defaults.get("base_url", ""))
        model = getattr(settings, f"{pid}_model", defaults.get("model", ""))

        # 检查 API key 是否已配置（非空且非占位符）
        has_key = bool(api_key and api_key != "sk-xxx" and not api_key.startswith("sk-xxx"))
        if pid == "ollama":
            has_key = True  # Ollama 本地不需要 key

        providers.append({
            "id": pid,
            "name": PROVIDER_NAMES.get(pid, pid),
            "base_url": base_url,
            "model": model,
            "has_api_key": has_key,
            "is_active": pid == settings.llm_provider,
            "needs_api_key": pid not in ("ollama",),
        })

    return providers


@router.get("/speech")
async def list_speech_providers():
    """列出语音识别/合成服务提供商（含当前URL配置和实时可用性探测）。"""

    def voice_service_detail(service_id: str) -> dict:
        meta = VOICE_SERVICE_META.get(service_id, {})
        url = settings.get_voice_service_url(service_id) or meta.get("default_url", "")
        api_key = ""
        if service_id == "openai_whisper":
            key = settings.openai_whisper_api_key or settings.openai_api_key
            api_key = "***" if key else ""
        elif service_id == "openai_tts":
            key = settings.openai_api_key
            api_key = "***" if key else ""
        return {
            "url": url,
            "default_url": meta.get("default_url", ""),
            "needs_api_key": meta.get("needs_api_key", False),
            "has_api_key": bool(api_key),
        }

    # ── 并行探测所有本地服务的可用性 ──────────────────────────────────────
    # 列出需要实时探测的本地服务（排除浏览器、禁用和纯API类服务）
    _local_probes = {
        pid: (
            settings.get_voice_service_url(pid)
            or LOCAL_SERVICE_DEFAULTS.get(pid, {}).get("url", ""),
            # 优先使用 LOCAL_SERVICE_DEFAULTS 的直连 health 路径，
            # 避免 VOICE_SERVICE_META 中指向网关的聚合路径
            LOCAL_SERVICE_DEFAULTS.get(pid, {}).get("health")
            or VOICE_SERVICE_META.get(pid, {}).get("health_path", "/health"),
        )
        for pid in list(ASR_PROVIDERS.keys()) + list(TTS_PROVIDERS.keys())
        if pid not in ("browser", "disabled", "openai_whisper", "openai_tts")
        and LOCAL_SERVICE_DEFAULTS.get(pid)
    }
    # 去重（asr+tts 字典合并后同一 pid 只探测一次）
    _probe_ids = list(dict.fromkeys(_local_probes.keys()))
    _probe_results: dict[str, bool] = {}
    if _probe_ids:
        probe_coros = []
        for pid in _probe_ids:
            url, _health = _local_probes[pid]
            probe_coros.append(_semantic_voice_probe(pid, url, timeout_sec=2.0))
        results = await asyncio.gather(*probe_coros, return_exceptions=True)
        for pid, result in zip(_probe_ids, results):
            if isinstance(result, dict):
                _probe_results[pid] = bool(result.get("reachable"))
            else:
                _probe_results[pid] = False

    def _available(pid: str) -> bool:
        if pid in ("browser", "disabled"):
            return True
        if pid == "openai_whisper":
            return bool(settings.openai_whisper_api_key or settings.openai_api_key)
        if pid == "openai_tts":
            return bool(settings.openai_api_key)
        return _probe_results.get(pid, False)

    return {
        "asr": [
            {
                "id": pid,
                "name": name,
                "is_active": pid == settings.asr_provider,
                "available": _available(pid),
                **voice_service_detail(pid),
            }
            for pid, name in ASR_PROVIDERS.items()
        ],
        "tts": [
            {
                "id": pid,
                "name": name,
                "is_active": pid == settings.tts_provider,
                "available": _available(pid),
                **voice_service_detail(pid),
            }
            for pid, name in TTS_PROVIDERS.items()
        ],
        "push_to_talk": settings.push_to_talk,
        "tts_voice": settings.tts_voice,
        "cosyvoice_voice": settings.cosyvoice_voice,
    }


@router.get("/health")
async def check_services_health():
    """检查本地服务健康度（分类 + 语义探测）。"""
    results: dict[str, dict] = {}
    tasks = []

    # 使用当前配置的实际 URL
    service_urls = {
        "edge_tts": (settings.edge_tts_url, "/v1/models"),
        "cosyvoice": (settings.cosyvoice_url, "/health"),
        "funasr": (settings.funasr_url, "/"),
        "ollama": (settings.ollama_base_url.replace("/v1", ""), "/api/tags"),
    }
    async def check_service(name: str, url: str, health_path: str):
        semantic = await _semantic_voice_probe(name, url)
        if semantic.get("reachable"):
            results[name] = semantic
            return
        # 回退到基础可达性探测，便于区分“协议失败”和“网络不可达”。
        basic = await _probe_service(url, health_path)
        basic["probe_type"] = "basic"
        basic["category"] = "asr" if name in ASR_PROVIDERS else "tts"
        results[name] = basic

    for name, (url, health) in service_urls.items():
        tasks.append(check_service(name, url, health))

    await asyncio.gather(*tasks)
    return results


@router.get("/current")
async def get_current_config():
    """获取当前生效的配置（隐藏 API key 中间部分）。"""
    provider = settings.llm_provider

    def mask_key(key: str) -> str:
        if not key or key == "sk-xxx" or key.startswith("sk-xxx"):
            return ""
        if len(key) <= 8:
            return "***"
        return key[:4] + "..." + key[-4:]

    api_key = getattr(settings, f"{provider}_api_key", "")

    return {
        "llm_provider": provider,
        "llm_provider_name": PROVIDER_NAMES.get(provider, provider),
        "api_key_masked": mask_key(api_key),
        "model": getattr(settings, f"{provider}_model", ""),
        "asr_provider": settings.asr_provider,
        "tts_provider": settings.tts_provider,
        "push_to_talk": settings.push_to_talk,
        "hardware_detection_on_startup": settings.hardware_detection_on_startup,
        "web_search_enabled": settings.web_search_enabled,
        "tavily_configured": bool(settings.tavily_api_key),
    }


@router.get("/validate")
@router.post("/validate")
async def validate_current_config():
    """验证当前 LLM 配置是否有效（GET/POST 均支持）。"""
    checks: list[dict] = []
    provider_id = settings.llm_provider
    model_name = getattr(settings, f"{provider_id}_model", "")
    basic_ok, basic_err = settings.validate_llm_config()
    if not basic_ok:
        checks.append({
            "name": f"LLM（{provider_id}）",
            "ok": False,
            "detail": basic_err,
        })
    else:
        probe = await test_provider(
            {
                "provider_id": provider_id,
                "model": model_name,
            }
        )
        checks.append({
            "name": f"LLM 连接（{provider_id}）",
            "ok": bool(probe.get("success")),
            "detail": probe.get("error") or "连接可用",
        })
        checks.append({
            "name": f"模型可用性（{model_name or '-'}）",
            "ok": bool(probe.get("model_valid")),
            "detail": (
                f"模型已验证，可用于当前提供商（候选 {len(probe.get('models', []))} 个）"
                if probe.get("model_valid")
                else probe.get("error") or "模型不可用，请重新测试连接并选择可用模型"
            ),
        })

    service_meta = VOICE_SERVICE_META

    async def validate_voice_item(label: str, sid: str):
        if sid in ("browser", "disabled"):
            checks.append({
                "name": label,
                "ok": True,
                "detail": f"{sid} 模式不依赖后端语音服务",
            })
            return
        url = settings.get_voice_service_url(sid) or service_meta.get(sid, {}).get("default_url", "")
        if not url:
            checks.append({
                "name": label,
                "ok": False,
                "detail": "未配置服务 URL",
            })
            return
        r = await _semantic_voice_probe(sid, url)
        checks.append({
            "name": label,
            "ok": r["reachable"],
            "detail": r["detail"],
            "status_code": r["status_code"],
            "latency_ms": r["latency_ms"],
            "probe_type": r.get("probe_type", "semantic"),
            "category": r.get("category", "asr" if sid in ASR_PROVIDERS else "tts"),
        })

    await asyncio.gather(
        validate_voice_item(f"ASR（{settings.asr_provider}）", settings.asr_provider),
        validate_voice_item(f"TTS（{settings.tts_provider}）", settings.tts_provider),
    )

    all_ok = all(c.get("ok", False) for c in checks)
    return {
        "valid": all_ok,
        "message": "配置有效" if all_ok else "存在不可用项，请根据红叉提示修复",
        "checks": checks,
    }


@router.post("/update")
async def update_config(updates: dict):
    """运行时更新配置（不持久化到 .env 文件）。"""
    try:
        settings.update_runtime(updates)
        return {
            "success": True,
            "message": "配置已更新",
            "current_provider": settings.llm_provider,
            "current_model": getattr(settings, f"{settings.llm_provider}_model", ""),
        }
    except Exception as e:
        return {"success": False, "message": f"更新配置失败: {e}"}


@router.post("/save")
async def save_config_to_env(updates: dict):
    """将配置持久化写入 .env 文件（同时更新运行时）。"""
    try:
        env_path = Path(settings.base_dir) / ".env"

        # 读取现有 .env
        env_lines: list[str] = []
        if env_path.exists():
            env_lines = env_path.read_text(encoding="utf-8").splitlines()

        # 逐一更新或追加
        saved_keys: list[str] = []
        for key, value in updates.items():
            key_upper = key.upper()
            found = False
            for i, line in enumerate(env_lines):
                stripped = line.split("=")[0].strip()
                if stripped == key_upper:
                    env_lines[i] = f"{key_upper}={value}"
                    found = True
                    break
            if not found:
                env_lines.append(f"{key_upper}={value}")
            saved_keys.append(key)

        env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        settings.update_runtime(updates)

        return {"success": True, "saved_keys": saved_keys, "message": f"已保存 {len(saved_keys)} 项配置"}
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return {"success": False, "message": f"保存失败: {e}"}


@router.post("/test-provider")
async def test_provider(body: dict):
    """测试 LLM 提供商连接，并尝试获取可用模型列表。

    Body:
        provider_id: 提供商 ID
        api_key: API Key（可选，为空时使用当前配置）
        base_url: Base URL（可选，为空时使用当前配置）
    """
    provider_id = body.get("provider_id", "")
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()
    requested_model = body.get("model", "").strip()

    def done(success: bool, models: list[str], error: str | None = None):
        model_valid = True
        model_error = error
        if requested_model:
            model_valid = requested_model in models
            if success and not model_valid:
                model_error = f"指定的模型不存在或不可用: {requested_model}"
        return {
            "success": bool(success and model_valid),
            "models": models,
            "error": model_error,
            "requested_model": requested_model,
            "model_valid": model_valid,
        }

    # 回退到当前配置
    if not api_key:
        api_key = getattr(settings, f"{provider_id}_api_key", "")
    if not base_url:
        base_url = getattr(settings, f"{provider_id}_base_url", "")
    if not base_url:
        base_url = PROVIDER_DEFAULTS.get(provider_id, {}).get("base_url", "")

    if not base_url:
        return done(False, [], "未配置 Base URL")

    # Ollama 本地：使用 /api/tags 获取模型列表
    if provider_id == "ollama":
        ollama_root = base_url.replace("/v1", "").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{ollama_root}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = sorted([m["name"] for m in data.get("models", [])])
                    return done(True, models)
                return done(False, [], f"HTTP {resp.status_code}")
        except Exception as e:
            return done(False, [], str(e))

    # Anthropic 特殊处理（不支持标准 /models）
    if provider_id == "anthropic":
        # Anthropic does not have a public model list endpoint; return curated list
        models = [
            "claude-opus-4-5",
            "claude-sonnet-4-5",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ]
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                # Send a minimal request to verify key validity
                resp = await client.post(
                    f"{base_url}/messages",
                    headers=headers,
                    json={"model": "claude-3-5-haiku-20241022", "max_tokens": 1,
                          "messages": [{"role": "user", "content": "hi"}]},
                )
                if resp.status_code in (200, 201):
                    return done(True, models)
                err = resp.json().get("error", {}).get("message", f"HTTP {resp.status_code}")
                return done(False, [], err)
        except Exception as e:
            return done(False, [], str(e))

    # Gemini 特殊处理
    if provider_id == "gemini":
        models = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"]
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = sorted([
                        m["name"].replace("models/", "")
                        for m in data.get("models", [])
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                    ])
                    return done(True, models)
                return done(False, [], f"HTTP {resp.status_code}")
        except Exception as e:
            return done(False, [], str(e))

    # 标准 OpenAI 兼容：GET /models
    headers: dict = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("data", data if isinstance(data, list) else [])
                models = sorted(
                    [m.get("id", m) if isinstance(m, dict) else str(m) for m in raw]
                )
                return done(True, models)
            # Try to extract error message
            try:
                err_body = resp.json()
                err_msg = (err_body.get("error", {}) or {}).get("message", f"HTTP {resp.status_code}")
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            return done(False, [], err_msg)
    except Exception as e:
        return done(False, [], str(e))


@router.post("/test-voice-service")
async def test_voice_service(body: dict):
    """测试语音服务连接，尝试获取可用音色/模型列表。

    Body:
        service: 服务 ID (funasr / edge_tts / cosyvoice / openai_tts / openai_whisper)
        url: 服务 URL（可选，为空时使用当前配置）
        api_key: API Key（可选，仅部分服务需要）
    """
    service = body.get("service", "")
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()

    if not url:
        url = settings.get_voice_service_url(service)
    if not url:
        meta = VOICE_SERVICE_META.get(service, {})
        url = meta.get("default_url", "")

    if not url:
        return {"success": False, "status_code": None, "url": url, "voices": [], "error": "未配置 URL"}

    meta = VOICE_SERVICE_META.get(service, {})
    health_path = meta.get("health_path", "/")
    voices: list[str] = []

    # Special handling per service
    try:
        probe = await _semantic_voice_probe(service, url)
        reachable = bool(probe.get("reachable"))
        status_code = probe.get("status_code")
        latency_ms = probe.get("latency_ms")
        detail = (probe.get("detail") or "").strip()

        if reachable and (url.startswith("http://") or url.startswith("https://")):
            async with httpx.AsyncClient(timeout=6.0) as client:
                headers: dict = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                elif service in ("openai_whisper", "openai_tts"):
                    key = settings.openai_whisper_api_key or settings.openai_api_key
                    if key:
                        headers["Authorization"] = f"Bearer {key}"

                # Try to extract voice/model list
                if service == "edge_tts":
                    try:
                        voices_resp = await client.get(f"{url.rstrip('/')}/v1/models", headers=headers)
                        if voices_resp.status_code == 200:
                            raw = voices_resp.json()
                            if isinstance(raw, list):
                                voices = sorted([m.get("id", "") or m.get("name", "") for m in raw if isinstance(m, dict)][:50])
                    except Exception:
                        pass
                elif service == "cosyvoice":
                    try:
                        v_resp = await client.get(f"{url.rstrip('/')}/speakers", headers=headers)
                        if v_resp.status_code == 200:
                            raw = v_resp.json()
                            voices = raw if isinstance(raw, list) else list(raw.get("speakers", []))
                    except Exception:
                        voices = ["default", "中文女声", "中文男声"]
                elif service in ("openai_tts",):
                    voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
                elif service == "funasr":
                    voices = []  # ASR has no voice list

        return {
            "success": reachable,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "url": url,
            "voices": voices,
            "probe_type": probe.get("probe_type", "semantic"),
            "category": probe.get("category", "asr" if service in ASR_PROVIDERS else "tts"),
            "error": None if reachable else (detail or (f"HTTP {status_code}" if status_code is not None else "服务不可用")),
        }
    except Exception as e:
        return {"success": False, "status_code": None, "url": url, "voices": [], "error": str(e)}


@router.get("/web-search")
async def get_web_search_config():
    """获取网络搜索配置。"""
    key = settings.tavily_api_key
    return {
        "enabled": settings.web_search_enabled,
        "has_api_key": bool(key),
        "api_key_masked": (key[:4] + "..." + key[-4:]) if key and len(key) > 8 else ("***" if key else ""),
        "base_url": settings.tavily_base_url,
    }


@router.post("/test-web-search")
async def test_web_search(body: dict):
    """测试 Tavily 网络搜索 API 连接。"""
    api_key = body.get("api_key", "").strip() or settings.tavily_api_key
    if not api_key:
        return {"success": False, "error": "未配置 Tavily API Key"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{settings.tavily_base_url}/search",
                json={"api_key": api_key, "query": "test", "max_results": 1},
            )
            if resp.status_code == 200:
                return {"success": True, "error": None}
            try:
                err_body = resp.json()
                err_msg = err_body.get("detail", err_body.get("message", f"HTTP {resp.status_code}"))
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            return {"success": False, "error": err_msg}
    except Exception as e:
        return {"success": False, "error": str(e)}