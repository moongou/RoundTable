"""会议历史附件 API。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.core.meeting_history import (
    build_meeting_script_package,
    build_meeting_script_export,
    load_meeting_script,
    store_meeting_recording,
)

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/sessions/{session_id}/script")
async def get_meeting_script(session_id: str) -> dict[str, object]:
    try:
        return load_meeting_script(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/script/export")
async def export_meeting_script(
    session_id: str,
    format: str = Query("markdown"),
) -> Response:
    try:
        media_type, filename, payload = build_meeting_script_export(session_id, format)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/sessions/{session_id}/script/package")
async def export_meeting_script_package(
    session_id: str,
    include_markdown: bool = Query(True),
    include_json: bool = Query(False),
    include_recording_manifest: bool = Query(False),
    include_recording_audio: bool = Query(False),
) -> Response:
    try:
        media_type, filename, payload = build_meeting_script_package(
            session_id,
            include_markdown=include_markdown,
            include_json=include_json,
            include_recording_manifest=include_recording_manifest,
            include_recording_audio=include_recording_audio,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/sessions/{session_id}/recordings")
async def upload_meeting_recording(
    session_id: str,
    speaker: str = Form(...),
    transcript: str = Form(""),
    duration_ms: int | None = Form(None),
    audio: UploadFile = File(...),
) -> dict[str, object]:
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频数据为空")

    suffix = Path(audio.filename or "").suffix.lstrip(".") or None

    try:
        recording = store_meeting_recording(
            session_id,
            speaker=speaker,
            audio_bytes=audio_bytes,
            extension=suffix,
            content_type=(audio.content_type or "application/octet-stream"),
            duration_ms=duration_ms,
            transcript=transcript,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "ok": True,
        "recording": recording,
    }