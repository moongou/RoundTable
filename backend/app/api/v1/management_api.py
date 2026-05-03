"""管理后台 API — 服务状态、日志、会议管理、硬件信息"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.v1.admin_guard import require_management_token
from app.core.meeting_history import (
    MEETING_HISTORY_DIR,
    _normalize_summary_status,
    _parse_iso_datetime,
    _read_meeting_summary,
    _safe_json_load,
    _sanitize_session_id,
    build_meeting_script_package,
    load_meeting_script,
)

router = APIRouter(
    prefix="/management",
    dependencies=[Depends(require_management_token)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _list_session_dirs() -> list[Path]:
    if not MEETING_HISTORY_DIR.exists():
        return []
    dirs: list[Path] = []
    for entry in sorted(
        MEETING_HISTORY_DIR.iterdir(), key=lambda e: e.name, reverse=True
    ):
        if entry.is_dir() and (entry / "summary.json").exists():
            dirs.append(entry)
    return dirs


def _summary_sort_key(summary_path: Path) -> tuple[str, float]:
    payload = _safe_json_load(summary_path, default={})
    if not isinstance(payload, dict):
        return ("", summary_path.stat().st_mtime)
    started = str(payload.get("started_at") or payload.get("updated_at") or "")
    return (started, 0)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
async def service_status():
    """后端服务运行状态（兼容 systemd 和独立进程）。"""
    info: dict[str, Any] = {
        "checked_at": _utc_now_iso(),
        "pid": os.getpid(),
    }

    # systemd
    unit = os.environ.get("ROUNDTABLE_SYSTEMD_UNIT", "roundtable")
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        info["systemd_unit"] = unit
        info["systemd_status"] = result.stdout.strip()
    except Exception:
        info["systemd_unit"] = None
        info["systemd_status"] = "not_managed"

    # python executable
    info["python"] = sys.executable

    return info


# ---------------------------------------------------------------------------
# Logs (SSE)
# ---------------------------------------------------------------------------

@router.get("/logs/stream")
async def logs_stream(request: Request):
    """SSE 实时日志推送（journalctl -f 或 tail -f）。"""
    unit = os.environ.get("ROUNDTABLE_SYSTEMD_UNIT", "roundtable")

    async def event_generator():
        proc = None
        try:
            # try journalctl first, fall back to log file
            try:
                subprocess.run(
                    ["journalctl", "--version"],
                    capture_output=True, timeout=2,
                )
                proc = await asyncio.create_subprocess_exec(
                    "journalctl", "-u", unit, "-f", "-n", "200", "--no-pager",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception:
                log_path = Path(__file__).resolve().parents[3] / "runtime" / "roundtable.log"
                if log_path.exists():
                    proc = await asyncio.create_subprocess_exec(
                        "tail", "-f", "-n", "200", str(log_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                else:
                    yield "data: {\"line\": \"No log source available\"}\n\n"
                    return

            while True:
                if await request.is_disconnected():
                    break
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                yield f"data: {text}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if proc is not None:
                proc.kill()
                await proc.wait()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------

@router.get("/meetings")
async def list_meetings(
    search: str = Query(""),
    status: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """列出所有会议历史，支持搜索和状态筛选。"""
    dirs = _list_session_dirs()
    results: list[dict[str, Any]] = []

    for session_dir in dirs:
        summary = _normalize_summary_status(
            _safe_json_load(session_dir / "summary.json", default={})
        )
        if not isinstance(summary, dict) or not summary:
            continue

        # status filter
        if status:
            s = str(summary.get("status", "")).strip().lower()
            if s != status.strip().lower():
                continue

        topic = summary.get("topic") or {}
        if isinstance(topic, dict):
            topic_title = str(topic.get("title", ""))
        else:
            topic_title = str(topic)

        session_id = str(summary.get("session_id") or session_dir.name)

        # search filter (session_id, topic, participants)
        if search:
            q = search.lower()
            participants = summary.get("participants") or []
            participant_text = " ".join(
                str(p) for p in (participants if isinstance(participants, list) else [])
            )
            if (
                q not in session_id.lower()
                and q not in topic_title.lower()
                and q not in participant_text.lower()
            ):
                continue

        results.append({
            "session_id": session_id,
            "safe_session_id": summary.get("safe_session_id", ""),
            "topic": topic_title,
            "status": summary.get("status", ""),
            "started_at": summary.get("started_at", ""),
            "ended_at": summary.get("ended_at", ""),
            "participants": summary.get("participants", []),
            "line_count": summary.get("line_count", 0),
            "recording_count": summary.get("recording_count", 0),
            "finish_reason": summary.get("finish_reason", ""),
        })

    total = len(results)
    return {
        "meetings": results[offset : offset + limit],
        "total": total,
    }


@router.get("/meetings/{session_id}")
async def get_meeting_detail(session_id: str):
    """获取单场会议详情：摘要 + 脚本 + 录音清单。"""
    try:
        summary = _read_meeting_summary(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="会议记录不存在")

    script = {}
    try:
        script = load_meeting_script(session_id)
    except FileNotFoundError:
        pass

    safe_id = _sanitize_session_id(session_id)
    recordings = []
    manifest = _safe_json_load(
        MEETING_HISTORY_DIR / safe_id / "recordings.json", default={}
    )
    if isinstance(manifest, dict):
        recordings = manifest.get("recordings", [])

    # Extract user_review from ended event if present
    user_review = None
    events_path = MEETING_HISTORY_DIR / safe_id / "events.jsonl"
    if events_path.exists():
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                import json
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if isinstance(entry, dict) and entry.get("entry_type") == "ended":
                    data = entry.get("data")
                    if isinstance(data, dict):
                        user_review = data.get("user_review")
                    break
        except Exception:
            pass

    return {
        "summary": summary,
        "script": script,
        "recordings": recordings,
        "user_review": user_review,
    }


@router.delete("/meetings/{session_id}")
async def delete_meeting(session_id: str):
    """删除单场会议记录。"""
    import shutil
    safe_id = _sanitize_session_id(session_id)
    session_dir = MEETING_HISTORY_DIR / safe_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="会议记录不存在")
    shutil.rmtree(session_dir, ignore_errors=True)
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

@router.get("/hardware")
async def hardware_info():
    """服务器硬件信息。"""
    info: dict[str, Any] = {"checked_at": _utc_now_iso()}

    # CPU
    try:
        result = subprocess.run(["nproc"], capture_output=True, text=True, timeout=3)
        info["cpu_cores"] = result.stdout.strip()
    except Exception:
        info["cpu_cores"] = "unknown"

    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        info["cpu_model"] = "unknown"

    # Memory
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=3)
        info["memory"] = result.stdout.strip()
    except Exception:
        info["memory"] = "unknown"

    # Disk
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5,
        )
        info["disk"] = result.stdout.strip()
    except Exception:
        info["disk"] = "unknown"

    # Uptime
    try:
        with open("/proc/uptime") as f:
            uptime_s = float(f.readline().split()[0])
            hours = int(uptime_s // 3600)
            minutes = int((uptime_s % 3600) // 60)
            info["uptime"] = f"{hours}h {minutes}m"
    except Exception:
        info["uptime"] = "unknown"

    # nvidia-smi if available
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["gpu"] = result.stdout.strip()
    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

@router.post("/restart")
async def restart_service():
    """重启后端服务。"""
    info: dict[str, Any] = {"requested_at": _utc_now_iso(), "restarted": False}

    unit = os.environ.get("ROUNDTABLE_SYSTEMD_UNIT", "roundtable")

    # Background restart so the API can respond before the process dies
    async def _do_restart():
        await asyncio.sleep(0.5)
        try:
            subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    asyncio.create_task(_do_restart())
    info["restarted"] = True
    return info
