"""ChatTTS Gradio WebUI provider.

This adapter drives the local ChatTTS WebUI running on port 9998 through its
Gradio queue API. The flow mirrors the working browser sequence:

1. render dynamic audio output blocks via hidden `apply`
2. run the pre-generate button state update
3. refine the input text
4. generate audio and download the produced file

Speaker identity is stabilized by hashing the requested voice name into a
deterministic audio seed, then resolving the corresponding speaker embedding
through the public `/on_audio_seed_change` API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from typing import Any

import httpx

from app.voice.base import TTSProvider


def _stable_audio_seed(voice: str) -> int:
    """Map a voice identifier to a stable 32-bit seed in ChatTTS range."""
    key = (voice or "alloy").strip() or "alloy"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:4], "big")
    # ChatTTS seeds are expected to be within (0, 2^32 - 1].
    return (seed % 4294967294) + 1


def _iter_sse_events(payload: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_block in payload.split("\n\n"):
        block = raw_block.strip()
        if not block:
            continue
        lines = [line for line in block.splitlines() if line.startswith("data: ")]
        if not lines:
            continue
        data_str = "\n".join(line[6:] for line in lines).strip()
        if not data_str:
            continue
        try:
            parsed = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _extract_process_completed_value(payload: str) -> str | None:
    for event in _iter_sse_events(payload):
        if event.get("msg") != "process_completed":
            continue
        output = event.get("output") or {}
        data = output.get("data") or []
        if data and isinstance(data[0], str):
            return data[0]
    return None


def _extract_generated_file_url(payload: str) -> str | None:
    for event in _iter_sse_events(payload):
        if event.get("msg") not in {"process_generating", "process_completed"}:
            continue
        output = event.get("output") or {}
        data = output.get("data") or []
        if not data or not isinstance(data[0], dict):
            continue
        url = data[0].get("url")
        if isinstance(url, str) and url:
            return url
    return None


class ChatTTSProvider(TTSProvider):
    """Drive the local ChatTTS Gradio WebUI over HTTP."""

    _APPLY_FN_INDEX = 8
    _PREPARE_FN_INDEX = 10
    _REFINE_FN_INDEX = 11
    _GENERATE_FN_INDEX = 12
    _GENERATE_TRIGGER_ID = 39

    def __init__(self, base_url: str = "http://localhost:9998"):
        self.base_url = base_url.rstrip("/")
        self._speaker_cache: dict[int, str] = {}
        self._lock = asyncio.Lock()

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/gradio_api/info")
                if resp.status_code != 200:
                    return False
                payload = resp.json()
                named_endpoints = payload.get("named_endpoints") or {}
                return "/on_audio_seed_change" in named_endpoints
        except Exception:
            return False

    async def synthesize(self, text: str, voice: str = "alloy") -> bytes:
        content = (text or "").strip()
        if not content:
            raise RuntimeError("ChatTTS text is empty")

        async with self._lock:
            async with httpx.AsyncClient(timeout=60.0) as client:
                session_hash = f"rt{secrets.token_hex(6)}"

                # Initialize the dynamic render block that hosts the output audio.
                await client.get(f"{self.base_url}/")
                await self._queue_join(
                    client,
                    session_hash,
                    fn_index=self._APPLY_FN_INDEX,
                    trigger_id=0,
                    data=[False, False],
                )
                await self._queue_until_message(
                    client,
                    session_hash,
                    expected_event_id=None,
                    success_msg="process_completed",
                )

                speaker_seed = _stable_audio_seed(voice)
                speaker_embedding = await self._speaker_embedding(client, speaker_seed)

                await self._queue_join(
                    client,
                    session_hash,
                    fn_index=self._PREPARE_FN_INDEX,
                    trigger_id=self._GENERATE_TRIGGER_ID,
                    data=["Generate", "Interrupt"],
                )
                await self._queue_until_message(
                    client,
                    session_hash,
                    expected_event_id=None,
                    success_msg="process_completed",
                )

                refine_event_id = await self._queue_join(
                    client,
                    session_hash,
                    fn_index=self._REFINE_FN_INDEX,
                    trigger_id=self._GENERATE_TRIGGER_ID,
                    data=[content, 42, True, 0.3, 0.7, 20, 4],
                    include_event_data=True,
                )
                refined_payload = await self._queue_until_message(
                    client,
                    session_hash,
                    expected_event_id=refine_event_id,
                    success_msg="process_completed",
                )
                refined_text = _extract_process_completed_value(refined_payload)
                if not refined_text:
                    raise RuntimeError("ChatTTS refine_text returned no output")

                generate_event_id = await self._queue_join(
                    client,
                    session_hash,
                    fn_index=self._GENERATE_FN_INDEX,
                    trigger_id=self._GENERATE_TRIGGER_ID,
                    data=[
                        refined_text,
                        0.3,
                        0.7,
                        20,
                        speaker_embedding,
                        False,
                        speaker_seed,
                        "",
                        None,
                        4,
                    ],
                    include_event_data=True,
                )
                audio_payload = await self._queue_until_message(
                    client,
                    session_hash,
                    expected_event_id=generate_event_id,
                    success_msg="process_generating",
                )
                file_url = _extract_generated_file_url(audio_payload)
                if not file_url:
                    raise RuntimeError("ChatTTS generate_audio returned no file URL")

                response = await client.get(file_url)
                response.raise_for_status()
                return response.content

    async def _speaker_embedding(self, client: httpx.AsyncClient, audio_seed: int) -> str:
        cached = self._speaker_cache.get(audio_seed)
        if cached:
            return cached

        resp = await client.post(
            f"{self.base_url}/gradio_api/call/v2/on_audio_seed_change",
            json={"audio_seed_input": audio_seed},
        )
        resp.raise_for_status()
        event_id = resp.json().get("event_id")
        if not event_id:
            raise RuntimeError("ChatTTS speaker embedding call returned no event_id")

        stream = await client.get(
            f"{self.base_url}/gradio_api/call/on_audio_seed_change/{event_id}"
        )
        stream.raise_for_status()

        for raw_block in stream.text.split("\n\n"):
            block = raw_block.strip()
            if not block:
                continue
            data_lines = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
            if not data_lines:
                continue
            try:
                parsed = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], str):
                self._speaker_cache[audio_seed] = parsed[0]
                return parsed[0]

        raise RuntimeError("ChatTTS speaker embedding stream returned no data")

    async def _queue_join(
        self,
        client: httpx.AsyncClient,
        session_hash: str,
        *,
        fn_index: int,
        trigger_id: int,
        data: list[Any],
        include_event_data: bool = False,
    ) -> str | None:
        payload: dict[str, Any] = {
            "data": data,
            "fn_index": fn_index,
            "trigger_id": trigger_id,
            "session_hash": session_hash,
        }
        if include_event_data:
            payload["event_data"] = None
        resp = await client.post(f"{self.base_url}/gradio_api/queue/join?", json=payload)
        resp.raise_for_status()
        body = resp.json()
        return body.get("event_id") if isinstance(body, dict) else None

    async def _queue_until_message(
        self,
        client: httpx.AsyncClient,
        session_hash: str,
        *,
        expected_event_id: str | None,
        success_msg: str,
    ) -> str:
        resp = await client.get(
            f"{self.base_url}/gradio_api/queue/data",
            params={"session_hash": session_hash},
            timeout=120.0,
        )
        resp.raise_for_status()
        payload = resp.text

        matched_event = expected_event_id is None
        for event in _iter_sse_events(payload):
            event_id = event.get("event_id")
            if expected_event_id is not None and event_id != expected_event_id:
                continue
            matched_event = True
            if event.get("success") is False:
                raise RuntimeError(event.get("message") or "ChatTTS queue request failed")
            if event.get("msg") == success_msg:
                return payload

        if matched_event:
            raise RuntimeError(f"ChatTTS queue stream missing {success_msg}")
        raise RuntimeError("ChatTTS queue stream missing expected event_id")