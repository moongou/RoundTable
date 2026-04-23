import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/models/discussion_models.dart';
import 'package:roundtable/features/session/immersive_session_screen.dart';

void main() {
  test('human turn auto skip window stays at 30 seconds', () {
    expect(
      ImmersiveSessionScreen.humanTurnAutoSkipWindow,
      const Duration(seconds: 30),
    );
  });

  test('session voice defaults stay on funasr and edge_tts during boot', () {
    expect(
      ImmersiveSessionScreen.defaultAsrProvider,
      'funasr',
    );
    expect(
      ImmersiveSessionScreen.defaultTtsProvider,
      'edge_tts',
    );
  });

  test('golden quotes do not activate on thin opening material', () {
    final messages = <ChatMessage>[
      const ChatMessage(source: '李老师', content: '同学们好，我是李老师。'),
      const ChatMessage(source: '李老师', content: '今天我们聊聊分数。'),
      const ChatMessage(source: '小探', content: '我觉得分数像温度计。'),
    ];

    expect(ImmersiveSessionScreen.hasGoldenQuoteMaterial(messages), isFalse);
  });

  test('golden quotes require a real multi-speaker discussion', () {
    final messages = <ChatMessage>[
      const ChatMessage(source: '李老师', content: '同学们好，我们聊聊分数。'),
      const ChatMessage(source: '小探', content: '我觉得分数只能看到表面。'),
      const ChatMessage(source: '豆苗', content: '有时做对了也不代表真懂。'),
      const ChatMessage(source: '小理', content: '理解过程比结果更重要。'),
    ];

    expect(ImmersiveSessionScreen.hasGoldenQuoteMaterial(messages), isTrue);
  });
}
