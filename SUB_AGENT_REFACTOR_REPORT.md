# RoundTable 多角色子 Agent 架构改造总结

## 问题根源

AutoGen `SelectorGroupChat` 中，所有角色的发言以**匿名 user 消息**拼接进每个角色的模型上下文——模型只能"脑补"谁说了什么，这是长期存在角色混乱（张冠李戴、抢台词）的根本原因。

## 解决方案：每个角色一个独立子 Agent

```
RoleDirectory（共享角色注册表：agent名 → [身份] 中文名）
      │
      ├── RoleSubAgent 李老师   ← 私有台账 + 专属client + 隔离上下文
      ├── RoleSubAgent 小探     ← 私有台账 + 专属client + 隔离上下文
      ├── RoleSubAgent 小疑     ← 私有台账 + 专属client + 隔离上下文
      └── RoleSubAgent 思想家   ← 私有台账 + 专属client + 隔离上下文
```

每次轮到某角色发言时，用**一次性全新上下文**调用 LLM：
`人格 system 消息 + 子Agent身份协议 + 结构化发言台账（[身份] 名字：内容，谁说了什么一目了然）+ 轮次指令`

关键机制：
1. **私有结构化台账**：他人发言不再以匿名 user 消息混入自身上下文，而是登记为带明确归属的台账条目；自己的历史发言标注"（就是你）"
2. **隔离生成上下文**：每次发言重新构建一次性上下文，内部模型上下文始终为空（零污染）
3. **流式保留**：复用 AssistantAgent 的 `_call_llm` 通道，`ModelClientStreamingChunkEvent` 照常产出，前端增量渲染/流式 TTS 不受影响
4. **独立模型客户端**：每个角色一个 client 实例，请求完全隔离
5. **归属防护**：prompt 内置"引用前必须核对台账、查不到用中性表述"协议，从结构+规则双重杜绝张冠李戴

## 角色数量限制（2+1+1）

| 角色 | 上限 | 强制点 |
|------|------|--------|
| 同学 | **2** | WebSocket + REST + 前端 UI 三层拦截 |
| 老师（主持人） | 1（系统固定） | — |
| 思想家 | **1** | WebSocket + REST + 前端 UI 三层拦截 |

## 改动文件

| 文件 | 改动 |
|------|------|
| `backend/app/agents/role_sub_agent.py` | **新增** RoleSubAgent + RoleDirectory |
| `backend/app/core/role_limits.py` | **新增** 2+1+1 阵容校验 |
| `backend/app/agents/moderator.py` | 主持人工厂产出子 agent |
| `backend/app/agents/virtual_character.py` | 同学/思想家工厂产出子 agent |
| `backend/app/api/v1/websocket.py` | 新限额 + 注册表 + 每角色独立 client |
| `backend/app/api/v1/sessions.py` | REST 同步限额 |
| `frontend/lib/.../home_screen.dart` | UI 选择上限 2 同学 |
| `frontend/lib/.../immersive_home_screen.dart` | UI 选择上限 2 同学 + 1 思想家 |

## 测试结果

- **单元/集成测试**（`tests/test_role_sub_agent.py`）：8/8 通过
  - 归因精准：他人发言必带 `[老师（主持人）] 李老师：` 标签，无标签裸消息为零
  - 隔离：全部子 agent 内部上下文始终为空
  - 流式：`ModelClientStreamingChunkEvent` 正常产出
  - 去重：自发言回放/消息重投递不产生重复台账
  - 限额：3 同学 / 2 思想家被拒绝
  - 群聊端到端：4 子 agent 真实编排，发言顺序与台账归属全部正确
- **全栈 WebSocket 冒烟**（`tests/test_fullstack_smoke_sub_agents.py`）：2/2 通过——完整生产链路（限额→合格开场→点名链→多轮发言→收尾→ended）在 **1.2 秒**内流畅跑完，每次发言均正确指认上一位发言者，无 error 事件
- **全量回归**：**361/361 全部通过**（8.03s），无任何破坏
