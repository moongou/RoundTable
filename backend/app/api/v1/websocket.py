"""WebSocket 端点

实时推送讨论事件到前端，接收人类参与者的输入。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.character_templates import load_all_templates
from app.agents.human_proxy import clear_human_queues, create_human_proxy, put_human_input
from app.agents.moderator import create_moderator
from app.agents.virtual_character import create_virtual_character
from app.core.floor_manager import FloorManager
from app.core.llm_factory import create_character_client, create_moderator_client
from app.core.safety_filter import SafetyFilter
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
        "type": "human_input",
        "speaker": "小明",
        "content": "我觉得..."
    }

    服务端推送格式:
    {
        "event_type": "message" | "turn_change" | "stream" | "system" | "error" | "ended",
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

        # 创建 Agent 实例
        moderator_client = create_moderator_client()
        character_client = create_character_client()

        all_participant_names = [templates["moderator"].name]
        all_participant_names += [templates[cid].name for cid in character_ids]
        all_participant_names += human_names

        moderator = create_moderator(
            model_client=moderator_client,
            topic=topic.title + " - " + topic.description,
            participant_names=all_participant_names,
        )

        characters = [
            create_virtual_character(cid, model_client=character_client, topic=topic.title)
            for cid in character_ids
        ]

        humans = [create_human_proxy(name) for name in human_names]

        # 创建讨论团队
        team = create_discussion_team(
            moderator=moderator,
            characters=characters,
            humans=humans,
            selector_client=moderator_client,
        )

        # 创建安全过滤器
        safety_filter = SafetyFilter(model_client=character_client)

        # 创建 Floor Manager
        floor_manager = FloorManager(
            team=team,
            ai_agents=[moderator] + characters,
            human_agents=humans,
            safety_filter=safety_filter,
        )

        # 注册回调，将事件推送到 WebSocket
        async def on_message(source, content, msg_type):
            await websocket.send_json(
                {
                    "event_type": "message",
                    "data": {"source": source, "content": content, "msg_type": msg_type},
                }
            )

        async def on_turn_change(speaker, is_human):
            await websocket.send_json(
                {
                    "event_type": "turn_change",
                    "data": {"speaker": speaker, "is_human": is_human},
                }
            )

        async def on_state_change(old_state, new_state):
            await websocket.send_json(
                {
                    "event_type": "state_change",
                    "data": {"old_state": old_state.value, "new_state": new_state.value},
                }
            )

        floor_manager.on_message(on_message)
        floor_manager.on_turn_change(on_turn_change)
        floor_manager.on_state_change(on_state_change)

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
        elif event["event_type"] == "error":
            await websocket.send_json(event)
            break
        elif event["event_type"] == "stream":
            # 流式文本推送
            await websocket.send_json(event)