import 'package:flutter_test/flutter_test.dart';

import 'package:roundtable/services/speech_contract.dart';

void main() {
  group('TTS 流式播放', () {
    test('仅云端顶级服务启用流式', () {
      expect(supportsStreaming('elevenlabs_tts'), isTrue);
      expect(supportsStreaming('minimax_tts'), isTrue);
      expect(supportsStreaming(' edge_tts '), isFalse);
      expect(supportsStreaming('openvoice'), isFalse);
      expect(supportsStreaming(''), isFalse);
    });

    test('流式集合与非流式服务互不重叠', () {
      for (final providerId in kStreamingTtsProviders) {
        expect(supportsStreaming(providerId), isTrue);
      }
      expect(kStreamingTtsProviders.contains('edge_tts'), isFalse);
    });
  });
}
