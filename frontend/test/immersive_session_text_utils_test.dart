import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/features/session/immersive_session_text_utils.dart';

void main() {
  group('mergeStreamingDraft', () {
    test('returns incoming when current is empty', () {
      expect(mergeStreamingDraft('', '你好'), '你好');
    });

    test('keeps longer prefix-compatible value', () {
      expect(mergeStreamingDraft('你好', '你好呀'), '你好呀');
      expect(mergeStreamingDraft('你好呀', '你好'), '你好呀');
    });

    test('concats with blank when no overlap', () {
      expect(mergeStreamingDraft('你好', '世界'), '你好 世界');
    });
  });

  group('polishTranscript', () {
    test('deduplicates fillers and normalizes punctuation', () {
      final polished = polishTranscript('嗯 嗯  我觉得，，，，这个  这个  可以！!');
      expect(polished, isNot(contains(r'\$1')));
      expect(polished, isNot(contains('，，')));
      expect(RegExp(r'[。！？!?]$').hasMatch(polished), isTrue);
    });

    test('appends sentence ending when missing', () {
      expect(polishTranscript('我同意这个观点'), '我同意这个观点。');
    });

    test('returns empty for blank input', () {
      expect(polishTranscript('   '), '');
    });
  });

  group('friendlyErrorMessage', () {
    test('silences asgi transport noise', () {
      expect(
          friendlyErrorMessage('Unexpected ASGI message: websocket.send'), '');
    });

    test('maps queue/input failure to friendly text', () {
      expect(
        friendlyErrorMessage('Failed to get user input from queue'),
        '用户输入通道暂时不可用，系统已自动跳过本轮并继续讨论。',
      );
    });
  });

  group('format helpers', () {
    test('averageOf returns 0 for empty', () {
      expect(averageOf(<double>[]), 0);
    });

    test('averageOf calculates mean', () {
      expect(averageOf(<double>[1, 2, 3, 4]), 2.5);
    });

    test('formatTtsTraceValue falls back on blank', () {
      expect(formatTtsTraceValue('  '), '—');
      expect(formatTtsTraceValue('edge_tts'), 'edge_tts');
    });
  });
}
