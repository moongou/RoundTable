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

  test('voice service rebind waits until capture and playback are idle', () {
    expect(
      ImmersiveSessionScreen.shouldDeferVoiceServiceRebind(
        isRecording: true,
        isSpeaking: false,
        hasQueuedPlayback: false,
      ),
      isTrue,
    );
    expect(
      ImmersiveSessionScreen.shouldDeferVoiceServiceRebind(
        isRecording: false,
        isSpeaking: true,
        hasQueuedPlayback: false,
      ),
      isTrue,
    );
    expect(
      ImmersiveSessionScreen.shouldDeferVoiceServiceRebind(
        isRecording: false,
        isSpeaking: false,
        hasQueuedPlayback: true,
      ),
      isTrue,
    );
    expect(
      ImmersiveSessionScreen.shouldDeferVoiceServiceRebind(
        isRecording: false,
        isSpeaking: false,
        hasQueuedPlayback: false,
      ),
      isFalse,
    );
  });

  test('speak button tap is disabled while ctrl hold owns the session', () {
    expect(
      ImmersiveSessionScreen.canTapSpeakButton(
        canSpeakNow: true,
        ctrlHeld: true,
        isRecording: false,
        recordingControlledByHoldCtrl: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.canTapSpeakButton(
        canSpeakNow: true,
        ctrlHeld: true,
        isRecording: true,
        recordingControlledByHoldCtrl: true,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.canTapSpeakButton(
        canSpeakNow: true,
        ctrlHeld: false,
        isRecording: false,
        recordingControlledByHoldCtrl: false,
      ),
      isTrue,
    );
  });

  test('pending human turn only waits for current speaker playback to finish',
      () {
    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: true,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: true,
      ),
      isTrue,
    );
  });

  test('completed human turn ignores duplicate prompt for same speaker', () {
    expect(
      ImmersiveSessionScreen.shouldIgnoreRepeatedHumanInputRequest(
        requestedSpeaker: '豆苗',
        completedSpeaker: '豆苗',
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldIgnoreRepeatedHumanInputRequest(
        requestedSpeaker: '**豆苗**',
        completedSpeaker: '豆苗',
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldIgnoreRepeatedHumanInputRequest(
        requestedSpeaker: '豆苗',
        completedSpeaker: '',
      ),
      isFalse,
    );
  });

  test('human subtitle hold gives long replies enough dwell time', () {
    expect(
      ImmersiveSessionScreen.humanSubtitleHoldDurationFor('短句').inMilliseconds,
      inInclusiveRange(1800, 2200),
    );

    expect(
      ImmersiveSessionScreen.humanSubtitleHoldDurationFor(
        '这是一个比较长的用户发言，用来确认分成两页字幕时，保留窗口会被拉长，而不是只有短短三秒就切走。',
      ).inMilliseconds,
      greaterThan(3000),
    );
  });

  test('subtitle page delay can follow remaining human hold window', () {
    expect(
      ImmersiveSessionScreen.subtitlePageAdvanceDelayMs(
        pageText: '第一页字幕',
        isFirstPage: true,
        remainingHumanSubtitleHold: const Duration(milliseconds: 4200),
        remainingPages: 2,
      ),
      2100,
    );

    expect(
      ImmersiveSessionScreen.subtitlePageAdvanceDelayMs(
        pageText: '普通 AI 字幕页面',
        isFirstPage: false,
        remainingHumanSubtitleHold: null,
        remainingPages: 1,
      ),
      inInclusiveRange(2400, 5600),
    );
  });

  test('ending quotes screen stays manual even after discussion ends', () {
    expect(
      ImmersiveSessionScreen.shouldPresentEndingQuotesScreen(
        discussionEnded: true,
        hasGoldenQuotes: true,
        manuallyRequested: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldPresentEndingQuotesScreen(
        discussionEnded: true,
        hasGoldenQuotes: true,
        manuallyRequested: true,
      ),
      isTrue,
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

  test('fixed role voices stay stable for openvoice and edge providers', () {
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '李老师',
        ttsProvider: 'openvoice',
      ),
      'ov:teacher_li',
    );
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '老师',
        ttsProvider: 'openvoice',
      ),
      'ov:teacher_li',
    );

    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '**小疑**',
        ttsProvider: 'openvoice',
      ),
      'ov:student_xiaoyi',
    );

    expect(
      ImmersiveSessionScreen.normalizeSpeakerLabel(' **豆苗** '),
      '豆苗',
    );
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '小疑',
        ttsProvider: 'openvoice',
      ),
      'ov:student_xiaoyi',
    );
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '小和',
        ttsProvider: 'openvoice',
      ),
      'ov:student_xiaohe',
    );
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '韦伯先生',
        ttsProvider: 'openvoice',
      ),
      'ov:thinker_elder',
    );

    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '小爱',
        ttsProvider: 'edge_tts',
      ),
      'zh-CN-XiaoyouNeural',
    );
    expect(
      ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
        '小理',
        ttsProvider: 'edge_tts',
      ),
      'zh-CN-YunyangNeural',
    );
  });

  test('role speech rate stays deterministic and thinker remains slower', () {
    final studentExcited = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      '小探',
      text: '哇，太棒了！这也太有意思了！',
    );
    final teacherNeutral = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      '李老师',
      text: '我们慢慢来，仔细想一想。',
    );
    final thinkerCalm = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      '韦伯先生',
      text: '也许我们可以再仔细想一想。',
    );

    expect(studentExcited, inInclusiveRange(0.8, 1.1));
    expect(teacherNeutral, inInclusiveRange(0.8, 1.1));
    expect(thinkerCalm, inInclusiveRange(0.8, 1.1));
    expect(studentExcited, greaterThan(teacherNeutral));
    expect(teacherNeutral, greaterThan(thinkerCalm));
    expect(
      ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        '小探',
        text: '哇，太棒了！这也太有意思了！',
      ),
      studentExcited,
    );
  });

  test('teacher classroom prompts stay more measured than short remarks', () {
    final teacherMeasured = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      '李老师',
      text: '同学们，我们先别着急，慢慢来，一步一步把这个问题想清楚，好吗？',
    );
    final teacherBrief = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      '李老师',
      text: '今天我们讨论分数。',
    );

    expect(teacherMeasured, inInclusiveRange(0.8, 1.1));
    expect(teacherBrief, inInclusiveRange(0.8, 1.1));
    expect(teacherMeasured, lessThan(teacherBrief));
    expect(
      ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        '老师',
        text: '同学们，我们先别着急，慢慢来，一步一步把这个问题想清楚，好吗？',
      ),
      teacherMeasured,
    );
  });

  test('tts timeout leaves room for slow teacher playback', () {
    final teacherTimeout = ImmersiveSessionScreen.ttsPlaybackTimeoutFor(
      '同学们好，我是李老师。今天我们围坐在一起，要聊一个特别有意思的话题——分数能衡量学习吗？嗯，可能你们都有过这样的经历，考了高分特别开心，或者考砸了有点沮丧。但分数真的能完全代表我们学到的知识吗？我们先请小探同学来说说你的想法吧。',
      rate: 0.873,
    );
    final shortStudentTimeout = ImmersiveSessionScreen.ttsPlaybackTimeoutFor(
      '我觉得分数像温度计。',
      rate: 1.0,
    );

    expect(teacherTimeout, greaterThan(const Duration(seconds: 40)));
    expect(
        shortStudentTimeout, greaterThanOrEqualTo(const Duration(seconds: 30)));
    expect(teacherTimeout, greaterThan(shortStudentTimeout));
  });
}
