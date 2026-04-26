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
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.meeting_history import MeetingHistoryStore
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
    human_hand_raise_counts: dict[str, int] = {}

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
                await history_store.append_entry(
                    "internal",
                    "send_drop",
                    {"reason": "ws_closed_guard", "event_type": event_type},
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
                await history_store.append_entry(
                    "internal",
                    "send_drop",
                    {"reason": "send_exception", "event_type": event_type},
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

        topic_id = (config.get("topic_id") or "").strip()
        free_topic = (config.get("free_topic") or "").strip()
        free_topic_detail = (config.get("free_topic_detail") or "").strip()
        character_ids = config.get("character_ids", ["explorer", "skeptic"])
        thinker_ids = config.get("thinker_ids", [])
        human_names = config.get("human_names", ["豆苗"])
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
            from app.api.v1.sessions import _sessions  # noqa: PLC0415

            session = _sessions.get(session_id)
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

        # 创建讨论团队
        team = create_discussion_team(
            moderator=moderator_agent,
            characters=[a for a in ai_agents if a is not moderator_agent],
            humans=human_agents,
            selector_client=moderator_client,
            max_turns=max_turns,
            consume_designated_speaker=consume_session_designated_speaker,
            on_designation_lifecycle=on_designation_lifecycle,
            display_name_to_agent=display_name_to_agent,
            get_human_engagement_level=get_human_engagement_level,
        )

        # 创建安全过滤器
        safety_filter = SafetyFilter(model_client=character_client)

        # 创建 Floor Manager
        floor_manager = FloorManager(
            team=team,
            ai_agents=ai_agents,
            human_agents=human_agents,
            safety_filter=safety_filter,
            human_timeout=max(60, int(getattr(settings, "human_turn_timeout", 15) or 15)),
            designated_speaker_setter=set_session_designated_speaker,
            summary_memory=summary_memory,
            human_guidance_memory=moderator_guidance_memory,
            human_hand_raise_notifier=note_human_hand_raise,
            human_queue_scope=session_id,
        )

        # 设置 display name 映射（需求4：用于指定发言者解析）
        floor_manager.set_display_name_map(agent_display_map)

        # 注册回调，将事件推送到 WebSocket
        async def on_message(source, content, msg_type):
            # 需求8：暂停期间丢弃 AI/角色消息，避免恢复后出现堆积重放与错乱
            if floor_manager is not None and getattr(floor_manager, "_paused", False):
                if msg_type != "system":
                    logger.debug(
                        "[pause] drop message during pause source=%s len=%d",
                        source, len(content or ""),
                    )
                    return
            display_source = agent_display_map.get(source, source)
            await send_event(
                "message",
                {"source": display_source, "content": content, "msg_type": msg_type},
            )

        async def on_turn_change(speaker, is_human):
            display_speaker = agent_display_map.get(speaker, speaker)
            await send_event(
                "turn_change",
                {"speaker": display_speaker, "is_human": is_human},
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

        async def on_interrupt(interrupter, current_speaker, approved_by):
            await send_event(
                "interrupt",
                {
                    "interrupter": agent_display_map.get(interrupter, interrupter),
                    "interrupted_speaker": agent_display_map.get(current_speaker, current_speaker),
                    "approved": True,
                    "approved_by": approved_by,
                },
            )

        async def on_error(error_msg):
            msg = (str(error_msg or "") or "").strip() or "讨论流程出现异常"
            await send_event("error", {"message": msg})

        floor_manager.on_message(on_message)
        floor_manager.on_turn_change(on_turn_change)
        floor_manager.on_state_change(on_state_change)
        floor_manager.on_interrupt(on_interrupt)
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
            )
        )

        # 同时处理来自客户端的人类输入
        async def handle_human_input():
            nonlocal ws_closed
            try:
                while True:
                    data = await websocket.receive_text()
                    msg = json.loads(data)
                    if history_store is not None:
                        await history_store.append_entry(
                            "inbound",
                            str(msg.get("type", "unknown") or "unknown"),
                            msg,
                        )

                    if msg.get("type") == "human_input":
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        content = (msg.get("content", "") or "").strip()
                        if not content:
                            content = "（跳过）"
                        await floor_manager.submit_human_input(speaker, content)
                    elif msg.get("type") == "designate_speaker":
                        # 用户通过 UI 指定下一位发言者
                        target = msg.get("target", "")
                        if target:
                            # 反查 agent name
                            reverse_map = {v: k for k, v in agent_display_map.items()}
                            agent_name = reverse_map.get(target, target)
                            set_session_designated_speaker(agent_name)
                            logger.info("用户指定下一位发言者: %s (agent: %s)", target, agent_name)
                    elif msg.get("type") == "interrupt":
                        # 打断请求 - 通知主持人并切换状态
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        await floor_manager.request_interrupt(speaker)
                    elif msg.get("type") == "push_to_talk_start":
                        # PTT 开始 - 标记用户开始发言
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        await floor_manager.handle_push_to_talk_start(speaker)
                    elif msg.get("type") == "push_to_talk_end":
                        # PTT 结束 - 标记用户结束发言
                        speaker = normalize_display_name(msg.get("speaker", ""))
                        await floor_manager.handle_push_to_talk_end(speaker)
                    elif msg.get("type") == "asr_status":
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
                    elif msg.get("type") == "pause":
                        if floor_manager is not None:
                            floor_manager.set_paused(True)
                            await send_event("system", {"message": "讨论已暂停"})
                    elif msg.get("type") == "resume":
                        if floor_manager is not None:
                            floor_manager.set_paused(False)
                            await send_event("system", {"message": "讨论已恢复"})

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
                logger.error(f"讨论运行出错: {e}")
                history_final_status = "error"
                history_final_reason = str(e).strip() or history_final_reason
                msg = str(e).strip()
                if ws_closed:
                    continue
                if "websocket.send" in msg and "Unexpected ASGI message" in msg:
                    continue
                if not msg:
                    continue
                await send_event("error", {"message": msg})

    except WebSocketDisconnect:
        ws_closed = True
        history_final_status = "disconnected"
        history_final_reason = "websocket_disconnect"
        logger.info(f"WebSocket 断开: session_id={session_id}")
    except Exception as e:
        ws_closed = True
        history_final_status = "error"
        history_final_reason = str(e).strip() or history_final_reason
        logger.error(f"WebSocket 错误: {e}")
        try:
            msg = str(e).strip()
            if msg:
                await send_event("error", {"message": msg})
        except Exception:
            pass
    finally:
        ws_closed = True
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
            await history_store.finish(
                status=final_status,
                reason=history_final_reason,
                final_stats={
                    "send_observability": {
                        "drop_total": send_drop_total,
                        "drop_reasons": dict(send_drop_reasons),
                        "last_drop": dict(last_send_drop) if last_send_drop else None,
                    }
                },
            )
        logger.info(f"WebSocket 清理完成: session_id={session_id}")


async def _run_discussion(
    send_event: Callable[[str, dict], Awaitable[bool]],
    floor_manager: FloorManager,
    topic: str,
    observer_mode: bool = False,
    agent_display_map: dict[str, str] | None = None,
):
    """运行讨论并推送事件。"""
    async for event in floor_manager.run(topic):
        # 事件已经通过回调推送，这里只处理特殊事件
        if event["event_type"] == "ended":
            await send_event("ended", event.get("data", {}))
            return "completed"
        elif event["event_type"] in ("error", "api_error"):
            await send_event(event["event_type"], event.get("data", {}))
            return "error"
        elif event["event_type"] == "stream":
            # 流式文本推送
            data = dict(event.get("data", {}))
            source = (data.get("source") or "").strip()
            if source and agent_display_map is not None:
                data["source"] = agent_display_map.get(source, source)
            await send_event("stream", data)
        elif event["event_type"] == "human_input_requested":
            # 请求人类输入 - 直接推送给前端
            data = dict(event.get("data", {}))
            speaker = (data.get("speaker") or "").strip()
            display_speaker = speaker
            if speaker and agent_display_map is not None:
                display_speaker = agent_display_map.get(speaker, speaker)
                data["speaker"] = display_speaker
            await send_event("human_input_requested", data)
            if observer_mode and display_speaker:
                try:
                    await floor_manager.submit_human_input(display_speaker, "（旁听）")
                except Exception:
                    logger.debug("observer auto-skip failed", exc_info=True)
    return "completed"