# RoundTable - 圆桌思辨讨论平台

面向中国小学生的 AI 引导思辨讨论平台。1-3名人类参与者 + AI虚拟角色，由AI主持人引导圆桌讨论。

## 架构

- **后端**: Python FastAPI + AutoGen v0.4 (SelectorGroupChat)
- **前端**: Flutter (跨平台)
- **语音** (Phase 2): LiveKit + Deepgram STT + Azure TTS
- **LLM**: 可插拔，支持 OpenAI / 通义千问 / DeepSeek

## 快速开始

### 后端

```bash
cd backend
cp .env.example .env  # 填入你的 API Key
pip install -e .
python -m app.main
```

### 前端

```bash
cd frontend
flutter pub get
flutter run
```

## 项目状态

Phase 1 (文字版 MVP) 开发中。