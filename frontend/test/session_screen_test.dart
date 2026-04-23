import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/features/session/session_screen.dart';
import 'package:roundtable/models/config_models.dart';

void main() {
  test('session screen boot defaults stay on funasr and edge_tts', () {
    expect(SessionScreen.defaultAsrProvider, 'funasr');
    expect(SessionScreen.defaultTtsProvider, 'edge_tts');
  });

  test('session screen prefers chattts only for the default edge_tts path', () {
    final speechConfig = SpeechConfig(
      asrProviders: const [],
      ttsProviders: const [
        SpeechProviderInfo(
          id: 'chattts',
          name: 'ChatTTS',
          isActive: false,
          available: true,
        ),
      ],
      pushToTalk: true,
    );

    expect(
      SessionScreen.preferAvailableChatTts('edge_tts', speechConfig),
      'chattts',
    );
    expect(
      SessionScreen.preferAvailableChatTts('cosyvoice', speechConfig),
      'cosyvoice',
    );
  });
}
