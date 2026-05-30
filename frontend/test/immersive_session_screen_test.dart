import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

  test(
      'ready prompt in auto mode keeps mic on standby instead of auto-starting',
      () {
    expect(
      ImmersiveSessionScreen.humanTurnReadyPrompt(
        humanName: '豆苗',
        hotkeyLabel: 'Alt',
        autoOpenMic: true,
        isPushToTalk: false,
      ),
      allOf(
        contains('麦克风已经准备好'),
        isNot(contains('自动开启')),
      ),
    );
  });

  test('ready prompt keeps the named human cue direct and short', () {
    expect(
      ImmersiveSessionScreen.humanTurnReadyPrompt(
        humanName: '豆苗',
        hotkeyLabel: 'Alt',
        autoOpenMic: false,
        isPushToTalk: false,
      ),
      startsWith('李老师：豆苗，你怎么看？'),
    );
  });

  test('blank-area cards are centered within the available whitespace', () {
    expect(
      ImmersiveSessionScreen.centeredBlankAreaLeft(
        areaLeft: 24,
        areaRight: 324,
        childWidth: 180,
      ),
      84,
    );

    expect(
      ImmersiveSessionScreen.centeredBlankAreaLeft(
        areaLeft: 24,
        areaRight: 180,
        childWidth: 220,
      ),
      24,
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

  test('session payload participant names stay stable and skip duplicates', () {
    expect(
      ImmersiveSessionScreen.extractParticipantNamesFromSessionPayload([
        {'name': '李老师'},
        {'name': '巴甫洛夫'},
        {'name': '豆苗'},
        {'name': '巴甫洛夫'},
      ]),
      ['李老师', '巴甫洛夫', '豆苗'],
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

  test('right option mic hotkey does not include control keys', () {
    final keys = ImmersiveSessionScreen.micHotkeyLogicalKeys('right_alt');

    expect(keys, contains(LogicalKeyboardKey.altRight));
    expect(keys, isNot(contains(LogicalKeyboardKey.controlLeft)));
    expect(keys, isNot(contains(LogicalKeyboardKey.controlRight)));
  });

  test('invalid mic hotkey falls back to right option', () {
    expect(
      ImmersiveSessionScreen.micHotkeyLogicalKeys('legacy_ctrl'),
      {LogicalKeyboardKey.altRight},
    );
  });

  test('pending human turn waits only for active or current-speaker playback',
      () {
    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: false,
        hasQueuedSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: true,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: false,
        hasQueuedSpeech: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: true,
        hasQueuedSpeech: true,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: false,
        hasQueuedSpeech: true,
      ),
      isFalse,
    );
  });

  test('single mic defers speaker switch until queued speech is finished', () {
    expect(
      ImmersiveSessionScreen.shouldDeferTurnSwitchForSingleMic(
        speakerChanged: true,
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedSpeech: true,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldDeferTurnSwitchForSingleMic(
        speakerChanged: true,
        ttsPlaying: true,
        ttsServiceSpeaking: false,
        hasQueuedSpeech: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldDeferTurnSwitchForSingleMic(
        speakerChanged: false,
        ttsPlaying: true,
        ttsServiceSpeaking: true,
        hasQueuedSpeech: true,
      ),
      isFalse,
    );
  });

  test('human review waits until main room speech is fully settled', () {
    final now = DateTime(2026, 4, 28, 12);

    expect(
      ImmersiveSessionScreen.shouldWaitForHumanReviewAfterMainSpeech(
        ttsPlaying: true,
        ttsPumpRunning: false,
        ttsServiceSpeaking: false,
        browserFallbackSpeaking: false,
        hasQueuedSpeech: false,
        hasActiveTtsItem: false,
        lastActivityAt: now.subtract(const Duration(seconds: 5)),
        now: now,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldWaitForHumanReviewAfterMainSpeech(
        ttsPlaying: false,
        ttsPumpRunning: false,
        ttsServiceSpeaking: false,
        browserFallbackSpeaking: false,
        hasQueuedSpeech: true,
        hasActiveTtsItem: false,
        lastActivityAt: now.subtract(const Duration(seconds: 5)),
        now: now,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldWaitForHumanReviewAfterMainSpeech(
        ttsPlaying: false,
        ttsPumpRunning: false,
        ttsServiceSpeaking: false,
        browserFallbackSpeaking: false,
        hasQueuedSpeech: false,
        hasActiveTtsItem: false,
        lastActivityAt: now.subtract(const Duration(milliseconds: 600)),
        now: now,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldWaitForHumanReviewAfterMainSpeech(
        ttsPlaying: false,
        ttsPumpRunning: false,
        ttsServiceSpeaking: false,
        browserFallbackSpeaking: false,
        hasQueuedSpeech: false,
        hasActiveTtsItem: false,
        lastActivityAt: now.subtract(const Duration(seconds: 2)),
        now: now,
      ),
      isFalse,
    );
  });

  test('human review auto read only runs once per same review', () {
    expect(
      ImmersiveSessionScreen.shouldAutoReadHumanReview(
        review: '你刚才那个比喻很有意思。',
        lastAutoReadReview: '',
        isMuted: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldAutoReadHumanReview(
        review: '你刚才那个比喻很有意思。',
        lastAutoReadReview: '你刚才那个比喻很有意思。',
        isMuted: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldAutoReadHumanReview(
        review: '你刚才那个比喻很有意思。',
        lastAutoReadReview: '',
        isMuted: true,
      ),
      isFalse,
    );
  });

  test('concurrent human turn signals merge into one user turn', () {
    expect(
      ImmersiveSessionScreen.shouldMergeConcurrentHumanTurnSignals(
        requestedSpeaker: '豆苗',
        humanName: '豆苗',
        isMyTurn: false,
        pendingHumanTurn: true,
        handApprovedToSpeak: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldMergeConcurrentHumanTurnSignals(
        requestedSpeaker: '豆苗',
        humanName: '豆苗',
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: true,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldMergeConcurrentHumanTurnSignals(
        requestedSpeaker: '李老师',
        humanName: '豆苗',
        isMyTurn: false,
        pendingHumanTurn: true,
        handApprovedToSpeak: true,
      ),
      isFalse,
    );
  });

  test('pending human turn never hard-cuts current speaker queue', () {
    expect(
      ImmersiveSessionScreen.shouldYieldQueuedSpeechForHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: true,
      ),
      isFalse,
    );
  });

  test('raise hand is suppressed once human turn is already reserved', () {
    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: true,
        handApprovedToSpeak: false,
        hasRaisedHand: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: true,
        hasRaisedHand: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: false,
        hasRaisedHand: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: false,
        hasRaisedHand: false,
        completedSpeaker: '豆苗',
      ),
      isTrue,
    );
  });

  test('raise hand stays dim during one-turn cooldown after user finished', () {
    // 规则3：用户发言结束后再过一个非人类轮次后举手按钮才可重新启用。
    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: false,
        hasRaisedHand: false,
        raiseHandCooldownTurns: 1,
      ),
      isTrue,
    );
    expect(
      ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
        isMyTurn: false,
        pendingHumanTurn: false,
        handApprovedToSpeak: false,
        hasRaisedHand: false,
        raiseHandCooldownTurns: 0,
      ),
      isFalse,
    );
  });

  test('inter-speaker pause only applies between AI speakers', () {
    expect(
      ImmersiveSessionScreen.shouldInsertInterSpeakerPause(
        currentSpeaker: '李老师',
        nextSpeaker: '巴甫洛夫',
        humanName: '豆苗',
        pendingHumanTurn: false,
        isMyTurn: false,
        isRecording: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldInsertInterSpeakerPause(
        currentSpeaker: '巴甫洛夫',
        nextSpeaker: '豆苗',
        humanName: '豆苗',
        pendingHumanTurn: false,
        isMyTurn: false,
        isRecording: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldInsertInterSpeakerPause(
        currentSpeaker: '李老师',
        nextSpeaker: '巴甫洛夫',
        humanName: '豆苗',
        pendingHumanTurn: true,
        isMyTurn: false,
        isRecording: false,
      ),
      isFalse,
    );
  });

  test('inter-speaker pause duration stays within one to three seconds', () {
    final values = <int>{
      for (var i = 0; i < 9; i++)
        ImmersiveSessionScreen.interSpeakerPauseDuration(
          currentSpeaker: '李老师',
          nextSpeaker: '巴甫洛夫',
          turnSeed: i,
        ).inSeconds,
    };

    expect(values.every((value) => value >= 1 && value <= 3), isTrue);
    expect(values.length >= 2, isTrue);
  });

  test('subtitle lead-in stays synchronized with AI voice playback', () {
    expect(
      ImmersiveSessionScreen.aiSubtitleLeadIn,
      Duration.zero,
    );
  });

  testWidgets('ending quotes exit clears stack and returns home',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        initialRoute: '/session',
        routes: {
          ImmersiveSessionScreen.homeRouteName: (_) =>
              const Scaffold(body: Text('home-screen')),
          '/session': (context) => Scaffold(
                body: Center(
                  child: FilledButton(
                    onPressed: () =>
                        ImmersiveSessionScreen.exitEndingQuotesToHome(context),
                    child: const Text('exit-ending-quotes'),
                  ),
                ),
              ),
        },
      ),
    );

    expect(find.text('exit-ending-quotes'), findsOneWidget);

    await tester.tap(find.text('exit-ending-quotes'));
    await tester.pumpAndSettle();

    expect(find.text('home-screen'), findsOneWidget);
    expect(find.text('exit-ending-quotes'), findsNothing);
  });

  test('thinker handoff pauses are longer than ordinary AI handoffs', () {
    final teacherToStudent = ImmersiveSessionScreen.interSpeakerPauseDuration(
      currentSpeaker: '李老师',
      nextSpeaker: '小探',
      turnSeed: 0,
    ).inSeconds;
    final teacherToThinker = ImmersiveSessionScreen.interSpeakerPauseDuration(
      currentSpeaker: '李老师',
      nextSpeaker: '巴甫洛夫',
      turnSeed: 0,
    ).inSeconds;

    expect(teacherToStudent >= 1 && teacherToStudent <= 2, isTrue);
    expect(teacherToThinker >= 2 && teacherToThinker <= 3, isTrue);
  });

  test('queued tail is preserved for current speaker before human turn', () {
    expect(
      ImmersiveSessionScreen.shouldYieldQueuedSpeechForHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: true,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldYieldQueuedSpeechForHumanTurn(
        ttsPlaying: true,
        ttsServiceSpeaking: false,
        hasQueuedCurrentSpeakerSpeech: true,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldYieldQueuedSpeechForHumanTurn(
        ttsPlaying: false,
        ttsServiceSpeaking: true,
        hasQueuedCurrentSpeakerSpeech: true,
      ),
      isFalse,
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

  test('incoming ai speech clears completed human turn marker', () {
    expect(
      ImmersiveSessionScreen.shouldResetCompletedHumanTurnOnIncomingSpeech(
        source: '李老师',
        humanName: '豆苗',
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldResetCompletedHumanTurnOnIncomingSpeech(
        source: '豆苗',
        humanName: '豆苗',
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldResetCompletedHumanTurnOnIncomingSpeech(
        source: '系统',
        humanName: '豆苗',
        msgType: 'system',
      ),
      isFalse,
    );
  });

  test('human state text is ignored while finalizing or after speaker moved on',
      () {
    expect(
      ImmersiveSessionScreen.shouldApplyHumanStateStatus(
        newState: 'human_turn_waiting',
        humanName: '豆苗',
        currentSpeaker: '豆苗',
        isMyTurn: false,
        isRecording: false,
        isCompletingHumanTurn: true,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldApplyHumanStateStatus(
        newState: 'human_speaking',
        humanName: '豆苗',
        currentSpeaker: '苏格拉底',
        isMyTurn: false,
        isRecording: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldApplyHumanStateStatus(
        newState: 'human_speaking',
        humanName: '豆苗',
        currentSpeaker: '豆苗',
        isMyTurn: true,
        isRecording: true,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldApplyHumanStateStatus(
        newState: 'human_turn_waiting',
        humanName: '豆苗',
        currentSpeaker: '豆苗',
        isMyTurn: false,
        isRecording: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldApplyHumanStateStatus(
        newState: 'human_turn_waiting',
        humanName: '豆苗',
        currentSpeaker: '豆苗',
        isMyTurn: true,
        isRecording: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
        hasStartedSpeechThisTurn: true,
      ),
      isFalse,
    );
  });

  test('turn countdown stops once speech has begun or turn is finishing', () {
    expect(
      ImmersiveSessionScreen.shouldKeepTurnCountdownActive(
        isMyTurn: true,
        hasSpeechDraft: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldKeepTurnCountdownActive(
        isMyTurn: true,
        hasSpeechDraft: true,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldKeepTurnCountdownActive(
        isMyTurn: false,
        hasSpeechDraft: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldKeepTurnCountdownActive(
        isMyTurn: true,
        hasSpeechDraft: false,
        isCompletingHumanTurn: true,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );
  });

  test('human turn prompt cue is hidden while speech is finalizing', () {
    expect(
      ImmersiveSessionScreen.shouldShowHumanTurnPromptCue(
        isMyTurn: true,
        isRecording: false,
        isCompletingHumanTurn: true,
        isFinalizingSpeech: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldShowHumanTurnPromptCue(
        isMyTurn: true,
        isRecording: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: true,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldShowHumanTurnPromptCue(
        isMyTurn: true,
        isRecording: false,
        isCompletingHumanTurn: false,
        isFinalizingSpeech: false,
      ),
      isTrue,
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

  test('human subtitle switches to floating style with larger type', () {
    expect(
      ImmersiveSessionScreen.shouldUseFloatingHumanSubtitle(
        currentCenterSpeaker: '豆苗',
        humanName: '豆苗',
      ),
      isTrue,
    );
    expect(
      ImmersiveSessionScreen.shouldUseFloatingHumanSubtitle(
        currentCenterSpeaker: '李老师',
        humanName: '豆苗',
      ),
      isFalse,
    );
    expect(
      ImmersiveSessionScreen.subtitleFontSizeForSpeaker(
        baseFontSize: 22,
        isHumanSpeaker: true,
      ),
      26,
    );
  });

  test('human subtitle stays visible until AI playback actually starts', () {
    expect(
      ImmersiveSessionScreen.shouldSwapSubtitleBeforeAiPlaybackStarts(
        currentCenterSpeaker: '豆苗',
        humanName: '豆苗',
        playbackStarted: false,
      ),
      isFalse,
    );

    expect(
      ImmersiveSessionScreen.shouldSwapSubtitleBeforeAiPlaybackStarts(
        currentCenterSpeaker: '豆苗',
        humanName: '豆苗',
        playbackStarted: true,
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.shouldSwapSubtitleBeforeAiPlaybackStarts(
        currentCenterSpeaker: '李老师',
        humanName: '豆苗',
        playbackStarted: false,
      ),
      isTrue,
    );
  });

  test('teacher and students keep a gentler speech rate', () {
    expect(
      ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        '李老师',
        text: '同学们，我们先看看这个问题。',
      ),
      lessThanOrEqualTo(0.91),
    );

    expect(
      ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        '小明',
        text: '我觉得可以先试试看。',
      ),
      lessThanOrEqualTo(0.94),
    );

    expect(
      ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        '小爱',
        text: '我觉得可以先试试看。',
      ),
      lessThanOrEqualTo(0.97),
    );
  });

  test('tts payload normalization splits sentences and keeps unfinished tail',
      () {
    expect(
      ImmersiveSessionScreen.splitTtsSentenceUnits('“先想一想。”然后再回答'),
      ['“先想一想。”', '然后再回答'],
    );

    expect(
      ImmersiveSessionScreen.normalizeTtsSegmentsPayload(
        rawSegments: ['第一句。第二句', '第三句？'],
      ),
      ['第一句。', '第二句', '第三句？'],
    );

    expect(
      ImmersiveSessionScreen.normalizeTtsSegmentsPayload(
        rawSegments: '',
        fallbackText: '补一句。再补一句',
      ),
      ['补一句。', '再补一句'],
    );
  });

  test('message tts tail is derived from prior streamed segments', () {
    expect(
      ImmersiveSessionScreen.deriveRemainingTtsTextAfterStream(
        messageText: '同学们好！我是李老师。今天我们来讨论语言学习。',
        streamedSegments: ['同学们好！', '我是李老师。'],
      ),
      '今天我们来讨论语言学习。',
    );

    expect(
      ImmersiveSessionScreen.deriveRemainingTtsTextAfterStream(
        messageText: '同学们好！我是李老师。',
        streamedSegments: ['同学们好！', '我是李老师。'],
        fallbackTtsText: '同学们好！我是李老师。',
      ),
      '',
    );

    expect(
      ImmersiveSessionScreen.deriveRemainingTtsTextAfterStream(
        messageText: '（兴奋地举手）李老师！这个问题我昨天也想过。',
        streamedSegments: ['李老师！'],
        fallbackTtsText: '这个问题我昨天也想过。',
      ),
      '这个问题我昨天也想过。',
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

  test('human review requires a real user contribution', () {
    final messages = <ChatMessage>[
      const ChatMessage(source: '李老师', content: '同学们好，我们聊聊分数。'),
      const ChatMessage(source: '小探', content: '我觉得分数像温度计。'),
      const ChatMessage(source: '豆苗', content: '我想知道会做题是不是就算真懂。'),
      const ChatMessage(source: '小理', content: '如果能讲清楚原因，才更像真懂。'),
    ];

    expect(
      ImmersiveSessionScreen.hasHumanReviewMaterial(
        messages,
        humanName: '豆苗',
      ),
      isTrue,
    );

    expect(
      ImmersiveSessionScreen.hasHumanReviewMaterial(
        messages.where((message) => message.source != '豆苗'),
        humanName: '豆苗',
      ),
      isFalse,
    );
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
