library;

import 'browser_speech.dart';
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
    case 'capswriter':
      return GatewayStreamingAsrService(service: providerId);
    case 'funasr':
    case 'openai_whisper':
      return ServerAsrService(serverUrl: serverUrl);
    default:
      return BrowserAsrService();
  }
}
