library;

import 'browser_speech.dart';
import 'funasr_streaming.dart';
import 'gateway_speech.dart';
import 'server_speech.dart';
import 'speech_contract.dart';

TtsService createWebTtsService(
  String providerId, {
  required String serverUrl,
  String? providerUrl,
}) {
  switch (providerId) {
    case 'browser':
      return BrowserTtsService();
    case 'chattts':
    case 'edge_tts':
    case 'cosyvoice':
    case 'openai_tts':
    case 'vibevoice':
    case 'fireredtts':
    case 'openvoice':
      return ServerTtsService(serverUrl: serverUrl, providerId: providerId);
    default:
      return BrowserTtsService();
  }
}

AsrService createWebAsrService(
  String providerId, {
  required String serverUrl,
  String? providerUrl,
  bool preferServerProxy = false,
}) {
  if (preferServerProxy &&
      providerId != 'browser' &&
      providerId != 'disabled') {
    return ServerAsrService(serverUrl: serverUrl, providerId: providerId);
  }

  switch (providerId) {
    case 'browser':
      return BrowserAsrService();
    case 'vosk':
      return GatewayStreamingAsrService(
        service: providerId,
        gatewayUrl: providerUrl?.trim().isNotEmpty == true
            ? providerUrl!.trim()
            : 'http://localhost:6702',
      );
    case 'capswriter':
      return GatewayStreamingAsrService(
        service: providerId,
        gatewayUrl: providerUrl?.trim().isNotEmpty == true
            ? providerUrl!.trim()
            : 'http://localhost:6701',
      );
    case 'funasr':
      // 需求5b：FunASR 直连流式，优先使用后端配置页返回的 WebSocket 地址。
      // 提供在线增量（text_online）+ 离线整句（text_offline）。
      return FunasrStreamingAsrService(
        wsUrl: providerUrl?.trim().isNotEmpty == true
            ? providerUrl!.trim()
            : 'ws://localhost:10095',
      );
    case 'openai_whisper':
      return ServerAsrService(serverUrl: serverUrl, providerId: providerId);
    default:
      return BrowserAsrService();
  }
}
