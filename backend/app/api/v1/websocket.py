"""WebSocket 端点

实时推送讨论事件到前端，接收人类参与者的输入。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.character_templates import load_all_templates
from app.agents.human_proxy import clear_human_queues, create_human_proxy, put_human_input, safe_agent_name
from app.agents.moderator import create_moderator
from app.agents.virtual_character import create_virtual_character, create_thinker_agent
from app.config import settings
from app.core.floor_manager import FloorManager
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.safety_filter import SafetyFilter
from app.core.thinkers import get_thinker
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
    await websocket.accept()
    logger.info(f"WebSocket 连接建立: session_id={session_id}")

    try:
        # 接收初始配置
        config_msg = await websocket.receive_text()
        config = json.loads(config_msg)

        topic_id = config.get("topic_id")
        character_ids = config.get("character_ids", ["explorer", "skeptic"])
        thinker_ids = config.get("thinker_ids", [])
        human_names = config.get("human_names", ["同学"])

        # 验证话题
        topic = get_topic_by_id(topic_id)
        if not topic:
            await websocket.send_json({"event_type": "error", "data": {"message": f"话题 '{topic_id}' 不存在"}})
            await websocket.close()
            return

        # 验证角色
        templates = load_all_templates()
        for char_id in character_ids:
            if char_id not in templates:
                await websocket.send_json(
                    {"event_type": "error", "data": {"message": f"角色 '{char_id}' 不存在"}}
                )
                await websocket.close()
                return

        # 验证思想家
        for tid in thinker_ids:
            if not get_thinker(tid):
                await websocket.send_json(
                    {"event_type": "error", "data": {"message": f"思想家 '{tid}' 不存在"}}
                )
                await websocket.close()
                return

        # 确保至少有一个角色参与
        if not character_ids and not thinker_ids:
            character_ids = ["explorer", "skeptic"]

        # 验证 LLM 配置
        is_valid, error_msg = settings.validate_llm_config()
        if not is_valid:
            await websocket.send_json({
                "event_type": "api_error",
                "data": {
                    "message": error_msg,
                    "code": "api_key_missing",
                    "recoverable": False,
                },
            })
            await websocket.close()
            return

        # 创建 Agent 实例
        try:
            moderator_client = create_moderator_client()
            character_client = create_character_client()
        except Exception as e:
            await websocket.send_json({
                "event_type": "api_error",
                "data": {
                    "message": f"创建 AI 客户端失败: {e}",
                    "recoverable": False,
                },
            })
            await websocket.close()
            return

        # Display names (Chinese) for system prompt and frontend display
        all_participant_names = [templates["moderator"].name]
        all_participant_names += [templates[cid].name for cid in character_ids if cid in templates and cid != "moderator"]
        all_participant_names += [get_thinker(tid).get("name", tid) for tid in thinker_ids]
        all_participant_names += human_names

        # internal agent name → Chinese display name (for translating events to frontend)
        agent_display_map: dict[str, str] = {"moderator": templates["moderator"].name}
        for cid in character_ids:
            if cid in templates and cid != "moderator":
                agent_display_map[cid] = templates[cid].name
        for tid in thinker_ids:
            agent_display_map[tid] = get_thinker(tid).get("name", tid)
        for hn in dict.fromkeys(human_names):
            agent_display_map[safe_agent_name(hn)] = hn

        moderator = create_moderator(
            model_client=moderator_client,
            topic=topic.title + " - " + topic.description,
            participant_names=all_participant_names,
        )

        characters = [
            create_virtual_character(cid, model_client=character_client, topic=topic.title)
            for cid in character_ids if cid != "moderator"
        ]

        # 创建思想家角色
        thinker_agents = [
            create_thinker_agent(tid, model_client=character_client, topic=topic.title)
            for tid in thinker_ids
        ]

        humans = [create_human_proxy(name) for name in dict.fromkeys(human_names)]

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

        # 创建讨论团队
        team = create_discussion_team(
            moderator=moderator_agent,
            characters=[a for a in ai_agents if a is not moderator_agent],
            humans=human_agents,
            selector_client=moderator_client,
        )

        # 创建安全过滤器
        safety_filter = SafetyFilter(model_client=character_client)

        # 创建 Floor Manager
        floor_manager = FloorManager(
            team=team,
            ai_agents=ai_agents,
            human_agents=human_agents,
            safety_filter=safety_filter,
        )

        # 注册回调，将事件推送到 WebSocket
        async def on_message(source, content, msg_type):
            display_source = agent_display_map.get(source, source)
            await websocket.send_json(
                {
                    "event_type": "message",
                    "data": {"source": display_source, "content": content, "msg_type": msg_type},
                }
            )

        async def on_turn_change(speaker, is_human):
            display_speaker = agent_display_map.get(speaker, speaker)
            await websocket.send_json(
                {
                    "event_type": "turn_change",
                    "data": {"speaker": display_speaker, "is_human": is_human},
                }
            )

        async def on_state_change(old_state, new_state):
            await websocket.send_json(
                {
                    "event_type": "state_change",
                    "data": {"old_state": old_state.value, "new_state": new_state.value},
                }
            )

        async def on_interrupt(interrupter, current_speaker):
            await websocket.send_json(
                {
                    "event_type": "interrupt",
                    "data": {
                        "interrupter": agent_display_map.get(interrupter, interrupter),
                        "interrupted_speaker": agent_display_map.get(current_speaker, current_speaker),
                    },
                }
            )

        floor_manager.on_message(on_message)
        floor_manager.on_turn_change(on_turn_change)
        floor_manager.on_state_change(on_state_change)
        floor_manager.on_interrupt(on_interrupt)

        # 通知客户端讨论开始
        await websocket.send_json(
            {
                "event_type": "system",
                "data": {
                    "message": "讨论开始",
                    "topic": topic.title,
                    "participants": all_participant_names,
                },
            }
        )

        # 运行讨论（异步任务）
        discussion_task = asyncio.create_task(
            _run_discussion(websocket, floor_manager, topic.title + "\n\n" + topic.description)
        )

        # 同时处理来自客户端的人类输入
        async def handle_human_input():
            try:
                while True:
                    data = await websocket.receive_text()
                    msg = json.loads(data)

                    if msg.get("type") == "human_input":
                        speaker = msg.get("speaker", "")
                        content = msg.get("content", "")
                        await floor_manager.submit_human_input(speaker, content)
                    elif msg.get("type") == "interrupt":
                        # 打断请求 - 通知主持人并切换状态
                        speaker = msg.get("speaker", "")
                        await floor_manager.request_interrupt(speaker)
                    elif msg.get("type") == "push_to_talk_start":
                        # PTT 开始 - 标记用户开始发言
                        speaker = msg.get("speaker", "")
                        await floor_manager.handle_push_to_talk_start(speaker)
                    elif msg.get("type") == "push_to_talk_end":
                        # PTT 结束 - 标记用户结束发言
                        speaker = msg.get("speaker", "")
                        await floor_manager.handle_push_to_talk_end(speaker)

            except WebSocketDisconnect:
                logger.info("WebSocket 断开连接")
            except Exception as e:
                logger.error(f"处理人类输入时出错: {e}")

        # 并行运行讨论和人类输入处理
        input_task = asyncio.create_task(handle_human_input())

        try:
            await discussion_task
        except Exception as e:
            logger.error(f"讨论运行出错: {e}")
            await websocket.send_json({"event_type": "error", "data": {"message": str(e)}})

    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: session_id={session_id}")
    except Exception as e:
        logger.error(f"WebSocket 错误: {e}")
        try:
            await websocket.send_json({"event_type": "error", "data": {"message": str(e)}})
        except Exception:
            pass
    finally:
        clear_human_queues()
        logger.info(f"WebSocket 清理完成: session_id={session_id}")


async def _run_discussion(websocket: WebSocket, floor_manager: FloorManager, topic: str):
    """运行讨论并推送事件。"""
    async for event in floor_manager.run(topic):
        # 事件已经通过回调推送，这里只处理特殊事件
        if event["event_type"] == "ended":
            await websocket.send_json(event)
            break
        elif event["event_type"] in ("error", "api_error"):
            await websocket.send_json(event)
            break
        elif event["event_type"] == "stream":
            # 流式文本推送
            await websocket.send_json(event)