/// 语音服务抽象接口和工厂
///
/// 定义 TTS 和 ASR 的接口契约，以及基于配置创建具体实现的工厂方法。
library;

import 'speech_contract.dart';
import 'speech_stub_factories.dart'
    if (dart.library.html) 'speech_web_factories.dart' as impl;

export 'speech_contract.dart';

/// 语音服务工厂：根据配置创建 TTS/ASR 实例
TtsService createTtsService(String providerId,
    {String serverUrl = 'http://localhost:8001', String? providerUrl}) {
  return impl.createWebTtsService(
    providerId,
    serverUrl: serverUrl,
    providerUrl: providerUrl,
  );
}

AsrService createAsrService(
  String providerId, {
  String serverUrl = 'http://localhost:8001',
  String? providerUrl,
  bool preferServerProxy = false,
}) {
  return impl.createWebAsrService(
    providerId,
    serverUrl: serverUrl,
    providerUrl: providerUrl,
    preferServerProxy: preferServerProxy,
  );
}
