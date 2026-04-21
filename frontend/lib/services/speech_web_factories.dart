library;

import 'browser_speech.dart';
import 'funasr_streaming.dart';
import 'gateway_speech.dart';
import 'server_speech.dart';
import 'speech_contract.dart';

TtsService createWebTtsService(String providerId, {required String serverUrl}) {
  switch (providerId) {
    case 'browser':
      return BrowserTtsService();
    case 'vibevoice':
    case 'fireredtts':
    case 'openvoice':
      return GatewayTtsService(service: providerId);
    case 'edge_tts':
    case 'cosyvoice':
    case 'openai_tts':
      return ServerTtsService(serverUrl: serverUrl);
    default:
      return BrowserTtsService();
  }
}

AsrService createWebAsrService(String providerId, {required String serverUrl}) {
  switch (providerId) {
    case 'browser':
      return BrowserAsrService();
    case 'vosk':
      return GatewayStreamingAsrService(
        service: providerId,
        gatewayUrl: 'http://localhost:6702',
      );
    case 'capswriter':
      return GatewayStreamingAsrService(
        service: providerId,
        gatewayUrl: 'http://localhost:6701',
      );
    case 'funasr':
      // 需求5b：FunASR 直连流式（浏览器 → ws://localhost:10095），
      // 提供在线增量（text_online）+ 离线整句（text_offline）。
      return FunasrStreamingAsrService();
    case 'openai_whisper':
      return ServerAsrService(serverUrl: serverUrl);
    default:
      return BrowserAsrService();
  }
}
