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
  final normalizedProvider = providerId.trim().toLowerCase();
  const localStreamingProviders = <String>{'capswriter', 'vosk', 'funasr'};

  if (preferServerProxy &&
      !localStreamingProviders.contains(normalizedProvider) &&
      normalizedProvider != 'browser' &&
      normalizedProvider != 'disabled') {
    return ServerAsrService(
      serverUrl: serverUrl,
      providerId: normalizedProvider,
    );
  }

  if (normalizedProvider == 'capswriter' || normalizedProvider == 'vosk') {
    final resolvedUrl = providerUrl?.trim().isNotEmpty == true
        ? providerUrl!.trim()
        : (normalizedProvider == 'capswriter'
            ? 'ws://localhost:6016'
            : 'http://localhost:6702');
    return GatewayStreamingAsrService(
      service: normalizedProvider,
      gatewayUrl: resolvedUrl,
      wsPath: normalizedProvider == 'capswriter' ? '/ws' : '/stream',
      wsProtocols: normalizedProvider == 'capswriter'
          ? const <String>['binary']
          : const <String>[],
      capsWriterJsonProtocol: normalizedProvider == 'capswriter',
    );
  }

  if (normalizedProvider == 'funasr') {
    return FunasrStreamingAsrService(
      wsUrl: providerUrl?.trim().isNotEmpty == true
          ? providerUrl!.trim()
          : 'ws://localhost:10095',
    );
  }

  switch (normalizedProvider) {
    case 'browser':
      return BrowserAsrService();
    case 'openai_whisper':
    case 'siliconflow_asr':
    case 'groq_whisper':
      return ServerAsrService(
        serverUrl: serverUrl,
        providerId: normalizedProvider,
      );
    default:
      return BrowserAsrService();
  }
}
