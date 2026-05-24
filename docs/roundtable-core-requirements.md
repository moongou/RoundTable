# RoundTable 圆桌讨论核心要求

本文件用于把当前项目中分散在运行时代码、调度器、主持人提示词、验收测试里的圆桌讨论规则，整理为一份统一的核心要求树。

适用范围：

- 后端讨论调度与状态机
- 主持人 / 虚拟同学 / 思想家 / 真人学生的轮次规则
- 开场、点名、引用、点评、收尾、时间限制、容错恢复、语音播报相关规则

权威层级（冲突时按此顺序解释）：

1. 运行时硬约束：backend/app/core/floor_manager.py
2. 调度器硬约束：backend/app/core/turn_scheduler.py
3. 主持人语言与行为规范：backend/app/agents/moderator.py
4. 验收与告警指标：backend/tests/test_acceptance_guards.py

说明：

- “硬约束”表示系统已经在运行时强制执行。
- “软指标”表示系统会诊断、告警或在提示词中强调，但未必在每一轮都由代码强制卡死。
- 本文件以当前实现为准，不以历史设想或注释中的未落地目标为准。

## 0. 要求树

```text
RoundTable 圆桌讨论
|- 1. 总体控场与状态机
|  |- 1.1 单一发言权
|  |- 1.2 真人学生优先级
|  |- 1.3 暂停 / 恢复 / 容错恢复
|- 2. 开场要求
|  |- 2.1 老师必须先开场
|  |- 2.2 开场必须交代来源、定义、背景/争议
|  |- 2.3 首轮禁止互引
|  |- 2.4 陌生概念必须解释
|- 3. 发言调度与点名
|  |- 3.1 明确点名优先
|  |- 3.2 不连续发言
|  |- 3.3 优先未发言者
|  |- 3.4 真人发言预算与分布
|  |- 3.5 思想家必须被邀请
|- 4. 真人学生规则
|  |- 4.1 首次发言时机
|  |- 4.2 麦克风授权条件
|  |- 4.3 发言后的老师点评比例
|  |- 4.4 跳过、沉默、举手、冷却
|- 5. 非真人角色规则
|  |- 5.1 老师简短主持
|  |- 5.2 虚拟同学 / 思想家单轮时长限制
|  |- 5.3 老师不垄断话轮
|- 6. 引用、归因、点评规则
|  |- 6.1 不得张冠李戴
|  |- 6.2 只引用短片段，不长复述
|  |- 6.3 未发言/已跳过者不可被引用
|  |- 6.4 不确定时用匿名归纳
|- 7. 收尾规则
|  |- 7.1 收尾前置条件
|  |- 7.2 两次收尾征询
|  |- 7.3 收尾必须含“再见”并结束讨论
|- 8. 语音与流式展示规则
|  |- 8.1 流式 TTS 切句
|  |- 8.2 尾段补播
|  |- 8.3 暂停后不允许错位续播
|- 9. 诊断与软指标
   |- 9.1 老师发言占比
   |- 9.2 老师点名占比
   |- 9.3 真人发言后的老师接话率
   |- 9.4 收尾闸门 blocker 输出
```

## 1. 总体控场与状态机

### 1.1 单一发言权

规则：

- 任意时刻只允许一名参与者处于有效发言位。
- FloorManager 统一协调 AI 文本生成、人类输入等待、TTS/ASR、WebSocket 事件，不允许各链路绕开总调度直接推进轮次。
- 人类发言环节为最高优先级，任何并行任务都不能抢占真人发言。

强度：硬约束。

来源：

- FloorManager 总说明与 FloorState 状态机
- _pause_team_for_human_input
- _resume_team_after_human_input
- _sync_ai_turn_without_selection_event

### 1.2 状态机要求

状态集合：

- init
- moderator_opening
- selecting_speaker
- ai_speaking
- human_turn_waiting
- human_speaking
- interrupted
- closing
- ended

规则：

- 新讨论必须从 moderator_opening 开始。
- 进入 human_turn_waiting / human_speaking 时，团队流需要暂停或让出控制权。
- ended 为唯一合法终止状态。

强度：硬约束。

来源：

- FloorState
- run
- _set_state

### 1.3 暂停 / 恢复 / 容错恢复

规则：

- 暂停时 watchdog 停止检查，恢复时重置进度时间戳。
- human wait 阶段若出现 AI 乱入，应压制非主持人 AI 发言。
- 若流式讨论停滞，系统必须使用 fallback speaker / restart 来恢复，而不是无限等待。
- 失效的人类请求（stale human request）不能只记录 warning，必须清空旧状态并重启 team stream。

强度：

- AI 压制、stale request 恢复、stream restart：硬约束
- 具体恢复路径与提示文字：实现策略

来源：

- _should_suppress_ai_while_human_waiting
- _watchdog_loop
- _select_fallback_and_designate
- _recover_from_stale_human_request

## 2. 开场要求

### 2.1 老师必须先开场

规则：

- 每个新话题第一位发言者必须是老师。
- 如果选择器首轮错误地给到非老师，调度器要强制改回老师。

强度：硬约束。

来源：

- turn_scheduler.moderator_led_selector
- FloorManager._is_discussion_opening_message

### 2.2 开场内容必须完整

规则：

- 老师第一轮开场必须覆盖：话题来源、概念/问题定义、背景或争议点中的核心信息。
- 如果开场不充分，系统要强制替换或补齐基线开场内容。
- 不能跳过话题介绍直接点名。

强度：硬约束。

来源：

- _moderator_opening_has_context
- _build_moderator_opening_baseline
- _validate_moderator_opening
- _ensure_moderator_opening_context

### 2.3 首轮禁止互引

规则：

- 老师开场不能引用任何人。
- 首轮阶段（老师开场 + 每位同学首次发言）禁止“上一位同学说得对”“刚才某某说得好”这类互引表达。

强度：

- 老师开场不引用：硬约束
- 首轮阶段互引禁止：调度器约束 + prompt 约束

来源：

- turn_scheduler selector prompt 0.1
- _sanitize_all_references
- moderator prompt 首轮开场规则

### 2.4 陌生概念解释

规则：

- 若话题包含低碳、碳足迹、算法公平等小学生陌生概念，开场后必须先用“是什么 + 生活例子”做 1-2 句解释，再点名。

强度：Prompt 规则，部分话题通过 opening hint 注入。

来源：

- moderator prompt 概念解释开场规则
- _build_topic_opening_hint

## 3. 发言调度与点名

### 3.1 明确点名优先

规则：

- 老师 / 用户 / 同学若明确点名下一位，应优先执行该指定。
- “请 X 发言”“X，你怎么看”优于默认选择。
- 解析点名时只认邀请动词后第一个受邀者，不把后文提到的人当作被点名者。

强度：硬约束。

来源：

- parse_speaker_designation
- get_designated_speaker / set_designated_speaker
- moderator_led_selector 优先级 1 和 3

### 3.2 不连续发言

规则：

- 除老师外，不能让同一个人连续发言两次。
- 最近发言者应被排除，除非场面恢复需要老师介入。

强度：调度器硬约束。

来源：

- _exclude_recent_speakers
- moderator_led_selector
- _is_repeated_non_moderator_turn

### 3.3 优先未发言者

规则：

- 在一般情况下，优先安排尚未发言的虚拟同学、思想家、真人学生。
- 思想家和虚拟同学的覆盖率，在收尾前必须补齐。

强度：

- 覆盖优先：调度器硬约束
- 未覆盖禁止收尾：运行时硬约束

来源：

- _smart_fallback_speaker
- _missing_required_ai_speakers
- _closing_gate_status

### 3.4 老师主导点名

规则：

- 老师承担主要点名职责，目标约 80% 点名来自老师。
- 同学可以偶尔点名，但不能频繁替代老师调度。

强度：软指标 + prompt 规范。

来源：

- moderator prompt 讨论秩序规范
- `_moderator_nomination_count` / `_peer_nomination_count`
- discussion_metrics

### 3.5 泛化点名的兜底

规则：

- 如果老师只说“请其他同学说说”“大家怎么看”，系统应优先转给合适的未覆盖非真人角色，而不是让流停住。

强度：硬约束。

来源：

- _generic_moderator_handoff_fallback
- _select_fallback_and_designate

## 4. 真人学生规则

### 4.1 真人学生是讨论核心

规则：

- 真人学生是讨论核心，讨论要围绕其观点形成网络。
- 常规讨论中，真人学生总发言目标为 5-7 次，默认目标约 6 次。
- 当前运行时最低硬门槛为至少 5 次；用户积极举手时可放宽上限。

强度：

- 真人最低 5 次：硬约束
- 5-7 次、30%-50% 重要性：Prompt 规则 + 调度目标

来源：

- _HUMAN_TURN_MIN_TARGET
- turn_scheduler selector prompt 1 / 3 / 3.2 / 3.3
- moderator prompt 核心职责

### 4.2 首次真人发言时机

规则：

- 老师开场后，通常先让 1-2 位非真人参与者铺垫，再安排真人第一次发言。
- 真人学生必须在开场后的前 2 分钟内获得第一次发言机会。

强度：

- 前 2 分钟 / 开场后 warmup：硬约束 + 调度器规则

来源：

- _FIRST_HUMAN_MIN_WARMUP_TURNS
- _FIRST_HUMAN_MAX_WAIT_SEC
- _should_force_first_human_invite
- turn_scheduler selector prompt 3.1

### 4.3 真人麦克风授权条件

规则：

- 真人学生只能在以下三种情况下获得发言权：
  - 老师明确点名真人学生
  - 某位同学或思想家明确把话题交给真人学生
  - 真人学生主动举手并获准插话
- 除举手获准外，禁止无指向地把下一轮直接给真人学生。

强度：硬约束。

来源：

- _AUTHORIZED_HUMAN_REQUEST_REASONS
- _is_authorized_human_request_reason
- turn_scheduler selector prompt 3.4
- moderator prompt 真人麦克风授权规则

### 4.4 真人发言后的老师点评比例

规则：

- 真人发言后，至少一半轮次要由老师立即给出支持式点评。
- 其余轮次允许虚拟同学先直接回应，但老师仍负责串联。

强度：

- 当前主要作为调度策略和诊断目标，不是逐轮硬卡死。

来源：

- turn_scheduler 优先级 2
- moderator prompt 用户发言处理规范
- _POST_HUMAN_MODERATOR_FEEDBACK_TARGET
- discussion_metrics

### 4.5 真人发言冷却、跳过与恢复

规则：

- 真人发言后通常至少间隔 1-2 位非真人，再重新安排。
- 最近发生“跳过”后，系统不应立刻再次强行邀请真人。
- 空输入或“跳过”指令应按跳过处理，并恢复讨论流。
- 若真人请求已失效，不允许保留旧请求卡住系统。

强度：硬约束。

来源：

- _non_human_turns_since_last_human
- _recent_human_skip_pending
- submit_human_input
- _recover_from_stale_human_request

## 5. 非真人角色规则

### 5.1 老师主持必须简短

规则：

- 老师常规主持默认 1 句。
- 点名时最多 2 句。
- 收尾最多 2 句。
- 开场最多 5 句。

强度：硬约束。

来源：

- _MODERATOR_REGULAR_SENTENCE_LIMIT
- _MODERATOR_INVITE_SENTENCE_LIMIT
- _MODERATOR_CLOSING_SENTENCE_LIMIT
- _MODERATOR_OPENING_SENTENCE_LIMIT
- _enforce_moderator_brevity

### 5.2 虚拟同学 / 思想家单轮时长限制

规则：

- 非真人角色每轮通常不超过 3 句。
- 非真人角色每轮通常不超过约 200 个中文字符，约等价于 35-40 秒 TTS。

强度：硬约束。

来源：

- _NON_HUMAN_AI_MAX_SENTENCES
- _NON_HUMAN_AI_MAX_CHARS
- _enforce_non_human_ai_duration

### 5.3 老师不能垄断话轮

规则：

- 老师发言总占比目标约为 30%-40%。
- 超过 0.45 时应告警，并在调度上优先让非老师继续。

强度：软指标 + 调度倾向。

来源：

- _MODERATOR_TURN_SHARE_TARGET_MIN / MAX / WARN_OVER
- discussion_metrics
- turn_scheduler 中“当老师发言占比过高时优先让非老师继续”

## 6. 引用、归因、点评规则

### 6.1 不得张冠李戴

规则：

- 引用或表扬必须对应真实发言者。
- 不能把 A 的观点说成 B 的。
- 曾经被点名但尚未真正开口的人，不得被当成“刚才说话的人”。

强度：硬约束 + prompt 高优先级规则。

来源：

- _sanitize_reference_attribution
- _sanitize_all_references
- moderator prompt 引用准确性规则

### 6.2 只引用短片段，不长复述

规则：

- 引用他人观点时，只允许摘一个关键词或不超过约 12 个字的短句。
- 不应连续长段复述上一位发言。

强度：Prompt 规则 + 部分运行时清洗。

来源：

- moderator prompt 观点优先规则
- _sanitize_grounded_quote_attribution
- _sanitize_moderator_quote_whitelist

### 6.3 未发言 / 已跳过者不可被引用

规则：

- 若系统显示某人已跳过、输入超时、未输入有效内容，则后续任何点评都不能说“你刚才说……”。
- 对跳过者只能做温和鼓励，不能编造观点。

强度：Prompt 规则 + 运行时归因清洗。

来源：

- moderator prompt 跳过/未发言规则
- _sanitize_reference_attribution

### 6.4 不确定时用匿名归纳

规则：

- 如果不完全确定是谁说的，应该改成“刚才有同学提到……”，而不是强行点名。

强度：Prompt 规则，部分由清洗函数兜底。

来源：

- moderator prompt 引用准确性规则
- _sanitize_ungrounded_neutral_quote_claims

### 6.5 称呼与身份必须正确

规则：

- 老师是老师，不是同学。
- 思想家是“先生”，不是同学。
- 真人学生是独立参与者，不能冒充、替说、挂名。
- 老师必须用第一人称“我”，不能第三人称自称“李老师觉得……”。

强度：Prompt 规则 + 部分运行时清洗。

来源：

- moderator prompt 身份认知与自我意识
- _format_display_vocative
- _sanitize_unknown_student_vocatives

### 6.6 点评必须“先接住，再推进”

规则：

- 对真人学生发言，先接住对方核心意思，再做一句总结或追问。
- 如果内容弱、空泛或离题，不允许硬夸“很深刻”，而是先接住再温和拉回主题。

强度：Prompt 规则。

来源：

- moderator prompt 真人学生发言处理规范

## 7. 点名格式规则

### 7.1 点名格式必须简洁明确

规则：

- 推荐格式：
  - 请 X 同学发言
  - X 同学，你怎么看？
  - 请 X 先生发言
- 不要在邀请动词和名字之间塞太多修饰语。
- 若无法明确判断下一位是谁，应改用开放式征询，不要乱猜名字。

强度：Prompt 规则 + 解析器兼容规则。

来源：

- moderator prompt 点名格式规范
- parse_speaker_designation

### 7.2 点名后一锤定音

规则：

- 一旦明确点名 X，下一轮必须交给 X。
- 点名真人学生的句尾，不允许再追加第二个点名对象。

强度：硬约束。

来源：

- parse_speaker_designation
- _enforce_expected_ai_speaker
- moderator prompt 点名格式规范

### 7.3 真人被同学点到时的准入条件

规则：

- 若同学 / 思想家点到真人学生，只有在真人符合插入条件时才直接交麦。
- 否则由老师接管，先做正式邀请。

强度：硬约束。

来源：

- _should_allow_participant_human_handoff
- _moderator_roleplay_target

## 8. 思想家参与规则

规则：

- 思想家是特邀嘉宾，必须至少发言 1 次。
- 如果热身后思想家还未发言，调度器应优先把老师拉回邀请位，由老师显式邀请思想家。
- 收尾前若思想家未发言，绝不能结束讨论。

强度：

- 邀请与收尾前必须发言：硬约束

来源：

- turn_scheduler 中 thinker gate
- moderator prompt 思想家嘉宾参与规则

## 9. 收尾规则

### 9.1 收尾前置条件

规则：

- 在以下条件全部满足前，禁止进入真正收尾：
  - 真人学生本场发言至少 5 次
  - 思想家至少发言 1 次
  - 每位虚拟同学 / 思想家至少发言 1 次
  - 已形成相对完整的多轮互动

强度：

- 当前运行时硬 gate 已覆盖：真人最低预算 + 虚拟/思想家覆盖率
- “完整多轮互动”更多由调度器与 prompt 共同约束

来源：

- _closing_gate_status
- _should_block_moderator_final_closing
- turn_scheduler 收尾门槛注释
- moderator prompt 收尾流程规范

### 9.2 两次收尾征询

规则：

- 第一次收尾提示词：收尾前，我想先问问大家，还有没有什么想说的或者想要分享的……
- 第二次收尾提示词：最后我再问一次，还有没有什么想说的或者想要分享的……
- 第二次收尾前，至少要有 2 条非主持人实质发言，防止“秒收尾”。

强度：调度器硬约束 + prompt 规则。

来源：

- FIRST_CLOSING_PROMPT_MARKER
- SECOND_CLOSING_PROMPT_MARKER
- turn_scheduler 接近收尾逻辑

### 9.3 老师提前收尾时必须被拦回

规则：

- 如果老师在门槛未达成时说出结束语，系统必须把收尾改写为继续邀请未完成参与者发言，而不是照单结束。
- 若缺真人，优先转成真人 handoff；若真人已够但还缺未发言 AI / 思想家，则转成 targeted handoff。

强度：硬约束。

来源：

- _is_moderator_final_closing
- _should_block_moderator_final_closing
- _build_first_human_handoff_text
- _build_targeted_handoff_text

### 9.4 最终结束语格式

规则：

- 真正结束时，主持人最后一句必须包含“再见”。
- 最终讨论结束事件必须进入 ended 状态。

强度：硬约束。

来源：

- _ensure_moderator_explicit_goodbye
- run finally 阶段 forced goodbye

## 10. 语音、流式展示与字幕规则

### 10.1 流式 TTS 切句

规则：

- 流式 TTS 以句号、问号、叹号、分号为句末切分。
- 闭合引号和括号属于句尾的一部分。

强度：硬约束。

来源：

- _drain_complete_stream_sentences
- _STREAM_SENTENCE_ENDINGS
- _STREAM_CLOSING_CHARS

### 10.2 流式已播前缀与尾段补播

规则：

- 如果前半段已通过 tts_segments 播放，最终 message 只能补播剩余尾段。
- 未真正播出的流式句子不能被错误标记为“已播”。

强度：硬约束。

来源：

- _mark_streaming_segments_emitted
- _pop_streaming_message_tail
- 流式 TTS 尾段相关验收测试

### 10.3 暂停时不允许错位续播

规则：

- 暂停期间新增 TTS 必须丢弃。
- 恢复后只允许从当前片段起点重读，不能继续错位排队播放。

强度：前后端共同硬约束。

来源：

- backend websocket pause guard
- frontend immersive_session_screen TTS queue / pause logic

## 11. 容错恢复规则

### 11.1 真人等待阶段压制 AI 乱入

规则：

- 若系统正处于 human_turn_waiting，非主持人 AI 流和完整消息都应被丢弃，避免打断真人回合。

强度：硬约束。

来源：

- _should_suppress_ai_while_human_waiting
- _dropped_ai_stream_while_human_waiting
- _dropped_ai_message_while_human_waiting

### 11.2 队流停滞时必须恢复

规则：

- 如果 team stream 长时间没有新事件，watchdog 必须重启 continuation。
- 必要时可使用 smart fallback 指定下一位继续，避免无限卡死。

强度：硬约束。

来源：

- _watchdog_loop
- _general_stall_timeout_sec
- _select_fallback_and_designate

### 11.3 真人请求失效时必须清空旧状态

规则：

- 若旧 team stream 抛出过期的 human_input_requested，系统必须清空旧 request id、speaker、pending reason，并重启 stream。

强度：硬约束。

来源：

- _recover_from_stale_human_request

## 12. 软指标与诊断输出

### 12.1 老师发言占比

要求：

- 目标区间 30%-40%
- 超过 0.45 告警

强度：软指标。

来源：

- discussion_metrics

### 12.2 老师点名占比

要求：

- 目标约 0.80
- 低于 0.65 告警

强度：软指标。

来源：

- discussion_metrics

### 12.3 真人发言后的老师接话率

要求：

- 目标至少 0.50

强度：软指标。

来源：

- discussion_metrics

### 12.4 收尾闸门 blocker 可观测性

要求：

- 会后 summary 必须包含 closing_gate，至少输出：
  - human_turn_count
  - human_turn_target
  - remaining_human_turns
  - missing_ai_display_names
  - blockers

强度：硬要求（诊断可观测性）。

来源：

- diagnostics
- devpanel 历史详情摘要条

## 13. 当前落地现状总结

已经硬化到运行时的部分：

- 老师先开场
- 开场内容补齐
- 真人最低发言预算
- 虚拟角色 / 思想家覆盖率
- 非真人单轮长度限制
- 人类发言授权原因校验
- 提前收尾拦截
- 流停滞 fallback / restart
- stale human request 恢复
- 流式 TTS 尾段补播

目前仍主要依赖 Prompt / 调度倾向的部分：

- 真人学生在整场讨论中的“重要性 30%-50%”
- 点评的情感自然度、趣味性、故事感
- 不同老师点评风格的细腻程度

## 14. 严谨性与技术硬化补充

本节用于把最新一轮“可程序化、可验证、可观测”的硬化要求补充到核心规则中，并明确区分三类状态：

- 已实现：已经进入运行时守卫、解析器或测试
- 文档硬化：已成为正式规则，但当前尚未全部落入代码
- 待实现：已确认实施方向，下一轮应转成 enum、guard clause、计数器或诊断项

### 14.1 状态转换矩阵与守护条件

正常路径白名单：

- init -> moderator_opening
- moderator_opening -> selecting_speaker
- selecting_speaker -> ai_speaking | human_turn_waiting | closing
- ai_speaking -> selecting_speaker | closing
- human_turn_waiting -> human_speaking | interrupted | selecting_speaker
- human_speaking -> human_turn_waiting | selecting_speaker | closing
- interrupted -> selecting_speaker
- closing -> ended

恢复例外：

- 为了保证 owner loop 在异常、超时、强制结束时能够落盘和收束，运行时允许受控的 terminalization 跳转到 ended；这属于异常/收束例外，不属于常规调度路径。
- 插话获准时，运行时会先进入 interrupted，再显式回到 selecting_speaker，然后转入 human_turn_waiting；不允许从 human_turn_waiting 直接切到 ai_speaking。

实施状态：

- 已实现：FloorManager 已新增状态迁移白名单守卫。
- 已实现：selecting_speaker 已从隐式阶段改为显式状态。
- 已实现：closing 已接入主持人最终收尾与 forced goodbye 路径。
- 已实现：新增聚焦测试覆盖非法跳转拒绝与 selecting 显式化。

### 14.2 状态驻留时间上限

规则：

- moderator_opening 最大驻留 60 秒。
- selecting_speaker 最大驻留 15 秒。
- ai_speaking 最大驻留 45 秒。
- human_turn_waiting 继续沿用现有真人超时与恢复逻辑。

技术要求：

- 该上限应以“状态进入时间戳 + watchdog 检查”实现，而不是仅靠单轮文本长度间接约束。
- 超时后的恢复动作应与状态有关：selecting_speaker 优先 fallback designation，ai_speaking / moderator_opening 优先 restart continuation。

实施状态：

- 已实现：FloorManager watchdog 已新增 state dwell timeout 常量、状态进入时间和恢复分支。
- 已实现：状态超时现在会写入 diagnostics.state_timeout_counts，供 summary/devpanel 查看。

### 14.3 点名冲突裁决与泛化点名

冲突裁决顺序：

1. 本轮最新解析出的明确点名。
2. 上一轮尚未消费的遗留 designated speaker，仅在本轮无新点名时生效。
3. 若仍无明确目标，交由 fallback selector / smart fallback 处理。

泛化点名定义：

- 视为泛化：大家怎么看、其他同学呢、还有谁想说、谁来说说、请其他同学说说、别的同学怎么看、还有哪位同学、谁还想说。
- 不视为泛化：请张三同学发言、张三，你怎么看、阿德勒先生您先说说。

技术要求：

- set_designated_speaker 在写入新目标前必须覆盖旧目标，不能保留并列点名状态。
- 泛化点名只能触发非阻塞 fallback，不应被误判成明确指派。

实施状态：

- 已实现：designated speaker 采用 latest assignment wins，并记录覆盖日志。
- 已实现：已抽出 is_generic_nomination 与 GENERIC_NOMINATION_PATTERNS。
- 已实现：FloorManager 的老师泛化交接 fallback 已改用统一泛化判定。
- 已实现：新增测试覆盖“新点名覆盖旧点名”和“泛化点名不等于明确点名”。

### 14.4 真人学生规则的边界澄清

规则：

- “前 2 分钟获得第一次发言机会”的计时起点应为 moderator_opening 完成，而不是 topic 创建时刻。
- 计时终点应为第一次进入 human_turn_waiting 或 human_speaking。
- 若 120 秒内未达成，下一轮 selecting_speaker 必须拒绝所有非真人候选人，直到真人获得首次机会。

真人响应降级阶梯：

1. 正常点名并等待输入。
2. 用户跳过或超时，计入 skipped_count。
3. 连续跳过 2 次，最低门槛降为 4。
4. 连续跳过 3 次，最低门槛降为 3，并在 summary 标注“真人参与不足”。
5. 若总举手次数为 0 且连续跳过 3 次，允许不达标收尾，但必须记录异常来源。

实施状态：

- 文档硬化：首次发言的起止定义已明确。
- 已实现：human budget fallback、skipped_count、timeout_count、hand_raise_count 已进入运行时。
- 已实现：连续跳过 2 次后最低目标降为 4，连续跳过 3 次后降为 3。
- 已实现：0 举手且累计跳过 3 次时，closing gate 会放开真人预算阻塞并标记 participation_insufficient。
- 待实现：首次发言计时起点改为 moderator_opening 完成，仍需单独代码化。

### 14.5 引用归因的技术可验证性

规则：

- “已真正开口”不再只靠是否出现在发言历史中判断，而应绑定结构化 utterance status。
- 推荐状态枚举：nominated_only、skipped、timed_out、spoke_with_content、spoke_empty。
- 短引用是硬约束：单次引用不超过 12 字，一轮发言内引用不超过 3 处，引用总字数不超过本轮字数的 30%。

技术要求：

- _sanitize_reference_attribution 应以 utterance status 过滤可引用对象。
- 生成完成后应增加 quote validator，而不是只依赖 prompt。

实施状态：

- 已实现：FloorManager 为参与者维护结构化 utterance status，并在 diagnostics 中导出 `speaker_utterance_statuses`。
- 已实现：`_sanitize_reference_attribution` 先按 utterance status + 实质发言资格过滤引用对象；即使尚无 `last_display`，也会先清理未真实开口者的命名引用。
- 已实现：完整 AI 消息在引用清洗末尾统一执行短引用校验，硬性限制单条引用 <= 12 字、单轮 <= 3 处、引用字数占比 <= 30%。
- 已验证：验收测试覆盖 nominated_only / skipped / timed_out / spoke_empty / spoke_with_content 以及短引用长度、数量、占比约束。

### 14.6 收尾逻辑的严格化

规则：

- 第二次收尾前的“有效响应”必须满足：字数不少于 15 字、不是纯附和语、跳过/超时不计入、同一人连续两次只计 1 次。
- 收尾被拦截后，状态必须从 closing 回退到 selecting_speaker，而不是直接 ended。
- closing_attempt_count 每拦截一次加 1；达到 3 次仍 blocked 时，允许带异常标签结束，防止无限循环。

技术要求：

- closing_gate 需要从“是否 ready”扩展到“已拦截次数、最后一次 blocker、是否触发异常收尾”。
- _build_targeted_handoff_text 应成为 closing blocked 后的唯一回退出口之一。

实施状态：

- 已实现：提前收尾会被拦回并改写为继续邀请。
- 已实现：closing_attempt_count、attempt limit=3、forced_ready_due_to_attempt_limit 已进入 closing_gate。
- 已实现：非主持人实质新进展会重置 blocked closing attempt 计数，避免旧拦截次数污染后续轮次。
- 文档硬化：有效响应定义已纳入正式要求，但字数/语义阈值仍待下一轮代码化。

### 14.7 思想家角色的定位细化

规则：

- 引用思想家时必须带“先生”敬称。
- 思想家的观点可以被追问、展开、借用，但不应被直接说成“你说得不对”。
- 思想家定位是引路者，不是普通辩论对手。

技术要求：

- moderator prompt 与引用清洗应共享同一套思想家称呼和否定约束。
- 若出现对思想家的直接否定句式，应优先改写为追问或补充视角。

实施状态：

- 文档硬化：角色定位已明确。
- 待实现：反驳式句型改写与思想家专用引用守卫仍需代码化。

### 14.8 诊断指标与跨规则一致性

新增可计算指标：

- 完整多轮互动：至少 3 个不同非真人发言者、最近 5 轮中至少 1 轮引用前文、总轮数不少于 12、真人次数达标。
- 话题偏离度：连续 3 轮未出现核心主题关键词则触发“可能偏题”。

跨规则冲突修复：

- 真人有实质发言后，老师点评上限可临时放宽到 3 句，以兼容“先接住再推进”和点名职责。
- 当唯一未覆盖者是真人时，覆盖率优先级高于真人冷却规则，冷却必须让步。

实施状态：

- 文档硬化：指标口径和优先级冲突已明确。
- 待实现：interaction completeness、topic drift、老师 3 句特例、coverage-over-cooldown 让步逻辑仍需落入代码与 summary 诊断。
- “完整多轮互动”的语义判断

因此，项目后续若要继续强化稳定性，应优先把以下几类规则继续从 Prompt 升级为硬 gate：

- 第二次收尾前的“有效回应数量”
- 真人发言分布的均匀性
- 老师点评质量是否真的做到“先接住再推进”
- 关键引用是否可追溯到最近真实发言片段
