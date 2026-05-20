"""WebSocket 端点

实时推送讨论事件到前端，接收人类参与者的输入。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.character_templates import load_all_templates
from app.agents.human_proxy import clear_human_queues, create_human_proxy, normalize_display_name, safe_agent_name
from app.agents.moderator import create_moderator
from app.agents.virtual_character import create_virtual_character, create_thinker_agent
from app.config import settings
from app.core.floor_manager import FloorManager
from app.store import user_store
from autogen_core.models import UserMessage
from app.core.llm_errors import describe_model_error
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.meeting_history import MeetingHistoryStore
from app.core.user_review import (
    build_user_review_prompt,
    has_enough_user_review_material,
    parse_user_review_response,
)
from app.core.rolling_summary_memory import HumanResponseGuidanceMemory, RollingSummaryMemory
from app.core.safety_filter import SafetyFilter
from app.core.thinkers import get_thinker, thinker_label
from app.core.topics import get_topic_by_id
from app.core.turn_scheduler import create_discussion_team

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/discussion/{session_id}")
async def discussion_websocket(websocket: WebSocket, session_id: str):
    """讨论 WebSocket 端点。

    连接后，客户端发送 JSON 消息来参与讨论，
    服务端推送讨论事件（轮次变更、AI发言、系统消息等）。

    客户端发送格式:
    {
        "type": "human_input" | "push_to_talk_start" | "push_to_talk_end" | "interrupt",
        "speaker": "小明",
        "content": "我觉得..."
    }

    服务端推送格式:
    {
        "event_type": "message" | "turn_change" | "stream" | "system" | "error" | "api_error" | "ended",
        "data": {...}
    }
    """
    human_names: list[str] = []
    ws_closed = False

    await websocket.accept()
    logger.info(f"WebSocket 连接建立: session_id={session_id}")
    event_seq = 0
    designated_next_speaker: str | None = None
    background_tasks: set[asyncio.Task] = set()
    floor_manager: FloorManager | None = None
    history_store: MeetingHistoryStore | None = None
    history_final_status = "running"
    history_final_reason = ""
    send_drop_total = 0
    send_drop_reasons: dict[str, int] = {}
    last_send_drop: dict[str, str] = {}
    _send_drop_history_last_ts: dict[str, float] = {}
    _send_drop_history_throttle_sec = 30.0
    human_hand_raise_counts: dict[str, int] = {}
    pending_human_request_ts: dict[str, float] = {}
    pending_human_request_id: dict[str, str] = {}
    client_metric_stats: dict[str, dict[str, object]] = {}

    def note_human_hand_raise(agent_name: str) -> None:
        normalized = (agent_name or "").strip()
        if not normalized:
            return
        human_hand_raise_counts[normalized] = human_hand_raise_counts.get(normalized, 0) + 1

    def get_human_engagement_level() -> int:
        if not human_hand_raise_counts:
            return 0
        highest_raise_count = max(human_hand_raise_counts.values())
        if highest_raise_count >= 3:
            return 2
        if highest_raise_count >= 2:
            return 1
        return 0

    def record_send_drop(reason: str, dropped_event_type: str) -> None:
        nonlocal send_drop_total, last_send_drop
        send_drop_total += 1
        send_drop_reasons[reason] = send_drop_reasons.get(reason, 0) + 1
        last_send_drop = {
            "reason": reason,
            "event_type": dropped_event_type,
        }

    def note_client_metric(
        name: str,
        value_ms: int,
        *,
        speaker: str,
        phase: str,
        event_seq: int | None,
        detail: str,
    ) -> None:
        normalized_name = (name or "").strip()
        if not normalized_name:
            return
        bucket = client_metric_stats.setdefault(
            normalized_name,
            {
                "count": 0,
                "total_ms": 0,
                "max_ms": value_ms,
                "min_ms": value_ms,
                "last_ms": value_ms,
                "last_speaker": speaker,
                "last_phase": phase,
                "last_event_seq": event_seq,
                "last_detail": detail,
            },
        )
        bucket["count"] = int(bucket.get("count", 0)) + 1
        bucket["total_ms"] = int(bucket.get("total_ms", 0)) + value_ms
        bucket["max_ms"] = max(int(bucket.get("max_ms", value_ms)), value_ms)
        bucket["min_ms"] = min(int(bucket.get("min_ms", value_ms)), value_ms)
        bucket["last_ms"] = value_ms
        bucket["last_speaker"] = speaker
        bucket["last_phase"] = phase
        bucket["last_event_seq"] = event_seq
        bucket["last_detail"] = detail

    def export_client_metric_stats() -> dict[str, dict[str, object]]:
        exported: dict[str, dict[str, object]] = {}
        for name, bucket in client_metric_stats.items():
            count = int(bucket.get("count", 0))
            total_ms = int(bucket.get("total_ms", 0))
            exported[name] = {
                "count": count,
                "avg_ms": round(total_ms / count, 2) if count else 0,
                "max_ms": int(bucket.get("max_ms", 0)),
                "min_ms": int(bucket.get("min_ms", 0)),
                "last_ms": int(bucket.get("last_ms", 0)),
                "last_speaker": str(bucket.get("last_speaker", "") or ""),
                "last_phase": str(bucket.get("last_phase", "") or ""),
                "last_event_seq": bucket.get("last_event_seq"),
                "last_detail": str(bucket.get("last_detail", "") or ""),
            }
        return exported

    def with_send_observability(data: dict) -> dict:
        payload = dict(data)
        payload["send_observability"] = {
            "drop_total": send_drop_total,
            "drop_reasons": dict(send_drop_reasons),
            "last_drop": dict(last_send_drop) if last_send_drop else None,
        }
        return payload

    async def send_event(event_type: str, data: dict) -> bool:
        nonlocal ws_closed
        nonlocal history_final_status, history_final_reason
        if ws_closed:
            record_send_drop("ws_closed_guard", event_type)
            if history_store is not None:
                _now = asyncio.get_running_loop().time()
                _reason_key = "ws_closed_guard"
                _last = _send_drop_history_last_ts.get(_reason_key, 0.0)
                if _now - _last >= _send_drop_history_throttle_sec:
                    _send_drop_history_last_ts[_reason_key] = _now
                    await history_store.append_entry(
                        "internal",
                        "send_drop",
                        {"reason": _reason_key, "event_type": event_type},
                    )
            return False
        nonlocal event_seq
        event_seq += 1
        payload = {
            "event_type": event_type,
            "event_seq": event_seq,
            "data": data,
        }
        try:
            await websocket.send_json(payload)
            if history_store is not None:
                await history_store.append_entry(
                    "outbound",
                    event_type,
                    data,
                    event_seq=event_seq,
                )
            if event_type == "ended":
                history_final_status = "completed"
            elif event_type in {"error", "api_error"}:
                if history_final_status == "running":
                    history_final_status = "error"
                if not history_final_reason:
                    history_final_reason = str(data.get("message", "") or "").strip()
            return True
        except (RuntimeError, WebSocketDisconnect) as e:
            # RuntimeError: Unexpected ASGI message 'websocket.send' after close.
            reason = "runtime_after_close" if isinstance(e, RuntimeError) else "websocket_disconnect"
            record_send_drop(reason, event_type)
            if history_store is not None:
                _now = asyncio.get_running_loop().time()
                _last = _send_drop_history_last_ts.get(reason, 0.0)
                if _now - _last >= _send_drop_history_throttle_sec:
                    _send_drop_history_last_ts[reason] = _now
                    await history_store.append_entry(
                        "internal",
                        "send_drop",
                        {"reason": reason, "event_type": event_type},
                    )
            ws_closed = True
            return False
        except Exception:
            record_send_drop("send_exception", event_type)
            if history_store is not None:
                _now = asyncio.get_running_loop().time()
                _reason = "send_exception"
                _last = _send_drop_history_last_ts.get(_reason, 0.0)
                if _now - _last >= _send_drop_history_throttle_sec:
                    _send_drop_history_last_ts[_reason] = _now
                    await history_store.append_entry(
                        "internal",
                        "send_drop",
                        {"reason": _reason, "event_type": event_type},
                    )
            ws_closed = True
            logger.debug("send_event failed for type=%s", event_type, exc_info=True)
            return False

    async def send_phase_telemetry(data: dict) -> bool:
        return await send_event("phase_telemetry", with_send_observability(data))

    def publish_designate_telemetry(stage: str, target: str | None) -> None:
        if ws_closed:
            return

        async def _emit() -> None:
            await send_phase_telemetry(
                {
                    "source": "backend",
                    "phase": "selecting_speaker",
                    "reason": f"designate_{stage}",
                    "recovery": False,
                    "session_id": session_id,
                    "designate_stage": stage,
                    "designate_target": target or "",
                },
            )

        try:
            task = asyncio.create_task(_emit())
            background_tasks.add(task)
            task.add_done_callback(lambda t: background_tasks.discard(t))
        except Exception:
            logger.debug("publish_designate_telemetry failed", exc_info=True)

    def set_session_designated_speaker(name: str | None) -> None:
        nonlocal designated_next_speaker
        designated_next_speaker = name
        if name:
            publish_designate_telemetry("requested", name)

    def consume_session_designated_speaker() -> str | None:
        nonlocal designated_next_speaker
        value = designated_next_speaker
        designated_next_speaker = None
        if value:
            publish_designate_telemetry("consumed", value)
        return value

    def on_designation_lifecycle(stage: str, target: str | None) -> None:
        # consumed is emitted by consume_session_designated_speaker.
        if stage == "consumed":
            return
        publish_designate_telemetry(stage, target)

    try:
        # 接收初始配置
        config_msg = await websocket.receive_text()
        config = json.loads(config_msg)

        _session_start_time: float | None = None
        _session_user_id: int | None = None
        try:
            raw_uid = config.get("user_id")
            if raw_uid is not None:
                _session_user_id = int(raw_uid)
                _session_start_time = asyncio.get_running_loop().time()
                user_store.increment_session(_session_user_id)
                user_store.record_event(_session_user_id, "session_start", f"session_id={session_id}")
        except (ValueError, TypeError):
            logger.debug("Invalid user_id in websocket config: %s", raw_uid)

        topic_id = (config.get("topic_id") or "").strip()
        free_topic = (config.get("free_topic") or "").strip()
        free_topic_detail = (config.get("free_topic_detail") or "").strip()
        character_ids = config.get("character_ids", ["explorer", "skeptic"])
        thinker_ids = config.get("thinker_ids", [])
        human_names = config.get("human_names", ["同学"])
        max_turns = max(1, int(config.get("max_turns") or settings.max_turns))
        # 需求16：旁听模式——用户只观看讨论，每次轮到用户时系统自动跳过
        observer_mode = bool(config.get("observer_mode", False))

        history_store = MeetingHistoryStore(session_id)
        await history_store.start(
            topic={
                "topic_id": topic_id,
                "free_topic": free_topic,
                "free_topic_detail": free_topic_detail,
            },
            config={
                "topic_id": topic_id,
                "free_topic": free_topic,
                "free_topic_detail": free_topic_detail,
                "character_ids": character_ids,
                "thinker_ids": thinker_ids,
                "human_names": human_names,
                "max_turns": max_turns,
                "observer_mode": observer_mode,
            },
        )
        await history_store.append_entry("inbound", "session_config", config)

        # 验证话题；自由话题优先复用已创建 session 中的 topic，避免 websocket
        # 再次把占位 topic_id 当成预设话题去查表。
        topic = None
        session_topic = None
        try:
            from app.api.v1.sessions import get_cached_session  # noqa: PLC0415

            session = get_cached_session(session_id)
            if session is not None:
                session_topic = session.topic
        except Exception:
            logger.debug("resolve session topic failed", exc_info=True)

        is_free_placeholder = topic_id in ("", "free", "free_topic")
        if not is_free_placeholder:
            topic = get_topic_by_id(topic_id)

        if topic is None and session_topic is not None:
            session_topic_id = (getattr(session_topic, "id", "") or "").strip()
            if is_free_placeholder or session_topic_id == topic_id:
                topic = session_topic

        if topic is None and free_topic:
            from app.models.session import Topic as TopicModel  # noqa: PLC0415
            from app.core.topics import FREE_TOPIC_CATEGORY_ID, FREE_TOPIC_CATEGORY_NAME  # noqa: PLC0415

            topic = TopicModel(
                id="free_topic",
                title=free_topic,
                description=f"由用户发起的自由讨论话题：{free_topic_detail or free_topic}",
                category=FREE_TOPIC_CATEGORY_ID,
                age_range="8-12",
                guide_questions=[],
                tags=[FREE_TOPIC_CATEGORY_NAME, "自由话题"],
            )

        if not topic:
            await send_event("error", {"message": f"话题 '{topic_id}' 不存在"})
            await websocket.close()
            return

        # 验证角色
        templates = load_all_templates()
        for char_id in character_ids:
            if char_id not in templates:
                # normalize via monotonic sender for frontend ordering
                await send_event("error", {"message": f"角色 '{char_id}' 不存在"})
                await websocket.close()
                return

        # 验证思想家
        for tid in thinker_ids:
            if not get_thinker(tid):
                await send_event("error", {"message": f"思想家 '{tid}' 不存在"})
                await websocket.close()
                return

        # 最多 8 个虚拟角色（不含主持人李老师）
        non_moderator_chars = [c for c in character_ids if c != "moderator"]
        virtual_count = len(non_moderator_chars) + len(thinker_ids)
        if virtual_count > 8:
            await send_event(
                "error",
                {
                    "message": f"虚拟角色最多 8 人（当前选择了 {virtual_count} 人），请减少选择"
                },
            )
            await websocket.close()
            return

        # 确保至少有一个角色参与
        if not character_ids and not thinker_ids:
            character_ids = ["explorer", "skeptic"]

        # 验证 LLM 配置
        is_valid, error_msg = settings.validate_llm_config()
        if not is_valid:
            await send_event(
                "api_error",
                {
                    "message": error_msg,
                    "code": "api_key_missing",
                    "recoverable": False,
                },
            )
            await websocket.close()
            return

        # 创建 Agent 实例
        try:
            moderator_client = create_moderator_client()
            character_client = create_character_client()
        except Exception as e:
            await send_event(
                "api_error",
                {
                    "message": f"创建 AI 客户端失败: {e}",
                    "recoverable": False,
                },
            )
            await websocket.close()
            return

        # Display names (Chinese) for system prompt and frontend display
        student_names = [templates[cid].name for cid in character_ids if cid in templates and cid != "moderator"]
        thinker_display_names = [thinker_label(tid, get_thinker(tid)) for tid in thinker_ids]
        all_participant_names = [templates["moderator"].name] + student_names + thinker_display_names + human_names

        # internal agent name → Chinese display name (for translating events to frontend)
        agent_display_map: dict[str, str] = {"moderator": templates["moderator"].name}
        for cid in character_ids:
            if cid in templates and cid != "moderator":
                agent_display_map[cid] = templates[cid].name
        for tid in thinker_ids:
            agent_display_map[safe_agent_name(tid)] = thinker_label(tid, get_thinker(tid))
        for hn in dict.fromkeys(human_names):
            agent_display_map[safe_agent_name(hn)] = hn

        await history_store.update_context(
            topic={
                "id": getattr(topic, "id", ""),
                "title": topic.title,
                "description": topic.description,
                "category": getattr(topic, "category", ""),
            },
            participants=all_participant_names,
            agent_display_map=agent_display_map,
        )

        summary_memory = RollingSummaryMemory()
        moderator_guidance_memory = HumanResponseGuidanceMemory()
        shared_memory = [summary_memory]
        moderator_memory = [summary_memory, moderator_guidance_memory]

        moderator = create_moderator(
            model_client=moderator_client,
            topic=topic.title + " - " + topic.description,
            participant_names=all_participant_names,
            student_names=student_names,
            thinker_names=thinker_display_names,
            human_names=human_names,
            memory=moderator_memory,
        )

        characters = [
            create_virtual_character(
                cid,
                model_client=character_client,
                topic=topic.title,
                participant_names=all_participant_names,
                memory=shared_memory,
            )
            for cid in character_ids if cid != "moderator"
        ]

        # 创建思想家角色
        thinker_agents = [
            create_thinker_agent(
                tid,
                model_client=character_client,
                topic=topic.title,
                participant_names=all_participant_names,
                memory=shared_memory,
            )
            for tid in thinker_ids
        ]

        humans = [
            create_human_proxy(name, session_scope=session_id)
            for name in dict.fromkeys(human_names)
        ]

        # 去重：确保没有同名 Agent（AutoGen 要求名字唯一）
        seen_names: set[str] = set()
        unique_agents = []
        for agent in [moderator] + characters + thinker_agents + humans:
            if agent.name not in seen_names:
                seen_names.add(agent.name)
                unique_agents.append(agent)

        moderator_agent = unique_agents[0]
        ai_agents = [a for a in unique_agents if a in ([moderator] + characters + thinker_agents)]
        human_agents = [a for a in unique_agents if a in humans]

        # 构建 display_name → agent_name 映射（用于 turn_scheduler 解析点名）
        display_name_to_agent: dict[str, str] = {}
        for agent_name, display_name in agent_display_map.items():
            display_name_to_agent[display_name] = agent_name

        def build_discussion_team() -> SelectorGroupChat:
            return create_discussion_team(
                moderator=moderator_agent,
                characters=[a for a in ai_agents if a is not moderator_agent],
                humans=human_agents,
                selector_client=moderator_client,
                max_turns=max_turns,
                consume_designated_speaker=consume_session_designated_speaker,
                on_designation_lifecycle=on_designation_lifecycle,
                display_name_to_agent=display_name_to_agent,
                get_human_engagement_level=get_human_engagement_level,
                thinker_agent_names=[a.name for a in thinker_agents],
            )

        # 创建讨论团队
        team = build_discussion_team()

        # 创建安全过滤器
        safety_filter = SafetyFilter(model_client=character_client)

        # 创建 Floor Manager
        floor_manager = FloorManager(
            team=team,
            team_factory=build_discussion_team,
            ai_agents=ai_agents,
            human_agents=human_agents,
            safety_filter=safety_filter,
            human_timeout=max(60, int(getattr(settings, "human_turn_timeout", 15) or 15)),
            designated_speaker_setter=set_session_designated_speaker,
            summary_memory=summary_memory,
            human_guidance_memory=moderator_guidance_memory,
            human_hand_raise_notifier=note_human_hand_raise,
            human_queue_scope=session_id,
            thinker_agent_names=[a.name for a in thinker_agents],
            nominal_max_turns=max_turns,
            is_connected=lambda: not ws_closed,
        )

        # 设置 display name 映射（需求4：用于指定发言者解析）
        floor_manager.set_display_name_map(agent_display_map)

        # 注册回调，将事件推送到 WebSocket
        async def on_message(source, content, msg_type, tts_text=""):
            # 需求8：暂停期间丢弃 AI/角色消息，避免恢复后出现堆积重放与错乱
            if floor_manager is not None and getattr(floor_manager, "_paused", False):
                if msg_type != "system":
                    logger.debug(
                        "[pause] drop message during pause source=%s len=%d",
                        source, len(content or ""),
                    )
                    return
            display_source = agent_display_map.get(source, source)
            payload = {
                "source": display_source,
                "agent_source": source,
                "content": content,
                "msg_type": msg_type,
            }
            if (tts_text or "").strip():
                payload["tts_text"] = tts_text
            await send_event(
                "message",
                payload,
            )

        async def on_turn_change(speaker, is_human):
            display_speaker = agent_display_map.get(speaker, speaker)
            await send_event(
                "turn_change",
                {
                    "speaker": display_speaker,
                    "agent_speaker": speaker,
                    "is_human": is_human,
                },
            )

        async def on_state_change(old_state, new_state, reason="", recovery=False):
            # 需求13：把机器代号转成用户可读的中文，前端直接展示即可
            state_label_map = {
                "init": "正在准备讨论",
                "moderator_opening": "李老师正在开场",
                "selecting_speaker": "李老师正在安排下一位发言",
                "ai_speaking": "思考中……",
                "human_turn_waiting": "轮到你了",
                "human_speaking": "正在听你说",
                "interrupted": "有人想补充一句",
                "closing": "正在收尾",
                "ended": "讨论已结束",
            }
            await send_event(
                "state_change",
                {
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                    "old_label": state_label_map.get(old_state.value, old_state.value),
                    "new_label": state_label_map.get(new_state.value, new_state.value),
                },
            )
            await send_phase_telemetry(
                {
                    "source": "backend",
                    "phase": new_state.value,
                    "prev_phase": old_state.value,
                    "reason": reason or "state_transition",
                    "recovery": bool(recovery),
                    "speaker": agent_display_map.get(
                        floor_manager.current_speaker or "",
                        floor_manager.current_speaker or "",
                    ),
                    "session_id": session_id,
                },
            )

        async def on_interrupt(interrupter, current_speaker, approved_by, request_id):
            await send_event(
                "interrupt",
                {
                    "interrupter": agent_display_map.get(interrupter, interrupter),
                    "interrupted_speaker": agent_display_map.get(current_speaker, current_speaker),
                    "approved": True,
                    "approved_by": approved_by,
                    "request_id": str(request_id or ""),
                },
            )

        async def on_human_input_requested(data):
            payload = dict(data or {})
            speaker = (payload.get("speaker") or "").strip()
            request_reason = (payload.get("reason") or "normal").strip().lower()
            display_speaker = speaker
            if speaker:
                display_speaker = agent_display_map.get(speaker, speaker)
                payload["agent_speaker"] = speaker
                payload["speaker"] = display_speaker
            if pending_human_request_ts is not None and display_speaker:
                pending_human_request_ts[display_speaker] = asyncio.get_running_loop().time()
            if pending_human_request_id is not None and display_speaker:
                request_id = str(payload.get("request_id") or "").strip()
                if request_id:
                    pending_human_request_id[display_speaker] = request_id
            await send_event("human_input_requested", payload)
            await send_event(
                "phase_telemetry",
                {
                    "source": "backend",
                    "phase": "human_turn_waiting",
                    "reason": f"human_input_requested_{request_reason}",
                    "recovery": request_reason in {"moderator_designated_human", "watchdog"},
                    "speaker": display_speaker,
                    "agent_speaker": speaker,
                    "session_id": session_id,
                },
            )

        async def on_error(error_msg):
            msg = (str(error_msg or "") or "").strip() or "讨论流程出现异常"
            await send_event("error", {"message": msg})

        floor_manager.on_message(on_message)
        floor_manager.on_turn_change(on_turn_change)
        floor_manager.on_state_change(on_state_change)
        floor_manager.on_interrupt(on_interrupt)
        floor_manager.on_human_input_requested(on_human_input_requested)
        floor_manager.on_error(on_error)

        # 通知客户端讨论开始
        await send_event(
            "system",
            {
                "message": "讨论开始",
                "topic": topic.title,
                "participants": all_participant_names,
            },
        )

        # 运行讨论（异步任务）
        discussion_task = asyncio.create_task(
            _run_discussion(
                send_event,
                floor_manager,
                topic.title + "\n\n" + topic.description,
                observer_mode=observer_mode,
                agent_display_map=agent_display_map,
                session_id=session_id,
                pending_human_request_ts=pending_human_request_ts,
                pending_human_request_id=pending_human_request_id,
                history_store=history_store,
                moderator_client=moderator_client,
                human_name=human_names[0] if human_names else "",
                topic_title=topic.title,
            )
        )

        # 同时处理来自客户端的人类输入
        async def handle_human_input():
            nonlocal ws_closed
            try:
                while True:
                    data = await websocket.receive_text()
                    msg = json.loads(data)
                    msg_type = str(msg.get("type", "unknown") or "unknown")

                    if msg_type == "human_input":
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        pending_ts = pending_human_request_ts.pop(speaker, None)
                        pending_id = pending_human_request_id.pop(speaker, "")
                        if pending_ts is not None:
                            wait_ms = int(
                                max(0.0, (asyncio.get_running_loop().time() - pending_ts) * 1000)
                            )
                            msg["request_wait_ms"] = wait_ms
                        if pending_id:
                            msg["request_id"] = pending_id
                    elif msg_type == "client_metric":
                        name = str(msg.get("name", "") or "").strip()
                        raw_value_ms = msg.get("value_ms")
                        try:
                            value_ms = max(0, int(raw_value_ms))
                        except (TypeError, ValueError):
                            value_ms = -1
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        phase = str(msg.get("phase", "") or "").strip()
                        detail = str(msg.get("detail", "") or "").strip()
                        raw_event_seq = msg.get("event_seq")
                        try:
                            linked_event_seq = int(raw_event_seq) if raw_event_seq is not None else None
                        except (TypeError, ValueError):
                            linked_event_seq = None
                        msg["speaker"] = speaker
                        msg["name"] = name
                        msg["phase"] = phase
                        msg["detail"] = detail
                        msg["event_seq"] = linked_event_seq
                        if value_ms >= 0:
                            msg["value_ms"] = value_ms
                            note_client_metric(
                                name,
                                value_ms,
                                speaker=speaker,
                                phase=phase,
                                event_seq=linked_event_seq,
                                detail=detail,
                            )
                    elif msg_type == "end_discussion":
                        msg["speaker"] = normalize_display_name(msg.get("speaker", ""))
                        msg["reason"] = str(msg.get("reason", "") or "").strip()

                    if history_store is not None:
                        await history_store.append_entry(
                            "inbound",
                            msg_type,
                            msg,
                        )

                    if msg_type == "human_input":
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        content = (msg.get("content", "") or "").strip()
                        if not content:
                            content = "（跳过）"
                        if _session_user_id is not None and content != "（跳过）":
                            user_store.add_speech_count(_session_user_id)
                        await floor_manager.submit_human_input(speaker, content)
                    elif msg_type == "designate_speaker":
                        # 用户通过 UI 指定下一位发言者
                        target = msg.get("target", "")
                        if target:
                            # 反查 agent name
                            reverse_map = {v: k for k, v in agent_display_map.items()}
                            agent_name = reverse_map.get(target, target)
                            set_session_designated_speaker(agent_name)
                            logger.info("用户指定下一位发言者: %s (agent: %s)", target, agent_name)
                    elif msg_type == "interrupt":
                        # 打断请求 - 通知主持人并切换状态
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        request_id = str(msg.get("request_id", "") or "").strip()
                        await floor_manager.request_interrupt(speaker, request_id=request_id)
                    elif msg_type == "end_discussion":
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        reason = str(msg.get("reason", "") or "button").strip() or "button"
                        await floor_manager.request_end_discussion(
                            speaker,
                            source=reason,
                        )
                        await send_phase_telemetry(
                            {
                                "source": "frontend",
                                "phase": "closing",
                                "reason": f"human_requested_end_{reason}",
                                "recovery": False,
                                "speaker": speaker,
                            }
                        )
                    elif msg_type == "push_to_talk_start":
                        # PTT 开始 - 标记用户开始发言
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        await floor_manager.handle_push_to_talk_start(speaker)
                    elif msg_type == "push_to_talk_end":
                        # PTT 结束 - 标记用户结束发言
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        await floor_manager.handle_push_to_talk_end(speaker)
                    elif msg_type == "asr_status":
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        provider = str(msg.get("provider", "unknown") or "unknown")
                        status = str(msg.get("status", "unknown") or "unknown")
                        available = msg.get("available")
                        listening = msg.get("listening")
                        text_len = msg.get("text_len")
                        is_final = msg.get("is_final")
                        error = (msg.get("error", "") or "").strip()

                        logger.info(
                            "[ASR] speaker=%s provider=%s status=%s available=%s listening=%s text_len=%s is_final=%s error=%s",
                            speaker,
                            provider,
                            status,
                            available,
                            listening,
                            text_len,
                            is_final,
                            error,
                        )
                        await send_phase_telemetry(
                            {
                                "source": "frontend_asr",
                                "phase": "human_speaking",
                                "reason": f"asr_{status}",
                                "recovery": False,
                                "speaker": speaker,
                                "session_id": session_id,
                                "asr_provider": provider,
                                "asr_available": available,
                                "asr_listening": listening,
                                "asr_text_len": text_len,
                                "asr_is_final": is_final,
                                "asr_error": error,
                            }
                        )
                    elif msg_type == "pause":
                        if floor_manager is not None:
                            floor_manager.set_paused(True)
                            await send_event("system", {"message": "讨论已暂停"})
                    elif msg_type == "resume":
                        if floor_manager is not None:
                            floor_manager.set_paused(False)
                            await send_event("system", {"message": "讨论已恢复"})
                            pending_request = floor_manager.pending_human_input_request_snapshot()
                            if pending_request is not None:
                                data = dict(pending_request)
                                speaker = (data.get("speaker") or "").strip()
                                display_speaker = speaker
                                if speaker:
                                    display_speaker = agent_display_map.get(speaker, speaker)
                                    data["agent_speaker"] = speaker
                                    data["speaker"] = display_speaker
                                await send_event("human_input_requested", data)
                                request_reason = (data.get("reason") or "normal").strip().lower()
                                await send_phase_telemetry(
                                    {
                                        "source": "backend",
                                        "phase": "human_turn_waiting",
                                        "reason": f"resume_human_input_resync_{request_reason}",
                                        "recovery": True,
                                        "speaker": display_speaker,
                                        "agent_speaker": speaker,
                                        "session_id": session_id,
                                    }
                                )

            except WebSocketDisconnect:
                ws_closed = True
                logger.info("WebSocket 断开连接")
            except Exception as e:
                logger.error(f"处理人类输入时出错: {e}")

        # 并行运行讨论和人类输入处理。任一侧结束时，取消另一侧，避免连接关闭后继续发送。
        input_task = asyncio.create_task(handle_human_input())
        done, pending = await asyncio.wait(
            {discussion_task, input_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for t in done:
            try:
                result = await t
                if t is discussion_task and result == "completed":
                    history_final_status = "completed"
                elif t is discussion_task and result == "error":
                    history_final_status = "error"
            except asyncio.CancelledError:
                pass
            except Exception as e:
                error_info = describe_model_error(e)
                logger.error("讨论运行出错: %s", error_info.technical_detail or e, exc_info=True)
                history_final_status = "error"
                history_final_reason = error_info.message or history_final_reason
                msg = error_info.message.strip()
                if ws_closed:
                    continue
                if "websocket.send" in msg and "Unexpected ASGI message" in msg:
                    continue
                if not msg:
                    continue
                await send_event(
                    "api_error" if error_info.is_model_error else "error",
                    error_info.to_event_data(),
                )

    except WebSocketDisconnect:
        ws_closed = True
        history_final_status = "disconnected"
        history_final_reason = "websocket_disconnect"
        logger.info(f"WebSocket 断开: session_id={session_id}")
    except Exception as e:
        error_info = describe_model_error(e)
        history_final_status = "error"
        history_final_reason = error_info.message or history_final_reason
        logger.error("WebSocket 错误: %s", error_info.technical_detail or e, exc_info=True)
        try:
            msg = error_info.message.strip()
            if msg:
                await send_event(
                    "api_error" if error_info.is_model_error else "error",
                    error_info.to_event_data(),
                )
        except Exception:
            pass
    finally:
        ws_closed = True
        # Track session duration for logged-in users
        if _session_user_id is not None and _session_start_time is not None:
            try:
                session_ms = int((asyncio.get_running_loop().time() - _session_start_time) * 1000)
                if session_ms > 0:
                    user_store.add_online_time(_session_user_id, session_ms)
                    user_store.record_event(_session_user_id, "session_end", f"duration_ms={session_ms}")
            except Exception:
                logger.debug("Failed to track session duration", exc_info=True)
        if floor_manager is not None:
            # 连接关闭后停止 FloorManager 对外回调，避免 finally 阶段继续尝试 websocket.send。
            floor_manager._on_message = None
            floor_manager._on_turn_change = None
            floor_manager._on_state_change = None
            floor_manager._on_interrupt = None
            floor_manager._on_error = None
        if background_tasks:
            for task in list(background_tasks):
                task.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
            background_tasks.clear()
        clear_human_queues(session_scope=session_id)
        if history_store is not None:
            final_status = history_final_status
            if final_status == "running":
                final_status = "disconnected"
            floor_diag = floor_manager.diagnostics() if floor_manager is not None else {}
            await history_store.finish(
                status=final_status,
                reason=history_final_reason,
                final_stats={
                    "send_observability": {
                        "drop_total": send_drop_total,
                        "drop_reasons": dict(send_drop_reasons),
                        "last_drop": dict(last_send_drop) if last_send_drop else None,
                    },
                    "client_metrics": export_client_metric_stats(),
                    "floor_manager": floor_diag,
                },
            )
        logger.info(f"WebSocket 清理完成: session_id={session_id}")


async def _run_discussion(
    send_event: Callable[[str, dict], Awaitable[bool]],
    floor_manager: FloorManager,
    topic: str,
    observer_mode: bool = False,
    agent_display_map: dict[str, str] | None = None,
    session_id: str = "",
    pending_human_request_ts: dict[str, float] | None = None,
    pending_human_request_id: dict[str, str] | None = None,
    *,
    history_store: MeetingHistoryStore | None = None,
    moderator_client=None,
    human_name: str = "",
    topic_title: str = "",
):
    """运行讨论并推送事件。"""
    async for event in floor_manager.run(topic):
        # 事件已经通过回调推送，这里只处理特殊事件
        if event["event_type"] == "ended":
            data = dict(event.get("data", {}))

            # --- Mandatory user review ---
            if history_store is not None and moderator_client is not None and human_name:
                try:
                    messages = await history_store.get_review_messages()
                    if has_enough_user_review_material(messages, human_name=human_name):
                        prompt = build_user_review_prompt(
                            topic_title, human_name, messages
                        )
                        response = await moderator_client.create(
                            [UserMessage(content=prompt, source="user")]
                        )
                        raw_content = (
                            response.content
                            if isinstance(response.content, str)
                            else str(response.content)
                        )
                        review = parse_user_review_response(raw_content)
                        if review:
                            data["user_review"] = review
                            logger.info(
                                "User review generated for session=%s human=%s (%d chars)",
                                session_id, human_name, len(review),
                            )
                except Exception:
                    logger.debug(
                        "User review generation failed for session=%s",
                        session_id, exc_info=True,
                    )

            await send_event("ended", data)
            return "completed"
        elif event["event_type"] in ("error", "api_error"):
            await send_event(event["event_type"], event.get("data", {}))
            return "error"
        elif event["event_type"] == "stream":
            # 流式文本推送
            data = dict(event.get("data", {}))
            source = (data.get("source") or "").strip()
            if source and agent_display_map is not None:
                data["agent_source"] = source
                data["source"] = agent_display_map.get(source, source)
            await send_event("stream", data)
        elif event["event_type"] == "human_input_requested":
            # 请求人类输入 - 直接推送给前端
            data = dict(event.get("data", {}))
            speaker = (data.get("speaker") or "").strip()
            request_reason = (data.get("reason") or "normal").strip().lower()
            display_speaker = speaker
            if speaker and agent_display_map is not None:
                display_speaker = agent_display_map.get(speaker, speaker)
                data["agent_speaker"] = speaker
                data["speaker"] = display_speaker
            if pending_human_request_ts is not None and display_speaker:
                pending_human_request_ts[display_speaker] = asyncio.get_running_loop().time()
            if pending_human_request_id is not None and display_speaker:
                request_id = str(data.get("request_id") or "").strip()
                if request_id:
                    pending_human_request_id[display_speaker] = request_id
            await send_event("human_input_requested", data)
            await send_event(
                "phase_telemetry",
                {
                    "source": "backend",
                    "phase": "human_turn_waiting",
                    "reason": f"human_input_requested_{request_reason}",
                    "recovery": request_reason in {"moderator_designated_human", "watchdog"},
                    "speaker": display_speaker,
                    "agent_speaker": speaker,
                    "session_id": session_id,
                },
            )
            if observer_mode and display_speaker and request_reason != "interrupt":
                try:
                    await floor_manager.submit_human_input(display_speaker, "（旁听）")
                except Exception:
                    logger.debug("observer auto-skip failed", exc_info=True)
    return "completed"