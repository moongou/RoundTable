from __future__ import annotations

from typing import Any

import pytest

from app.api.v1 import config_api
from app.config import settings


def _set_setting(name: str, value: Any) -> None:
    object.__setattr__(settings, name, value)


@pytest.fixture(autouse=True)
def _restore_settings():
    tracked = {
        key: getattr(settings, key)
        for key in (
            'llm_provider',
            'doubao_api_key',
            'doubao_base_url',
            'doubao_model',
            'volcengine_api_key',
            'volcengine_base_url',
            'volcengine_model',
            'deepseek_api_key',
            'deepseek_base_url',
            'deepseek_model',
            'siliconflow_api_key',
            'siliconflow_asr_model',
            'siliconflow_tts_model',
            'siliconflow_tts_voice',
            'tts_provider',
        )
    }
    try:
        yield
    finally:
        for key, value in tracked.items():
            _set_setting(key, value)


@pytest.mark.asyncio
async def test_list_providers_hides_doubao_alias_and_keeps_legacy_config_active() -> None:
    _set_setting('llm_provider', 'doubao')
    _set_setting('doubao_api_key', 'legacy-doubao-key')
    _set_setting('doubao_base_url', 'https://ark.cn-beijing.volces.com/api/v3')
    _set_setting('doubao_model', 'doubao-pro-32k')
    _set_setting('volcengine_api_key', '')

    providers = await config_api.list_providers()
    current = await config_api.get_current_config()

    provider_ids = [item['id'] for item in providers]
    assert 'doubao' not in provider_ids
    assert 'volcengine' in provider_ids

    volcengine = next(item for item in providers if item['id'] == 'volcengine')
    assert volcengine['is_active'] is True
    assert volcengine['has_api_key'] is True
    assert volcengine['model'] == 'doubao-pro-32k'

    assert current['llm_provider'] == 'volcengine'
    assert current['llm_provider_name'] == '火山引擎（豆包）'
    assert current['model'] == 'doubao-pro-32k'


@pytest.mark.asyncio
async def test_deepseek_test_provider_uses_chat_completion_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_setting('deepseek_api_key', 'deepseek-key')
    _set_setting('deepseek_base_url', 'https://api.deepseek.com/v1')
    _set_setting('deepseek_model', 'deepseek-chat')

    calls: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, headers: dict[str, Any] | None = None, json: dict[str, Any] | None = None):
            calls.append({'url': url, 'headers': headers or {}, 'json': json or {}})
            return _FakeResponse()

    monkeypatch.setattr(config_api.httpx, 'AsyncClient', _FakeAsyncClient)

    result = await config_api.test_provider({'provider_id': 'deepseek'})

    assert result['success'] is True
    assert 'deepseek-chat' in result['models']
    assert 'deepseek-reasoner' in result['models']
    assert calls[0]['url'] == 'https://api.deepseek.com/v1/chat/completions'
    assert calls[0]['headers']['Authorization'] == 'Bearer deepseek-key'
    assert calls[0]['json']['model'] == 'deepseek-chat'


@pytest.mark.asyncio
async def test_list_providers_includes_siliconflow_defaults() -> None:
    providers = await config_api.list_providers()

    siliconflow = next(item for item in providers if item['id'] == 'siliconflow')
    assert siliconflow['name'] == '硅基流动'
    assert siliconflow['base_url'] == 'https://api.siliconflow.cn/v1'
    assert siliconflow['model'] == 'deepseek-ai/DeepSeek-R1-0528-Qwen3-8B'
    assert siliconflow['needs_api_key'] is True


@pytest.mark.asyncio
async def test_list_speech_providers_includes_cloud_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_probe(service_id: str) -> bool:
        return service_id == 'siliconflow_tts'

    monkeypatch.setattr(config_api, '_probe_openai_voice_service', _fake_probe)

    payload = await config_api.list_speech_providers()

    asr_ids = [item['id'] for item in payload['asr']]
    tts_ids = [item['id'] for item in payload['tts']]
    assert 'siliconflow_asr' in asr_ids
    assert 'groq_whisper' in asr_ids
    assert 'openai_tts' in tts_ids
    assert 'siliconflow_tts' in tts_ids

    siliconflow_tts = next(item for item in payload['tts'] if item['id'] == 'siliconflow_tts')
    assert siliconflow_tts['model'] == 'FunAudioLLM/CosyVoice2-0.5B'
    assert siliconflow_tts['default_voice'] == 'FunAudioLLM/CosyVoice2-0.5B:alex'
    assert siliconflow_tts['available'] is True


@pytest.mark.asyncio
async def test_test_voice_service_validates_siliconflow_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_setting('siliconflow_api_key', 'sf-key')
    _set_setting('siliconflow_tts_model', 'FunAudioLLM/CosyVoice2-0.5B')
    _set_setting('siliconflow_tts_voice', 'FunAudioLLM/CosyVoice2-0.5B:alex')

    async def _fake_probe(service_id: str, url: str, timeout_sec: float = 4.0, api_key: str = '') -> dict:
        assert service_id == 'siliconflow_tts'
        assert api_key == 'sf-key'
        return {
            'url': url,
            'target': f'{url.rstrip('/')}/models',
            'reachable': True,
            'status_code': 200,
            'latency_ms': 12.3,
            'probe_type': 'semantic',
            'category': 'tts',
            'detail': 'models=2, HTTP 200',
        }

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict | list | None = None, content: bytes = b'') -> None:
            self.status_code = status_code
            self._payload = payload if payload is not None else {}
            self.content = content

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None):
            assert headers == {'Authorization': 'Bearer sf-key'}
            assert url == 'https://api.siliconflow.cn/v1/models'
            return _FakeResponse(
                200,
                payload={
                    'data': [
                        {'id': 'FunAudioLLM/CosyVoice2-0.5B'},
                        {'id': 'TeleAI/TeleSpeechASR'},
                    ]
                },
            )

        async def post(self, url: str, headers: dict[str, str] | None = None, json: dict | None = None):
            assert url == 'https://api.siliconflow.cn/v1/audio/speech'
            assert headers == {
                'Authorization': 'Bearer sf-key',
                'Content-Type': 'application/json',
            }
            assert json == {
                'model': 'FunAudioLLM/CosyVoice2-0.5B',
                'input': '你好',
                'voice': 'FunAudioLLM/CosyVoice2-0.5B:alex',
                'response_format': 'mp3',
            }
            return _FakeResponse(200, content=b'ID3fake')

    monkeypatch.setattr(config_api, '_semantic_voice_probe', _fake_probe)
    monkeypatch.setattr(config_api.httpx, 'AsyncClient', _FakeAsyncClient)

    result = await config_api.test_voice_service({'service': 'siliconflow_tts', 'api_key': 'sf-key'})

    assert result['success'] is True
    assert result['models'] == ['FunAudioLLM/CosyVoice2-0.5B', 'TeleAI/TeleSpeechASR']
    assert result['model_valid'] is True
    assert result['voice_used'] == 'FunAudioLLM/CosyVoice2-0.5B:alex'
    assert result['voices'] == ['FunAudioLLM/CosyVoice2-0.5B:alex']