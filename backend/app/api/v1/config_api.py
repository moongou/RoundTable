"""配置管理 API

提供 LLM 提供商列表、语音服务状态、本地服务健康检查等接口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
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
    canonical_provider_id,
    provider_candidate_ids,
    settings,
)
from app.voice.openvoice_profiles import list_openvoice_profile_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["config"])
CONFIG_PROFILES_DIR = Path(settings.base_dir) / "runtime" / "config_profiles"

_CLOUD_VOICE_SERVICE_IDS = {
    "openai_whisper",
    "siliconflow_asr",
    "groq_whisper",
    "openai_tts",
    "siliconflow_tts",
}

_CONFIG_PROFILE_FIELD_NAMES = {
    "llm_provider",
    "asr_provider",
    "tts_provider",
    "push_to_talk",
    "max_turns",
    "human_turn_timeout",
    "hardware_detection_on_startup",
    "chattts_url",
    "capswriter_url",
    "vosk_url",
    "funasr_url",
    "edge_tts_url",
    "cosyvoice_url",
    "vibevoice_url",
    "fireredtts_url",
    "openvoice_url",
    "openai_whisper_api_key",
    "openai_whisper_base_url",
    "openai_whisper_model",
    "siliconflow_asr_api_key",
    "siliconflow_asr_base_url",
    "siliconflow_asr_model",
    "groq_whisper_api_key",
    "groq_whisper_base_url",
    "groq_whisper_model",
    "openai_tts_api_key",
    "openai_tts_base_url",
    "openai_tts_model",
    "openai_tts_voice",
    "siliconflow_tts_api_key",
    "siliconflow_tts_base_url",
    "siliconflow_tts_model",
    "siliconflow_tts_voice",
    "tts_voice",
    "cosyvoice_voice",
    "tavily_api_key",
    "tavily_base_url",
    "web_search_enabled",
}
for _provider_id in PROVIDER_DEFAULTS:
    _CONFIG_PROFILE_FIELD_NAMES.update(
        {
            f"{_provider_id}_api_key",
            f"{_provider_id}_base_url",
            f"{_provider_id}_model",
        }
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_profile_id(value: str) -> str:
    normalized = "_".join(
        part for part in "".join(ch if ch.isalnum() else " " for ch in (value or "").strip()).split()
    )
    return normalized[:80] or "profile"


def _config_profile_path(profile_id: str) -> Path:
    return CONFIG_PROFILES_DIR / f"{profile_id}.json"


def _capture_runtime_config_snapshot() -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for field_name in sorted(_CONFIG_PROFILE_FIELD_NAMES):
        snapshot[field_name] = getattr(settings, field_name)
    return snapshot


def _build_config_profile_summary(payload: dict) -> dict[str, object]:
    runtime_config = payload.get("runtime_config", {})
    return {
        "profile_id": payload.get("profile_id", ""),
        "name": payload.get("name", ""),
        "description": payload.get("description", ""),
        "created_at": payload.get("created_at", ""),
        "updated_at": payload.get("updated_at", ""),
        "llm_provider": runtime_config.get("llm_provider", ""),
        "model": runtime_config.get(
            f"{runtime_config.get('llm_provider', '')}_model",
            runtime_config.get("openai_model", ""),
        ),
        "asr_provider": runtime_config.get("asr_provider", ""),
        "tts_provider": runtime_config.get("tts_provider", ""),
        "has_local_settings": bool(payload.get("local_settings")),
    }


def _read_config_profile(profile_id: str) -> dict:
    profile_path = _config_profile_path(profile_id)
    if not profile_path.exists():
        raise FileNotFoundError(profile_id)
    return json.loads(profile_path.read_text(encoding="utf-8"))


def _write_config_profile(payload: dict) -> None:
    CONFIG_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = _config_profile_path(str(payload.get("profile_id", "")))
    profile_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _effective_tts_provider_id() -> str:
    current = (settings.tts_provider or "").strip().lower()
    if current not in TTS_PROVIDERS:
        return "edge_tts"
    return current


def _has_real_api_key(value: str) -> bool:
    key = (value or "").strip()
    return bool(key and key not in {"sk-xxx", "your-api-key"} and not key.startswith("sk-xxx"))


def _public_provider_ids() -> list[str]:
    return [pid for pid, defaults in PROVIDER_DEFAULTS.items() if not defaults.get("alias_of")]


def _provider_config_value(provider_id: str, field_suffix: str, default: str = "") -> str:
    for pid in provider_candidate_ids(provider_id):
        value = getattr(settings, f"{pid}_{field_suffix}", "")
        if value:
            return value
    return default


def _normalize_config_updates(updates: dict) -> dict:
    normalized = dict(updates)
    if "llm_provider" in normalized:
        normalized["llm_provider"] = canonical_provider_id(str(normalized["llm_provider"]))
    return normalized


def _provider_display_source_id(provider_id: str) -> str:
    active_provider = canonical_provider_id(settings.llm_provider)
    if active_provider == canonical_provider_id(provider_id):
        return settings.llm_provider
    return provider_id


async def _probe_openai_voice_service(service_id: str, timeout_sec: float = 2.0) -> bool:
    url = settings.get_voice_service_url(service_id) or VOICE_SERVICE_META.get(service_id, {}).get(
        "default_url", ""
    )
    if not url:
        return False

    api_key = settings.get_voice_service_api_key(service_id)

    if not _has_real_api_key(api_key):
        return False

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return 200 <= resp.status_code < 300
    except Exception:
        return False


def _is_cloud_voice_service(service_id: str) -> bool:
    return (service_id or "").strip().lower() in _CLOUD_VOICE_SERVICE_IDS


def _parse_model_ids(payload: object) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("data", payload.get("models", []))
    elif isinstance(payload, list):
        raw = payload
    else:
        raw = []
    return sorted(
        [
            item.get("id", item.get("name", "")) if isinstance(item, dict) else str(item)
            for item in raw
            if item
        ]
    )


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


async def _semantic_voice_probe(
    service_id: str, url: str, timeout_sec: float = 4.0, api_key: str = ""
) -> dict:
    """语音服务语义探测：不仅测连通性，也测关键能力是否可用。"""
    if _is_cloud_voice_service(service_id):
        key = api_key.strip() or settings.get_voice_service_api_key(service_id)
        if not _has_real_api_key(key):
            return {
                "url": url,
                "target": f"{url.rstrip('/')}/models" if url else "",
                "reachable": False,
                "status_code": None,
                "latency_ms": None,
                "probe_type": "semantic",
                "category": "asr" if service_id in ASR_PROVIDERS else "tts",
                "detail": "缺少 API Key",
            }

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                resp = await client.get(
                    f"{url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                models = _parse_model_ids(resp.json()) if resp.status_code == 200 else []
                return {
                    "url": url,
                    "target": f"{url.rstrip('/')}/models",
                    "reachable": resp.status_code == 200 and len(models) > 0,
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "probe_type": "semantic",
                    "category": "asr" if service_id in ASR_PROVIDERS else "tts",
                    "detail": f"models={len(models)}, HTTP {resp.status_code}",
                }
        except Exception as e:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "url": url,
                "target": f"{url.rstrip('/')}/models" if url else "",
                "reachable": False,
                "status_code": None,
                "latency_ms": latency_ms,
                "probe_type": "semantic",
                "category": "asr" if service_id in ASR_PROVIDERS else "tts",
                "detail": str(e),
            }

    if service_id == "funasr" and (url.startswith("ws://") or url.startswith("wss://")):
        t0 = time.perf_counter()
        try:
            async with websockets.connect(
                url,
                subprotocols=["binary"],
                proxy=None,
                open_timeout=timeout_sec,
                close_timeout=timeout_sec,
            ) as ws:
                await ws.send(
                    '{"chunk_size":[5,10,5],"wav_name":"health","is_speaking":true,"chunk_interval":10,"itn":true,"mode":"2pass","wav_format":"PCM","audio_fs":16000}'
                )
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
                named_endpoints = (
                    payload.get("named_endpoints", {}) if isinstance(payload, dict) else {}
                )
                has_seed_endpoint = "/on_audio_seed_change" in named_endpoints
                base["reachable"] = resp.status_code == 200 and has_seed_endpoint
                endpoint_status = "ok" if has_seed_endpoint else "missing"
                base["detail"] = f"gradio endpoints={endpoint_status}, HTTP {resp.status_code}"
                base["status_code"] = resp.status_code
            elif service_id == "vosk":
                resp = await client.get(f"{url.rstrip('/')}/health")
                payload = resp.json() if resp.status_code == 200 else {}
                model_loaded = (
                    bool(payload.get("model_loaded")) if isinstance(payload, dict) else False
                )
                base["reachable"] = resp.status_code == 200 and model_loaded
                base["detail"] = (
                    f"model={'loaded' if model_loaded else 'missing'}, HTTP {resp.status_code}"
                )
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
            proxy=None,
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


_FULL_HEALTH_SERVICE_IDS = tuple(sid for sid in LOCAL_SERVICE_DEFAULTS.keys() if sid != "gateway")


def _health_category_for_service(service_id: str) -> str:
    if service_id == "ollama":
        return "llm"
    if service_id in ASR_PROVIDERS:
        return "asr"
    return "tts"


def _health_candidate_service_ids(*, current_only: bool) -> list[str]:
    if current_only:
        candidate_ids = [settings.asr_provider, _effective_tts_provider_id()]
        if settings.llm_provider == "ollama":
            candidate_ids.append("ollama")
    else:
        candidate_ids = [*_FULL_HEALTH_SERVICE_IDS, "ollama"]

    service_ids: list[str] = []
    for raw_id in candidate_ids:
        service_id = (raw_id or "").strip().lower()
        if not service_id or service_id in {"browser", "disabled", "gateway"}:
            continue
        service_ids.append(service_id)
    return list(dict.fromkeys(service_ids))


async def _probe_health_service(service_id: str) -> tuple[str, dict]:
    category = _health_category_for_service(service_id)
    url = settings.get_voice_service_url(service_id)
    health_path = LOCAL_SERVICE_DEFAULTS.get(service_id, {}).get(
        "health"
    ) or VOICE_SERVICE_META.get(service_id, {}).get("health_path", "/")

    if not url:
        return service_id, {
            "url": "",
            "target": "",
            "reachable": False,
            "status_code": None,
            "latency_ms": None,
            "probe_type": "basic",
            "category": category,
            "detail": "未配置服务 URL",
        }

    if service_id == "ollama":
        result = await _probe_service(url, health_path)
        result["probe_type"] = "basic"
        result["category"] = category
        return service_id, result

    semantic = await _semantic_voice_probe(service_id, url)
    if semantic.get("reachable"):
        semantic["category"] = category
        return service_id, semantic

    basic = await _probe_service(url, health_path)
    basic["probe_type"] = "basic"
    basic["category"] = category
    return service_id, basic


@router.get("/providers")
async def list_providers():
    """列出所有可用的 LLM 提供商及其默认配置。"""
    providers = []
    active_provider = canonical_provider_id(settings.llm_provider)
    for pid in _public_provider_ids():
        defaults = PROVIDER_DEFAULTS[pid]
        source_provider = _provider_display_source_id(pid)
        # 读取当前配置值
        api_key = _provider_config_value(source_provider, "api_key", "")
        base_url = _provider_config_value(
            source_provider,
            "base_url",
            defaults.get("base_url", ""),
        )
        model = _provider_config_value(source_provider, "model", defaults.get("model", ""))

        # 检查 API key 是否已配置（非空且非占位符）
        has_key = bool(api_key and api_key != "sk-xxx" and not api_key.startswith("sk-xxx"))
        if pid == "ollama":
            has_key = True  # Ollama 本地不需要 key

        providers.append(
            {
                "id": pid,
                "name": PROVIDER_NAMES.get(pid, pid),
                "base_url": base_url,
                "model": model,
                "has_api_key": has_key,
                "is_active": pid == active_provider,
                "needs_api_key": pid not in ("ollama",),
            }
        )

    return providers


@router.get("/speech")
async def list_speech_providers():
    """列出语音识别/合成服务提供商（含当前URL配置和实时可用性探测）。"""

    def voice_service_detail(service_id: str) -> dict:
        meta = VOICE_SERVICE_META.get(service_id, {})
        url = settings.get_voice_service_url(service_id) or meta.get("default_url", "")
        api_key = settings.get_voice_service_api_key(service_id)
        return {
            "url": url,
            "default_url": meta.get("default_url", ""),
            "needs_api_key": meta.get("needs_api_key", False),
            "has_api_key": _has_real_api_key(api_key),
            "model": settings.get_voice_service_model(service_id),
            "default_model": meta.get("default_model", ""),
            "voice": settings.get_tts_voice_for_provider(service_id)
            if meta.get("type") == "tts"
            else "",
            "default_voice": meta.get("default_voice", ""),
            "mode": meta.get("mode", "local"),
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
        if pid not in ("browser", "disabled")
        and not _is_cloud_voice_service(pid)
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

    _remote_probe_results: dict[str, bool] = {}
    if _CLOUD_VOICE_SERVICE_IDS:
        remote_probe_ids = list(_CLOUD_VOICE_SERVICE_IDS)
        remote_probe_results = await asyncio.gather(
            *(_probe_openai_voice_service(pid) for pid in remote_probe_ids),
            return_exceptions=True,
        )
        for pid, result in zip(remote_probe_ids, remote_probe_results):
            _remote_probe_results[pid] = (
                bool(result) if not isinstance(result, Exception) else False
            )

    def _available(pid: str) -> bool:
        if pid in ("browser", "disabled"):
            return True
        if _is_cloud_voice_service(pid):
            return _remote_probe_results.get(pid, False)
        return _probe_results.get(pid, False)

    active_tts_provider = _effective_tts_provider_id()

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
                "is_active": pid == active_tts_provider,
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
async def check_services_health(current_only: bool = False):
    """检查本地服务健康度（分类 + 语义探测）。"""
    results: dict[str, dict] = {}
    service_ids = _health_candidate_service_ids(current_only=current_only)
    probed = await asyncio.gather(
        *(_probe_health_service(service_id) for service_id in service_ids)
    )
    for service_id, payload in probed:
        results[service_id] = payload
    return results


@router.get("/current")
async def get_current_config():
    """获取当前生效的配置（隐藏 API key 中间部分）。"""
    provider = canonical_provider_id(settings.llm_provider)

    def mask_key(key: str) -> str:
        if not key or key == "sk-xxx" or key.startswith("sk-xxx"):
            return ""
        if len(key) <= 8:
            return "***"
        return key[:4] + "..." + key[-4:]

    source_provider = _provider_display_source_id(provider)
    api_key = _provider_config_value(source_provider, "api_key", "")

    return {
        "llm_provider": provider,
        "llm_provider_name": PROVIDER_NAMES.get(provider, provider),
        "api_key_masked": mask_key(api_key),
        "model": _provider_config_value(
            source_provider,
            "model",
            PROVIDER_DEFAULTS.get(provider, {}).get("model", ""),
        ),
        "asr_provider": settings.asr_provider,
        "tts_provider": _effective_tts_provider_id(),
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
    provider_id = canonical_provider_id(settings.llm_provider)
    model_name = _provider_config_value(
        provider_id,
        "model",
        PROVIDER_DEFAULTS.get(provider_id, {}).get("model", ""),
    )
    basic_ok, basic_err = settings.validate_llm_config()
    if not basic_ok:
        checks.append(
            {
                "name": f"LLM（{provider_id}）",
                "ok": False,
                "detail": basic_err,
            }
        )
    else:
        probe = await test_provider(
            {
                "provider_id": provider_id,
                "model": model_name,
            }
        )
        checks.append(
            {
                "name": f"LLM 连接（{provider_id}）",
                "ok": bool(probe.get("success")),
                "detail": probe.get("error") or "连接可用",
            }
        )
        checks.append(
            {
                "name": f"模型可用性（{model_name or '-'}）",
                "ok": bool(probe.get("model_valid")),
                "detail": (
                    f"模型已验证，可用于当前提供商（候选 {len(probe.get('models', []))} 个）"
                    if probe.get("model_valid")
                    else probe.get("error") or "模型不可用，请重新测试连接并选择可用模型"
                ),
            }
        )

    service_meta = VOICE_SERVICE_META

    async def validate_voice_item(label: str, sid: str):
        if sid in ("browser", "disabled"):
            checks.append(
                {
                    "name": label,
                    "ok": True,
                    "detail": f"{sid} 模式不依赖后端语音服务",
                }
            )
            return
        url = settings.get_voice_service_url(sid) or service_meta.get(sid, {}).get(
            "default_url", ""
        )
        if not url:
            checks.append(
                {
                    "name": label,
                    "ok": False,
                    "detail": "未配置服务 URL",
                }
            )
            return
        r = await _semantic_voice_probe(sid, url)
        checks.append(
            {
                "name": label,
                "ok": r["reachable"],
                "detail": r["detail"],
                "status_code": r["status_code"],
                "latency_ms": r["latency_ms"],
                "probe_type": r.get("probe_type", "semantic"),
                "category": r.get("category", "asr" if sid in ASR_PROVIDERS else "tts"),
            }
        )

    await asyncio.gather(
        validate_voice_item(f"ASR（{settings.asr_provider}）", settings.asr_provider),
        validate_voice_item(f"TTS（{_effective_tts_provider_id()}）", _effective_tts_provider_id()),
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
        normalized_updates = _normalize_config_updates(updates)
        settings.update_runtime(normalized_updates)
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
        normalized_updates = _normalize_config_updates(updates)
        env_path = Path(settings.base_dir) / ".env"

        # 读取现有 .env
        env_lines: list[str] = []
        if env_path.exists():
            env_lines = env_path.read_text(encoding="utf-8").splitlines()

        # 逐一更新或追加
        saved_keys: list[str] = []
        for key, value in normalized_updates.items():
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
        settings.update_runtime(normalized_updates)

        return {
            "success": True,
            "saved_keys": saved_keys,
            "message": f"已保存 {len(saved_keys)} 项配置",
        }
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return {"success": False, "message": f"保存失败: {e}"}


@router.get("/profiles")
async def list_config_profiles():
    """列出已保存的配置集。"""
    if not CONFIG_PROFILES_DIR.exists():
        return {"profiles": []}

    profiles: list[dict[str, object]] = []
    for profile_path in CONFIG_PROFILES_DIR.glob("*.json"):
        try:
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("读取配置集失败 %s: %s", profile_path, exc)
            continue
        profiles.append(_build_config_profile_summary(payload))

    profiles.sort(
        key=lambda item: str(item.get("updated_at", "")),
        reverse=True,
    )
    return {"profiles": profiles}


@router.post("/profiles")
async def save_config_profile(body: dict):
    """保存当前完整配置为一个可复用的配置集。"""
    profile_name = str(body.get("name", "")).strip()
    if not profile_name:
        return {"success": False, "message": "配置名称不能为空"}

    profile_id = _sanitize_profile_id(str(body.get("profile_id", "")) or profile_name)
    existing_payload: dict | None = None
    try:
        existing_payload = _read_config_profile(profile_id)
    except FileNotFoundError:
        existing_payload = None

    now = _utc_now_iso()
    payload = {
        "profile_id": profile_id,
        "name": profile_name,
        "description": str(body.get("description", "")).strip(),
        "created_at": (existing_payload or {}).get("created_at", now),
        "updated_at": now,
        "runtime_config": _capture_runtime_config_snapshot(),
        "local_settings": body.get("local_settings", {}) or {},
    }
    _write_config_profile(payload)

    return {
        "success": True,
        "message": f"已保存配置“{profile_name}”",
        "profile": _build_config_profile_summary(payload),
    }


@router.post("/profiles/{profile_id}/load")
async def load_config_profile(profile_id: str):
    """载入已保存的配置集，并立即应用到当前运行时。"""
    try:
        payload = _read_config_profile(profile_id)
    except FileNotFoundError:
        return {"success": False, "message": "配置集不存在"}
    except Exception as exc:
        logger.error("读取配置集失败 %s: %s", profile_id, exc)
        return {"success": False, "message": f"读取配置集失败: {exc}"}

    runtime_config = payload.get("runtime_config", {}) or {}
    try:
        normalized_updates = _normalize_config_updates(dict(runtime_config))
        settings.update_runtime(normalized_updates)
    except Exception as exc:
        logger.error("应用配置集失败 %s: %s", profile_id, exc)
        return {"success": False, "message": f"应用配置集失败: {exc}"}

    payload["updated_at"] = _utc_now_iso()
    _write_config_profile(payload)
    return {
        "success": True,
        "message": f"已载入配置“{payload.get('name', profile_id)}”",
        "profile": _build_config_profile_summary(payload),
        "local_settings": payload.get("local_settings", {}),
    }


@router.delete("/profiles/{profile_id}")
async def delete_config_profile(profile_id: str):
    """删除一个已保存的配置集。"""
    profile_path = _config_profile_path(profile_id)
    if not profile_path.exists():
        return {"success": False, "message": "配置集不存在"}
    profile_path.unlink()
    return {"success": True, "message": "配置集已删除", "profile_id": profile_id}


@router.post("/test-provider")
async def test_provider(body: dict):
    """测试 LLM 提供商连接，并尝试获取可用模型列表。

    Body:
        provider_id: 提供商 ID
        api_key: API Key（可选，为空时使用当前配置）
        base_url: Base URL（可选，为空时使用当前配置）
    """
    provider_id = canonical_provider_id(str(body.get("provider_id", "")))
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
        api_key = _provider_config_value(provider_id, "api_key", "")
    if not base_url:
        base_url = _provider_config_value(provider_id, "base_url", "")
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
                    json={
                        "model": "claude-3-5-haiku-20241022",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                if resp.status_code in (200, 201):
                    return done(True, models)
                err = resp.json().get("error", {}).get("message", f"HTTP {resp.status_code}")
                return done(False, [], err)
        except Exception as e:
            return done(False, [], str(e))

    # DeepSeek 专用探测：/models 在某些账号环境下不稳定，
    # 直接发最小 chat completion 更可靠。
    if provider_id == "deepseek":
        probe_model = requested_model or _provider_config_value(
            provider_id,
            "model",
            PROVIDER_DEFAULTS.get(provider_id, {}).get("model", "deepseek-chat"),
        )
        models = sorted({"deepseek-chat", "deepseek-reasoner", probe_model})
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json={
                        "model": probe_model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                        "stream": False,
                    },
                )
                if resp.status_code in (200, 201):
                    return done(True, models)
                try:
                    err_body = resp.json()
                    err_msg = (err_body.get("error", {}) or {}).get(
                        "message", f"HTTP {resp.status_code}"
                    )
                except Exception:
                    err_msg = f"HTTP {resp.status_code}"
                return done(False, [], err_msg)
        except Exception as e:
            return done(False, [], str(e))

    # Gemini 特殊处理
    if provider_id == "gemini":
        models = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"]
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = sorted(
                        [
                            m["name"].replace("models/", "")
                            for m in data.get("models", [])
                            if "generateContent" in m.get("supportedGenerationMethods", [])
                        ]
                    )
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
                models = sorted([m.get("id", m) if isinstance(m, dict) else str(m) for m in raw])
                return done(True, models)
            # Try to extract error message
            try:
                err_body = resp.json()
                err_msg = (err_body.get("error", {}) or {}).get(
                    "message", f"HTTP {resp.status_code}"
                )
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            return done(False, [], err_msg)
    except Exception as e:
        return done(False, [], str(e))


@router.post("/test-voice-service")
async def test_voice_service(body: dict):
    """测试语音服务连接，尝试获取可用音色/模型列表。

    Body:
        service: 服务 ID (funasr / edge_tts / cosyvoice / openai_whisper)
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
        return {
            "success": False,
            "status_code": None,
            "url": url,
            "voices": [],
            "error": "未配置 URL",
        }

    voices: list[str] = []

    # Special handling per service
    try:
        probe = await _semantic_voice_probe(service, url, api_key=api_key)
        reachable = bool(probe.get("reachable"))
        status_code = probe.get("status_code")
        latency_ms = probe.get("latency_ms")
        detail = (probe.get("detail") or "").strip()
        requested_model = (
            body.get("model", "")
            or settings.get_voice_service_model(service)
            or meta.get("default_model", "")
        ).strip()
        requested_voice = (
            body.get("voice", "")
            or settings.get_tts_voice_for_provider(service)
            or meta.get("default_voice", "")
        ).strip()
        models: list[str] = []
        model_valid = True

        if reachable and (url.startswith("http://") or url.startswith("https://")):
            async with httpx.AsyncClient(timeout=6.0) as client:
                headers: dict = {}
                effective_key = api_key or settings.get_voice_service_api_key(service)
                if effective_key:
                    headers["Authorization"] = f"Bearer {effective_key}"

                # Try to extract voice/model list
                if service == "edge_tts":
                    try:
                        voices_resp = await client.get(
                            f"{url.rstrip('/')}/v1/models", headers=headers
                        )
                        if voices_resp.status_code == 200:
                            raw = voices_resp.json()
                            if isinstance(raw, list):
                                voices = sorted(
                                    [
                                        m.get("id", "") or m.get("name", "")
                                        for m in raw
                                        if isinstance(m, dict)
                                    ][:50]
                                )
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
                elif service == "openvoice":
                    voices = list_openvoice_profile_ids()
                elif service == "funasr":
                    voices = []  # ASR has no voice list
                elif _is_cloud_voice_service(service):
                    models_resp = await client.get(f"{url.rstrip('/')}/models", headers=headers)
                    if models_resp.status_code == 200:
                        models = _parse_model_ids(models_resp.json())
                    if requested_model:
                        model_valid = requested_model in models
                        if not model_valid:
                            reachable = False
                            detail = f"指定模型不可用: {requested_model}"
                    if reachable and service in ("openai_tts", "siliconflow_tts"):
                        voice_to_use = requested_voice or meta.get("default_voice", "alloy")
                        synth_resp = await client.post(
                            f"{url.rstrip('/')}/audio/speech",
                            headers={**headers, "Content-Type": "application/json"},
                            json={
                                "model": requested_model,
                                "input": "你好",
                                "voice": voice_to_use,
                                "response_format": "mp3",
                            },
                        )
                        status_code = synth_resp.status_code
                        reachable = 200 <= synth_resp.status_code < 300 and bool(synth_resp.content)
                        detail = (
                            f"TTS synth OK, bytes={len(synth_resp.content)}"
                            if reachable
                            else f"TTS synth failed: HTTP {synth_resp.status_code}"
                        )
                        voices = [voice_to_use]
                    elif reachable:
                        detail = f"models={len(models)}, model={'ok' if model_valid else 'missing'}"

        return {
            "success": reachable,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "url": url,
            "voices": voices,
            "models": models,
            "requested_model": requested_model,
            "model_valid": model_valid,
            "voice_used": requested_voice if service in TTS_PROVIDERS else "",
            "probe_type": probe.get("probe_type", "semantic"),
            "category": probe.get("category", "asr" if service in ASR_PROVIDERS else "tts"),
            "error": None
            if reachable
            else (detail or (f"HTTP {status_code}" if status_code is not None else "服务不可用")),
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
        "api_key_masked": (key[:4] + "..." + key[-4:])
        if key and len(key) > 8
        else ("***" if key else ""),
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
                err_msg = err_body.get(
                    "detail", err_body.get("message", f"HTTP {resp.status_code}")
                )
            except Exception:
                err_msg = f"HTTP {resp.status_code}"
            return {"success": False, "error": err_msg}
    except Exception as e:
        return {"success": False, "error": str(e)}
