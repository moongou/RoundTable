from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 6016

    sherpa_tokens: str = "./models/tokens.txt"
    sherpa_encoder: str = "./models/encoder.onnx"
    sherpa_decoder: str = "./models/decoder.onnx"
    sherpa_joiner: str = "./models/joiner.onnx"

    sample_rate: int = 16000
    num_threads: int = 2
    provider: str = "cpu"


settings = Settings()
