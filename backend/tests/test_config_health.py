from app.api.v1.config_api import _health_candidate_service_ids
from app.config import LOCAL_SERVICE_DEFAULTS, settings


def test_get_voice_service_url_falls_back_to_known_defaults() -> None:
    assert settings.get_voice_service_url("openvoice") == LOCAL_SERVICE_DEFAULTS["openvoice"]["url"]
    assert (
        settings.get_voice_service_url("capswriter") == LOCAL_SERVICE_DEFAULTS["capswriter"]["url"]
    )


def test_get_voice_service_url_uses_capswriter_runtime_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "capswriter_url", "ws://localhost:7616")

    assert settings.get_voice_service_url("capswriter") == "ws://localhost:7616"


def test_get_voice_service_url_uses_vosk_runtime_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "vosk_url", "http://localhost:7702")

    assert settings.get_voice_service_url("vosk") == "http://localhost:7702"


def test_get_voice_service_url_uses_vibevoice_runtime_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "vibevoice_url", "http://localhost:7704")

    assert settings.get_voice_service_url("vibevoice") == "http://localhost:7704"


def test_get_voice_service_url_uses_fireredtts_runtime_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fireredtts_url", "http://localhost:7706")

    assert settings.get_voice_service_url("fireredtts") == "http://localhost:7706"


def test_health_candidate_service_ids_follow_current_runtime_config(monkeypatch) -> None:
    monkeypatch.setattr(settings, "asr_provider", "funasr")
    monkeypatch.setattr(settings, "tts_provider", "openvoice")
    monkeypatch.setattr(settings, "llm_provider", "deepseek")

    assert _health_candidate_service_ids(current_only=True) == ["funasr", "openvoice"]


def test_health_candidate_service_ids_include_local_ollama_when_active(monkeypatch) -> None:
    monkeypatch.setattr(settings, "asr_provider", "browser")
    monkeypatch.setattr(settings, "tts_provider", "disabled")
    monkeypatch.setattr(settings, "llm_provider", "ollama")

    assert _health_candidate_service_ids(current_only=True) == ["ollama"]
