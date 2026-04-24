import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/models/config_models.dart';
import 'package:roundtable/state/settings_provider.dart';

void main() {
  const remoteConfig = CurrentConfig(
    llmProvider: 'deepseek',
    llmProviderName: 'DeepSeek',
    apiKeyMasked: 'sk-***',
    model: 'deepseek-chat',
    asrProvider: 'browser',
    ttsProvider: 'openvoice',
    pushToTalk: true,
  );

  test('resolveInitialLocalSettings falls back to remote speech config', () {
    final settings = resolveInitialLocalSettings(
      serverUrl: 'http://localhost:8001',
      storedLlmProvider: null,
      storedAsrProvider: null,
      storedTtsProvider: null,
      hasStoredPushToTalk: false,
      storedPushToTalk: null,
      storedMicActivationMode: null,
      storedMicControlMode: null,
      remoteConfig: remoteConfig,
    );

    expect(settings.serverUrl, 'http://localhost:8001');
    expect(settings.asrProvider, 'browser');
    expect(settings.ttsProvider, 'openvoice');
    expect(settings.pushToTalk, isTrue);
    expect(settings.streamUserSubtitles, isTrue);
    expect(settings.micActivationMode, 'manual');
    expect(settings.micControlMode, 'hold_ctrl');
  });

  test('resolveInitialLocalSettings keeps explicit local speech settings', () {
    final settings = resolveInitialLocalSettings(
      serverUrl: 'http://localhost:8001',
      storedLlmProvider: 'openai',
      storedAsrProvider: 'funasr',
      storedTtsProvider: 'edge_tts',
      hasStoredPushToTalk: true,
      storedPushToTalk: false,
      storedMicActivationMode: 'auto',
      storedMicControlMode: 'hold_ctrl',
      remoteConfig: remoteConfig,
    );

    expect(settings.asrProvider, 'funasr');
    expect(settings.ttsProvider, 'edge_tts');
    expect(settings.pushToTalk, isFalse);
    expect(settings.streamUserSubtitles, isTrue);
    expect(settings.micActivationMode, 'auto');
    expect(settings.micControlMode, 'hold_ctrl');
  });

  test('resolveInitialLocalSettings migrates legacy double ctrl to hold ctrl',
      () {
    final settings = resolveInitialLocalSettings(
      serverUrl: 'http://localhost:8001',
      storedLlmProvider: null,
      storedAsrProvider: null,
      storedTtsProvider: null,
      hasStoredPushToTalk: true,
      storedPushToTalk: true,
      storedMicActivationMode: null,
      storedMicControlMode: 'double_ctrl',
      remoteConfig: remoteConfig,
    );

    expect(settings.micControlMode, 'hold_ctrl');
  });

  test('resolveInitialLocalSettings keeps explicit subtitle streaming choice',
      () {
    final settings = resolveInitialLocalSettings(
      serverUrl: 'http://localhost:8001',
      storedLlmProvider: null,
      storedAsrProvider: null,
      storedTtsProvider: null,
      hasStoredPushToTalk: true,
      storedPushToTalk: true,
      hasStoredStreamUserSubtitles: true,
      storedStreamUserSubtitles: false,
      storedMicActivationMode: null,
      storedMicControlMode: null,
      remoteConfig: remoteConfig,
    );

    expect(settings.streamUserSubtitles, isFalse);
  });

  test('resolveInitialLocalSettings migrates removed openai_tts to edge_tts',
      () {
    final settings = resolveInitialLocalSettings(
      serverUrl: 'http://localhost:8001',
      storedLlmProvider: null,
      storedAsrProvider: null,
      storedTtsProvider: 'openai_tts',
      hasStoredPushToTalk: true,
      storedPushToTalk: true,
      storedMicActivationMode: null,
      storedMicControlMode: null,
      remoteConfig: remoteConfig,
    );

    expect(settings.ttsProvider, 'edge_tts');
  });
}
