import 'package:flutter_test/flutter_test.dart';

import 'package:roundtable/models/config_models.dart';

void main() {
  group('云端音色库', () {
    test('仅顶级云端服务启用音色库', () {
      expect(supportsVoiceLibrary('elevenlabs_tts'), isTrue);
      expect(supportsVoiceLibrary('minimax_tts'), isTrue);
      expect(supportsVoiceLibrary('edge_tts'), isFalse);
      expect(supportsVoiceLibrary('openvoice'), isFalse);
      expect(supportsVoiceLibrary(''), isFalse);
    });

    test('预置预设的服务仍走静态音色库', () {
      expect(voiceServicePalette('edge_tts'), isNotNull);
      expect(voiceServicePalette('elevenlabs_tts'), isNull);
    });

    test('解析服务端返回的音色条目', () {
      final voice = VoiceLibraryVoice.fromJson(<String, dynamic>{
        'id': 'voice-abc',
        'name': 'Rachel',
        'labels': <String, dynamic>{'accent': 'american', 'age': 'young'},
      });
      expect(voice.id, 'voice-abc');
      expect(voice.name, 'Rachel');
      expect(voice.subtitle.contains('american'), isTrue);
    });

    test('缺少 name/labels 时退化为 id 兜底', () {
      final voice = VoiceLibraryVoice.fromJson(<String, dynamic>{
        'id': 'vo-1',
        'category': 'cloned',
      });
      expect(voice.id, 'vo-1');
      expect(voice.name, '');
      expect(voice.subtitle, 'cloned');
    });
  });
}
