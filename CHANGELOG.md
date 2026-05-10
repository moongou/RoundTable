# Changelog

<!-- markdownlint-disable MD024 MD022 MD032 -->

本项目所有推送到 `origin/main` 的版本都在此记录新增功能、Bug 修复和稳定性改进。
每次推送版本号最后一位 +1（patch）。

---

## v1.0.13 - 2026-05-10

### Bug 修复

- 修复真人在超时提醒后重新提交内容时，消息已写入历史但 owner loop 未稳定接管的问题：现在人类提交、手动跳过和“结束讨论”统一只发出 stream restart 信号，由 owner loop 自己完成取消与恢复，降低 `task_done()` / `aclose()` 竞态。
- 修复首页从沉浸式讨论返回后，上一轮已选角色和思想家残留到下一轮的问题；返回首页时会清空这两组选中状态，避免连续 live 轮次串场。
- 修复主持人台词中的表层污染：会去掉 `老师**：` 一类 markdown/角色标签残留，改写 `谢谢老师分享` 这类老师自指感谢，并压缩 `有有有` 这类重复填词。
- 模型配置页支持手动输入模型名，并以用户手填值优先于测试连接返回的下拉选择，降低新模型或私有模型接入门槛。
- 修复 ECS 快速部署后的目录残留问题：`deploy.sh` 现在默认把远端顶层旧源码树、旧前端构建和常见 IDE/cache 杂项清理掉，`deploy_aliyun.sh` 也可通过 `CLEAN_STALE_TOP_LEVEL=true` 单独复用这套清理逻辑。

### 验证

- 后端守卫与清洗测试：`backend/.venv/bin/python -m pytest backend/tests/test_acceptance_guards.py -k 'test_submit_human_input_requests_restart_without_external_wait_cancel or test_submit_human_skip_clears_stale_designation or test_submit_human_input_requests_owner_loop_recovery_when_no_active_wait_task or test_sanitize_all_references_strips_moderator_surface_noise or test_sanitize_all_references_moderator_quote_whitelist_keeps_traceable_quote'`。
- 前端静态检查：`flutter analyze --no-pub lib/features/home/immersive_home_screen.dart`（仅剩既有 info 级提示，无新增 error/warning）。
- Flutter Web 已重建并同步到 `backend/static`。
- ECS 部署脚本语法校验：`bash -n deploy/deploy.sh && bash -n deploy/deploy_aliyun.sh`。
- 使用 `CLEAN_STALE_TOP_LEVEL=true SKIP_WEB_BUILD=true ./deploy/deploy.sh` 实际部署验证，线上日志显示自动清理钩子已触发，且 `roundtable`、`roundtable-devpanel`、`nginx` 保持 `active`。

## v1.0.12 - 2026-05-04

### Bug 修复

- 修复主持人开场过短且过早把话筒交给真人的问题：后端现在会把老师首轮发言强制补成带话题背景的完整开场，并要求至少先经过 1 位非真人暖场后，才允许第一次明确点名真人发言。
- 修复“未明确点名就自动开麦”与 ASR 空结果/错误后反复亮麦的问题：前端把自动开麦改为“麦克风已准备好”的待命态，用户需自己开始说话，不再被系统反复自动拉起录音。
- 修复开场字幕/语音被截断的问题：当流式 TTS 只先播出前半段时，后端现在会继续通过 websocket 透传消息尾段 `tts_text`，前端和历史记录都能补齐剩余老师台词，不再出现“只有首句、后半段被吞”的静默跳句。
- 修复 `siliconflow_tts` / CosyVoice2 的默认音色回退错误：OpenAI 兼容 TTS provider 在未显式传入音色、或误传 `alloy` 时，会优先使用 provider 自身配置的默认音色，避免启动预热和运行时合成再向上游发出无效 voice。
- 调整首页“后台服务”入口：本地开发环境仍保留入口，但跳转改为 `localhost:8888`；云端产品页默认隐藏该入口，避免普通用户看到运维入口。

### 验证

- 后端守卫与链路测试：`backend/.venv/bin/python -m pytest backend/tests/test_acceptance_guards.py -k 'test_floor_manager_rewrites_short_opening_into_topic_context or test_floor_manager_blocks_opening_human_handoff_before_warmup or test_floor_manager_forces_first_human_invitation_after_two_warmup_turns or test_floor_manager_message_callback_receives_streaming_tail_tts'`。
- TTS 默认音色回退测试：`backend/.venv/bin/python -m pytest backend/tests/test_voice_factory.py -k 'test_create_siliconflow_tts_provider_uses_openai_compatible_client or test_openai_tts_provider_uses_configured_default_voice_when_voice_is_omitted or test_openai_tts_provider_replaces_alloy_with_provider_default_voice'`。
- 前端测试：`flutter test --no-pub test/immersive_session_screen_test.dart test/immersive_home_screen_test.dart`。
- Flutter Web 已重建并同步到 `backend/static`。

## v1.0.11 - 2026-05-04

### Bug 修复

- 修复“暂停后继续只重读当前字幕并卡住”的核心链路：恢复时后端会重同步挂起的真人输入请求，前端可正确恢复真人回合与麦克风准备状态。
- 修复停滞恢复仅提示“已指定真人继续发言”但未真正触发真人回合的问题：当 fallback 指向真人时，立即发出 `human_input_requested`，确保讨论按正常流程续行。

### 规则加固

- 规则 3：新增“真人发言结束后需再经过 1 个非真人轮次才能再次举手”的前端冷却守卫，避免连续抢麦。
- 规则 5：非真人单轮发言长度上限从 160 字提升到 200 字，更贴近 40 秒以内播报窗口。
- 规则 7/11/13/14：新增后端 `discussion_metrics` 指标，持续跟踪老师发言占比、老师点名占比、真人发言后老师即时点评率、未发言虚拟角色等，并在偏离目标时输出告警。

### 验证

- 后端全量测试：`backend/.venv/bin/python -m pytest backend/tests -q --timeout=30 -p no:cacheprovider`（202 passed）。
- 前端会话测试：`flutter test --no-pub test/immersive_session_screen_test.dart`（38 passed）。
- Flutter Web 已重建并同步到 `backend/static`。

## v1.0.10 - 2026-05-03

### Bug 修复

- 修复云端 `/ops/` 开发面板的接口路径前缀问题：面板请求统一兼容 `/ops` 反向代理，解决“启动后端按钮无响应”和部分日志/历史接口取数失败。
- 修复开发面板统计自动鉴权链路：新增管理令牌回退访问，避免仅依赖固定管理员账号导致“统计加载失败”。
- 修复本机 `localhost:8001` 下语音配置无法保存：管理接口鉴权放开 loopback 请求，ASR/TTS 本地 provider 可正常切换并即时生效。

### 体验优化

- 优化字幕字体稳定性：关闭 Google Fonts 运行时拉取，补充 CJK 字体回退链，降低字幕首帧偶发乱码/字形闪烁。
- 字幕关键文本样式显式使用 CJK fallback，提升不同系统字体环境下的一致性。

### 验证

- 后端测试：`backend/.venv/bin/python -m pytest backend/tests -q`（195 passed）。
- 前端测试：`flutter test --no-pub`（85 passed）。
- 云端验收：`/ops/api/status`、`/ops/api/admin/stats`、`/ops/api/start/backend` 令牌访问返回 200。

## v1.0.9 - 2026-05-03

### 新增功能

- 新增账号体系：注册用户名必须为 11 位手机号，支持登录、登出、个人资料查看与昵称修改。
- 首页新增账号菜单，展示手机号与昵称，并把登录昵称作为讨论中的真人称呼和席位名称。
- 新增管理与运维入口、后台接口、部署脚本和运维说明文档，便于本地开发、烟测和线上管理。
- 新增 HarmonyOS 工程骨架，保留移动端后续适配入口。

### 体验优化

- 设置页允许先保存离线本地 ASR/TTS provider 配置，待本地服务启动后自动生效。
- 优化语音服务链路与 WebSocket 会话事件，补充用户身份、录音、历史和复盘相关字段。
- 扩展 TTS/ASR provider 支持与前端音色选择逻辑，增强网关语音、FunASR、浏览器语音和 OpenAI 兼容 TTS 的稳定性。

### 讨论规则加固

- 强制老师开场介绍话题来源、基本情况与核心争议，避免直接跳入点名。
- 确保真人学生在开场后 2 分钟内获得第一次发言机会，常规发言总量控制在 5-7 次，积极举手时可适当增加。
- 加固举手机制：举手后立即熄灭按钮，获准后安排下一轮真人发言，真人发言结束后再过一轮才允许再次举手。
- 限制非真人角色单轮发言长度，降低超 40 秒长段独白风险。
- 真人发言后至少一半机会由老师立即肯定、支持式点评，其余轮次允许同学自然回应。
- 加强点名、引用和角色归属守卫，防止张冠李戴、引用未出现字幕、自我引用和老师/同学/思想家身份混淆。

### 验证

- 后端核心守卫测试通过：`backend/.venv/bin/python -m pytest backend/tests/test_acceptance_guards.py -q`（128 passed）。
- 前端会话逻辑测试通过：`flutter test --no-pub test/immersive_session_screen_test.dart test/session_frontend_commander_test.dart`（45 passed）。
- Flutter Web 已重新构建并同步到 `backend/static`；依赖刷新遇到 `pub.dev` socket 异常时已自动使用本地缓存继续构建。

---

## v1.0.6 - 2026-04-30

### Bug 修复

- 修复 FloorManager 在 continuation 过程中对 AutoGen 终止计数重置的问题：新增本地消息预算上限（`nominal_max_turns * 2 + 8`），避免单轮讨论异常拉长到失控。
- 修复 human turn 恢复链路：human-turn stream 提前结束后会自动重启 continuation stream，避免讨论停滞。
- 修复通用流式卡顿恢复：对 `__anext__` 增加超时保护，超时后触发恢复重启，减少 live 讨论中断。
- 修复 `pause/resume` 清理协程未真正执行的问题，确保 team control 在异常路径也能被正确收口。

### 验证

- `backend/tests/test_acceptance_guards.py` 关键守卫用例通过（包含 `general_stall`、`human_turn_recovery`、`pause_and_resume`）。
- 5 轮 live 质量审计完成，全部满足 `human_speech_count >= 5` 门槛。

## v1.0.5 - 2026-04-30

### Bug 修复

- 修复主持人对刚发完角色再次使用“请X发言”造成的重复点名空转，避免 designation 循环继续放大老师话轮占比。
- 收紧真人麦克风授权链：前后端统一只在老师明确点名、同学明确交棒或真人举手获准时打开麦克风，减少未授权 human_input_requested 和误开麦。
- 调整真人调度预算与主持人提示词，统一把 10 分钟讨论的真人发言目标收敛到 5 到 8 次，并缩短真人再次被邀请前的等待间隔。
- 优化真人发言后的老师接话体验：前端新增老师接话热身提示，降低真人提交后老师尚未开口时的等待落差。
- live 质量审计脚本在本地 Ollama 超时或 HTTP 异常时会自动回退 deterministic human reply，避免 10 轮审计被单次代理超时整批打断。

### 验证

- 后端守卫测试与前端 human turn commander 定向测试通过。
- Flutter Web 构建产物已同步部署到 backend/static，并随版本号一并更新。

## v1.0.4 - 2026-04-29

### Bug 修复

- 修复 SESSION-4（在家上学）中“老师点名后由非被点名者发言”的调度穿透问题：新增点名后目标 AI 的强约束拦截，未到被点名者发言前会阻断其他 AI 抢话。
- 收紧主持人话轮占比：下调调度器主持人软上限并压缩主持人单轮句数，显著减少老师连续长段输出。
- 强化思想家发言保障：老师点名思想家后，必须先由该思想家实际发言，避免“老师代替思想家发言”的错位表现。
- 增加首次真人发言时间兜底：除轮次门槛外新增时间上限触发，超过阈值会强制把麦克风交还真人，确保首轮真人邀请不被拖延。
- 加固引用真实性清洗：扩展展示名别名识别（含简称/尾名），对不可追溯引语与错归属引用进行更严格降级或改写，降低“引用未说过内容”的风险。

### 验证

- 新增并通过 SESSION-4 定向回归守卫测试（点名执行、思想家发言强约束、首次真人时间兜底、简称错引清洗）。
- 后端守卫测试通过：`backend/tests/test_acceptance_guards.py`（91 passed）。

## v1.0.3 - 2026-04-29

### 新增功能

- 讨论现场新增顶部电子表计时器，与暂停/继续/结束状态联动，方便实时掌握已讨论时长。
- 复盘场景新增语速调节滑杆，支持 0.8x-1.3x，默认 1.0x（常规语速）。
- 新增 5 轮自动模拟讨论脚本，可批量选择真实话题、虚拟同学与思想家，自动生成真人发言并回读会议记录做回归分析。

### 体验优化

- 优化复盘台词衔接节奏：相邻句间停顿更紧凑，连续发言体感更接近真实讨论现场。
- 暂停后恢复时从当前字幕开头重读，减少中断恢复时的理解断层。
- 复盘优先复用角色原音色回退策略，并过滤括号内舞台提示文本，避免把注释内容读出来。
- 历史状态归一化逻辑覆盖后端与开发面板本地读取路径，过期 running 会话会自动修正为 disconnected，历史显示一致性提升。
- 强化主持人约束：老师收尾必须明确说“再见”，点评引用与点名更严格，未落地引号和误指向会被自动清洗或降级。
- 讨论页点评时序改为“小结阶段后台准备、老师再见后显示并立即朗读”，避免结束前提前弹出会后点评。
- 会议历史新增 request_id、request_wait_ms 与错插 AI 丢弃计数，后续复盘可直接量化真人等待时延与调度噪声。

### 验证

- 后端历史与防护相关回归测试通过（含 stale running 场景）。
- 前端讨论页与文本处理相关测试通过，Web 构建产物已同步部署到 backend/static。
- 主持人防护与引用归因守卫测试通过（88 passed），并完成 5 轮真实话题自动模拟讨论，目标错误为 0。

## v1.0.2 - 2026-04-28

### Bug 修复

- 基于 243 条历史发言记录复盘，修正老师、虚拟学生、思想家嘉宾之间的角色定位与自我认知规则，避免把老师/思想家错误称为“同学”。
- 加固发言引用归因：点评、转述、引用他人观点时优先绑定真实发言者，防止把未发言角色、上一位发言者或当前发言者自己错置到对方名字上。
- 修复“请小明评价小明刚才说法”这类自我追问循环，主持人会改为邀请其他同学回应。
- 清理模型流式输出中的英文元推理片段，避免内部思考混入字幕、TTS 或历史记录。
- 修复“麦克风自动打开”设置未真正生效的问题：轮到真人发言时会按设置自动开麦，并忽略重复真人回合事件造成的状态跳变。
- 修复热键逻辑仍硬编码 Ctrl 的问题：运行时改为读取设置页配置，默认 Right Option；Ctrl 只有在用户显式选择时才会生效。
- 调整短讨论的收尾调度门槛，真人发言最低预算随讨论轮数缩放，避免小场景无法进入正常收尾征询。

### 验证

- 新增并通过后端发言归因、元推理清理和自我追问回归测试。
- 新增并通过前端麦克风热键与设置归一化测试。

## v1.0.1 - 2026-04-28

### 新增功能

#### 思辨复盘（Replay）
- 首页右上角新增「思辨复盘」按钮（图标 replay_circle_filled），路由 `/replay`。
- 新页面支持从本地选择 ZIP 复盘包，解析 `script/*-script.json` 与 `recordings/*` 内嵌音频，按原顺序逐句回放完整会议过程。
- 优先使用 ZIP 内的真实录音；缺失录音的发言自动调用 `/api/v1/voice/tts`，以相同 `character_id` 重读相同文稿，不重新生成讨论内容。
- 列表展示每句标注「原声」或「TTS」，含进度条、暂停 / 继续 / 回到开头控制。
- 新增 Flutter 依赖：`file_picker ^8.1.2`、`archive ^3.4.10`。

#### 自定义 LLM 提供商
- 设置 → AI 模型提供商新增「自定义一（OpenAI 兼容）」和「自定义二（OpenAI 兼容）」两个槽位。
- 每个自定义提供商可独立配置 API Key、Base URL、模型名称、显示名称，适配任何兼容 OpenAI API 的第三方服务。
- `/providers` 接口为自定义项返回 `is_custom`、`display_name`、`hint` 字段，供前端区别渲染。

#### 主持人与讨论流程（v1.0.0 后续提交）
- 话题感知参与者推荐：首页新增 `_ThinkerRecommendations` 组件，根据议题关键词动态推荐相关角色，支持直接点击加入。
- 后端新增 `POST /api/v1/thinkers/recommend` 接口，按类别-领域打分并返回排名前 3 的角色与推荐理由。
- 新增「可乐」角色：一年级小男生，Edge TTS 音色 `zh-CN-YunxiNeural`，暖橙色 + 🥤 头像。
- 麦克风热键可配置：Right Option / Left Option / 任意 Option / F12 / Ctrl / Space，支持在设置页调整。
- 自动开麦模式支持单击热键或点击屏幕按钮关闭。
- 主持人点名后等待真人发言，不抢话（优雅过渡）。
- 自由话题保存后自动预热推荐角色。
- 第一位真人参与者在首轮享有约 1 分钟思考时间。
- 老师点评等 TTS 队列清空后再呈现，改为全屏中央弹窗，宽度上限 360 px，高度 56% 视口。
- 金句改为点评弹窗内「查看金句」按钮触发，不再常驻讨论页。
- 讨论目标时长调整为 10–15 分钟 / 14–22 轮。

### Bug 修复

- **修复 `model_info is required` 错误**：旧逻辑对 `provider=openai` 但 `base_url` 指向第三方兼容服务的情况漏注入 `model_info`。现改为同时校验 `base_url` 主机名是否以 `openai.com` 结尾，只有真实 OpenAI 端点 + 原生模型前缀时才省略，其余一律显式注入。
- **DevPanel ZIP 打包录音勾选始终可用**：`recordings.json` manifest 未及时刷新时，「录音清单」和「音频附件」勾选框被错误禁用。现同时检查 `summary.recording_count`，只要后台已有录音记录即可勾选。
- **语音路由与 CapsWriter 录音流修复**：修复语音网关路由逻辑及 CapsWriter 录音流中的边界问题。
- **发言者点名解析加固**：`parse_speaker_designation` 新增对修饰短语（"想问问一直没说话的…"）、`'X同学，你听了…'` 等模式的支持；`_sanitize_reference_attribution` 扩展表扬动词列表，增加行首指代重写规则。
- **字幕与弹窗布局修复**：字幕宽度调整为 85%，三行时首行下移 ~30 px；老师点评弹窗垂直居中，不再遮挡座位区。
- **讨论结束与点评衔接**：结束与点评弹窗/TTS 之间强制 3 s 延迟，避免两路语音重叠播放。

### 性能优化

- TTS 预取批量并发从 2 升至 3，开场白多句并行合成，第二句停顿明显缩短。
- `_kickBatchPrefetch` 绕过 120 ms 防抖，多段开场词入队时立即批量触发预合成。

---

## v1.0.0 - 2026-04-27

### 讨论体验

- 新增旁听模式举手机制：旁听时不参与常规轮次，但可以举手申请发言。
- 修复“老师已安排发言时，用户再次举手”造成的人类回合并发问题，将点名与举手合并为同一轮发言，提升容错与稳定性。
- 在右侧操作列增加旁听席徽章，旁听状态、举手状态和待发言状态更清晰。
- 讨论结束后新增真人用户会后点评卡片，展示在左侧空白区，默认由老师音色自动朗读，并支持静音/重播。

### 历史、回放与导出

- 完整保存讨论剧本，可在历史详情中回看整场讨论。
- 保留真人用户录音，并与历史详情联动回放。
- 支持正式脚本导出。
- 支持 ZIP 打包导出，便于统一归档讨论内容与附件。
- devpanel 增强历史查看、回放与导出操作。

### 音色与语音系统

- 设置页新增音色工坊，可分别为 Edge TTS、OpenVoice、VibeVoice、ChatTTS 配置角色音色。
- 保持当前 OpenVoice 教师音色与儿童音色基线配置。
- 后端新增 WAV 响度守卫，尽量把音量控制在设定值附近，减少忽大忽小问题。
- 移除 Python audioop 依赖，提前兼容 Python 3.13。

### 构建与发布质量

- Web 构建关闭图标裁剪，避免 CupertinoIcons 在 Web 端缺字形。
- README 全量重写，补齐架构、功能、构建方式和当前实现状态。
- 正式引入 CHANGELOG，作为后续带标签版本的发布记录入口。
