from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .config import settings

logger = logging.getLogger(__name__)


class StreamRecognizer(Protocol):
    def push_pcm16(self, pcm_bytes: bytes) -> str:
        ...

    def finalize(self) -> str:
        ...


@dataclass
class SherpaOnnxStreamingRecognizer:
    """Simple streaming recognizer wrapper around sherpa-onnx online API."""

    recognizer: object
    stream: object
    sample_rate: int

    @classmethod
    def create(cls) -> "SherpaOnnxStreamingRecognizer":
        try:
            import sherpa_onnx  # type: ignore
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(
                "sherpa-onnx is not installed. Run: pip install sherpa-onnx"
            ) from exc

        feature_config = sherpa_onnx.FeatureConfig(
            sample_rate=settings.sample_rate,
            feature_dim=80,
        )

        model_config = sherpa_onnx.OnlineModelConfig(
            transducer=sherpa_onnx.OnlineTransducerModelConfig(
                encoder=settings.sherpa_encoder,
                decoder=settings.sherpa_decoder,
                joiner=settings.sherpa_joiner,
            ),
            tokens=settings.sherpa_tokens,
            num_threads=settings.num_threads,
            provider=settings.provider,
            debug=False,
        )

        recognizer_config = sherpa_onnx.OnlineRecognizerConfig(
            feat_config=feature_config,
            model_config=model_config,
            decoding_method="greedy_search",
        )

        recognizer = sherpa_onnx.OnlineRecognizer(recognizer_config)
        stream = recognizer.create_stream()
        return cls(recognizer=recognizer, stream=stream, sample_rate=settings.sample_rate)

    def _decode_until_not_ready(self) -> str:
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        return self.recognizer.get_result(self.stream)

    def push_pcm16(self, pcm_bytes: bytes) -> str:
        if not pcm_bytes:
            return ""

        pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.stream.accept_waveform(self.sample_rate, pcm)
        return self._decode_until_not_ready()

    def finalize(self) -> str:
        self.stream.input_finished()
        return self._decode_until_not_ready()
