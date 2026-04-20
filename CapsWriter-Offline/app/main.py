from __future__ import annotations

import logging
from contextlib import suppress

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import settings
from .recognizer import SherpaOnnxStreamingRecognizer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("capswriter_offline")

app = FastAPI(
    title="CapsWriter-Offline",
    description="sherpa-onnx based offline ASR over WebSocket",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "service": "CapsWriter-Offline",
        "engine": "sherpa-onnx",
        "port": settings.port,
        "sample_rate": settings.sample_rate,
    }


@app.websocket("/ws")
async def ws_asr(websocket: WebSocket) -> None:
    """WebSocket ASR endpoint.

    Client input:
    - Binary frame: PCM16 mono audio chunk
    - Text frame: {"type":"eof"} to finalize

    Server output:
    - {"type":"partial","text":"..."}
    - {"type":"final","text":"..."}
    - {"type":"error","error":"..."}
    """
    await websocket.accept()

    try:
        recognizer = SherpaOnnxStreamingRecognizer.create()
    except Exception as exc:
        await websocket.send_json({"type": "error", "error": f"Recognizer init failed: {exc}"})
        await websocket.close(code=1011)
        return

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            binary_data = message.get("bytes")
            text_data = message.get("text")

            if binary_data is not None:
                partial = recognizer.push_pcm16(binary_data)
                if partial:
                    await websocket.send_json({"type": "partial", "text": partial})
                continue

            if text_data is None:
                continue

            if '"type":"eof"' in text_data.replace(" ", ""):
                final_text = recognizer.finalize()
                await websocket.send_json({"type": "final", "text": final_text})
                break

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as exc:
        logger.exception("WebSocket ASR error")
        with suppress(Exception):
            await websocket.send_json({"type": "error", "error": str(exc)})
    finally:
        with suppress(Exception):
            await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
