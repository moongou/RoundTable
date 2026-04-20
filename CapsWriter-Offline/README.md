# CapsWriter-Offline

离线语音识别服务，基于 sherpa-onnx + WebSocket。

固定运行端口：`6016`（可通过环境变量覆盖）。

## 1. 安装

```bash
cd CapsWriter-Offline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2. 准备模型

将 sherpa-onnx transducer 模型文件放到 `models/` 目录：

- `models/tokens.txt`
- `models/encoder.onnx`
- `models/decoder.onnx`
- `models/joiner.onnx`

也可复制 `.env.example` 为 `.env` 后改路径。

## 3. 启动（端口 6016）

```bash
cd CapsWriter-Offline
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 6016
```

## 4. API

### 健康检查

`GET /health`

### WebSocket 识别

`WS /ws`

输入：
- 二进制帧：PCM16LE、单声道、16kHz 音频切片
- 文本帧：`{"type":"eof"}` 表示结束并触发最终识别

输出：
- `{"type":"partial","text":"..."}`
- `{"type":"final","text":"..."}`
- `{"type":"error","error":"..."}`

## 5. 最小客户端示例

```python
import asyncio
import json
import websockets

async def main():
    uri = "ws://127.0.0.1:6016/ws"
    async with websockets.connect(uri, max_size=4 * 1024 * 1024) as ws:
        # 这里替换成真实 PCM16 数据块
        ws.send(b"\x00\x00" * 1600)
        print(await ws.recv())

        await ws.send(json.dumps({"type": "eof"}))
        print(await ws.recv())

asyncio.run(main())
```

## 6. 备注

- 当前协议是通用 WebSocket 流式 ASR 协议，不依赖浏览器特定 API。
- 如果你要接入 RoundTable，可把 ASR 服务地址指向 `http://localhost:6016`。
