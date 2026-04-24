import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:roundtable/features/settings/settings_screen.dart';
import 'package:roundtable/models/config_models.dart';
import 'package:roundtable/state/settings_provider.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('settings screen removes standalone validation entry',
      (tester) async {
    SharedPreferences.setMockInitialValues({
      'server_url': 'http://localhost:8001',
      'llm_provider': 'deepseek',
      'asr_provider': 'funasr',
      'tts_provider': 'edge_tts',
      'push_to_talk': true,
      'mic_control_mode': 'hold_ctrl',
    });

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          providersProvider.overrideWith(
            (ref) async => const [
              ProviderInfo(
                id: 'deepseek',
                name: 'DeepSeek',
                baseUrl: 'https://api.deepseek.com',
                model: 'deepseek-chat',
                hasApiKey: true,
                isActive: true,
                needsApiKey: true,
              ),
            ],
          ),
          speechConfigProvider.overrideWith(
            (ref) async => const SpeechConfig(
              asrProviders: [
                SpeechProviderInfo(
                  id: 'funasr',
                  name: 'FunASR',
                  isActive: true,
                  available: true,
                ),
              ],
              ttsProviders: [
                SpeechProviderInfo(
                  id: 'edge_tts',
                  name: 'Edge TTS',
                  isActive: true,
                  available: true,
                ),
              ],
              pushToTalk: true,
            ),
          ),
          healthStatusProvider.overrideWith(
            (ref) async => const {
              'llm': ServiceHealth(
                name: 'llm',
                url: 'http://localhost:8001/api/v1/config/health',
                reachable: true,
                detail: 'LLM 正常',
              ),
            },
          ),
          currentConfigProvider.overrideWith(
            (ref) async => const CurrentConfig(
              llmProvider: 'deepseek',
              llmProviderName: 'DeepSeek',
              apiKeyMasked: 'sk-***',
              model: 'deepseek-chat',
              asrProvider: 'funasr',
              ttsProvider: 'edge_tts',
              pushToTalk: true,
              webSearchEnabled: false,
              tavilyConfigured: false,
            ),
          ),
        ],
        child: const MaterialApp(home: SettingsScreen()),
      ),
    );

    await tester.pumpAndSettle();

    expect(find.text('设置'), findsOneWidget);
    expect(find.text('验证'), findsNothing);
    expect(find.text('AI 模型'), findsWidgets);
    expect(find.text('通用'), findsWidgets);

    await tester.tap(find.text('通用').last);
    await tester.pumpAndSettle();

    expect(
      find.text(
        '这里是系统状态的统一展示入口。是否可用请以本地服务状态为准，不再单独显示“验证”结果。',
        skipOffstage: false,
      ),
      findsOneWidget,
    );
    expect(find.text('自动开启麦克风', skipOffstage: false), findsOneWidget);
    expect(find.text('用户手动开启', skipOffstage: false), findsOneWidget);
    expect(find.text('按住说话（Push-to-Talk）', skipOffstage: false), findsNothing);
  });
}
