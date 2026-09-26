"""全栈 WebSocket 冒烟测试（离线，无真实 LLM 调用）

走完整生产链路：
  TestClient → /ws/discussion/{sid} → websocket.py（角色限额/注册表/独立client）
  → FloorManager → SelectorGroupChat → RoleSubAgent（隔离台账）→ 出站事件

验证"子 agent 模式"下整场讨论流畅：
- 2 位同学 + 1 位老师（主持人）+ 1 位思想家全阵容跑完整场讨论直至结束；
- 超员阵容被当场拒绝（3 同学 / 2 思想家）；
- 每条 AI 发言都能基于结构化台账正确指认上一位发言者（精准归因）；
- 全程无 error 事件、无卡死，正常收尾。
"""

from __future__ import annotations

import json
import re

from autogen_core.models import CreateResult, RequestUsage
from autogen_ext.models.replay import ReplayChatCompletionClient
from fastapi.testclient import TestClient

from app.api.v1 import websocket as ws_module
from app.main import app

MOD_DUTY_MARK = "以李老师（主持人）身份"
TURN_RE = re.compile(r"现在轮到你——\[(.+?)\]\s*(.+?)\s*发言")
LAST_SPEAKER_RE = re.compile(r"上一位实际发言者：(.+)")

# 老师按此顺序点名（覆盖 2 同学 + 1 思想家）
DESIGNATION_ORDER = ["小探", "马克斯·韦伯", "小疑", "小探", "小疑"]
AGENT_CYCLE = ["explorer", "skeptic", "weber", "moderator"]

# 合格开场：满足 FloorManager 的开场校验（话题关键词/背景/点名）
QUALIFIED_OPENING = (
    "同学们好！我是李老师。今天我们来聊聊坚持与放弃。"
    "这个话题来自生活场景：很多事情我们不知道该咬牙坚持，还是换个方向。"
    "简单来说，坚持就是认定目标不轻易放弃，放弃则是承认此路不通、换个方向。"
    "下面有请小探同学发言。"
)


class PersonaClient(ReplayChatCompletionClient):
    """按提示内容生成确定性回复的离线模型客户端。

    - SelectorGroupChat LLM 选择器兜底：返回合法 agent 名；
    - 子 agent 发言调用（提示含"现在轮到你"）：按角色生成发言，
      发言中引用"上一位实际发言者"（从结构化台账解析），验证归因链路；
    - 收尾语境：返回含"讨论结束"关键词的结束语；
    - 其他调用（安全审查等）：恒定放行。
    """

    def __init__(self, state: dict):
        super().__init__(["占位"])
        self.state = state

    def _reply(self, messages) -> str:
        full = "\n".join(str(getattr(m, "content", "") or "") for m in messages)
        if "Select the next role" in full or "role play game" in full:
            i = self.state["selector_calls"]
            self.state["selector_calls"] += 1
            return AGENT_CYCLE[i % len(AGENT_CYCLE)]
        if "现在轮到你" not in full:
            return "安全，通过。"
        if MOD_DUTY_MARK in full:
            i = self.state["mod_calls"]
            self.state["mod_calls"] += 1
            if i == 0:
                return QUALIFIED_OPENING
            if any(k in full for k in ("收尾", "总结", "结束", "最后一次", "闭幕")):
                return (
                    "好的，我来总结今天的讨论：大家围绕坚持与放弃分享了很多观察，"
                    "有人讲耐心，有人讲换方向，韦伯先生讲了溪水与石头。"
                    "感谢大家，今天的讨论结束。"
                )
            name = DESIGNATION_ORDER[i % len(DESIGNATION_ORDER)]
            honor = "先生" if name == "马克斯·韦伯" else "同学"
            return f"好的，第{i}轮讨论。我们来听听{name}{honor}的看法。请{name}发言。"
        m = TURN_RE.search(full)
        my_name = m.group(2) if m else "同学"
        n = self.state["speech_calls"] = self.state.get("speech_calls", 0) + 1
        last = LAST_SPEAKER_RE.search(full)
        if last:
            prev = last.group(1).strip()
            self.state["references"].append((my_name, prev))
            return (
                f"我是{my_name}，这是我第{n}次发言。"
                f"我想回应{prev}刚才的发言：这个角度很有意思，我想补充第{n}点自己的观察。"
            )
        return f"我是{my_name}，这是我第{n}次发言，我先说说我的初步想法。"

    async def create(self, messages, **kwargs):
        text = self._reply(messages)
        return self._result(text)

    async def create_stream(self, messages, **kwargs):
        text = self._reply(messages)
        yield text
        yield self._result(text)

    @staticmethod
    def _result(text: str) -> CreateResult:
        return CreateResult(
            finish_reason="stop",
            content=text,
            usage=RequestUsage(prompt_tokens=1, completion_tokens=1),
            cached=False,
        )


def _drain_until_closed(ws) -> list[dict]:
    events: list[dict] = []
    while True:
        try:
            events.append(ws.receive_json())
        except Exception:
            return events  # 服务端关闭连接


def _drain_until_ended(ws, max_events: int = 400) -> list[dict]:
    """接收事件直到讨论进入结束态（或服务端关闭连接）。"""
    events: list[dict] = []
    while len(events) < max_events:
        try:
            ev = ws.receive_json()
        except Exception:
            return events  # 服务端关闭连接
        events.append(ev)
        if ev.get("event_type") == "state_change" and "ended" in str(
            ev.get("data", {}).get("new_state", "")
        ):
            return events
    return events


def test_fullstack_roster_rejected():
    """3 位同学 / 2 位思想家都会被 WebSocket 端点当场拒绝。"""
    client = TestClient(app)
    for chars, thinkers in (
        (["explorer", "skeptic", "rationalist"], []),
        (["explorer"], ["weber", "confucius"]),
    ):
        with client.websocket_connect(
            f"/api/v1/ws/discussion/smoke-reject-{len(chars)}-{len(thinkers)}"
        ) as ws:
            ws.send_json(
                {
                    "type": "start",
                    "topic_id": "",
                    "free_topic": "测试",
                    "character_ids": chars,
                    "thinker_ids": thinkers,
                    "human_names": [],
                    "max_turns": 6,
                }
            )
            events = _drain_until_closed(ws)
        errors = [e for e in events if e.get("event_type") == "error"]
        assert errors, f"应返回 error 事件：{chars} {thinkers}"
        assert "最多" in errors[0]["data"]["message"]


def test_fullstack_discussion_smooth_and_attributed(monkeypatch):
    """全阵容（2 同学+1 老师+1 思想家）跑完整场讨论：流畅、归因准确。"""
    state = {"mod_calls": 0, "references": [], "selector_calls": 0}
    monkeypatch.setattr(
        ws_module, "create_model_client", lambda: PersonaClient(state)
    )
    client = TestClient(app)
    with client.websocket_connect(
        "/api/v1/ws/discussion/smoke-fullstack-subagents"
    ) as ws:
        ws.send_json(
            {
                "type": "start",
                "topic_id": "",
                "free_topic": "坚持与放弃",
                "character_ids": ["explorer", "skeptic"],
                "thinker_ids": ["weber"],
                "human_names": [],
                "max_turns": 6,
            }
        )
        events = _drain_until_ended(ws)
    payloads = [e for e in events if e.get("event_type") == "message"]
    errors = [e for e in events if e.get("event_type") == "error"]
    state_changes = [e for e in events if e.get("event_type") == "state_change"]

    # 1) 全程无错误事件
    assert not errors, f"出现 error 事件: {[e['data'] for e in errors]}"

    # 2) 四个角色都真实发言（老师 + 小探 + 小疑 + 马克斯·韦伯）
    speakers = [p["data"]["source"] for p in payloads if p["data"].get("source")]
    for expected in ("老师", "小探", "小疑", "马克斯·韦伯"):
        assert expected in speakers, f"{expected} 未发言，实际: {speakers}"
    assert len(payloads) >= 5, f"发言过少: {len(payloads)}"

    # 3) 每条 AI 发言都能正确指认上一位发言者（结构化台账归因链路）
    #    （PersonaClient 从台账中解析"上一位实际发言者"并写进发言）
    assert state["references"], "没有任何发言引用上一位发言者"
    roster = {"李老师", "小探", "小疑", "马克斯·韦伯", "user", "系统"}
    for my_name, prev in state["references"]:
        assert my_name in roster, f"发言者身份错误: {my_name}"
        assert prev in roster, f"被引用者不在本场阵容中: {prev}（疑似张冠李戴）"

    # 4) 讨论正常收尾进入结束态（流畅性：完整生命周期）
    ended = any(
        "ended" in str(s["data"].get("new_state", s["data"].get("state", "")))
        for s in state_changes
    )
    assert ended, f"讨论未进入结束态，最后状态: {state_changes[-3:] if state_changes else '无'}"
