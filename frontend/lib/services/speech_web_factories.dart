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
    case 'vibevoice':
    case 'fireredtts':
    case 'openvoice':
    case 'openai_tts':
    case 'siliconflow_tts':
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

  if (providerId == 'capswriter' || providerId == 'vosk') {
    final resolvedUrl = providerUrl?.trim().isNotEmpty == true
        ? providerUrl!.trim()
        : (providerId == 'capswriter'
            ? 'ws://localhost:6016'
            : 'http://localhost:6702');
    return GatewayStreamingAsrService(
      service: providerId,
      gatewayUrl: resolvedUrl,
      wsPath: providerId == 'capswriter' ? '/ws' : '/stream',
      wsProtocols: providerId == 'capswriter'
          ? const <String>['binary']
          : const <String>[],
      capsWriterJsonProtocol: providerId == 'capswriter',
    );
  }

  if (providerId == 'funasr') {
    return FunasrStreamingAsrService(
      wsUrl: providerUrl?.trim().isNotEmpty == true
          ? providerUrl!.trim()
          : 'ws://localhost:10095',
    );
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
        wsPath: '/stream',
        wsProtocols: const <String>[],
      );
    case 'capswriter':
      return GatewayStreamingAsrService(
        service: providerId,
        gatewayUrl: providerUrl?.trim().isNotEmpty == true
            ? providerUrl!.trim()
            : 'ws://localhost:6016',
        wsPath: '/ws',
        wsProtocols: const <String>['binary'],
        capsWriterJsonProtocol: true,
      );
    case 'openai_whisper':
    case 'siliconflow_asr':
    case 'groq_whisper':
      return ServerAsrService(serverUrl: serverUrl, providerId: providerId);
    default:
      return BrowserAsrService();
  }
}
