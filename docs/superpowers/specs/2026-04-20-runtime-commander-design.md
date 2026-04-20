# Runtime Commander 设计（前后端总指挥）

## 为什么需要总指挥
当前会话链路同时依赖：
- 前端 UI 状态机（按钮可用、输入框显示、字幕/语音同步）
- 前端语音队列（TTS 队列、ASR 收尾、计时器）
- 后端会话状态机（选人、等待人类、打断、恢复）

一旦事件乱序或状态被污染（例如分支串流执行），容易出现：
- 用户轮次被跳过后流程停住
- 字幕与语音脱钩（只出字幕不播报）
- 举手获批后未真正进入用户可发言状态

因此建议引入“总指挥（Runtime Commander）”，统一调度“允许做什么、什么时候做、失败怎么回滚”。

## 目标
- 单一真相：任何时刻只存在一个会话阶段（Phase）
- 明确准入：用户发言必须满足 UI+语音+后端三方就绪
- 超时可恢复：任意环节超时后自动推进，不可卡死
- 可观测：每次状态迁移都可记录和追踪

## 总体架构

### 1) Frontend Commander（会话编排器）
职责：
- 管理前端会话阶段 Phase
- 接收 WebSocket 事件并做有序处理（队列化）
- 控制 UI/语音行为（按钮、输入框、TTS/ASR）
- 执行兜底恢复（超时推进、跳过后强制进入下一阶段）

建议阶段：
- idle
- connecting
- ai_speaking
- waiting_human_ready
- human_speaking
- submitting_human_input
- transitioning
- ended
- error_recovering

建议位置：
- frontend/lib/features/session/session_runtime_commander.dart

核心接口建议：
- applyEvent(WsEvent e)
- requestHumanTurn({reason})
- approveHumanInterrupt({approvedBy})
- onAutoSkip({reason})
- onTtsQueueDrained()
- onAsrFinalized({text})
- forceRecover({reason})

### 2) Backend Commander（会话守护器）
职责：
- 包装 FloorManager，提供“阶段看门人”
- 维护会话级超时（human_input_timeout / stalled_timeout）
- 对关键事件打序号（event_seq）避免前端重放污染
- 对人类输入请求设置“可恢复默认动作”（超时自动 skip）

建议位置：
- backend/app/core/runtime_commander.py

核心接口建议：
- on_select_speaker(...)
- on_human_input_requested(...)
- on_human_input_submitted(...)
- on_interrupt_approved(...)
- on_stall_detected(...)

## 关键调度规则

### 规则 A：用户轮次准入（三条件）
仅当以下都满足时，进入 waiting_human_ready/human_speaking：
- 前端：无 TTS 播放、无录音收尾
- 后端：当前 speaker 为人类且 state 允许
- 设备：ASR 可用或文本输入通道可用

不满足时：延后并启动短周期重试（200ms），最长 N 秒后自动进入文本模式。

### 规则 B：跳过后强制推进
用户自动/手动跳过后必须执行：
- 前端立即清理 human flags
- 前端触发“继续调度”信号（drain-next）
- 后端收到 skip 后确认进入 selecting_speaker
- 若 X 秒内无 turn_change，则前端触发 forceRecover，主动请求状态同步

### 规则 C：字幕与语音一致性
- AI 文本最终消息入队 TTS 才允许字幕占位
- stream 文本仅用于当前 owner 且会话序号匹配
- 若语音失败，必须发系统消息并继续播下一条，不可中断泵

### 规则 D：举手获批的一致动作
收到 interrupt approved 后，前端总指挥应：
- 立即标记 human_turn_reserved=true
- 在 TTS 清空后自动调用 activateHumanTurn
- 若超时仍未进入 human_speaking，自动降级为文本输入并提示

## 预热与提前准备（你提到的“预先一定时间做好准备”）
- 连接成功后立即并行预热：
  - TTS prefetch 下一条/下两条
  - ASR warmup（麦克风链路）
  - human input channel ready check
- 当检测到下一轮可能是人类发言时：
  - 预先显示输入框占位
  - 键盘焦点提前挂载
  - PTT 按钮提前置为可见（但禁用）

## 可观测性与调试
建议统一日志格式：
- commander_phase_from -> commander_phase_to
- trigger_event_type + event_seq
- guard_result（pass/defer/recover）
- pending_tts_count / is_recording / is_my_turn

建议前端增加一个轻量调试面板：
- 当前 phase
- 最近 20 条 phase transition
- 最近一次 recover 原因

## 接入计划（最小风险）

### 第 1 步（已完成基础修复）
- 修复事件处理串流执行问题（避免状态污染）

### 第 2 步（推荐下一迭代）
- 新建 Frontend Commander，仅接管：
  - human turn 激活
  - auto-skip 推进
  - interrupt approved -> human ready

### 第 3 步
- Backend Commander 增加 event_seq 与 stall watchdog

### 第 4 步
- 统一前后端 phase telemetry，形成可观测闭环

## 验收标准
- 自动跳过后 1 秒内必进入下一角色思考/发言流程
- 不再出现“仅字幕无语音”长时间挂起
- 举手获批后 1 秒内出现可用讲话入口（语音或文本其一）
- 任意异常在 5 秒内自动 recover 或明确报错并继续
