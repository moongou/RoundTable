/// 语音服务抽象接口和工厂
///
/// 定义 TTS 和 ASR 的接口契约，以及基于配置创建具体实现的工厂方法。
library;

import 'browser_speech.dart';
import 'server_speech.dart';

/// TTS 服务抽象接口
abstract class TtsService {
  /// 朗读文本
  Future<void> speak(String text, {String? voice});

  /// 停止朗读
  Future<void> stop();

  /// 是否正在朗读
  bool get isSpeaking;

  /// 释放资源
  void dispose();
}

/// ASR 服务抽象接口
abstract class AsrService {
  /// 开始监听（录音+识别）
  Future<void> startListening();

  /// 停止监听
  Future<void> stopListening();

  /// 识别结果流
  Stream<String> get transcriptionStream;

  /// 是否正在监听
  bool get isListening;

  /// 是否可用（浏览器可能不支持）
  bool get isAvailable;

  /// 释放资源
  void dispose();
}

/// 语音服务工厂：根据配置创建 TTS/ASR 实例
TtsService createTtsService(String providerId,
    {String serverUrl = 'http://localhost:8001'}) {
  switch (providerId) {
    case 'browser':
      return BrowserTtsService();
    case 'edge_tts':
    case 'cosyvoice':
    case 'openai_tts':
      return ServerTtsService(serverUrl: serverUrl);
    default:
      return BrowserTtsService();
  }
}

AsrService createAsrService(String providerId,
    {String serverUrl = 'http://localhost:8001'}) {
  switch (providerId) {
    case 'browser':
      return BrowserAsrService();
    case 'funasr':
    case 'openai_whisper':
      return ServerAsrService(serverUrl: serverUrl);
    default:
      return BrowserAsrService();
  }
}
