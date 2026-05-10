# RoundTable

RoundTable 是一个面向中文儿童讨论场景的沉浸式 AI 圆桌思辨平台。它把主持老师、虚拟学生、思想家角色和真人用户放进同一场讨论里，用语音、实时字幕、金句整理、历史回放和会后点评把一次讨论完整保留下来。

当前发布版本：v1.0.0

## 核心能力

- 沉浸式圆桌讨论：李老师主持，多名 AI 角色与真人用户围绕同一话题展开讨论。
- 纯语音真人参与：支持按住说话、自动跳过、发言窗口倒计时、语音识别状态反馈。
- 旁听模式可举手：旁听时不会进入常规轮次，但可以举手申请发言，并在 UI 中显示独立旁听席徽章。
- 会后老师点评：讨论结束后，左侧空白区会生成一段针对真人用户表现的会后评语，并默认使用老师音色自动朗读，可手动静音或重播。
- 今日金句：后端基于讨论内容提炼“今日金句”，前端支持独立查看与朗读。
- 完整历史留存：保存会话历史、完整剧本、真人录音、导出 JSON/Markdown、打包 ZIP。
- 开发面板联动：通过 devpanel 可查看历史、回放剧本、执行导出与调试运行状态。
- 多语音提供商：前端设置页支持 Edge TTS、OpenVoice、VibeVoice、ChatTTS 等音色分配与预览。
- 语音稳定性增强：后端对本地 WAV TTS 输出做响度守卫，尽量把音量控制在稳定范围内。

## 项目结构

- backend：FastAPI 后端、讨论调度、WebSocket、历史存储、TTS/ASR API。
- frontend：Flutter 客户端，负责沉浸式圆桌 UI、设置页、语音与 WebSocket 接线。
- backend/static：Flutter Web 构建产物，由根目录脚本同步生成。
- docs：重建方案、规格说明与补充设计资料。
- CapsWriter-Offline：离线识别相关独立子项目。
- devpanel.js：本地开发面板入口。

## 技术栈

- 后端：Python 3.11+、FastAPI、AutoGen AgentChat、httpx、pytest。
- 前端：Flutter 3、Riverpod、Dio、WebSocket、Google Fonts。
- 讨论模型：可插拔 LLM 工厂，支持主持老师、虚拟角色、思想家等多类 agent。
- 语音链路：统一 TTS/ASR API，支持多 provider 选择、配置保存与运行时切换。

## 快速开始

### 1. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### 2. 启动前端

```bash
cd frontend
flutter pub get
flutter run -d chrome
```

### 3. 构建 Web 并同步到后端

```bash
cd /path/to/RoundTable
./build_web.sh
```

构建完成后，Flutter Web 产物会覆盖到 backend/static，可直接由后端托管。

## 常用开发命令

后端测试：

```bash
cd backend
.venv/bin/pytest
```

前端测试：

```bash
cd frontend
flutter test
```

只跑会话页相关测试：

```bash
cd frontend
flutter test test/immersive_session_screen_test.dart
```

本地开发面板：

```bash
cd /path/to/RoundTable
node devpanel.js
```

- 面板地址：`http://localhost:8888`
- 应用地址：`http://localhost:8001`
- `localhost:8888` 已合并用户统计能力（与 `/admin` 同源数据：总用户、活跃用户、累计场次、用户详情与删除）。

ECS 上的同构能力：

- `deploy/deploy_aliyun.sh` 已支持项目级端口隔离，可通过环境变量设置 `BACKEND_PORT`、`DEVPANEL_PORT`、`PUBLIC_PORT`，并可通过 `ENABLE_PUBLIC_TLS=true` 让公网端口直接走 HTTPS。
- 推荐做法：每个项目占用独立后端端口，再由 Nginx 统一做代理；如果你希望直接通过域名加端口访问，也可以开启 `EXPOSE_PUBLIC_PORT=true`。
- RoundTable 当前建议的 ECS 组合是：后端内网端口 `18421`，开发面板 `8921`，外部访问端口 `8421`，公网协议 `HTTPS`。
- 示例：`BACKEND_PORT=18421 DEVPANEL_PORT=8921 PUBLIC_PORT=8421 EXPOSE_PUBLIC_PORT=true ENABLE_STANDARD_HTTP=false ENABLE_PUBLIC_TLS=true bash deploy/deploy_aliyun.sh`
- 可通过 SSH 隧道访问云上开发面板：`ssh -L 8921:127.0.0.1:8921 root@<ECS_IP>`

浏览器侧烟测清单：

- [docs/web-smoke-checklist.md](docs/web-smoke-checklist.md)

运维面板使用方法：

- [docs/ops-panel-usage.md](docs/ops-panel-usage.md)

## 当前实现重点

- 历史详情页可查看完整剧本，并联动真人录音回放。
- 导出支持正式脚本导出和可选 ZIP 打包。
- 设置页已支持多 TTS 服务的音色工坊配置与预览。
- 旁听模式下，真人用户可举手申请发言；当老师刚好已安排发言时，系统会把“点名”和“举手”合并成同一轮人类回合，避免重复回合导致卡住。
- 讨论结束后可自动生成金句与老师评语，两者互不冲突。

## 发布记录

- 当前正式版本见 [CHANGELOG.md](CHANGELOG.md)。
- 以后所有带 tag、version、v 标识的发布，都应在 CHANGELOG 中同步记录新增功能、稳定性改进和体验提升。

## 说明

- 仓库内部分语音服务依赖本地模型或外部服务，需要按本机环境单独配置。
- 若修改了 Flutter Web 运行时代码，请记得重新执行 build_web.sh，同步 backend/static。
