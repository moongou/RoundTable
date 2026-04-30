import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_fonts/google_fonts.dart';

import '../../models/config_models.dart';
import '../../models/discussion_models.dart';
import '../../painters/bookshelf_painter.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../services/speech_service.dart';
import '../../services/websocket_client.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
import 'chat_history_drawer.dart';
import 'glass_control_bar.dart';
import 'immersive_session_text_utils.dart';
import 'session_frontend_commander.dart';
import 'table_participant_ring.dart';

enum _OpeningCueState { preparing, ready, done }

/// 沉浸式讨论界面 - 圆桌围坐体验
class ImmersiveSessionScreen extends ConsumerStatefulWidget {
  static const String defaultServerUrl = 'http://localhost:8001';
  static const String homeRouteName = '/home';
  static const String defaultAsrProvider = 'funasr';
  static const String defaultTtsProvider = 'edge_tts';
  static const Duration humanTurnAutoSkipWindow = Duration(seconds: 30);
  static const Duration aiSubtitleLeadIn = Duration.zero;
  static const Duration openingStartCueMinDuration =
      Duration(milliseconds: 420);
  static const int minGoldenQuoteMessages = 4;
  static const int minGoldenQuoteSpeakers = 3;
  static const String teacherDisplayName = '李老师';
  static const Set<String> _teacherSpeakerAliases = <String>{
    teacherDisplayName,
    '老师',
  };
  static const String _edgeTeacherVoice = 'zh-CN-XiaoxiaoNeural';
  static const String _edgeThinkerVoice = 'zh-CN-YunzeNeural';
  static const String _openVoiceTeacherProfile = 'ov:teacher_li';
  static const String _openVoiceThinkerProfile = 'ov:thinker_elder';
  static const Set<String> _ttsSentenceEndings = <String>{
    '。',
    '！',
    '？',
    '!',
    '?',
    '；',
    ';',
  };
  static const Set<String> _ttsSentenceClosers = <String>{
    '"',
    '\'',
    '”',
    '’',
    ')',
    '）',
    ']',
    '】',
    '》',
    '」',
    '』',
  };
  static const Set<String> _femaleStudentSpeakers = <String>{
    '小疑',
    '小爱',
    '小说',
    '小想',
  };
  static const Map<String, String> _edgeStudentVoices = <String, String>{
    '小探': 'zh-CN-YunxiNeural',
    '小疑': 'zh-CN-XiaoyiNeural',
    '小和': 'zh-CN-YunhaoNeural',
    '小说': 'zh-CN-XiaohanNeural',
    '小明': 'zh-CN-YunjieNeural',
    '小思': 'zh-CN-YunxiaNeural',
    '小理': 'zh-CN-YunyangNeural',
    '小爱': 'zh-CN-XiaoyouNeural',
    '小想': 'zh-CN-XiaoxuanNeural',
    '小行': 'zh-CN-YunfengNeural',
    '可乐': 'zh-CN-YunxiNeural',
  };
  static const Map<String, String> _openVoiceStudentProfiles = <String, String>{
    '小探': 'ov:student_xiaotan',
    '小疑': 'ov:student_xiaoyi',
    '小和': 'ov:student_xiaohe',
    '小说': 'ov:student_xiaoshuo',
    '小明': 'ov:student_xiaoming',
    '小思': 'ov:student_xiaosi',
    '小理': 'ov:student_xiaoli',
    '小爱': 'ov:student_xiaoai',
    '小想': 'ov:student_xiaoxiang',
    '小行': 'ov:student_xiaoxing',
    '可乐': 'ov:student_xiaotan',
  };

  static bool shouldDeferVoiceServiceRebind({
    required bool isRecording,
    required bool isSpeaking,
    required bool hasQueuedPlayback,
  }) {
    return isRecording || isSpeaking || hasQueuedPlayback;
  }

  static bool canTapSpeakButton({
    required bool canSpeakNow,
    required bool ctrlHeld,
    required bool isRecording,
    required bool recordingControlledByHoldCtrl,
  }) {
    if (!canSpeakNow) {
      return false;
    }
    if (recordingControlledByHoldCtrl) {
      return false;
    }
    if (ctrlHeld && !isRecording) {
      return false;
    }
    return true;
  }

  static Set<LogicalKeyboardKey> micHotkeyLogicalKeys(String? hotkeyId) {
    switch (hotkeyId?.trim()) {
      case 'left_alt':
        return {LogicalKeyboardKey.altLeft};
      case 'right_alt':
        return {LogicalKeyboardKey.altRight};
      case 'any_alt':
        return {LogicalKeyboardKey.altLeft, LogicalKeyboardKey.altRight};
      case 'f12':
        return {LogicalKeyboardKey.f12};
      case 'left_ctrl':
        return {LogicalKeyboardKey.controlLeft};
      case 'right_ctrl':
        return {LogicalKeyboardKey.controlRight};
      case 'space':
        return {LogicalKeyboardKey.space};
      default:
        return {LogicalKeyboardKey.altRight};
    }
  }

  static Set<PhysicalKeyboardKey> micHotkeyPhysicalKeys(String? hotkeyId) {
    switch (hotkeyId?.trim()) {
      case 'left_alt':
        return {PhysicalKeyboardKey.altLeft};
      case 'right_alt':
        return {PhysicalKeyboardKey.altRight};
      case 'any_alt':
        return {PhysicalKeyboardKey.altLeft, PhysicalKeyboardKey.altRight};
      case 'f12':
        return {PhysicalKeyboardKey.f12};
      case 'left_ctrl':
        return {PhysicalKeyboardKey.controlLeft};
      case 'right_ctrl':
        return {PhysicalKeyboardKey.controlRight};
      case 'space':
        return {PhysicalKeyboardKey.space};
      default:
        return {PhysicalKeyboardKey.altRight};
    }
  }

  static bool shouldBlockPendingHumanTurn({
    required bool ttsPlaying,
    required bool ttsServiceSpeaking,
    required bool hasQueuedCurrentSpeakerSpeech,
    required bool hasQueuedSpeech,
  }) {
    return ttsPlaying ||
        ttsServiceSpeaking ||
        hasQueuedCurrentSpeakerSpeech ||
        hasQueuedSpeech;
  }

  static bool shouldDeferTurnSwitchForSingleMic({
    required bool speakerChanged,
    required bool ttsPlaying,
    required bool ttsServiceSpeaking,
    required bool hasQueuedSpeech,
  }) {
    return speakerChanged &&
        (ttsPlaying || ttsServiceSpeaking || hasQueuedSpeech);
  }

  static bool shouldWaitForHumanReviewAfterMainSpeech({
    required bool ttsPlaying,
    required bool ttsPumpRunning,
    required bool ttsServiceSpeaking,
    required bool browserFallbackSpeaking,
    required bool hasQueuedSpeech,
    required bool hasActiveTtsItem,
    required DateTime? lastActivityAt,
    required DateTime now,
    Duration quietPeriod = const Duration(milliseconds: 1600),
  }) {
    final fullyIdle = !ttsPlaying &&
        !ttsPumpRunning &&
        !ttsServiceSpeaking &&
        !browserFallbackSpeaking &&
        !hasQueuedSpeech &&
        !hasActiveTtsItem;
    if (!fullyIdle) {
      return true;
    }
    if (lastActivityAt == null) {
      return false;
    }
    return now.difference(lastActivityAt) < quietPeriod;
  }

  static bool shouldYieldQueuedSpeechForHumanTurn({
    required bool ttsPlaying,
    required bool ttsServiceSpeaking,
    required bool hasQueuedCurrentSpeakerSpeech,
  }) {
    return false;
  }

  static bool shouldIgnoreRepeatedHumanInputRequest({
    required String requestedSpeaker,
    required String completedSpeaker,
  }) {
    if (requestedSpeaker.isEmpty || completedSpeaker.isEmpty) {
      return false;
    }
    return normalizeSpeakerLabel(requestedSpeaker) ==
        normalizeSpeakerLabel(completedSpeaker);
  }

  static bool shouldMergeConcurrentHumanTurnSignals({
    required String requestedSpeaker,
    required String humanName,
    required bool isMyTurn,
    required bool pendingHumanTurn,
    required bool handApprovedToSpeak,
  }) {
    final normalizedRequested = normalizeSpeakerLabel(requestedSpeaker);
    final normalizedHuman = normalizeSpeakerLabel(humanName);
    if (normalizedRequested.isEmpty || normalizedRequested != normalizedHuman) {
      return false;
    }
    return isMyTurn || pendingHumanTurn || handApprovedToSpeak;
  }

  static bool shouldSuppressRaiseHandRequest({
    required bool isMyTurn,
    required bool pendingHumanTurn,
    required bool handApprovedToSpeak,
    required bool hasRaisedHand,
  }) {
    return hasRaisedHand || isMyTurn || pendingHumanTurn || handApprovedToSpeak;
  }

  static bool shouldResetCompletedHumanTurnOnIncomingSpeech({
    required String source,
    required String humanName,
    String msgType = 'text',
  }) {
    final normalizedSource = normalizeSpeakerLabel(source);
    final normalizedHuman = normalizeSpeakerLabel(humanName);
    if (normalizedSource.isEmpty || normalizedSource == normalizedHuman) {
      return false;
    }
    return msgType != 'system';
  }

  static bool shouldApplyHumanStateStatus({
    required String newState,
    required String humanName,
    required String currentSpeaker,
    required bool isMyTurn,
    required bool isRecording,
    required bool isCompletingHumanTurn,
    required bool isFinalizingSpeech,
    bool hasStartedSpeechThisTurn = false,
  }) {
    if (newState != 'human_turn_waiting' && newState != 'human_speaking') {
      return true;
    }
    if (isCompletingHumanTurn ||
        isFinalizingSpeech ||
        (newState == 'human_turn_waiting' && hasStartedSpeechThisTurn)) {
      return false;
    }

    final normalizedHuman = normalizeSpeakerLabel(humanName);
    final normalizedCurrent = normalizeSpeakerLabel(currentSpeaker);

    if (newState == 'human_speaking') {
      return isMyTurn ||
          isRecording ||
          (normalizedCurrent.isNotEmpty &&
              normalizedCurrent == normalizedHuman);
    }

    return isMyTurn;
  }

  static bool shouldKeepTurnCountdownActive({
    required bool isMyTurn,
    required bool hasSpeechDraft,
    required bool isCompletingHumanTurn,
    required bool isFinalizingSpeech,
  }) {
    return isMyTurn &&
        !hasSpeechDraft &&
        !isCompletingHumanTurn &&
        !isFinalizingSpeech;
  }

  static bool shouldShowHumanTurnPromptCue({
    required bool isMyTurn,
    required bool isRecording,
    required bool isCompletingHumanTurn,
    required bool isFinalizingSpeech,
  }) {
    return isMyTurn &&
        !isRecording &&
        !isCompletingHumanTurn &&
        !isFinalizingSpeech;
  }

  static String humanTurnIdleReminderText() {
    return '还在等你开口；如果你暂时不想说，可以手动点“跳过”';
  }

  static String humanResponseBufferText() {
    return '大家都在品味你的发言……';
  }

  static bool shouldSwapSubtitleBeforeAiPlaybackStarts({
    required String currentCenterSpeaker,
    required String humanName,
    required bool playbackStarted,
  }) {
    if (playbackStarted) {
      return true;
    }
    return _canonicalSpeakerName(currentCenterSpeaker) !=
        _canonicalSpeakerName(humanName);
  }

  static Duration humanSubtitleHoldDurationFor(String text) {
    final chars = text.trim().runes.length;
    final holdMs = (chars * 65).clamp(1800, 5200).toInt();
    return Duration(milliseconds: holdMs);
  }

  static bool shouldInsertInterSpeakerPause({
    required String currentSpeaker,
    required String nextSpeaker,
    required String humanName,
    required bool pendingHumanTurn,
    required bool isMyTurn,
    required bool isRecording,
  }) {
    if (pendingHumanTurn || isMyTurn || isRecording) {
      return false;
    }

    final current = _canonicalSpeakerName(currentSpeaker);
    final next = _canonicalSpeakerName(nextSpeaker);
    final human = _canonicalSpeakerName(humanName);
    if (current.isEmpty || next.isEmpty || current == next) {
      return false;
    }

    final currentIsAi = current != '系统' && current != human;
    final nextIsAi = next != '系统' && next != human;
    if (!currentIsAi || !nextIsAi) {
      return false;
    }

    return true;
  }

  static Duration interSpeakerPauseDuration({
    required String currentSpeaker,
    required String nextSpeaker,
    required int turnSeed,
  }) {
    final current = _canonicalSpeakerName(currentSpeaker);
    final next = _canonicalSpeakerName(nextSpeaker);
    if (current.isEmpty || next.isEmpty || current == next) {
      return Duration.zero;
    }

    final seed = '$current|$next|$turnSeed';
    var hash = 23;
    for (final unit in seed.codeUnits) {
      hash = (hash * 41 + unit) & 0x7fffffff;
    }
    final thinkerInvolved = isThinkerSpeaker(current) || isThinkerSpeaker(next);
    final seconds = thinkerInvolved ? 2 + (hash % 2) : 1 + (hash % 2);
    return Duration(seconds: seconds);
  }

  static int subtitlePageAdvanceDelayMs({
    required String pageText,
    required bool isFirstPage,
    Duration? remainingHumanSubtitleHold,
    required int remainingPages,
  }) {
    if (remainingHumanSubtitleHold != null && remainingPages > 0) {
      final holdMs = remainingHumanSubtitleHold.inMilliseconds;
      final perPageMs = holdMs ~/ remainingPages;
      return perPageMs.clamp(900, isFirstPage ? 4200 : 3600);
    }
    return (pageText.length * (isFirstPage ? 220 : 180))
        .clamp(isFirstPage ? 3200 : 2400, isFirstPage ? 7600 : 5600)
        .toInt();
  }

  static List<String> splitTtsSentenceUnits(String text) {
    final value = text.trim();
    if (value.isEmpty) {
      return const <String>[];
    }

    final segments = <String>[];
    var start = 0;
    for (var index = 0; index < value.length; index++) {
      final char = value[index];
      if (!_ttsSentenceEndings.contains(char)) {
        continue;
      }
      var end = index + 1;
      while (end < value.length && _ttsSentenceClosers.contains(value[end])) {
        end += 1;
      }
      final segment = value.substring(start, end).trim();
      if (segment.isNotEmpty) {
        segments.add(segment);
      }
      while (end < value.length && value[end].trim().isEmpty) {
        end += 1;
      }
      start = end;
    }

    final tail = value.substring(start).trim();
    if (tail.isNotEmpty) {
      segments.add(tail);
    }
    return segments;
  }

  static List<String> normalizeTtsSegmentsPayload({
    Object? rawSegments,
    String fallbackText = '',
  }) {
    final segments = <String>[];
    if (rawSegments is List) {
      for (final item in rawSegments) {
        segments.addAll(splitTtsSentenceUnits(item.toString()));
      }
    } else if (rawSegments != null) {
      final normalized = rawSegments.toString().trim();
      if (normalized.isNotEmpty) {
        segments.addAll(splitTtsSentenceUnits(normalized));
      }
    } else if (fallbackText.trim().isNotEmpty) {
      segments.addAll(splitTtsSentenceUnits(fallbackText));
    }

    if (segments.isEmpty && fallbackText.trim().isNotEmpty) {
      segments.addAll(splitTtsSentenceUnits(fallbackText));
    }

    final normalized = <String>[];
    for (final segment in segments) {
      final trimmed = segment.trim();
      if (trimmed.isEmpty) {
        continue;
      }
      if (normalized.isNotEmpty && normalized.last == trimmed) {
        continue;
      }
      normalized.add(trimmed);
    }
    return normalized;
  }

  final Topic topic;
  final List<String> characterIds;
  final List<String> thinkerIds;
  final String humanName;
  final bool observerMode;

  const ImmersiveSessionScreen({
    super.key,
    required this.topic,
    required this.characterIds,
    this.thinkerIds = const [],
    required this.humanName,
    this.observerMode = false,
  });

  @override
  ConsumerState<ImmersiveSessionScreen> createState() =>
      _ImmersiveSessionScreenState();

  static bool hasGoldenQuoteMaterial(Iterable<ChatMessage> messages) {
    final filtered = messages
        .where(_isMeaningfulGoldenQuoteSourceMessage)
        .toList(growable: false);
    if (filtered.length < minGoldenQuoteMessages) {
      return false;
    }
    final speakers = filtered
        .map((message) => message.source.trim())
        .where((source) => source.isNotEmpty)
        .toSet();
    return speakers.length >= minGoldenQuoteSpeakers;
  }

  static bool hasHumanReviewMaterial(
    Iterable<ChatMessage> messages, {
    required String humanName,
  }) {
    final normalizedHuman = normalizeSpeakerLabel(humanName);
    if (normalizedHuman.isEmpty) {
      return false;
    }

    final filtered = messages
        .where(_isMeaningfulGoldenQuoteSourceMessage)
        .toList(growable: false);
    if (filtered.length < minGoldenQuoteMessages) {
      return false;
    }

    final speakers = filtered
        .map((message) => normalizeSpeakerLabel(message.source))
        .where((source) => source.isNotEmpty)
        .toSet();
    if (speakers.length < minGoldenQuoteSpeakers) {
      return false;
    }

    return filtered.any(
      (message) => normalizeSpeakerLabel(message.source) == normalizedHuman,
    );
  }

  static bool _isMeaningfulGoldenQuoteSourceMessage(ChatMessage message) {
    final source = message.source.trim();
    final content = message.content.trim();
    if (message.type == 'system' || source.isEmpty || source == '系统') {
      return false;
    }
    if (content.isEmpty || content == '（跳过）') {
      return false;
    }
    return true;
  }

  static bool shouldPresentEndingQuotesScreen({
    required bool discussionEnded,
    required bool hasGoldenQuotes,
    required bool manuallyRequested,
  }) {
    return discussionEnded && hasGoldenQuotes && manuallyRequested;
  }

  static void exitEndingQuotesToHome(BuildContext context) {
    Navigator.of(context)
        .pushNamedAndRemoveUntil(homeRouteName, (route) => false);
  }

  static String normalizeSpeakerLabel(String speaker) {
    var normalized = speaker.trim();
    if (normalized.isEmpty) {
      return '';
    }

    const wrappers = <String>['**', '__', '`', '*', '_'];
    var changed = true;
    while (changed) {
      changed = false;
      for (final wrapper in wrappers) {
        if (normalized.length <= wrapper.length * 2) {
          continue;
        }
        if (!normalized.startsWith(wrapper) || !normalized.endsWith(wrapper)) {
          continue;
        }
        final inner = normalized
            .substring(wrapper.length, normalized.length - wrapper.length)
            .trim();
        if (inner.isEmpty) {
          continue;
        }
        normalized = inner;
        changed = true;
      }
    }

    return normalized;
  }

  static List<String> extractParticipantNamesFromSessionPayload(
      Object? rawParticipants) {
    if (rawParticipants is! List) {
      return const <String>[];
    }
    final names = <String>[];
    final seen = <String>{};
    for (final item in rawParticipants) {
      if (item is! Map) {
        continue;
      }
      final rawName = item['name'];
      if (rawName == null) {
        continue;
      }
      final normalized = normalizeSpeakerLabel(rawName.toString());
      if (normalized.isEmpty || !seen.add(normalized)) {
        continue;
      }
      names.add(normalized);
    }
    return names;
  }

  static String _canonicalSpeakerName(String speaker) {
    final normalized = normalizeSpeakerLabel(speaker);
    if (_teacherSpeakerAliases.contains(normalized)) {
      return teacherDisplayName;
    }
    return normalized;
  }

  static bool isTeacherSpeaker(String speaker) =>
      _teacherSpeakerAliases.contains(_canonicalSpeakerName(speaker));

  static bool isStudentSpeaker(String speaker) =>
      _edgeStudentVoices.containsKey(_canonicalSpeakerName(speaker));

  static bool isThinkerSpeaker(String speaker) {
    final normalized = _canonicalSpeakerName(speaker);
    return normalized.isNotEmpty &&
        normalized != '系统' &&
        !isTeacherSpeaker(normalized) &&
        !isStudentSpeaker(normalized);
  }

  static String fixedTtsVoiceForSpeaker(
    String speaker, {
    required String ttsProvider,
    bool isThinker = false,
  }) {
    final normalized = _canonicalSpeakerName(speaker);
    final useOpenVoice = ttsProvider == 'openvoice';

    if (isTeacherSpeaker(normalized)) {
      return useOpenVoice ? _openVoiceTeacherProfile : _edgeTeacherVoice;
    }

    final studentVoice = useOpenVoice
        ? _openVoiceStudentProfiles[normalized]
        : _edgeStudentVoices[normalized];
    if (studentVoice != null) {
      return studentVoice;
    }

    if (isThinker || isThinkerSpeaker(normalized)) {
      return useOpenVoice ? _openVoiceThinkerProfile : _edgeThinkerVoice;
    }

    return useOpenVoice ? _openVoiceTeacherProfile : _edgeTeacherVoice;
  }

  static double fixedSpeechRateForSpeaker(
    String speaker, {
    required String text,
    bool isThinker = false,
  }) {
    final normalizedSpeaker = _canonicalSpeakerName(speaker);
    final normalizedText = text.trim();
    if (normalizedText.isEmpty) {
      return 1.0;
    }

    final teacher = isTeacherSpeaker(normalizedSpeaker);
    final student = isStudentSpeaker(normalizedSpeaker);
    final thinker = isThinker || (!teacher && !student);

    double baseRate;
    if (thinker) {
      baseRate = 0.80;
    } else if (teacher) {
      baseRate = 0.89;
    } else if (_femaleStudentSpeakers.contains(normalizedSpeaker)) {
      baseRate = 0.95;
    } else {
      baseRate = 0.92;
    }

    const excitedHints = <String>[
      '太棒了',
      '好极了',
      '真厉害',
      '激动',
      '加油',
      '哇',
      '真的吗',
      '不可思议',
      '超有意思',
      '我完全没想到',
    ];
    const calmHints = <String>[
      '慢慢来',
      '想一想',
      '仔细',
      '平静',
      '安静',
      '沉思',
      '也许',
      '或许',
      '思考',
      '观察',
    ];
    const teacherGuidingHints = <String>[
      '同学们',
      '我们先',
      '先别着急',
      '一步一步',
      '想清楚',
      '看一看',
      '听我说',
      '好吗',
      '请你',
      '先说说',
    ];

    var excitedScore = 0;
    var calmScore = 0;
    var teacherGuidingScore = 0;
    for (final hint in excitedHints) {
      if (normalizedText.contains(hint)) {
        excitedScore += 1;
      }
    }
    for (final hint in calmHints) {
      if (normalizedText.contains(hint)) {
        calmScore += 1;
      }
    }
    if (teacher) {
      for (final hint in teacherGuidingHints) {
        if (normalizedText.contains(hint)) {
          teacherGuidingScore += 1;
        }
      }
    }

    final bangCount = RegExp(r'[!！]').allMatches(normalizedText).length;
    final commaCount = RegExp(r'[，、；：]').allMatches(normalizedText).length;
    final questionCount = RegExp(r'[?？]').allMatches(normalizedText).length;
    final ellipsisCount =
        RegExp(r'(…|\.\.\.)').allMatches(normalizedText).length;

    double delta = _deterministicSpeechRateOffset(
      normalizedSpeaker,
      normalizedText,
    );

    if (thinker) {
      delta += excitedScore * 0.008;
      delta -= calmScore > 0 ? (0.02 + (calmScore - 1) * 0.01) : 0.0;
      delta += bangCount > 0 ? 0.005 : 0.0;
      delta -= ellipsisCount > 0 ? 0.01 : 0.0;
    } else if (teacher) {
      delta += excitedScore * 0.008;
      delta -= calmScore * 0.018;
      delta += bangCount * 0.004;
      delta -= ellipsisCount * 0.012;
      delta -= teacherGuidingScore * 0.008;
      delta -= commaCount >= 2 ? 0.012 + (commaCount - 2) * 0.003 : 0.0;
      delta -= questionCount > 0 ? 0.01 : 0.0;
      if (normalizedText.length >= 28) {
        delta -= 0.012;
      }
      if (normalizedText.length >= 48) {
        delta -= 0.008;
      }
    } else {
      delta += excitedScore * 0.028;
      delta -= calmScore * 0.018;
      delta += bangCount * 0.012;
      delta -= ellipsisCount * 0.008;
    }

    return (baseRate + delta).clamp(0.8, 1.05).toDouble();
  }

  static Duration ttsPlaybackTimeoutFor(
    String text, {
    required double rate,
  }) {
    final normalizedText = text.trim();
    if (normalizedText.isEmpty) {
      return const Duration(seconds: 30);
    }

    final estimatedPlaybackSec =
        (normalizedText.runes.length * 0.33 / rate).clamp(18.0, 150.0);
    final synthesisBufferSec = normalizedText.runes.length >= 48 ? 14.0 : 10.0;
    final totalSec =
        (estimatedPlaybackSec + synthesisBufferSec).clamp(30.0, 180.0);
    return Duration(milliseconds: (totalSec * 1000).round());
  }

  static double _deterministicSpeechRateOffset(String speaker, String text) {
    final seed = '$speaker|$text';
    var hash = 17;
    for (final unit in seed.codeUnits) {
      hash = (hash * 37 + unit) & 0x7fffffff;
    }
    return ((hash % 5) - 2) * 0.01;
  }
}

class _ImmersiveSessionScreenState extends ConsumerState<ImmersiveSessionScreen>
    with TickerProviderStateMixin {
  final DiscussionWebSocket _wsClient = DiscussionWebSocket();
  final ScrollController _scrollController = ScrollController();
  final FocusNode _keyboardFocusNode = FocusNode();
  final SessionFrontendCommander _commander = SessionFrontendCommander();

  // 讨论状态
  final List<ChatMessage> _messages = [];
  String _currentSpeaker = '';
  bool _isMyTurn = false;
  String _statusText = '连接中...';
  String _discussionSessionId = '';
  _OpeningCueState _openingCueState = _OpeningCueState.preparing;
  DateTime? _openingReadyShownAt;
  bool _hasRaisedHand = false;
  bool _isPaused = false;
  DateTime? _discussionStartedAt;
  DateTime? _discussionPausedAt;
  Duration _discussionPausedAccumulated = Duration.zero;
  Duration _discussionElapsed = Duration.zero;
  Timer? _discussionClockTimer;
  // 需求4：用户发言完毕后，麦克风应立即置灰，直到下轮发言或举手经同意
  bool _micLocked = false;
  // 讨论结束后由后端 LLM 提炼出的金句。
  final List<String> _goldenQuotes = [];
  String _humanReview = '';
  bool _discussionEnded = false;
  bool _isGeneratingHumanReview = false;
  bool _humanReviewMuted = false;
  bool _humanReviewOverlayVisible = false;
  bool _humanReviewPrefetchStarted = false;
  bool _teacherFarewellHeard = false;
  bool _showEndingQuotesScreen = false;
  bool _mountEndingQuotesOverlay = false;
  bool _quickFeedbackVisible = false;
  String _quickFeedbackText = '';
  bool _teacherReplyWarmupVisible = false;
  bool _awaitingTeacherFeedbackMetric = false;
  DateTime? _pendingTeacherReplySeenAt;
  int? _pendingTeacherReplyEventSeq;
  final Map<String, ({DateTime replySeenAt, int? eventSeq})>
      _ttsFirstAudioPendingBySession = {};
  // TTS 顺序播放队列 (i)
  final List<
      ({
        String source,
        String text,
        String? voice,
        String playbackSessionId,
        DateTime enqueuedAt,
      })> _ttsQueue = [];
  bool _ttsPlaying = false;
  ({
    String source,
    String text,
    String? voice,
    String playbackSessionId,
    DateTime enqueuedAt,
  })? _activeTtsItem;
  ({
    String source,
    String text,
    String? voice,
  })? _pausedResumeTtsItem;

  /// Debug-only accessor so static analysis sees a read of the latent field.
  // ignore: unused_element
  String get _activeTtsSource => _activeTtsItem?.source ?? '';
  int _ttsSessionSeq = 0;
  String _activeTtsSessionId = '';
  bool _ttsPumpRunning = false;
  bool _humanReviewReadAloudActive = false;
  DateTime? _lastMainTtsQueuedAt;
  DateTime? _lastMainTtsCompletedAt;
  int _ttsSpeakerPauseCounter = 0;
  // 错误追踪：如果先收到错误事件，结束时显示错误原因而非"讨论已结束"
  String? _lastErrorMessage;
  // Thinking indicator state (f)
  bool _isThinking = false;
  // Streaming STT text (bug 3)
  String _sttPartialText = '';
  String _lastNonEmptySttText = '';
  int _ctrlTapCount = 0;
  DateTime? _lastCtrlTapAt;
  Timer? _ctrlTapTimer;
  bool _ctrlHeld = false;
  bool _recordingControlledByHoldCtrl = false;
  Timer? _speechFinalizeTimer;
  bool _isFinalizingSpeech = false; // 防止多次快速按热键导致并发 finalize
  bool _speechFinalizeRunning = false;
  bool _isCompletingHumanTurn = false;
  bool _hasStartedSpeechThisTurn = false;
  bool _awaitingAiResponseAfterHumanSubmit = false;
  bool get _pendingHumanTurn => _commander.pendingHumanTurn;
  String get _pendingHumanSpeaker => _commander.pendingHumanSpeaker;
  bool get _handApprovedToSpeak => _commander.handApprovedToSpeak;
  // User turn countdown timer (bug 3)
  int _turnCountdown = 0;
  // 3分钟最大发言计时器（需求2）
  Timer? _maxSpeechTimer;
  DateTime? _speechStartTime;
  // 保留字段：当前会话已切换为纯语音输入模式
  // 用户发言后字幕保留计时器（需求2）
  Timer? _subtitleRetainTimer;
  DateTime? _humanSubtitleLockUntil;
  Timer? _humanSubtitleLockTimer;
  Timer? _pendingHumanTurnGuardTimer;
  Timer? _ttsPumpGuardTimer;
  Timer? _humanResponseWatchdogTimer;
  Timer? _deferredAutoSkipTimer;
  Timer? _autoMicStartTimer;

  // 参与者
  List<SeatedParticipant> _participants = [];

  // 中心消息
  String _centerMessage = '';
  String _centerSpeaker = '';
  int _subtitleToken = 0;
  String _subtitleOwner = '';
  String _subtitleSessionId = '';
  int _lastEventSeq = 0;

  // 语音状态
  bool _isRecording = false;
  String _completedHumanTurnSpeaker = '';
  late TtsService _ttsService;
  late AsrService _asrService;
  TtsService? _browserFallbackTts;

  // 动画
  late AnimationController _candleController;
  late AnimationController _glowController;
  late AnimationController _micController;
  late AnimationController _thinkingController; // (f) thinking dots
  late AnimationController _endingQuotesTransitionController;
  Timer? _turnTimer; // (bug 3) user turn countdown
  List<CandleParticle>? _particles;
  OverlayEntry? _statusToastEntry;
  Timer? _statusToastTimer;
  Timer? _quickFeedbackTimer;
  Timer? _teacherReplyWarmupTimer;
  bool _disposed = false;
  int _bgTaskRunning = 0;
  final List<Future<void> Function()> _bgTaskQueue = [];
  DateTime _lastAsrWarmupAt = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _lastPrefetchAt = DateTime.fromMillisecondsSinceEpoch(0);
  bool _showPerfPanel = false;
  bool _showPhasePanel = false;
  int _ttsStartupSamples = 0;
  double _ttsStartupTotalMs = 0;
  DateTime? _asrListenStartAt;
  bool _awaitingAsrFirstPacket = false;
  int _asrFirstPacketSamples = 0;
  double _asrFirstPacketTotalMs = 0;
  String _asrProviderId = ImmersiveSessionScreen.defaultAsrProvider;
  String _ttsProviderId = ImmersiveSessionScreen.defaultTtsProvider;
  String _asrProviderUrl = '';
  String _ttsProviderUrl = '';
  String _speechServerUrl = ImmersiveSessionScreen.defaultServerUrl;
  bool _asrStreamingEnabled = true;
  bool _voiceServicesInitialized = false;
  ({
    String serverUrl,
    String ttsProvider,
    String asrProvider,
    String ttsProviderUrl,
    String asrProviderUrl,
    bool asrStreamingEnabled,
  })? _pendingVoiceConfig;
  StreamSubscription<AsrResult>? _asrTranscriptionSub;
  ProviderSubscription<AsyncValue<LocalSettings>>? _settingsSubscription;
  final List<double> _ttsStartupSeries = [];
  final List<double> _asrFirstPacketSeries = [];
  final List<double> _prefetchHitRateSeries = [];
  static const int _maxReportHistory = 12;
  final List<_PerfReportEntry> _reportHistory = [];
  static const int _maxPhaseTelemetryHistory = 80;
  final List<_PhaseTelemetryEntry> _phaseTelemetryHistory = [];
  DateTime? _pendingFeedbackOverlayMetricAt;

  String _resolveProviderUrl(
    SpeechConfig? speechConfig,
    String providerId, {
    required bool asr,
  }) {
    final providers =
        asr ? speechConfig?.asrProviders : speechConfig?.ttsProviders;
    for (final provider in providers ?? const <SpeechProviderInfo>[]) {
      if (provider.id == providerId) {
        return provider.url.isNotEmpty ? provider.url : provider.defaultUrl;
      }
    }
    return '';
  }

  String _preferAvailableChatTts(
    String requestedProvider,
    SpeechConfig? speechConfig,
  ) {
    if (requestedProvider != ImmersiveSessionScreen.defaultTtsProvider) {
      return requestedProvider;
    }
    for (final provider
        in speechConfig?.ttsProviders ?? const <SpeechProviderInfo>[]) {
      if (provider.id == 'chattts' && provider.available) {
        return 'chattts';
      }
    }
    return requestedProvider;
  }

  void _bindVoiceServices({
    required String serverUrl,
    required String ttsProvider,
    required String asrProvider,
    required String ttsProviderUrl,
    required String asrProviderUrl,
    required bool asrStreamingEnabled,
  }) {
    if (_voiceServicesInitialized &&
        _speechServerUrl == serverUrl &&
        _ttsProviderId == ttsProvider &&
        _ttsProviderUrl == ttsProviderUrl &&
        _asrProviderId == asrProvider &&
        _asrProviderUrl == asrProviderUrl &&
        _asrStreamingEnabled == asrStreamingEnabled) {
      return;
    }

    if (_voiceServicesInitialized) {
      _asrTranscriptionSub?.cancel();
      _asrTranscriptionSub = null;
      try {
        _ttsService.stop();
      } catch (_) {}
      try {
        _browserFallbackTts?.stop();
      } catch (_) {}
      try {
        _asrService.stopListening();
      } catch (_) {}
      _ttsService.dispose();
      _browserFallbackTts?.dispose();
      _asrService.dispose();
    }

    _speechServerUrl = serverUrl;
    _ttsProviderId = ttsProvider;
    _asrProviderId = asrProvider;
    _ttsProviderUrl = ttsProviderUrl;
    _asrProviderUrl = asrProviderUrl;
    _asrStreamingEnabled = asrStreamingEnabled;

    _ttsService = createTtsService(
      ttsProvider,
      serverUrl: serverUrl,
      providerUrl: ttsProviderUrl,
    );
    _asrService = createAsrService(
      asrProvider,
      serverUrl: serverUrl,
      providerUrl: asrProviderUrl,
      preferServerProxy: !asrStreamingEnabled &&
          asrProvider != 'browser' &&
          asrProvider != 'disabled',
    );
    // Do not replay server-side TTS failures through the browser voice layer.
    // It can duplicate the same sentence with an unrelated default voice.
    _browserFallbackTts = null;
    _reportAsrStatus(
      'initialized',
      available: _asrService.isAvailable,
      listening: _asrService.isListening,
    );
    _voiceServicesInitialized = true;
    if (_participants.isNotEmpty) {
      _buildParticipants();
    }

    _asrTranscriptionSub = _asrService.transcriptionStream.listen((result) {
      final chunk = result.text.trim();
      if (chunk.isEmpty) return;
      final shouldStreamUserSubtitles =
          ref.read(localSettingsProvider).valueOrNull?.streamUserSubtitles ??
              true;
      if (_awaitingAsrFirstPacket && _asrListenStartAt != null) {
        final firstPacketMs =
            DateTime.now().difference(_asrListenStartAt!).inMilliseconds;
        _awaitingAsrFirstPacket = false;
        _asrFirstPacketSamples += 1;
        _asrFirstPacketTotalMs += firstPacketMs.toDouble();
        _pushSeriesSample(_asrFirstPacketSeries, firstPacketMs.toDouble());
        _reportAsrStatus(
          'first_packet',
          textLen: chunk.length,
          isFinal: result.isFinal,
          listening: _asrService.isListening,
        );
      }
      _reportAsrStatus(
        result.isFinal ? 'chunk_final' : 'chunk_partial',
        textLen: chunk.length,
        isFinal: result.isFinal,
        listening: _asrService.isListening,
      );
      setState(() {
        _sttPartialText = result.isFinal
            ? chunk
            : mergeStreamingDraft(_sttPartialText, chunk);
        if (_sttPartialText.trim().isNotEmpty) {
          _lastNonEmptySttText = _sttPartialText.trim();
        }
        if (_isRecording && shouldStreamUserSubtitles) {
          _centerSpeaker = widget.humanName;
          _centerMessage = _sttPartialText;
        }
      });
      _debugSubtitleLog(triggerRole: widget.humanName, note: 'stt streaming');
      if (result.isFinal && !_isRecording && _isFinalizingSpeech) {
        unawaited(_flushSpeechFinalizeNow());
      }
    }, onError: (error) {
      _reportAsrStatus(
        'stream_error',
        listening: _asrService.isListening,
        error: error.toString(),
      );
    });

    _prepareUpcomingPipeline(reason: 'voice-init', includeAsrWarmup: true);
  }

  bool _hasActiveVoiceSession() {
    if (!_voiceServicesInitialized) {
      return false;
    }
    return ImmersiveSessionScreen.shouldDeferVoiceServiceRebind(
      isRecording: _isRecording,
      isSpeaking: _ttsService.isSpeaking,
      hasQueuedPlayback: _ttsPlaying || _activeTtsItem != null,
    );
  }

  Future<void> _applyPendingVoiceConfigIfIdle() async {
    final pending = _pendingVoiceConfig;
    if (pending == null || _hasActiveVoiceSession()) {
      return;
    }
    _pendingVoiceConfig = null;
    _bindVoiceServices(
      serverUrl: pending.serverUrl,
      ttsProvider: pending.ttsProvider,
      asrProvider: pending.asrProvider,
      ttsProviderUrl: pending.ttsProviderUrl,
      asrProviderUrl: pending.asrProviderUrl,
      asrStreamingEnabled: pending.asrStreamingEnabled,
    );
  }

  // 每次会话分配的参与者音色表（用于思想家的哈希分配）
  final Map<String, String> _voiceMap = {};

  String _resolveSpeakerVoice(
    String speaker, {
    bool isThinker = false,
  }) {
    final localSettings = ref.read(localSettingsProvider).valueOrNull;
    final configured = resolveConfiguredVoiceForSpeaker(
      settings: localSettings,
      providerId: _ttsProviderId,
      speaker: ImmersiveSessionScreen._canonicalSpeakerName(speaker),
    );
    final resolved = configured ??
        ImmersiveSessionScreen.fixedTtsVoiceForSpeaker(
          speaker,
          ttsProvider: _ttsProviderId,
          isThinker: isThinker,
        );
    _voiceMap[speaker] = resolved;
    return resolved;
  }

  String _observerSeatAvatarUrl(String seed) {
    final encoded = Uri.encodeComponent(seed);
    return 'https://api.dicebear.com/7.x/personas/png?seed=$encoded&size=128&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc';
  }

  String _observerSeatStatusLabel() {
    if (_isRecording) {
      return '发言中';
    }
    if (_isMyTurn || _handApprovedToSpeak || _pendingHumanTurn) {
      return '待发言';
    }
    if (_hasRaisedHand) {
      return '已举手';
    }
    return '旁听席';
  }

  Duration _computeDiscussionElapsedAt(DateTime now) {
    final startedAt = _discussionStartedAt;
    if (startedAt == null) return Duration.zero;
    var paused = _discussionPausedAccumulated;
    final pausedAt = _discussionPausedAt;
    if (pausedAt != null) {
      paused += now.difference(pausedAt);
    }
    final elapsed = now.difference(startedAt) - paused;
    return elapsed.isNegative ? Duration.zero : elapsed;
  }

  void _syncDiscussionElapsedNow({bool triggerSetState = false}) {
    final next = _computeDiscussionElapsedAt(DateTime.now());
    if (triggerSetState) {
      if (!mounted) return;
      setState(() => _discussionElapsed = next);
      return;
    }
    _discussionElapsed = next;
  }

  void _startDiscussionClockIfNeeded() {
    if (_discussionStartedAt != null) {
      _syncDiscussionElapsedNow(triggerSetState: mounted);
      return;
    }
    _discussionStartedAt = DateTime.now();
    _discussionPausedAt = null;
    _discussionPausedAccumulated = Duration.zero;
    _discussionElapsed = Duration.zero;
    _discussionClockTimer?.cancel();
    _discussionClockTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      _syncDiscussionElapsedNow(triggerSetState: true);
    });
  }

  void _pauseDiscussionClock() {
    if (_discussionStartedAt == null || _discussionPausedAt != null) {
      return;
    }
    _discussionPausedAt = DateTime.now();
    _syncDiscussionElapsedNow();
  }

  void _resumeDiscussionClock() {
    if (_discussionStartedAt == null) {
      return;
    }
    final pausedAt = _discussionPausedAt;
    if (pausedAt != null) {
      _discussionPausedAccumulated += DateTime.now().difference(pausedAt);
      _discussionPausedAt = null;
    }
    _syncDiscussionElapsedNow();
  }

  void _stopDiscussionClock({bool keepElapsed = true}) {
    if (keepElapsed) {
      _syncDiscussionElapsedNow();
    } else {
      _discussionElapsed = Duration.zero;
    }
    _discussionClockTimer?.cancel();
    _discussionClockTimer = null;
    if (!keepElapsed) {
      _discussionStartedAt = null;
      _discussionPausedAt = null;
      _discussionPausedAccumulated = Duration.zero;
    }
  }

  void _resumeCurrentSubtitleFromStartIfNeeded() {
    final item = _pausedResumeTtsItem;
    if (item == null || item.text.trim().isEmpty) {
      return;
    }
    final playbackSessionId = _nextTtsPlaybackSessionId();
    _ttsQueue.insert(
      0,
      (
        source: item.source,
        text: item.text,
        voice: item.voice,
        playbackSessionId: playbackSessionId,
        enqueuedAt: DateTime.now(),
      ),
    );
    _lastMainTtsQueuedAt = DateTime.now();
    _pausedResumeTtsItem = null;
    _scheduleTtsPumpGuard(delay: const Duration(milliseconds: 120));
    if (!_ttsPlaying && !_isMyTurn && !_isRecording && !_isPaused) {
      _playNextTts();
    }
  }

  String _formatDiscussionElapsed(Duration elapsed) {
    final totalSeconds = elapsed.inSeconds < 0 ? 0 : elapsed.inSeconds;
    final totalMinutes = totalSeconds ~/ 60;
    final seconds = totalSeconds % 60;
    return '${totalMinutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
  }

  @override
  void initState() {
    super.initState();
    _candleController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
    _micController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    );
    _thinkingController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
    _endingQuotesTransitionController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 820),
      reverseDuration: const Duration(milliseconds: 680),
    )..addStatusListener((status) {
        if (!mounted) return;
        if (status == AnimationStatus.dismissed && !_showEndingQuotesScreen) {
          setState(() {
            _mountEndingQuotesOverlay = false;
          });
        }
      });
    // Global hardware keyboard listener for the configured mic hotkey.
    HardwareKeyboard.instance.addHandler(_onHardwareKey);
    final initialSettings =
        ref.read(localSettingsProvider).valueOrNull ?? const LocalSettings();
    _bindVoiceServices(
      serverUrl: initialSettings.serverUrl,
      ttsProvider: initialSettings.ttsProvider,
      asrProvider: initialSettings.asrProvider,
      ttsProviderUrl: '',
      asrProviderUrl: '',
      asrStreamingEnabled: true,
    );
    unawaited(_initVoiceServices(settingsOverride: initialSettings));
    _settingsSubscription = ref.listenManual<AsyncValue<LocalSettings>>(
      localSettingsProvider,
      (previous, next) {
        final settings = next.valueOrNull;
        if (settings == null) return;
        unawaited(_initVoiceServices(settingsOverride: settings));
      },
    );
    _startDiscussion();
  }

  // PTT 触发逻辑：根据用户设置的麦克风热键开始/结束发言。
  Set<LogicalKeyboardKey> _resolveHotkeyKeys() {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return ImmersiveSessionScreen.micHotkeyLogicalKeys(settings?.micHotkey);
  }

  Set<PhysicalKeyboardKey> _resolvePhysicalHotkeyKeys() {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return ImmersiveSessionScreen.micHotkeyPhysicalKeys(settings?.micHotkey);
  }

  bool _isConfiguredHotkeyEvent(KeyEvent event) {
    return _resolveHotkeyKeys().contains(event.logicalKey) ||
        _resolvePhysicalHotkeyKeys().contains(event.physicalKey);
  }

  bool _isConfiguredHotkeyPressed() {
    final logicalPressed = HardwareKeyboard.instance.logicalKeysPressed;
    for (final key in _resolveHotkeyKeys()) {
      if (logicalPressed.contains(key)) return true;
    }
    final physicalPressed = HardwareKeyboard.instance.physicalKeysPressed;
    for (final key in _resolvePhysicalHotkeyKeys()) {
      if (physicalPressed.contains(key)) return true;
    }
    return false;
  }

  bool _onHardwareKey(KeyEvent event) {
    if (_isPaused) return false;
    if (!_isConfiguredHotkeyEvent(event)) return false;

    if (event is KeyUpEvent) {
      _ctrlHeld = _isConfiguredHotkeyPressed();
      if (_micControlMode == 'hold_ctrl' &&
          _recordingControlledByHoldCtrl &&
          _isRecording &&
          !_ctrlHeld) {
        _onPttEnd(reason: 'hotkey_release');
        return true;
      }
      return true;
    }

    if (event is! KeyDownEvent) return false;
    if (_ctrlHeld) return true;
    _ctrlHeld = true;

    if (_micControlMode == 'hold_ctrl') {
      if (_isRecording && !_recordingControlledByHoldCtrl) {
        _onPttEnd(reason: 'hotkey_tap_end');
        return true;
      }
      if ((_isMyTurn || _handApprovedToSpeak) && !_isRecording) {
        _onPttStart(startedFromHoldCtrl: _usesHoldToSpeak);
        return true;
      }
      return true;
    }

    // 兼容历史状态，正常会话页不会再走到这里。
    if ((_isMyTurn || _handApprovedToSpeak) && !_isRecording) {
      _onPttStart();
      return true;
    }
    if (_isRecording) {
      _onPttEnd(reason: 'double_ctrl_toggle');
      return true;
    }

    final now = DateTime.now();
    if (_lastCtrlTapAt == null ||
        now.difference(_lastCtrlTapAt!) > const Duration(milliseconds: 450)) {
      _ctrlTapCount = 1;
    } else {
      _ctrlTapCount++;
    }
    _lastCtrlTapAt = now;

    _ctrlTapTimer?.cancel();
    _ctrlTapTimer = Timer(const Duration(milliseconds: 450), () {
      _ctrlTapCount = 0;
    });

    if (_isMyTurn && !_isRecording && _ctrlTapCount >= 2) {
      _ctrlTapCount = 0;
      _onPttStart();
      return true;
    }
    if (_isRecording && _ctrlTapCount >= 2) {
      _ctrlTapCount = 0;
      _onPttEnd(reason: 'double_ctrl_confirmed');
      return true;
    }
    return false;
  }

  Future<void> _initVoiceServices({LocalSettings? settingsOverride}) async {
    final settings =
        settingsOverride ?? ref.read(localSettingsProvider).valueOrNull;
    final resolvedSettings = settings ??
        const LocalSettings(
          serverUrl: ImmersiveSessionScreen.defaultServerUrl,
          asrProvider: ImmersiveSessionScreen.defaultAsrProvider,
          ttsProvider: ImmersiveSessionScreen.defaultTtsProvider,
        );
    SpeechConfig? speechConfig = ref.read(speechConfigProvider).valueOrNull;
    if (speechConfig == null) {
      try {
        speechConfig = await ref.read(speechConfigProvider.future);
      } catch (_) {
        speechConfig = null;
      }
    }
    final serverUrl = resolvedSettings.serverUrl;
    final ttsProvider = _preferAvailableChatTts(
      resolvedSettings.ttsProvider,
      speechConfig,
    );
    final asrProvider = resolvedSettings.asrProvider;
    final ttsProviderUrl =
        _resolveProviderUrl(speechConfig, ttsProvider, asr: false);
    final asrProviderUrl =
        _resolveProviderUrl(speechConfig, asrProvider, asr: true);
    final nextConfig = (
      serverUrl: serverUrl,
      ttsProvider: ttsProvider,
      asrProvider: asrProvider,
      ttsProviderUrl: ttsProviderUrl,
      asrProviderUrl: asrProviderUrl,
      asrStreamingEnabled: resolvedSettings.asrStreamingEnabled,
    );
    if (_voiceServicesInitialized &&
        _speechServerUrl == nextConfig.serverUrl &&
        _ttsProviderId == nextConfig.ttsProvider &&
        _ttsProviderUrl == nextConfig.ttsProviderUrl &&
        _asrProviderId == nextConfig.asrProvider &&
        _asrProviderUrl == nextConfig.asrProviderUrl &&
        _asrStreamingEnabled == nextConfig.asrStreamingEnabled) {
      return;
    }

    if (_hasActiveVoiceSession()) {
      _pendingVoiceConfig = nextConfig;
      return;
    }

    _pendingVoiceConfig = null;
    _bindVoiceServices(
      serverUrl: nextConfig.serverUrl,
      ttsProvider: nextConfig.ttsProvider,
      asrProvider: nextConfig.asrProvider,
      ttsProviderUrl: nextConfig.ttsProviderUrl,
      asrProviderUrl: nextConfig.asrProviderUrl,
      asrStreamingEnabled: nextConfig.asrStreamingEnabled,
    );
  }

  Future<String> _refineTranscript(String raw) async {
    final polished = polishTranscript(raw);
    if (polished.isEmpty) return '';
    return _asrService.refineTranscript(polished);
  }

  void _reportAsrStatus(
    String status, {
    bool? available,
    bool? listening,
    int? textLen,
    bool? isFinal,
    String? error,
  }) {
    _wsClient.sendAsrStatus(
      speaker: widget.humanName,
      provider: _asrProviderId,
      status: status,
      available: available,
      listening: listening,
      textLen: textLen,
      isFinal: isFinal,
      error: error,
    );
    _pushPhaseTelemetry(
      source: 'frontend_asr',
      phase: 'human_speaking',
      reason: 'asr_$status',
      recovery: false,
      speaker: widget.humanName,
    );
  }

  bool get _hasQueuedCurrentSpeakerSpeech {
    return _ttsQueue.isNotEmpty &&
        _currentSpeaker.isNotEmpty &&
        _ttsQueue.first.source == _currentSpeaker;
  }

  bool _hasBlockingPlaybackForHumanTurn() {
    return _hasCurrentSpeakerSpeechToFinish();
  }

  bool _canPumpCurrentSpeakerSpeechForPendingHumanTurn() {
    return _ttsQueue.isNotEmpty &&
        !_ttsPlaying &&
        !_ttsService.isSpeaking &&
        !_isPaused &&
        !_isMyTurn &&
        !_isRecording;
  }

  void _pumpCurrentSpeakerSpeechBeforeHumanTurn() {
    if (!_canPumpCurrentSpeakerSpeechForPendingHumanTurn()) {
      return;
    }
    _pushPhaseTelemetry(
      source: 'frontend',
      phase: 'ai_speaking',
      reason: 'pending_human_wait_current_speaker_tts',
      recovery: true,
      speaker: _ttsQueue.first.source,
    );
    _playNextTts();
  }

  bool _hasCurrentSpeakerSpeechToFinish() {
    return ImmersiveSessionScreen.shouldBlockPendingHumanTurn(
      ttsPlaying: _ttsPlaying,
      ttsServiceSpeaking: _ttsService.isSpeaking,
      hasQueuedCurrentSpeakerSpeech: _hasQueuedCurrentSpeakerSpeech,
      hasQueuedSpeech: _ttsQueue.isNotEmpty,
    );
  }

  DateTime? get _lastMainTtsActivityAt {
    final queuedAt = _lastMainTtsQueuedAt;
    final completedAt = _lastMainTtsCompletedAt;
    if (queuedAt == null) return completedAt;
    if (completedAt == null) return queuedAt;
    return queuedAt.isAfter(completedAt) ? queuedAt : completedAt;
  }

  Future<void> _waitForMainTtsToSettleBeforeHumanReview({
    Duration initialDelay = const Duration(milliseconds: 1400),
    Duration quietPeriod = const Duration(milliseconds: 1600),
    Duration timeout = const Duration(seconds: 90),
  }) async {
    if (initialDelay > Duration.zero) {
      await Future<void>.delayed(initialDelay);
    }
    final deadline = DateTime.now().add(timeout);
    while (mounted && DateTime.now().isBefore(deadline)) {
      final lastActivityAt = _lastMainTtsActivityAt;
      final shouldWait =
          ImmersiveSessionScreen.shouldWaitForHumanReviewAfterMainSpeech(
        ttsPlaying: _ttsPlaying,
        ttsPumpRunning: _ttsPumpRunning,
        ttsServiceSpeaking: _ttsService.isSpeaking,
        browserFallbackSpeaking: _browserFallbackTts?.isSpeaking ?? false,
        hasQueuedSpeech: _ttsQueue.isNotEmpty,
        hasActiveTtsItem: _activeTtsItem != null,
        lastActivityAt: lastActivityAt,
        now: DateTime.now(),
        quietPeriod: quietPeriod,
      );
      if (!shouldWait) {
        return;
      }
      await Future<void>.delayed(const Duration(milliseconds: 250));
    }
  }

  String _humanTurnWaitingPrompt({bool approved = false}) {
    if (approved) {
      return '当前发言还在继续，请稍候，主持人会在合适时机切给你';
    }
    return '当前发言还在继续，请稍候，主持人会继续安排发言顺序';
  }

  void _schedulePendingHumanTurnGuard({
    Duration delay = const Duration(milliseconds: 700),
  }) {
    _pendingHumanTurnGuardTimer?.cancel();
    if (!_pendingHumanTurn || _isPaused) {
      return;
    }
    _pendingHumanTurnGuardTimer = Timer(delay, () {
      if (!mounted || !_pendingHumanTurn || _isPaused) {
        return;
      }
      if (_canPumpCurrentSpeakerSpeechForPendingHumanTurn()) {
        _pumpCurrentSpeakerSpeechBeforeHumanTurn();
        _schedulePendingHumanTurnGuard(
          delay: const Duration(milliseconds: 180),
        );
        return;
      }
      if (_hasBlockingPlaybackForHumanTurn()) {
        _schedulePendingHumanTurnGuard();
        return;
      }
      _activateHumanTurnNow(speaker: _pendingHumanSpeaker);
    });
  }

  void _deferHumanTurnUntilCurrentSpeechEnds({
    required String speaker,
    required String prompt,
  }) {
    setState(() {
      _isMyTurn = false;
      _statusText = prompt;
    });
    if (!_isPaused) {
      _glowController.repeat(reverse: true);
    }
    _schedulePendingHumanTurnGuard();
    _prepareUpcomingPipeline(
      reason: 'defer-human-turn:$speaker',
      includeAsrWarmup: true,
    );
  }

  String _friendlyStateText(String state, String label) {
    switch (state) {
      case 'human_turn_waiting':
        if (_isPaused) {
          return '暂停中...';
        }
        if (_hasStartedSpeechThisTurn) {
          return '正在整理你的话……';
        }
        final normalizedHuman =
            ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
        final normalizedCurrent =
            ImmersiveSessionScreen.normalizeSpeakerLabel(_currentSpeaker);
        if (_isMyTurn ||
            (normalizedCurrent.isNotEmpty &&
                normalizedCurrent == normalizedHuman)) {
          return _readyToSpeakStatusText();
        }
        return _humanTurnWaitingPrompt();
      case 'human_speaking':
        final normalizedHuman =
            ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
        final normalizedCurrent =
            ImmersiveSessionScreen.normalizeSpeakerLabel(_currentSpeaker);
        if (_isMyTurn ||
            _isRecording ||
            (normalizedCurrent.isNotEmpty &&
                normalizedCurrent == normalizedHuman)) {
          return '正在听你说……';
        }
        return _currentSpeaker.isEmpty ? '有人正在说话……' : '$_currentSpeaker 正在说话……';
      case 'ai_speaking':
        return _currentSpeaker.isEmpty ? '思考中……' : '$_currentSpeaker 思考中……';
      case 'selecting_speaker':
        return '李老师正在安排下一位发言';
      case 'moderator_opening':
        return '李老师正在开场';
      case 'closing':
        return '讨论快结束了，正在收尾';
      case 'ended':
        return '讨论已结束';
      default:
        return label;
    }
  }

  void _scheduleBackgroundTask(Future<void> Function() task) {
    if (_disposed) return;
    _bgTaskQueue.add(task);
    _pumpBackgroundTaskQueue();
  }

  void _pumpBackgroundTaskQueue() {
    if (_disposed) return;
    const maxConcurrent = 2;
    while (_bgTaskRunning < maxConcurrent && _bgTaskQueue.isNotEmpty) {
      final task = _bgTaskQueue.removeAt(0);
      _bgTaskRunning += 1;
      Future<void>(() async {
        try {
          await task();
        } catch (e) {
          if (kDebugMode) {
            debugPrint('[RuntimePipeline] background task failed: $e');
          }
        } finally {
          _bgTaskRunning = (_bgTaskRunning - 1).clamp(0, maxConcurrent);
          _pumpBackgroundTaskQueue();
        }
      });
    }
  }

  List<({String text, String? voice})> _collectUpcomingTtsItems({
    int limit = 3,
  }) {
    if (_ttsQueue.isEmpty || limit <= 0) return const [];
    final count = _ttsQueue.length < limit ? _ttsQueue.length : limit;
    return List.generate(
      count,
      (i) => (text: _ttsQueue[i].text, voice: _ttsQueue[i].voice),
    );
  }

  /// 在批量入队后强制触发一次预取（绕过 _prepareUpcomingPipeline 的 120ms
  /// 去抖），保证开场多句一次性并行合成。
  void _kickBatchPrefetch({
    required List<({String text, String? voice})> items,
  }) {
    if (items.isEmpty) return;
    _lastPrefetchAt = DateTime.now();
    _scheduleBackgroundTask(() async {
      await _ttsService.prefetchBatch(items, maxConcurrent: 3);
    });
    if (kDebugMode) {
      debugPrint('[RuntimePipeline] kickBatchPrefetch items=${items.length}');
    }
  }

  void _prepareUpcomingPipeline({
    required String reason,
    bool includeAsrWarmup = false,
  }) {
    final now = DateTime.now();

    final upcoming = _collectUpcomingTtsItems(limit: 3);
    if (upcoming.isNotEmpty &&
        now.difference(_lastPrefetchAt).inMilliseconds >= 120) {
      _lastPrefetchAt = now;
      _scheduleBackgroundTask(() async {
        // 开场阶段最常出现"第二句字幕已显示但音频还在合成"导致的停顿。
        // 提高并发度（2 → 3）后，老师的 1-3 句开场可以并行合成，显著缩短句间空白。
        await _ttsService.prefetchBatch(upcoming, maxConcurrent: 3);
      });
    }

    if (includeAsrWarmup &&
        now.difference(_lastAsrWarmupAt).inMilliseconds >= 1500) {
      _lastAsrWarmupAt = now;
      _scheduleBackgroundTask(() async {
        await _asrService.warmup();
      });
    }

    if (kDebugMode) {
      debugPrint(
          '[RuntimePipeline] reason=$reason prefetch=${upcoming.length} asrWarmup=$includeAsrWarmup');
    }
  }

  double get _avgTtsStartupMs {
    if (_ttsStartupSamples == 0) return 0;
    return _ttsStartupTotalMs / _ttsStartupSamples;
  }

  double get _avgAsrFirstPacketMs {
    if (_asrFirstPacketSamples == 0) return 0;
    return _asrFirstPacketTotalMs / _asrFirstPacketSamples;
  }

  TtsPerfSnapshot get _ttsPerf => _ttsService.getPerfSnapshot();

  double get _prefetchHitRatePercent {
    final total = _ttsPerf.playbackCount;
    if (total == 0) return 0;
    return (_ttsPerf.prefetchHit * 100.0) / total;
  }

  void _pushSeriesSample(List<double> target, double value) {
    if (value.isNaN || value.isInfinite) return;
    target.add(value);
    if (target.length > 30) {
      target.removeAt(0);
    }
  }

  bool get _debugSubtitleLogEnabled => kDebugMode;

  String get _micControlMode {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.micControlMode ?? 'hold_ctrl';
  }

  String get _micActivationMode {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.micActivationMode ?? 'manual';
  }

  String get _micHotkeyLabel {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return micHotkeyLabel(settings?.micHotkey);
  }

  bool get _autoOpenMic => _micActivationMode == 'auto';

  bool get _usesHoldToSpeak => _isPushToTalk && !_autoOpenMic;

  String _readyToSpeakStatusText() {
    final hotkey = _micHotkeyLabel;
    final teacherCue = '李老师：${widget.humanName}，请来谈谈这个话题吧。';
    if (_autoOpenMic) {
      return '$teacherCue 麦克风会自动开启，也可按 $hotkey 控制';
    }
    if (_isPushToTalk) {
      return '$teacherCue 按住 $hotkey 或点击“讲话”开始';
    }
    return '$teacherCue 按 $hotkey 或点击“讲话”开始/结束';
  }

  void _cancelAutoMicStart() {
    _autoMicStartTimer?.cancel();
    _autoMicStartTimer = null;
  }

  void _scheduleAutoMicStart({Duration delay = Duration.zero}) {
    _cancelAutoMicStart();
    if (!_autoOpenMic || _isPaused || _isRecording || _micLocked) {
      return;
    }
    if (!(_isMyTurn || _handApprovedToSpeak) || _hasStartedSpeechThisTurn) {
      return;
    }
    _autoMicStartTimer = Timer(delay, () {
      if (!mounted ||
          !_autoOpenMic ||
          _isPaused ||
          _isRecording ||
          _micLocked) {
        return;
      }
      if (!(_isMyTurn || _handApprovedToSpeak) || _hasStartedSpeechThisTurn) {
        return;
      }
      _onPttStart(startedFromHoldCtrl: false);
    });
  }

  void _debugSubtitleLog({required String triggerRole, required String note}) {
    if (!_debugSubtitleLogEnabled) return;
    debugPrint(
      '[SubtitleSync] trigger=$triggerRole current=$_currentSpeaker owner=$_subtitleOwner token=$_subtitleToken subtitleSession=$_subtitleSessionId activeTtsSession=$_activeTtsSessionId note=$note',
    );
  }

  void _acquireSubtitleToken(String owner, {String subtitleSessionId = ''}) {
    _subtitleToken += 1;
    _subtitleOwner = owner;
    _subtitleSessionId = subtitleSessionId;
    _debugSubtitleLog(triggerRole: owner, note: 'acquire');
  }

  void _clearSubtitleBeforeSpeakerSwitch(String nextSpeaker) {
    _debugSubtitleLog(triggerRole: nextSpeaker, note: 'pre-clear on switch');
    _centerMessage = '';
    _centerSpeaker = '';
  }

  bool _canRenderSubtitle({
    required String triggerRole,
    required int token,
  }) {
    return token == _subtitleToken && triggerRole == _currentSpeaker;
  }

  int _estimateSubtitleLineCount(
    BuildContext context,
    String text, {
    required double maxWidth,
    required TextStyle style,
    int maxLines = 2,
  }) {
    if (text.trim().isEmpty) return 0;
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: Directionality.of(context),
      maxLines: maxLines,
    )..layout(maxWidth: maxWidth);
    final metrics = tp.computeLineMetrics();
    if (metrics.isEmpty) return 0;
    return metrics.length > maxLines ? maxLines : metrics.length;
  }

  // 需求19：字幕翻页——把整条字幕按每页最多 2 行切分，随时间推进翻到下一页。
  List<ChatMessage> _goldenQuoteSourceMessages() {
    return _messages
        .where(ImmersiveSessionScreen._isMeaningfulGoldenQuoteSourceMessage)
        .toList(growable: false);
  }

  Future<void> _generateHumanReview() async {
    if (_isGeneratingHumanReview) return;
    final sourceMessages = _goldenQuoteSourceMessages();
    if (!ImmersiveSessionScreen.hasHumanReviewMaterial(
      sourceMessages,
      humanName: widget.humanName,
    )) {
      return;
    }

    setState(() {
      _isGeneratingHumanReview = true;
      if (_humanReview.isEmpty) {
        _humanReviewMuted = false;
      }
    });

    try {
      final apiClient = ref.read(apiClientProvider);
      final review = await apiClient.generateHumanReview(
        topic: widget.topic.title,
        humanName: widget.humanName,
        messages: sourceMessages,
      );
      final normalized = review.trim();
      if (!mounted || normalized.isEmpty) return;
      await _waitForMainTtsToSettleBeforeHumanReview(
        initialDelay: Duration.zero,
      );
      if (!mounted || _lastErrorMessage != null) return;
      setState(() {
        _humanReview = normalized;
        if (_humanReviewOverlayVisible) {
          _humanReviewMuted = false;
          _statusText = '讨论已结束，左侧可以查看李老师给你的会后点评';
        }
      });
      if (_humanReviewOverlayVisible && !_humanReviewMuted) {
        unawaited(_readAloudHumanReview(normalized));
      }
    } catch (error) {
      if (kDebugMode) {
        debugPrint('human review generation failed: $error');
      }
    } finally {
      if (mounted) {
        setState(() {
          _isGeneratingHumanReview = false;
        });
      }
    }
  }

  bool _isTeacherSpeechMessage({
    required String source,
    required String msgType,
  }) {
    if (msgType == 'system') return false;
    return ImmersiveSessionScreen.normalizeSpeakerLabel(source) ==
        ImmersiveSessionScreen.normalizeSpeakerLabel(
            ImmersiveSessionScreen.teacherDisplayName);
  }

  bool _looksLikeTeacherClosingCue(String content) {
    final compact = content.replaceAll(RegExp(r'\s+'), '');
    if (compact.isEmpty) return false;
    return compact.contains('收尾前') ||
        compact.contains('最后我再问一次') ||
        compact.contains('小总结') ||
        compact.contains('总结吧') ||
        compact.contains('讨论结束');
  }

  bool _containsTeacherFarewell(String content) {
    final compact = content.replaceAll(RegExp(r'\s+'), '');
    return compact.contains('再见');
  }

  void _startHumanReviewPrefetchIfNeeded() {
    if (_humanReviewPrefetchStarted ||
        _isGeneratingHumanReview ||
        _humanReview.isNotEmpty) {
      return;
    }
    if (!ImmersiveSessionScreen.hasHumanReviewMaterial(
      _goldenQuoteSourceMessages(),
      humanName: widget.humanName,
    )) {
      return;
    }
    _humanReviewPrefetchStarted = true;
    unawaited(_generateHumanReview());
  }

  void _revealHumanReviewOverlayAfterFarewell() {
    if (_lastErrorMessage != null) return;
    _teacherFarewellHeard = true;
    if (mounted) {
      setState(() {
        _humanReviewOverlayVisible = true;
        if (_discussionEnded) {
          _statusText =
              _humanReview.isEmpty ? '老师正在整理你的会后点评…' : '讨论已结束，左侧可以查看李老师给你的会后点评';
        }
      });
    }

    if (_humanReview.isNotEmpty) {
      if (!_humanReviewMuted) {
        unawaited(_readAloudHumanReview(_humanReview));
      }
      return;
    }

    _startHumanReviewPrefetchIfNeeded();
    if (!_isGeneratingHumanReview) {
      unawaited(_generateHumanReview());
    }
  }

  String _subtitlePageCacheKey = '';
  String _subtitlePageFlowKey = '';
  List<String> _subtitlePages = const [];
  int _subtitlePageIndex = 0;
  Timer? _subtitlePageTimer;

  String get _displayedSubtitleMessage {
    if (_subtitlePages.isEmpty) return _centerMessage;
    final idx = _subtitlePageIndex.clamp(0, _subtitlePages.length - 1);
    return _subtitlePages[idx];
  }

  Duration? get _remainingHumanSubtitleHold {
    final lockUntil = _humanSubtitleLockUntil;
    if (lockUntil == null) return null;
    final remaining = lockUntil.difference(DateTime.now());
    if (remaining <= Duration.zero) return null;
    return remaining;
  }

  bool get _shouldAutoAdvanceSubtitlePage {
    final item = _activeTtsItem;
    if (_ttsPlaying && item != null && _centerSpeaker == item.source) {
      return true;
    }
    return !_isRecording &&
        _centerSpeaker == widget.humanName &&
        _remainingHumanSubtitleHold != null;
  }

  void _scheduleSubtitlePageAdvance() {
    _subtitlePageTimer?.cancel();
    if (!_shouldAutoAdvanceSubtitlePage) return;
    if (_subtitlePages.length <= 1 ||
        _subtitlePageIndex >= _subtitlePages.length - 1) {
      return;
    }
    final pageText = _subtitlePages[_subtitlePageIndex];
    final isFirstPage = _subtitlePageIndex == 0;
    final remainingPages = _subtitlePages.length - _subtitlePageIndex;
    final delayMs = ImmersiveSessionScreen.subtitlePageAdvanceDelayMs(
      pageText: pageText,
      isFirstPage: isFirstPage,
      remainingHumanSubtitleHold: _centerSpeaker == widget.humanName
          ? _remainingHumanSubtitleHold
          : null,
      remainingPages: remainingPages,
    );
    _subtitlePageTimer = Timer(Duration(milliseconds: delayMs), () {
      if (!mounted || !_shouldAutoAdvanceSubtitlePage) return;
      if (_subtitlePageIndex >= _subtitlePages.length - 1) return;
      setState(() {
        _subtitlePageIndex += 1;
      });
      _scheduleSubtitlePageAdvance();
    });
  }

  void _maybeAdvanceSubtitlePage({
    required String fullText,
    required double maxWidth,
    required double lineHeight,
  }) {
    final flowSeed = _subtitleSessionId.isNotEmpty
        ? _subtitleSessionId
        : (_activeTtsSessionId.isNotEmpty
            ? _activeTtsSessionId
            : '$_centerSpeaker|${_isRecording ? 'live' : 'idle'}');
    final flowKey =
        '$flowSeed|${maxWidth.toStringAsFixed(1)}|${lineHeight.toStringAsFixed(2)}';
    final cacheKey = '$flowKey|$fullText';
    if (cacheKey == _subtitlePageCacheKey) return;

    final sameFlow = flowKey == _subtitlePageFlowKey;
    final oldPageCount = _subtitlePages.length;
    final oldPageIndex = _subtitlePageIndex;

    _subtitlePageCacheKey = cacheKey;
    _subtitlePageFlowKey = flowKey;
    _subtitlePages = _paginateSubtitle(
      text: _centerMessage,
      maxWidth: maxWidth,
      lineHeight: lineHeight,
    );

    if (_subtitlePages.isEmpty) {
      _subtitlePageIndex = 0;
      _subtitlePageTimer?.cancel();
      return;
    }

    if (!sameFlow) {
      _subtitlePageIndex = 0;
    } else if (_shouldAutoAdvanceSubtitlePage) {
      _subtitlePageIndex = min(oldPageIndex, _subtitlePages.length - 1);
    } else if (_subtitlePages.length > oldPageCount &&
        oldPageIndex >= oldPageCount - 1) {
      _subtitlePageIndex = _subtitlePages.length - 1;
    } else {
      _subtitlePageIndex = min(oldPageIndex, _subtitlePages.length - 1);
    }

    _scheduleSubtitlePageAdvance();
  }

  List<String> _splitSubtitleSegments(String text) {
    final segments = <String>[];
    final buffer = StringBuffer();
    const punctuation = '，。；！？,.?!;、';
    for (final rune in text.runes) {
      final char = String.fromCharCode(rune);
      buffer.write(char);
      if (punctuation.contains(char)) {
        final segment = buffer.toString().trim();
        if (segment.isNotEmpty) {
          segments.add(segment);
        }
        buffer.clear();
      }
    }
    final tail = buffer.toString().trim();
    if (tail.isNotEmpty) {
      segments.add(tail);
    }
    return segments.isEmpty ? [text.trim()] : segments;
  }

  List<String> _sliceSubtitleChunk({
    required String text,
    required TextStyle style,
    required double maxWidth,
    required int linesPerPage,
  }) {
    final slices = <String>[];
    var remaining = text.trim();
    while (remaining.isNotEmpty) {
      final painter = TextPainter(
        text: TextSpan(text: remaining, style: style),
        textDirection: TextDirection.ltr,
        maxLines: linesPerPage,
        ellipsis: null,
      )..layout(maxWidth: maxWidth);
      final endOffset =
          painter.getPositionForOffset(Offset(maxWidth, painter.height - 1));
      var take = endOffset.offset.clamp(1, remaining.length);
      if (take < remaining.length) {
        const punctuation = '，。；！？,.?!;、 ';
        for (var i = take; i > max(0, take - 16); i--) {
          if (punctuation.contains(remaining[i - 1])) {
            take = i;
            break;
          }
        }
      }
      final page = remaining.substring(0, take).trim();
      if (page.isNotEmpty) {
        slices.add(page);
      }
      remaining = remaining.substring(take).trimLeft();
    }
    return slices;
  }

  List<String> _paginateSubtitle({
    required String text,
    required double maxWidth,
    required double lineHeight,
    int linesPerPage = 3,
  }) {
    if (text.trim().isEmpty) return const [];
    final style = TextStyle(fontSize: 22, height: lineHeight);
    final painter = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: maxWidth);
    if (painter.computeLineMetrics().length <= linesPerPage) {
      return [text];
    }

    int lineCountOf(String value) {
      final tp = TextPainter(
        text: TextSpan(text: value, style: style),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: maxWidth);
      return tp.computeLineMetrics().length;
    }

    final pages = <String>[];
    var currentPage = '';
    for (final segment in _splitSubtitleSegments(text)) {
      final candidate = currentPage.isEmpty ? segment : '$currentPage$segment';
      if (lineCountOf(candidate) <= linesPerPage) {
        currentPage = candidate;
        continue;
      }

      if (currentPage.isNotEmpty) {
        pages.add(currentPage.trim());
      }

      if (lineCountOf(segment) <= linesPerPage) {
        currentPage = segment;
        continue;
      }

      final slices = _sliceSubtitleChunk(
        text: segment,
        style: style,
        maxWidth: maxWidth,
        linesPerPage: linesPerPage,
      );
      if (slices.isEmpty) {
        currentPage = '';
        continue;
      }
      if (slices.length == 1) {
        currentPage = slices.first;
        continue;
      }
      pages.addAll(slices.take(slices.length - 1));
      currentPage = slices.last;
    }

    if (currentPage.trim().isNotEmpty) {
      pages.add(currentPage.trim());
    }
    return pages.where((page) => page.isNotEmpty).toList(growable: false);
  }

  String _nextTtsPlaybackSessionId() {
    _ttsSessionSeq += 1;
    return 'tts-$_ttsSessionSeq-${DateTime.now().microsecondsSinceEpoch}';
  }

  String _extractEventPlaybackSessionId(Map<String, dynamic>? data) {
    if (data == null) return '';
    const sessionKeys = [
      'playback_session_id',
      'tts_session_id',
      'session_id',
      'subtitle_session_id',
    ];
    for (final key in sessionKeys) {
      final v = data[key];
      if (v == null) continue;
      final s = v.toString().trim();
      if (s.isNotEmpty) return s;
    }
    return '';
  }

  bool _isRealtimeSessionMatched({
    required Map<String, dynamic>? data,
    required String triggerRole,
    required String eventType,
  }) {
    if (_activeTtsSessionId.isEmpty) return true;
    final incoming = _extractEventPlaybackSessionId(data);
    if (incoming.isEmpty) {
      _debugSubtitleLog(
        triggerRole: triggerRole,
        note: '$eventType pass(no-session)',
      );
      return true;
    }
    final matched = incoming == _activeTtsSessionId;
    if (!matched) {
      _debugSubtitleLog(
        triggerRole: triggerRole,
        note:
            '$eventType blocked(session-mismatch incoming=$incoming active=$_activeTtsSessionId)',
      );
    }
    return matched;
  }

  void _showStatusToast(String message, {bool isError = false}) {
    if (!mounted) return;
    _statusToastTimer?.cancel();
    _statusToastEntry?.remove();

    final overlay = Overlay.of(context);

    _statusToastEntry = OverlayEntry(
      builder: (_) => Positioned(
        left: 16,
        bottom: 16 + MediaQuery.of(context).padding.bottom,
        child: IgnorePointer(
          child: Material(
            color: Colors.transparent,
            child: Container(
              constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.58,
              ),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: const Color(0xCC10141D),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(
                  color: isError
                      ? Colors.redAccent.withValues(alpha: 0.6)
                      : const Color(0xFF00FFCC).withValues(alpha: 0.35),
                ),
              ),
              child: Text(
                message,
                style: TextStyle(
                  color: isError ? Colors.redAccent.shade100 : Colors.white,
                  fontSize: 12,
                  height: 1.3,
                ),
              ),
            ),
          ),
        ),
      ),
    );
    overlay.insert(_statusToastEntry!);
    _statusToastTimer = Timer(const Duration(seconds: 4), () {
      _statusToastEntry?.remove();
      _statusToastEntry = null;
    });
  }

  void _reportClientMetric({
    required String name,
    required int valueMs,
    String? speaker,
    String? phase,
    int? eventSeq,
    String? detail,
  }) {
    if (valueMs < 0) {
      return;
    }
    _wsClient.sendClientMetric(
      name: name,
      valueMs: valueMs,
      speaker: speaker,
      phase: phase,
      eventSeq: eventSeq,
      detail: detail,
    );
  }

  void _clearPendingTeacherFeedbackMetric() {
    _dismissTeacherReplyWaitingUi(clearText: true);
    _awaitingTeacherFeedbackMetric = false;
    _pendingTeacherReplySeenAt = null;
    _pendingTeacherReplyEventSeq = null;
    _ttsFirstAudioPendingBySession.clear();
  }

  void _markPendingTeacherFeedbackMetric({int? eventSeq}) {
    if (!_awaitingTeacherFeedbackMetric || _pendingTeacherReplySeenAt != null) {
      return;
    }
    _pendingTeacherReplySeenAt = DateTime.now();
    _pendingTeacherReplyEventSeq = eventSeq;
    _promoteImmediateFeedbackToTeacherWarmup();
  }

  bool _isMeaningfulImmediateFeedbackText(String text) {
    final compact =
        text.replaceAll(RegExp(r'[\s\p{P}\p{S}]', unicode: true), '').trim();
    if (compact.isEmpty || compact == '跳过') {
      return false;
    }
    return compact.length >= 5;
  }

  String _buildImmediateFeedbackText(String text) {
    final compact = text.replaceAll(RegExp(r'\s+'), ' ').trim();
    if (compact.isEmpty) {
      return '已收到，老师正在组织回应';
    }
    final variants = <String>[
      '已收到，老师正在组织回应',
      '这个观点记下了，马上接着讨论',
      '你的补充已加入，老师很快回应',
    ];
    return variants[compact.runes.fold<int>(0, (sum, rune) => sum + rune) %
        variants.length];
  }

  String _teacherWarmupDots() {
    final dotCount = (_candleController.value * 3).floor() + 1;
    return '·' * dotCount;
  }

  List<double> _teacherWarmupSignalHeights() {
    final phase = _candleController.value * pi * 2;
    return <double>[
      7 + (sin(phase) + 1) * 5,
      7 + (sin(phase + 1.35) + 1) * 5,
      7 + (sin(phase + 2.7) + 1) * 5,
    ];
  }

  void _showImmediateFeedbackOverlay(String submitText) {
    final overlayText = _buildImmediateFeedbackText(submitText);
    _teacherReplyWarmupTimer?.cancel();
    _quickFeedbackTimer?.cancel();
    if (mounted) {
      setState(() {
        _quickFeedbackText = overlayText;
        _quickFeedbackVisible = true;
        _teacherReplyWarmupVisible = false;
      });
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_quickFeedbackVisible) {
        return;
      }
      final startedAt = _pendingFeedbackOverlayMetricAt;
      if (startedAt != null) {
        _reportClientMetric(
          name: 'feedback_overlay_shown_ms',
          valueMs: DateTime.now().difference(startedAt).inMilliseconds,
          speaker: widget.humanName,
          phase: 'human_submit',
          detail: 'quick_feedback_overlay',
        );
        _pendingFeedbackOverlayMetricAt = null;
      }
    });
    _quickFeedbackTimer = Timer(const Duration(milliseconds: 2600), () {
      if (!mounted) return;
      setState(() {
        _quickFeedbackVisible = false;
      });
    });
  }

  void _promoteImmediateFeedbackToTeacherWarmup() {
    _quickFeedbackTimer?.cancel();
    _teacherReplyWarmupTimer?.cancel();
    if (!mounted) {
      return;
    }
    setState(() {
      _quickFeedbackText = '老师接上了，马上开口';
      _quickFeedbackVisible = true;
      _teacherReplyWarmupVisible = true;
    });
    _teacherReplyWarmupTimer = Timer(const Duration(seconds: 6), () {
      if (!mounted) return;
      _dismissTeacherReplyWaitingUi(clearText: false);
    });
  }

  void _dismissTeacherReplyWaitingUi({bool clearText = false}) {
    _quickFeedbackTimer?.cancel();
    _teacherReplyWarmupTimer?.cancel();
    if (!mounted) {
      return;
    }
    setState(() {
      _quickFeedbackVisible = false;
      _teacherReplyWarmupVisible = false;
      if (clearText) {
        _quickFeedbackText = '';
      }
    });
  }

  void _activateHumanTurnNow({String speaker = ''}) {
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _cancelPendingHumanTurnGuard();
    _clearAwaitingAiResponseAfterHumanSubmit(resetSpeechTurnLatch: true);
    _clearPendingTeacherFeedbackMetric();
    final activateSpeaker = speaker.isEmpty ? widget.humanName : speaker;
    _completedHumanTurnSpeaker = '';
    _commander.markHumanTurnActivated(speaker: activateSpeaker);
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
    setState(() {
      _isMyTurn = true;
      _hasRaisedHand = false;
      // 需求4：新一轮/举手批准，解锁麦克风
      _micLocked = false;
      _isThinking = false;
      _thinkingController.stop();
      _sttPartialText = '';
      _clearSubtitleBeforeSpeakerSwitch(
          speaker.isEmpty ? widget.humanName : speaker);
      // 需求20：暂停时不提示"请按住麦克风讲话"
      _statusText = _isPaused ? '暂停中...' : _readyToSpeakStatusText();
      if (!_isPaused) {
        _glowController.repeat(reverse: true);
      }
      _currentSpeaker = activateSpeaker;
    });
    if (!_isPaused) {
      _keyboardFocusNode.requestFocus();
      _startTurnCountdown();
    }
    _prepareUpcomingPipeline(
        reason: 'activate-human-turn', includeAsrWarmup: true);
    _scheduleAutoMicStart();
  }

  void _handleAutoSkipReminder({String? reason}) {
    _cancelTurnCountdown();
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
    if (!mounted) {
      return;
    }
    final reminder = reason?.trim().isNotEmpty == true
        ? reason!.trim()
        : ImmersiveSessionScreen.humanTurnIdleReminderText();
    setState(() {
      _statusText = ImmersiveSessionScreen.humanTurnIdleReminderText();
    });
    _showStatusToast('$reminder；系统不会替你跳过');
  }

  void _cancelPendingHumanTurnGuard() {
    _pendingHumanTurnGuardTimer?.cancel();
    _pendingHumanTurnGuardTimer = null;
  }

  void _cancelHumanResponseWatchdog() {
    _humanResponseWatchdogTimer?.cancel();
    _humanResponseWatchdogTimer = null;
  }

  void _clearAwaitingAiResponseAfterHumanSubmit({
    bool resetSpeechTurnLatch = false,
  }) {
    _awaitingAiResponseAfterHumanSubmit = false;
    _cancelHumanResponseWatchdog();
    if (resetSpeechTurnLatch) {
      _hasStartedSpeechThisTurn = false;
    }
  }

  void _scheduleHumanResponseWatchdog({
    Duration delay = const Duration(seconds: 7),
  }) {
    _cancelHumanResponseWatchdog();
    _humanResponseWatchdogTimer = Timer(delay, () {
      if (!mounted || !_awaitingAiResponseAfterHumanSubmit) {
        return;
      }
      if (_isMyTurn || _isRecording || _isPaused) {
        return;
      }
      if (_statusText == '你说完了，大家正在回应……') {
        setState(() {
          _statusText = ImmersiveSessionScreen.humanResponseBufferText();
        });
      }
    });
  }

  void _scheduleTtsPumpGuard(
      {Duration delay = const Duration(milliseconds: 240)}) {
    _ttsPumpGuardTimer?.cancel();
    _ttsPumpGuardTimer = Timer(delay, () {
      if (!mounted) return;
      final shouldPump = _ttsQueue.isNotEmpty &&
          !_ttsPlaying &&
          !_isPaused &&
          !_isMyTurn &&
          !_isRecording;
      if (shouldPump) {
        _pushPhaseTelemetry(
          source: 'frontend',
          phase: 'ai_speaking',
          reason: 'tts_pump_guard_recover',
          recovery: true,
          speaker: _currentSpeaker,
        );
        _playNextTts();
      }
    });
  }

  void _pushPhaseTelemetry({
    required String source,
    required String phase,
    required String reason,
    required bool recovery,
    String speaker = '',
    int? eventSeq,
    String designateStage = '',
    String designateTarget = '',
    int sendDropTotal = 0,
    String sendDropReasons = '',
    String sendDropLast = '',
  }) {
    final entry = _PhaseTelemetryEntry(
      at: DateTime.now(),
      source: source,
      phase: phase,
      reason: reason,
      recovery: recovery,
      speaker: speaker,
      eventSeq: eventSeq,
      designateStage: designateStage,
      designateTarget: designateTarget,
      sendDropTotal: sendDropTotal,
      sendDropReasons: sendDropReasons,
      sendDropLast: sendDropLast,
    );
    setState(() {
      _phaseTelemetryHistory.insert(0, entry);
      if (_phaseTelemetryHistory.length > _maxPhaseTelemetryHistory) {
        _phaseTelemetryHistory.removeLast();
      }
    });
  }

  void _tryActivatePendingHumanTurn() {
    if (!_pendingHumanTurn) return;
    if (_canPumpCurrentSpeakerSpeechForPendingHumanTurn()) {
      _pumpCurrentSpeakerSpeechBeforeHumanTurn();
      return;
    }
    if (_hasBlockingPlaybackForHumanTurn()) return;
    _cancelPendingHumanTurnGuard();
    _activateHumanTurnNow(speaker: _pendingHumanSpeaker);
  }

  Future<void> _startDiscussion() async {
    _stopDiscussionClock(keepElapsed: false);
    setState(() {
      _statusText = '创建讨论会话...';
      _openingCueState = _OpeningCueState.preparing;
      _openingReadyShownAt = null;
    });

    try {
      final apiClient = ref.read(apiClientProvider);
      final humanNames =
          widget.observerMode ? const <String>[] : <String>[widget.humanName];

      // 创建会话
      final sessionData = await apiClient.createSession(
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        thinkerIds: widget.thinkerIds,
        humanNames: humanNames,
        freeTopic: widget.topic.id == 'free_topic' ? widget.topic.title : '',
        freeTopicDetail:
            widget.topic.id == 'free_topic' ? widget.topic.description : '',
      );

      final sessionId = sessionData['session_id'] as String;
      final wsUrl = apiClient.getWebSocketUrl(sessionId);
      final maxTurns = (sessionData['max_turns'] as num?)?.toInt() ?? 24;
      final seededParticipants =
          ImmersiveSessionScreen.extractParticipantNamesFromSessionPayload(
        sessionData['participants'],
      );

      // 连接 WebSocket
      await _wsClient.connect(
        wsUrl: wsUrl,
        sessionId: sessionId,
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        thinkerIds: widget.thinkerIds,
        humanNames: humanNames,
        maxTurns: maxTurns,
        observerMode: widget.observerMode,
      );

      setState(() {
        _discussionSessionId = sessionId;
        _statusText = '已连接';
      });

      // 连接后立即预填参与者，确保主持人(老师)和所有角色从一开始就显示在圆桌上
      _prePopulateParticipants(seededParticipants);

      // 监听事件
      _wsClient.events.listen((event) {
        unawaited(_handleEvent(event));
      });
    } catch (e) {
      setState(() {
        _statusText = '连接失败: $e';
        _openingCueState = _OpeningCueState.done;
      });
    }
  }

  // 从系统事件获取的完整参与者列表
  List<String> _knownParticipants = [];

  /// 角色 ID → 显示名映射（与后端 character_templates 对应）
  static const _charIdToName = <String, String>{
    'moderator': '李老师',
    'explorer': '小探',
    'skeptic': '小疑',
    'peacemaker': '小和',
    'storyteller': '小说',
    'optimist': '小明',
    'questioner': '小思',
    'rationalist': '小理',
    'empath': '小爱',
    'innovator': '小想',
    'pragmatist': '小行',
    'comedian': '可乐',
  };

  /// 连接成功后立即预填参与者，确保老师和所有角色出现在圆桌上
  void _prePopulateParticipants([List<String> seededParticipants = const []]) {
    // 始终包含老师（即使 characterIds 里没有 moderator，也强制加入）
    final names = <String>{'李老师'};

    for (final name in seededParticipants) {
      final normalizedName = ImmersiveSessionScreen.normalizeSpeakerLabel(name);
      if (normalizedName.isNotEmpty) {
        names.add(normalizedName);
      }
    }

    // 所有选中的角色
    for (final id in widget.characterIds) {
      final name = _charIdToName[id] ?? id;
      if (name != '李老师') names.add(name); // 避免重复
    }

    // 人类参与者
    if (!widget.observerMode) {
      names.add(widget.humanName);
    }

    _knownParticipants = names.toList();
    _buildParticipants();
  }

  void _buildParticipants() {
    // 构建参与者列表：AI 角色 + 思想家 + 人类
    final names = <String>{};
    final avatars = <String, String>{};
    final humanName =
        ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);

    // 从系统事件的参与者列表中获取
    for (final name in _knownParticipants) {
      final normalizedName = ImmersiveSessionScreen.normalizeSpeakerLabel(name);
      if (normalizedName.isNotEmpty) {
        names.add(normalizedName);
      }
    }

    // 从已有消息中补充参与者
    for (final msg in _messages) {
      if (msg.type != 'system') {
        final normalizedName =
            ImmersiveSessionScreen.normalizeSpeakerLabel(msg.source);
        if (normalizedName.isNotEmpty) {
          names.add(normalizedName);
        }
      }
    }

    // 从当前发言者中添加
    if (_currentSpeaker.isNotEmpty) {
      names.add(ImmersiveSessionScreen.normalizeSpeakerLabel(_currentSpeaker));
    }

    // 旧角色模板的头像映射 - fun character-specific pairings (d)
    const templateAvatarMap = {
      'moderator': '👩‍🏫',
      '李老师': '👩‍🏫',
      'explorer': '🧭',
      '小探': '🧭',
      'skeptic': '🔍',
      '小疑': '🔍',
      'peacemaker': '🕊️',
      '小和': '🕊️',
      'storyteller': '📖',
      '小说': '📖',
      'optimist': '🌞',
      '小明': '🌞',
      'questioner': '❓',
      '小思': '❓',
      'rationalist': '🧮',
      '小理': '🧮',
      'empath': '💗',
      '小爱': '💗',
      'innovator': '💡',
      '小想': '💡',
      'pragmatist': '🔧',
      '小行': '🔧',
      'comedian': '🥤',
      '可乐': '🥤',
    };

    // Character-specific DiceBear styles for more personality (d)
    const charDiceBearStyle = <String, String>{
      '李老师': 'avataaars',
      '小探': 'adventurer',
      '小疑': 'bottts',
      '小和': 'lorelei',
      '小说': 'fun-emoji',
      '小明': 'open-peeps',
      '小爱': 'lorelei',
      '小想': 'bottts',
      '小行': 'adventurer',
      '可乐': 'fun-emoji',
    };

    /// DiceBear avatar URL for a given seed name
    String diceBearUrl(String seed, {String style = 'notionists-neutral'}) {
      final encoded = Uri.encodeComponent(seed);
      return 'https://api.dicebear.com/7.x/$style/png?seed=$encoded&size=128&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc';
    }

    // 为所有参与者分配头像并建立固定音色映射
    for (final name in names) {
      if (name == humanName) {
        avatars[name] = diceBearUrl(name, style: 'personas');
        // 人类不需要 TTS 音色
      } else {
        // AI 角色使用 character-specific DiceBear styles (d)
        final style = charDiceBearStyle[name] ?? 'notionists-neutral';
        avatars[name] = diceBearUrl(name, style: style);
        _voiceMap[name] = _resolveSpeakerVoice(
          name,
          isThinker: !templateAvatarMap.containsKey(name),
        );
      }
    }

    setState(() {
      _participants = names.map((name) {
        final isHuman = name == humanName;
        final isSpeaking = name == _currentSpeaker &&
            (_ttsPlaying || (isHuman && _isRecording));
        final isCurrentSpeaker = isSpeaking;
        final isDimmed = _currentSpeaker.isNotEmpty && !isSpeaking && !isHuman;
        return SeatedParticipant(
          name: name,
          avatar: avatars[name] ?? '🤖',
          isSpeaking: isSpeaking,
          isHuman: isHuman,
          hasRaisedHand: false,
          isCurrentSpeaker: isCurrentSpeaker,
          isDimmed: isDimmed,
        );
      }).toList();
    });
  }

  Future<void> _handleEvent(WsEvent event) async {
    final seq = event.eventSeq;
    if (seq != null) {
      if (seq <= _lastEventSeq) {
        if (kDebugMode) {
          debugPrint(
              '[SessionSeq] drop stale event seq=$seq last=$_lastEventSeq type=${event.eventType.name}');
        }
        return;
      }
      _lastEventSeq = seq;
    }

    switch (event.eventType) {
      case WsEventType.message:
        final data = event.data;
        if (data != null) {
          final humanName =
              ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
          final rawSource = ((data['source'] ?? '未知') as Object).toString();
          final normalizedSource =
              ImmersiveSessionScreen.normalizeSpeakerLabel(rawSource);
          final source = normalizedSource.isEmpty ? '未知' : normalizedSource;
          final content = ((data['content'] ?? '') as Object).toString();
          final msgType = ((data['msg_type'] ?? 'text') as Object).toString();
          final shouldSpeak = msgType != 'system' && source != humanName;
          final teacherSpeech = shouldSpeak &&
              _isTeacherSpeechMessage(source: source, msgType: msgType);
          if (teacherSpeech) {
            _markPendingTeacherFeedbackMetric(eventSeq: event.eventSeq);
          }
          if (shouldSpeak) {
            _clearAwaitingAiResponseAfterHumanSubmit(
              resetSpeechTurnLatch: true,
            );
          }
          if (ImmersiveSessionScreen
              .shouldResetCompletedHumanTurnOnIncomingSpeech(
            source: source,
            humanName: humanName,
            msgType: msgType,
          )) {
            _completedHumanTurnSpeaker = '';
          }
          final queuedTtsSegments = shouldSpeak
              ? ImmersiveSessionScreen.normalizeTtsSegmentsPayload(
                  rawSegments: data['tts_segments'] ?? data['tts_text'],
                  fallbackText: '', // stream 事件已播放分段TTS，message 事件不再重复入队
                )
              : const <String>[];

          // 需求7：去重 - 如果最后一条消息与当前完全相同，跳过重复
          if (_messages.isNotEmpty) {
            final last = _messages.last;
            if (last.source == source &&
                last.content == content &&
                last.type == msgType) {
              if (kDebugMode) {
                debugPrint(
                    '[MessageDedup] drop duplicate source=$source len=${content.length}');
              }
              break;
            }
          }

          // 需求8：暂停期间不再接收/排队 TTS，避免恢复后出现错乱的连续播放
          if (_isPaused) {
            if (shouldSpeak) {
              if (kDebugMode) {
                debugPrint(
                    '[PauseGuard] drop message while paused source=$source len=${content.length}');
              }
              break;
            }
          }

          setState(() {
            _messages.add(
                ChatMessage(source: source, content: content, type: msgType));

            // 关键同步策略：
            // - 需要TTS的消息，不在接收时抢先更新字幕；
            // - 在真正开始播放时再更新中心字幕，确保音字同时出现。
            if (!shouldSpeak) {
              final isSystemLike = msgType == 'system' || source == '系统';
              // 用户轮次期间，避免其他角色/系统字幕抢占显示。
              // 需求三：不同角色的讲话必须独立显示，严禁两人的字幕互相覆盖。
              // 只有当前发言者本人的非系统消息才允许写入字幕，避免两段
              // 不同角色的句子在同一字幕区反复切换。
              final sameSpeaker = source == _currentSpeaker ||
                  _currentSpeaker.isEmpty ||
                  source == _centerSpeaker ||
                  _centerSpeaker.isEmpty;
              if (!isSystemLike &&
                  !_ttsPlaying &&
                  sameSpeaker &&
                  (!_isMyTurn || source == humanName)) {
                // 字幕去除表情提示（如"（微笑）"），保持干净显示
                _centerMessage = _stripStageDirectionsForSpeech(content);
                _centerSpeaker = source;
              }
            }

            // Clear thinking state when message arrives (f)
            if (source == _currentSpeaker) {
              _isThinking = false;
              _thinkingController.stop();
            }
          });

          // 如果是 AI 角色/主持人消息，排队 TTS 朗读（顺序播放，i）
          if (shouldSpeak) {
            final voice = _resolveSpeakerVoice(source);
            for (final segment in queuedTtsSegments) {
              _enqueueTts(
                source: source,
                text: segment,
                voice: voice,
                eventSeq: event.eventSeq,
              );
            }
            // 批量入队后立即触发一次"全段并行预取"，规避逐句入队时
            // 120ms debounce 导致只有第 1 句被预取的问题——这是开场
            // "第二句字幕出来后停顿较长"的主要原因。
            if (queuedTtsSegments.length > 1) {
              _kickBatchPrefetch(
                items: queuedTtsSegments
                    .map((s) => (text: s, voice: voice))
                    .toList(),
              );
            }
          }
          if (teacherSpeech && _looksLikeTeacherClosingCue(content)) {
            _startHumanReviewPrefetchIfNeeded();
          }
          if (teacherSpeech && _containsTeacherFarewell(content)) {
            _revealHumanReviewOverlayAfterFarewell();
          }

          _buildParticipants();
        }
        break;
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final humanName =
              ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
          final speaker = ImmersiveSessionScreen.normalizeSpeakerLabel(
            ((data['speaker'] ?? '') as Object).toString(),
          );
          final isHuman = data['is_human'] ?? false;
          if (!isHuman || speaker != humanName) {
            _clearAwaitingAiResponseAfterHumanSubmit(
              resetSpeechTurnLatch: true,
            );
          }
          _completedHumanTurnSpeaker = '';
          if (isHuman &&
              speaker == humanName &&
              (_isCompletingHumanTurn || _isFinalizingSpeech)) {
            setState(() {
              _statusText = '正在整理你的话……';
            });
            _buildParticipants();
            return;
          }
          final sessionMatched = _isRealtimeSessionMatched(
              data: data, triggerRole: speaker, eventType: 'turn_change');

          if (!sessionMatched) {
            setState(() {
              _statusText = '上一段发言还在收尾，马上切到 $speaker';
              _isThinking = true;
              _thinkingController.repeat();
            });
            _prepareUpcomingPipeline(
              reason: 'turn-change-session-blocked:$speaker',
              includeAsrWarmup: true,
            );
            return;
          }

          if (isHuman &&
              speaker == humanName &&
              _hasCurrentSpeakerSpeechToFinish()) {
            _pumpCurrentSpeakerSpeechBeforeHumanTurn();
            _deferHumanTurnUntilCurrentSpeechEnds(
              speaker: speaker,
              prompt: _humanTurnWaitingPrompt(),
            );
            _buildParticipants();
            return;
          }

          if (!(isHuman && speaker == humanName) &&
              ImmersiveSessionScreen.shouldDeferTurnSwitchForSingleMic(
                speakerChanged: speaker != _currentSpeaker,
                ttsPlaying: _ttsPlaying,
                ttsServiceSpeaking: _ttsService.isSpeaking,
                hasQueuedSpeech: _ttsQueue.isNotEmpty,
              )) {
            _pumpCurrentSpeakerSpeechBeforeHumanTurn();
            setState(() {
              _statusText = '上一位还在发言，马上切到 $speaker';
              _isThinking = true;
              _thinkingController.repeat();
            });
            _prepareUpcomingPipeline(
              reason: 'turn-change-single-mic-wait:$speaker',
              includeAsrWarmup: !isHuman,
            );
            _buildParticipants();
            return;
          }

          if (isHuman && speaker == humanName && (_isMyTurn || _isRecording)) {
            setState(() {
              _currentSpeaker = speaker;
            });
            _buildParticipants();
            return;
          }

          _cancelTurnCountdown();
          _cancelMaxSpeechTimer();
          setState(() {
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == humanName;
            _hasRaisedHand = false;
            _sttPartialText = '';
            if (_isMyTurn) {
              // 需求4：新一轮轮到我，解锁麦克风
              _micLocked = false;
              _isThinking = false;
              _thinkingController.stop();
              // 需求20：暂停时不提示"请按住麦克风讲话"
              _statusText = _isPaused ? '暂停中...' : _readyToSpeakStatusText();
              if (!_isPaused) {
                _glowController.repeat(reverse: true);
                _keyboardFocusNode.requestFocus();
                _startTurnCountdown();
              }
            } else if (isHuman) {
              _isThinking = false;
              _thinkingController.stop();
              _statusText = '$speaker 正在说话……';
              _glowController.stop();
            } else {
              _isThinking = true;
              _thinkingController.repeat();
              _statusText = '$speaker 思考中……';
              _glowController.stop();
            }
          });
          _prepareUpcomingPipeline(
            reason: 'turn-change:$speaker',
            includeAsrWarmup: !isHuman,
          );
          if (isHuman && speaker == humanName) {
            _scheduleAutoMicStart();
          }
          _buildParticipants();
          // 确保 TTS 队列在角色切换后继续播放（修复跳过后无声音 bug）
          if (!_ttsPlaying && _ttsQueue.isNotEmpty) {
            Future.delayed(const Duration(milliseconds: 160), () {
              if (mounted && !_ttsPlaying && _ttsQueue.isNotEmpty) {
                _playNextTts();
              }
            });
          }
        }
        break;
      case WsEventType.stream:
        final data = event.data;
        if (data != null) {
          final humanName =
              ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
          final source = ImmersiveSessionScreen.normalizeSpeakerLabel(
            (data['source'] ?? '').toString(),
          );
          final content = (data['content'] ?? '').toString();
          final queuedTtsSegments =
              ImmersiveSessionScreen.normalizeTtsSegmentsPayload(
            rawSegments: data['tts_segments'],
          );
          // 语音与字幕同步：AI 流式文本不提前渲染，统一在 TTS 开始时显示。
          if (source != humanName) {
            final teacherSpeech = _isTeacherSpeechMessage(
              source: source,
              msgType: 'text',
            );
            if (teacherSpeech && queuedTtsSegments.isNotEmpty) {
              _markPendingTeacherFeedbackMetric(eventSeq: event.eventSeq);
            }
            _clearAwaitingAiResponseAfterHumanSubmit(
              resetSpeechTurnLatch: true,
            );
            _completedHumanTurnSpeaker = '';
            _isRealtimeSessionMatched(
              data: data,
              triggerRole: source,
              eventType: 'stream',
            );
            if (queuedTtsSegments.isNotEmpty) {
              final voice = _resolveSpeakerVoice(source);
              for (final segment in queuedTtsSegments) {
                _enqueueTts(
                  source: source,
                  text: segment,
                  voice: voice,
                  eventSeq: event.eventSeq,
                );
              }
              if (queuedTtsSegments.length > 1) {
                _kickBatchPrefetch(
                  items: queuedTtsSegments
                      .map((s) => (text: s, voice: voice))
                      .toList(),
                );
              }
            }
            _debugSubtitleLog(
              triggerRole: source,
              note: queuedTtsSegments.isEmpty
                  ? 'stream subtitle blocked for non-human'
                  : 'stream tts queued; subtitle blocked for non-human',
            );
            break;
          }
          final token = _subtitleToken;
          if (source.isNotEmpty && content.isNotEmpty) {
            if (_canRenderSubtitle(triggerRole: source, token: token)) {
              setState(() {
                _centerSpeaker = source;
                _centerMessage = content;
              });
              _debugSubtitleLog(triggerRole: source, note: 'stream render');
            } else {
              _debugSubtitleLog(
                triggerRole: source,
                note: 'stream blocked (speaker mismatch)',
              );
            }
          }
        }
        break;
      case WsEventType.stateChange:
        final data = event.data;
        if (data != null) {
          final newState = data['new_state'] ?? '';
          final newLabel = data['new_label'] ?? newState;
          if (newState == 'ai_speaking' ||
              newState == 'selecting_speaker' ||
              newState == 'moderator_opening' ||
              newState == 'closing' ||
              newState == 'ended') {
            _clearAwaitingAiResponseAfterHumanSubmit(
              resetSpeechTurnLatch: true,
            );
          }
          if (!ImmersiveSessionScreen.shouldApplyHumanStateStatus(
            newState: newState,
            humanName: widget.humanName,
            currentSpeaker: _currentSpeaker,
            isMyTurn: _isMyTurn,
            isRecording: _isRecording,
            isCompletingHumanTurn: _isCompletingHumanTurn,
            isFinalizingSpeech: _isFinalizingSpeech,
            hasStartedSpeechThisTurn: _hasStartedSpeechThisTurn,
          )) {
            break;
          }
          if (newState == 'interrupted') {
            setState(() => _statusText = '有人想补充一句……');
          } else if (newState == 'human_turn_waiting' && _isPaused) {
            // 需求20：暂停状态下不显示"轮到你了"之类的提示
          } else {
            setState(
                () => _statusText = _friendlyStateText(newState, newLabel));
          }
        }
        break;
      case WsEventType.phaseTelemetry:
        final data = event.data;
        if (data != null) {
          final obs = data['send_observability'];
          int sendDropTotal = 0;
          String sendDropReasons = '';
          String sendDropLast = '';
          if (obs is Map) {
            final totalRaw = obs['drop_total'];
            if (totalRaw is num) {
              sendDropTotal = totalRaw.toInt();
            }
            final reasonsRaw = obs['drop_reasons'];
            if (reasonsRaw is Map) {
              final pairs = <String>[];
              reasonsRaw.forEach((k, v) {
                pairs.add('$k=$v');
              });
              sendDropReasons = pairs.join(', ');
            }
            final lastRaw = obs['last_drop'];
            if (lastRaw is Map) {
              final lastReason = (lastRaw['reason'] ?? '').toString();
              final lastType = (lastRaw['event_type'] ?? '').toString();
              if (lastReason.isNotEmpty || lastType.isNotEmpty) {
                sendDropLast =
                    '$lastReason${lastType.isNotEmpty ? ' ($lastType)' : ''}';
              }
            }
          }
          _pushPhaseTelemetry(
            source: (data['source'] ?? 'backend').toString(),
            phase: (data['phase'] ?? '').toString(),
            reason: (data['reason'] ?? 'phase_transition').toString(),
            recovery: data['recovery'] == true,
            speaker: (data['speaker'] ?? '').toString(),
            eventSeq: event.eventSeq,
            designateStage: (data['designate_stage'] ?? '').toString(),
            designateTarget: (data['designate_target'] ?? '').toString(),
            sendDropTotal: sendDropTotal,
            sendDropReasons: sendDropReasons,
            sendDropLast: sendDropLast,
          );
        }
        break;
      case WsEventType.system:
        final data = event.data;
        if (data != null) {
          // 从系统事件捕获参与者列表
          final participants = data['participants'];
          if (participants is List) {
            _knownParticipants = participants
                .map((participant) =>
                    ImmersiveSessionScreen.normalizeSpeakerLabel(
                        participant.toString()))
                .where((participant) => participant.isNotEmpty)
                .toList(growable: false);
            _buildParticipants();
          }
          setState(() {
            _messages.add(ChatMessage(
              source: '系统',
              content: data['message'] ?? '',
              type: 'system',
            ));
          });
        }
        break;
      case WsEventType.humanInputRequested:
        final humanName =
            ImmersiveSessionScreen.normalizeSpeakerLabel(widget.humanName);
        if (_awaitingAiResponseAfterHumanSubmit) {
          break;
        }
        final requestedSpeaker = ImmersiveSessionScreen.normalizeSpeakerLabel(
          ((event.data?['speaker'] ?? '') as Object).toString(),
        );
        final normalizedCurrentSpeaker =
            ImmersiveSessionScreen.normalizeSpeakerLabel(_currentSpeaker);
        if (requestedSpeaker.isEmpty &&
            normalizedCurrentSpeaker.isNotEmpty &&
            normalizedCurrentSpeaker != humanName &&
            !_isMyTurn) {
          break;
        }
        if (requestedSpeaker.isNotEmpty && requestedSpeaker != humanName) {
          break;
        }
        if (ImmersiveSessionScreen.shouldIgnoreRepeatedHumanInputRequest(
          requestedSpeaker:
              requestedSpeaker.isEmpty ? humanName : requestedSpeaker,
          completedSpeaker: _completedHumanTurnSpeaker,
        )) {
          break;
        }
        if (_isCompletingHumanTurn || _isFinalizingSpeech) {
          break;
        }
        final requestedHumanSpeaker =
            requestedSpeaker.isEmpty ? widget.humanName : requestedSpeaker;
        final requestReason =
            ((event.data?['reason'] ?? '') as Object).toString().trim();
        if (ImmersiveSessionScreen.shouldMergeConcurrentHumanTurnSignals(
          requestedSpeaker: requestedHumanSpeaker,
          humanName: widget.humanName,
          isMyTurn: _isMyTurn,
          pendingHumanTurn: _pendingHumanTurn,
          handApprovedToSpeak: _handApprovedToSpeak,
        )) {
          break;
        }
        // If already in user's turn or actively recording, ignore duplicate requests
        if (_isMyTurn || _isRecording) {
          break;
        }
        var shouldWaitForCurrentSpeech = _hasCurrentSpeakerSpeechToFinish();
        if (shouldWaitForCurrentSpeech) {
          _pumpCurrentSpeakerSpeechBeforeHumanTurn();
          shouldWaitForCurrentSpeech = _hasCurrentSpeakerSpeechToFinish();
        }
        final command = _commander.onHumanInputRequested(
          speaker: requestedHumanSpeaker,
          requestReason: requestReason,
          hasOngoingSpeechPlayback: shouldWaitForCurrentSpeech,
        );
        if (command == HumanTurnCommand.defer) {
          final waitingPrompt = shouldWaitForCurrentSpeech
              ? _humanTurnWaitingPrompt()
              : '李老师正在把发言权交给你，请稍候一下';
          _deferHumanTurnUntilCurrentSpeechEnds(
            speaker: requestedHumanSpeaker,
            prompt: waitingPrompt,
          );
        } else if (command == HumanTurnCommand.activateNow) {
          _activateHumanTurnNow(speaker: requestedHumanSpeaker);
        }
        break;
      case WsEventType.apiError:
        _clearAwaitingAiResponseAfterHumanSubmit();
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? 'AI 服务错误';
        final friendly = friendlyErrorMessage(errMsg.toString());
        if (friendly.isEmpty) {
          break;
        }
        setState(() {
          _lastErrorMessage = friendly;
          _statusText = friendly;
        });
        _showStatusToast(_lastErrorMessage!, isError: true);
        break;
      case WsEventType.error:
        _clearAwaitingAiResponseAfterHumanSubmit();
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? '未知错误';
        final friendly = friendlyErrorMessage(errMsg.toString());
        if (friendly.isEmpty) {
          break;
        }
        setState(() {
          _lastErrorMessage = friendly;
          _statusText = '错误: $_lastErrorMessage';
        });
        _showStatusToast(_lastErrorMessage!, isError: true);
        break;
      case WsEventType.ended:
        _clearAwaitingAiResponseAfterHumanSubmit(resetSpeechTurnLatch: true);
        _stopDiscussionClock();
        final endedWithError = _lastErrorMessage != null;
        final shouldGenerateHumanReview = !endedWithError &&
            ImmersiveSessionScreen.hasHumanReviewMaterial(
              _messages,
              humanName: widget.humanName,
            );
        setState(() {
          _statusText = endedWithError
              ? '会话已中断: ${_lastErrorMessage!}'
              : (_teacherFarewellHeard && shouldGenerateHumanReview)
                  ? '讨论已结束，李老师正在给你写会后点评'
                  : '讨论已结束';
          _isMyTurn = false;
          _glowController.stop();
          _goldenQuotes.clear();
          _discussionEnded = true;
          _showEndingQuotesScreen = false;
        });
        if (shouldGenerateHumanReview) {
          _startHumanReviewPrefetchIfNeeded();
          if (_teacherFarewellHeard) {
            unawaited(() async {
              await _waitForMainTtsToSettleBeforeHumanReview();
              if (!mounted || _lastErrorMessage != null) return;
              setState(() {
                _centerMessage = '';
              });
              _revealHumanReviewOverlayAfterFarewell();
            }());
          }
        }
        break;
      case WsEventType.interrupt:
        final data = event.data;
        if (data != null) {
          final interrupter = data['interrupter'] ?? '';
          final approvedBy = (data['approved_by'] ?? '李老师').toString();
          final approved = data['approved'] == true;
          final mineApproved = approved && interrupter == widget.humanName;
          final mineInterrupt = interrupter == widget.humanName;
          if (mineApproved) {
            _completedHumanTurnSpeaker = '';
          }
          var shouldWaitForCurrentSpeech = _hasCurrentSpeakerSpeechToFinish();
          if (mineApproved && shouldWaitForCurrentSpeech) {
            _pumpCurrentSpeakerSpeechBeforeHumanTurn();
            shouldWaitForCurrentSpeech = _hasCurrentSpeakerSpeechToFinish();
          }
          final shouldMergeApprovedInterrupt = mineApproved &&
              ImmersiveSessionScreen.shouldMergeConcurrentHumanTurnSignals(
                requestedSpeaker: widget.humanName,
                humanName: widget.humanName,
                isMyTurn: _isMyTurn,
                pendingHumanTurn: _pendingHumanTurn,
                handApprovedToSpeak: _handApprovedToSpeak,
              );
          final command = shouldMergeApprovedInterrupt
              ? HumanTurnCommand.none
              : _commander.onInterruptApprovedForHuman(
                  mineApproved: mineApproved,
                  speaker: widget.humanName,
                  hasOngoingSpeechPlayback: shouldWaitForCurrentSpeech,
                );
          setState(() {
            if (mineInterrupt) {
              _hasRaisedHand = false;
            }
            _messages.add(ChatMessage(
              source: approvedBy,
              content: '$interrupter 同学，请先发言。',
              type: 'system',
            ));
            if (mineApproved && shouldMergeApprovedInterrupt) {
              _statusText = _isMyTurn ? '老师已确认由你继续发言' : '老师已经把这一轮发言留给你了';
            } else if (mineApproved && command == HumanTurnCommand.defer) {
              _statusText = '李老师已同意你先说，现在可以开始了';
            }
          });
          if (mineApproved && command == HumanTurnCommand.activateNow) {
            _activateHumanTurnNow(speaker: widget.humanName);
          } else if (mineApproved && command == HumanTurnCommand.defer) {
            _deferHumanTurnUntilCurrentSpeechEnds(
              speaker: widget.humanName,
              prompt: _humanTurnWaitingPrompt(approved: true),
            );
          }
          _buildParticipants();
        }
        break;
    }
  }

  // ── 文本输入已完全移除：本会话为纯语音模式 ─────────────────────────────────

  // ── Push-to-Talk ────────────────────────────────────────────────────────────

  void _onPttStart({bool startedFromHoldCtrl = false}) {
    _cancelAutoMicStart();
    if (!(_isMyTurn || _handApprovedToSpeak)) {
      if (_pendingHumanTurn) {
        _showStatusToast('老师正在给你留出发言窗口，等麦克风亮起后再开始');
        return;
      }
      _showStatusToast('现在还没轮到你，先听听大家怎么说');
      return;
    }
    if (_isRecording) {
      // 已在录音中，忽略重复触发
      return;
    }
    if (!_asrService.isAvailable) {
      _reportAsrStatus(
        'not_available',
        available: false,
        listening: _asrService.isListening,
      );
      setState(() {
        _statusText = '麦克风暂时用不了，请先检查语音识别设置';
      });
      _showStatusToast('麦克风暂时用不了，请先在设置页检查语音识别');
      return;
    }
    _cancelTurnCountdown();
    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = null;
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
    _clearAwaitingAiResponseAfterHumanSubmit();

    // 需求2：纯语音模式，不存在文字输入

    _recordingControlledByHoldCtrl = startedFromHoldCtrl;
    _hasStartedSpeechThisTurn = true;
    _startDiscussionClockIfNeeded();
    setState(() {
      _isRecording = true;
      _centerSpeaker = widget.humanName;
      _centerMessage = '';
      _sttPartialText = '';
      _lastNonEmptySttText = '';
      // 需求四：用户按下热键/点击麦克风启动后，立即撤掉
      // "轮到你了，按住麦克风讲话"的残留提示，改成"正在聆听..."。
      _statusText = '正在听你说……';
    });
    _acquireSubtitleToken(widget.humanName);
    _micController.repeat(reverse: true);
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _reportAsrStatus(
      'start_requested',
      available: _asrService.isAvailable,
      listening: _asrService.isListening,
    );
    unawaited(() async {
      await _asrService.startListening();
      _reportAsrStatus(
        'start_result',
        available: _asrService.isAvailable,
        listening: _asrService.isListening,
      );
      if (!mounted) return;
      // 若语音识别未真正启动，不要继续假录音状态，提示用户直接重试语音。
      if (!_asrService.isListening) {
        _cancelMaxSpeechTimer();
        _awaitingAsrFirstPacket = false;
        _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
        _micController.stop();
        _micController.reset();
        _reportAsrStatus(
          'start_failed',
          available: _asrService.isAvailable,
          listening: _asrService.isListening,
        );
        _recordingControlledByHoldCtrl = false;
        _hasStartedSpeechThisTurn = false;
        setState(() {
          _isRecording = false;
          _statusText = '麦克风没有成功打开，请再试一次';
        });
        _showStatusToast('麦克风没有成功打开，请再试一次');
      }
    }());
    _asrListenStartAt = DateTime.now();
    _awaitingAsrFirstPacket = true;
    _speechStartTime = DateTime.now();
    _startMaxSpeechTimer();
  }

  void _onPttEnd({String reason = 'manual'}) {
    if (!_isRecording) return;
    _cancelAutoMicStart();
    if (_isFinalizingSpeech) return; // 防止并发 finalize
    _isFinalizingSpeech = true;
    _isCompletingHumanTurn = true;
    _clearAwaitingAiResponseAfterHumanSubmit();
    _cancelMaxSpeechTimer();
    _recordingControlledByHoldCtrl = false;
    setState(() {
      _isRecording = false;
      _micLocked = true;
      _statusText = '正在整理你的话……';
    });
    _micController.stop();
    _micController.reset();
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _reportAsrStatus(
      'stop_requested_$reason',
      listening: _asrService.isListening,
    );
    try {
      _asrService.stopListening();
    } catch (e) {
      if (kDebugMode) debugPrint('[ASR] stopListening error: $e');
    }
    _reportAsrStatus(
      'stop_called_$reason',
      listening: _asrService.isListening,
    );
    _awaitingAsrFirstPacket = false;

    _scheduleSpeechFinalize();
  }

  void _scheduleSpeechFinalize({
    Duration delay = const Duration(milliseconds: 140),
  }) {
    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = Timer(delay, () {
      unawaited(_flushSpeechFinalizeNow());
    });
  }

  Future<void> _flushSpeechFinalizeNow() async {
    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = null;
    if (!_isFinalizingSpeech || _speechFinalizeRunning) {
      return;
    }
    _speechFinalizeRunning = true;
    try {
      await _finalizeSpeechWithRetry();
    } finally {
      _speechFinalizeRunning = false;
      _isFinalizingSpeech = false;
    }
  }

  Future<void> _finalizeSpeechWithRetry() async {
    if (!mounted) return;
    String rawText = (_sttPartialText.trim().isNotEmpty
            ? _sttPartialText.trim()
            : _lastNonEmptySttText.trim())
        .trim();

    // 优先利用 final 包立即收尾；没有 final 时只保留很短的兜底等待。
    final deadline = DateTime.now().add(const Duration(milliseconds: 2400));
    while (rawText.isEmpty && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 120));
      if (!mounted) return;
      rawText = (_sttPartialText.trim().isNotEmpty
              ? _sttPartialText.trim()
              : _lastNonEmptySttText.trim())
          .trim();
    }

    var refined = '';
    if (rawText.isNotEmpty) {
      try {
        refined = await _refineTranscript(rawText).timeout(
          const Duration(milliseconds: 1600),
          onTimeout: () => polishTranscript(rawText),
        );
      } catch (_) {
        refined = polishTranscript(rawText);
      }
    }
    if (!mounted) return;
    setState(() => _sttPartialText = '');

    // 若 ASR 仍为空，不自动跳过，保留用户回合并允许继续语音重试。
    if (refined.trim().isEmpty && rawText.isEmpty) {
      _reportAsrStatus(
        'final_empty',
        listening: _asrService.isListening,
        textLen: 0,
      );
      setState(() {
        _isCompletingHumanTurn = false;
        _isMyTurn = true;
        _micLocked = false;
        _statusText = '我还没听清，再说一次吧';
      });
      _showStatusToast('我还没听清，再说一次吧');
      _startTurnCountdown();
      unawaited(_applyPendingVoiceConfigIfIdle());
      return;
    }
    final submitText = refined.trim().isNotEmpty ? refined.trim() : rawText;
    final recording = await _persistLastAsrCaptureForHistory(submitText);
    _reportAsrStatus(
      'submitted',
      listening: _asrService.isListening,
      textLen: submitText.length,
      isFinal: true,
    );

    _wsClient.sendHumanInput(
      speaker: widget.humanName,
      content: submitText,
      recording: recording,
    );
    _awaitingAiResponseAfterHumanSubmit = true;
    _awaitingTeacherFeedbackMetric = submitText != '（跳过）';
    _pendingTeacherReplySeenAt = null;
    _pendingTeacherReplyEventSeq = null;
    _ttsFirstAudioPendingBySession.clear();
    _teacherReplyWarmupVisible = false;
    if (_isMeaningfulImmediateFeedbackText(submitText)) {
      _pendingFeedbackOverlayMetricAt = DateTime.now();
      _showImmediateFeedbackOverlay(submitText);
    } else {
      _pendingFeedbackOverlayMetricAt = null;
    }
    _scheduleHumanResponseWatchdog();
    _completedHumanTurnSpeaker = widget.humanName;
    setState(() {
      // 不直接添加到 _messages，避免后端 message 事件重复添加
      // 后端收到 human_input 后会发送 message 事件，前端 _handleEvent 中会添加
      _isCompletingHumanTurn = false;
      _isMyTurn = false;
      // 需求4：发言完毕，锁定麦克风为灰色，直到下轮或举手批准
      _micLocked = true;
      _statusText = '你说完了，大家正在回应……';
      _centerMessage = submitText;
      _centerSpeaker = widget.humanName;
    });
    _commander.markHumanTurnCompleted();
    _cancelPendingHumanTurnGuard();

    // 字幕保留
    _startSubtitleRetain(submitText);
    _holdAiUntilHumanSubtitleDone(submitText);
    unawaited(_applyPendingVoiceConfigIfIdle());
  }

  Future<Map<String, dynamic>?> _persistLastAsrCaptureForHistory(
      String transcript) async {
    if (_discussionSessionId.isEmpty) {
      return null;
    }

    final capture = await _asrService.takeLastCapture();
    if (capture == null || capture.bytes.isEmpty) {
      return null;
    }

    try {
      final result = await ref.read(apiClientProvider).uploadMeetingRecording(
            sessionId: _discussionSessionId,
            speaker: widget.humanName,
            audioBytes: capture.bytes,
            fileExtension: capture.fileExtension,
            contentType: capture.contentType,
            durationMs: capture.durationMs,
            transcript: transcript,
          );
      final rawRecording = result['recording'];
      if (rawRecording is Map) {
        return Map<String, dynamic>.from(rawRecording);
      }
    } catch (error) {
      if (kDebugMode) {
        debugPrint('[HistoryRecording] upload failed: $error');
      }
    }
    return null;
  }

  // ── 跳过本轮发言 ────────────────────────────────────────────────────────────

  void _onSkipTurn({
    String? reason,
    bool addUserMessage = true,
    bool isAuto = false,
  }) {
    if (isAuto) {
      _handleAutoSkipReminder(reason: reason);
      return;
    }
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
    _clearAwaitingAiResponseAfterHumanSubmit(resetSpeechTurnLatch: true);
    _clearPendingTeacherFeedbackMetric();
    _activeTtsItem = null;
    _activeTtsSessionId = '';
    _wsClient.sendHumanInput(speaker: widget.humanName, content: '（跳过）');
    setState(() {
      if (addUserMessage) {
        _messages.add(ChatMessage(
          source: widget.humanName,
          content: '（跳过）',
          type: 'text',
        ));
      } else if (reason != null && reason.isNotEmpty) {
        _messages
            .add(ChatMessage(source: '系统', content: reason, type: 'system'));
      }
      _isMyTurn = false;
      // 需求4：跳过后也锁定麦克风
      _micLocked = true;
      _statusText = reason ?? '这一轮先听听别人怎么说';
    });
    if (reason != null && reason.isNotEmpty) {
      _showStatusToast(reason);
    }
    _humanSubtitleLockUntil = null;
    _humanSubtitleLockTimer?.cancel();
    _awaitingAsrFirstPacket = false;
    // 跳过后确保 TTS 队列继续处理（修复跳过后无声音的 bug）
    if (_commander.onAutoSkipShouldPumpTts(
      hasQueuedTts: _ttsQueue.isNotEmpty,
      isTtsPlaying: _ttsPlaying,
    )) {
      _cancelPendingHumanTurnGuard();
      Future.delayed(const Duration(milliseconds: 500), () {
        if (mounted && _ttsQueue.isNotEmpty && !_ttsPlaying) {
          _playNextTts();
        }
      });
    } else {
      _cancelPendingHumanTurnGuard();
    }
  }

  // ── User turn countdown (bug 3) ────────────────────────────────────────────

  void _startTurnCountdown() {
    _cancelTurnCountdown();
    setState(() => _turnCountdown = 30);
    _turnTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      final shouldKeepCountdown =
          ImmersiveSessionScreen.shouldKeepTurnCountdownActive(
        isMyTurn: _isMyTurn,
        hasSpeechDraft: _sttPartialText.trim().isNotEmpty,
        isCompletingHumanTurn: _isCompletingHumanTurn,
        isFinalizingSpeech: _isFinalizingSpeech,
      );
      if (!shouldKeepCountdown) {
        _cancelTurnCountdown();
        return;
      }
      if (_isRecording) {
        return;
      }
      setState(() => _turnCountdown--);
      if (_turnCountdown <= 0) {
        timer.cancel();
        _onSkipTurn(
          reason: '30秒还没开始语音发言',
          isAuto: true,
        );
      }
    });
  }

  void _cancelTurnCountdown() {
    _turnTimer?.cancel();
    _turnTimer = null;
    if (_turnCountdown > 0) setState(() => _turnCountdown = 0);
  }

  // ── 3分钟最大发言计时器（需求2）──────────────────────────────────────────────

  void _startMaxSpeechTimer() {
    _cancelMaxSpeechTimer();
    _maxSpeechTimer = Timer(const Duration(minutes: 3), () {
      if (!mounted) return;
      if (_isRecording) {
        // 自动结束录音并提交
        _onPttEnd(reason: 'max_duration');
        setState(() {
          _statusText = '你已经说了比较久，系统先帮你提交了';
        });
        _showStatusToast('你已经说了比较久，系统先帮你提交了');
      }
    });
  }

  void _cancelMaxSpeechTimer() {
    _maxSpeechTimer?.cancel();
    _maxSpeechTimer = null;
  }

  // ── 字幕保留计时器（需求2）──────────────────────────────────────────────────

  void _startSubtitleRetain(String text) {
    _subtitleRetainTimer?.cancel();
    // 保留时间 = min(2~4秒, 发言时长×0.5)，不少于1秒
    final speechDuration = _speechStartTime != null
        ? DateTime.now().difference(_speechStartTime!).inMilliseconds
        : 2000;
    final retainMs = (speechDuration * 0.5).clamp(1000, 4000).toInt();
    _subtitleRetainTimer = Timer(Duration(milliseconds: retainMs), () {
      // 仅当字幕仍然显示用户发言时才清除
      if (mounted && _centerSpeaker == widget.humanName) {
        setState(() {
          // 不清除字幕，让下一位发言者的TTS接管
        });
      }
    });
    _speechStartTime = null;
  }

  void _holdAiUntilHumanSubtitleDone(String text) {
    final holdDuration =
        ImmersiveSessionScreen.humanSubtitleHoldDurationFor(text);
    _humanSubtitleLockUntil = DateTime.now().add(holdDuration);
    _humanSubtitleLockTimer?.cancel();
    _subtitlePageTimer?.cancel();
    _scheduleSubtitlePageAdvance();
    _humanSubtitleLockTimer = Timer(holdDuration, () {
      if (!mounted) return;
      if (!_isMyTurn && !_isRecording && !_ttsPlaying && _ttsQueue.isNotEmpty) {
        _playNextTts();
      }
    });
  }

  // ── TTS 顺序播放队列 (i) ────────────────────────────────────────────────────

  /// Add text to the TTS queue and start playback if not already playing.
  void _enqueueTts(
      {required String source,
      required String text,
      String? voice,
      int? eventSeq}) {
    // 暂停期间直接丢弃新增 TTS 请求；恢复时仅重读暂停前的当前字幕。
    if (_isPaused) return;
    final speechText = _stripStageDirectionsForSpeech(text);
    if (speechText.isEmpty) return;
    _startDiscussionClockIfNeeded();
    final playbackSessionId = _nextTtsPlaybackSessionId();
    _ttsQueue.add((
      source: source,
      text: speechText,
      voice: voice,
      playbackSessionId: playbackSessionId,
      enqueuedAt: DateTime.now(),
    ));
    if (_pendingTeacherReplySeenAt != null &&
        _isTeacherSpeechMessage(source: source, msgType: 'text')) {
      _ttsFirstAudioPendingBySession[playbackSessionId] = (
        replySeenAt: _pendingTeacherReplySeenAt!,
        eventSeq: _pendingTeacherReplyEventSeq ?? eventSeq,
      );
      _teacherReplyWarmupVisible = true;
      _pendingTeacherReplySeenAt = null;
      _pendingTeacherReplyEventSeq = null;
    }
    _lastMainTtsQueuedAt = DateTime.now();
    if (_openingCueState == _OpeningCueState.preparing && mounted) {
      setState(() {
        _openingCueState = _OpeningCueState.ready;
        _openingReadyShownAt = DateTime.now();
      });
    }
    _prepareUpcomingPipeline(reason: 'enqueue-tts:$source:$playbackSessionId');
    _scheduleTtsPumpGuard();
    if (!_ttsPlaying) _playNextTts();
  }

  String _stripStageDirectionsForSpeech(String text) {
    var v = text.trim();
    if (v.isEmpty) return '';
    // 去掉舞台提示，如（微笑）或(轻声)
    v = v.replaceAll(RegExp(r'[（(][^）)]{1,40}[）)]'), ' ');
    v = v.replaceAll(RegExp(r'\s+'), ' ').trim();
    return v;
  }

  Future<void> _stopActiveSpeechPlayback({
    Duration settleDelay = const Duration(milliseconds: 80),
  }) async {
    try {
      await _ttsService.stop();
    } catch (_) {}
    try {
      await _browserFallbackTts?.stop();
    } catch (_) {}
    if (settleDelay > Duration.zero) {
      await Future<void>.delayed(settleDelay);
    }
  }

  /// Play next item in queue; called recursively until queue is empty.
  Future<void> _playNextTts() async {
    if (_ttsPumpRunning) return;
    _ttsPumpRunning = true;
    try {
      if (_ttsQueue.isEmpty || _isPaused) {
        _ttsPlaying = false;
        _activeTtsItem = null;
        _activeTtsSessionId = '';
        unawaited(_applyPendingVoiceConfigIfIdle());
        _tryActivatePendingHumanTurn();
        return;
      }
      if (_isMyTurn || _isRecording) {
        _ttsPlaying = false;
        _activeTtsItem = null;
        _activeTtsSessionId = '';
        Future.delayed(const Duration(milliseconds: 180), () {
          if (!mounted) return;
          if (_ttsQueue.isNotEmpty &&
              !_ttsPlaying &&
              !_isMyTurn &&
              !_isRecording) {
            _playNextTts();
          }
        });
        return;
      }
      final lockUntil = _humanSubtitleLockUntil;
      if (lockUntil != null && DateTime.now().isBefore(lockUntil)) {
        _ttsPlaying = false;
        _activeTtsItem = null;
        _activeTtsSessionId = '';
        if (mounted &&
            _centerSpeaker == widget.humanName &&
            !_isMyTurn &&
            !_isRecording &&
            _ttsQueue.isNotEmpty &&
            _statusText != ImmersiveSessionScreen.humanResponseBufferText()) {
          setState(() {
            _statusText = ImmersiveSessionScreen.humanResponseBufferText();
          });
        }
        final waitMs = lockUntil.difference(DateTime.now()).inMilliseconds;
        Future.delayed(Duration(milliseconds: waitMs.clamp(80, 3000)), () {
          if (mounted && !_ttsPlaying && _ttsQueue.isNotEmpty && !_isMyTurn) {
            _playNextTts();
          }
        });
        return;
      }
      if (_ttsService.isSpeaking ||
          (_browserFallbackTts?.isSpeaking ?? false)) {
        await _stopActiveSpeechPlayback();
      }
      _ttsPlaying = true;
      final item = _ttsQueue.removeAt(0);
      _activeTtsItem = item;
      _activeTtsSessionId = item.playbackSessionId;
      final ttsFirstAudioMetric =
          _ttsFirstAudioPendingBySession.remove(item.playbackSessionId);
      var ttsFirstAudioMetricReported = false;
      final startupWaitMs =
          DateTime.now().difference(item.enqueuedAt).inMilliseconds;
      _ttsStartupSamples += 1;
      _ttsStartupTotalMs += startupWaitMs.toDouble();
      _pushSeriesSample(_ttsStartupSeries, startupWaitMs.toDouble());
      _acquireSubtitleToken(
        item.source,
        subtitleSessionId: item.playbackSessionId,
      );

      // 发言进行中并行准备后续环节：预合成后续语音 + 预热人类输入链路。
      _prepareUpcomingPipeline(
        reason: 'during-tts:${item.source}',
        includeAsrWarmup: _pendingHumanTurn || item.source != widget.humanName,
      );

      if (_openingCueState == _OpeningCueState.ready) {
        final shownAt = _openingReadyShownAt;
        final elapsed = shownAt == null
            ? Duration.zero
            : DateTime.now().difference(shownAt);
        final remaining =
            ImmersiveSessionScreen.openingStartCueMinDuration - elapsed;
        if (remaining > Duration.zero) {
          await Future<void>.delayed(remaining);
        }
      }

      var subtitleActivated = false;

      void activateSubtitleAtSpeechStart({required bool playbackStarted}) {
        if (!mounted || subtitleActivated) return;
        final shouldSwap =
            ImmersiveSessionScreen.shouldSwapSubtitleBeforeAiPlaybackStarts(
          currentCenterSpeaker: _centerSpeaker,
          humanName: widget.humanName,
          playbackStarted: playbackStarted,
        );
        if (!shouldSwap) {
          return;
        }
        subtitleActivated = true;
        setState(() {
          _currentSpeaker = item.source;
          _centerSpeaker = item.source;
          _centerMessage = item.text;
          _openingCueState = _OpeningCueState.done;
          _isThinking = false;
          _thinkingController.stop();
        });
        _debugSubtitleLog(triggerRole: item.source, note: 'tts render start');
        _buildParticipants();
      }

      if (mounted) {
        activateSubtitleAtSpeechStart(playbackStarted: false);
      }
      await Future<void>.delayed(ImmersiveSessionScreen.aiSubtitleLeadIn);

      final speed = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
        item.source,
        text: item.text,
      );
      bool played = false;
      try {
        final timeout = ImmersiveSessionScreen.ttsPlaybackTimeoutFor(
          item.text,
          rate: speed,
        );
        Object? lastErr;
        for (var attempt = 0; attempt < 2 && !played; attempt++) {
          try {
            await _ttsService.speak(
              item.text,
              voice: item.voice,
              rate: speed,
              onStart: () {
                _dismissTeacherReplyWaitingUi(clearText: true);
                if (!ttsFirstAudioMetricReported &&
                    ttsFirstAudioMetric != null) {
                  ttsFirstAudioMetricReported = true;
                  _awaitingTeacherFeedbackMetric = false;
                  _reportClientMetric(
                    name: 'tts_first_audio_delay_ms',
                    valueMs: DateTime.now()
                        .difference(ttsFirstAudioMetric.replySeenAt)
                        .inMilliseconds,
                    speaker: item.source,
                    phase: 'teacher_feedback',
                    eventSeq: ttsFirstAudioMetric.eventSeq,
                    detail: 'teacher_first_audio',
                  );
                }
                activateSubtitleAtSpeechStart(
                  playbackStarted: true,
                );
              },
            ).timeout(timeout);
            played = true;
          } catch (e) {
            lastErr = e;
            if (attempt == 0) {
              await Future<void>.delayed(const Duration(milliseconds: 200));
            }
          }
        }
        if (!played && lastErr != null) {
          throw lastErr;
        }
      } catch (e) {
        await _stopActiveSpeechPlayback(settleDelay: Duration.zero);
        _activeTtsItem = null;
        _activeTtsSessionId = '';
        _ttsPlaying = false;
        _lastMainTtsCompletedAt = DateTime.now();
        if (mounted) {
          setState(() {
            _statusText =
                _pendingHumanTurn ? '语音异常，已跳过当前播报并继续轮转' : '语音异常，已跳过当前播报';
            _messages.add(ChatMessage(
              source: '系统',
              content: '${item.source} 语音播放异常，已跳过该段继续讨论',
              type: 'system',
            ));
          });
          _showStatusToast('语音播报异常，已跳过当前片段继续讨论', isError: true);
          _buildParticipants();
        }
        _tryActivatePendingHumanTurn();
        return;
      }
      if (!played) {
        return;
      }
      if (_isPaused) {
        _ttsPlaying = false;
        return;
      }
      _activeTtsItem = null;
      _activeTtsSessionId = '';
      _lastMainTtsCompletedAt = DateTime.now();
      // After speak() resolves, record current prefetch hit rate and play next.
      _pushSeriesSample(_prefetchHitRateSeries, _prefetchHitRatePercent);
      if (!mounted) return;
      // 确保 TTS 完全停止后再播放下一条
      await Future.delayed(const Duration(milliseconds: 40));
      if (_ttsQueue.isEmpty) {
        _ttsPlaying = false;
        _buildParticipants();
        if (mounted && _showPerfPanel) {
          setState(() {});
        }
        _tryActivatePendingHumanTurn();
        return;
      }
      _ttsPlaying = false;

      final nextItem = _ttsQueue.isNotEmpty ? _ttsQueue.first : null;
      if (nextItem != null &&
          ImmersiveSessionScreen.shouldInsertInterSpeakerPause(
            currentSpeaker: item.source,
            nextSpeaker: nextItem.source,
            humanName: widget.humanName,
            pendingHumanTurn: _pendingHumanTurn,
            isMyTurn: _isMyTurn,
            isRecording: _isRecording,
          )) {
        final pause = ImmersiveSessionScreen.interSpeakerPauseDuration(
          currentSpeaker: item.source,
          nextSpeaker: nextItem.source,
          turnSeed: _ttsSpeakerPauseCounter++,
        );
        if (pause > Duration.zero) {
          await Future<void>.delayed(pause);
          if (!mounted ||
              _isPaused ||
              _isMyTurn ||
              _isRecording ||
              _pendingHumanTurn) {
            return;
          }
        }
      }
    } finally {
      _ttsPumpRunning = false;
      final shouldContinue = mounted &&
          !_isPaused &&
          !_ttsPlaying &&
          _ttsQueue.isNotEmpty &&
          !_isMyTurn &&
          !_isRecording;
      if (shouldContinue) {
        _scheduleTtsPumpGuard(delay: const Duration(milliseconds: 180));
        Future.microtask(_playNextTts);
      }
    }
  }

  // ── 暂停 / 继续 ─────────────────────────────────────────────────────────────

  void _onTogglePause() {
    if (_isPaused) {
      setState(() {
        _isPaused = false;
        _statusText = _pausedResumeTtsItem != null
            ? '讨论继续中，正在从头重读当前字幕……'
            : (_isMyTurn ? _readyToSpeakStatusText() : '讨论继续中……');
      });
      _resumeDiscussionClock();
      if (_isMyTurn) {
        _glowController.repeat(reverse: true);
        _keyboardFocusNode.requestFocus();
        _startTurnCountdown();
      } else if (_isThinking) {
        _thinkingController.repeat();
      }
      _wsClient.sendResume();
      _resumeCurrentSubtitleFromStartIfNeeded();
    } else {
      final item = _activeTtsItem;
      _pausedResumeTtsItem = (item != null && item.text.trim().isNotEmpty)
          ? (
              source: item.source,
              text: item.text,
              voice: item.voice,
            )
          : null;
      setState(() {
        _isPaused = true;
        _statusText = '讨论已暂停';
        _isThinking = false;
      });
      _pauseDiscussionClock();
      _commander.markHumanTurnCompleted();
      _cancelPendingHumanTurnGuard();
      _cancelTurnCountdown();
      _cancelMaxSpeechTimer();
      _speechFinalizeTimer?.cancel();
      _speechFinalizeTimer = null;
      _cancelAutoMicStart();
      _clearAwaitingAiResponseAfterHumanSubmit(resetSpeechTurnLatch: true);
      _glowController.stop();
      _thinkingController.stop();
      if (_isRecording) {
        _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
        _asrService.stopListening();
        _recordingControlledByHoldCtrl = false;
        _isRecording = false;
      }
      _awaitingAsrFirstPacket = false;
      _sttPartialText = '';
      _lastNonEmptySttText = '';
      // 暂停时清空后续队列；恢复后仅从当前片段起点重新播放，避免续播错位。
      _ttsQueue.clear();
      _activeTtsItem = null;
      _activeTtsSessionId = '';
      _ttsPlaying = false;
      _ttsService.stop();
      _browserFallbackTts?.stop();
      _wsClient.sendPause();
    }
  }

  // ── 打断 ────────────────────────────────────────────────────────────────────

  void _onInterrupt() {
    if (ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
      isMyTurn: _isMyTurn,
      pendingHumanTurn: _pendingHumanTurn,
      handApprovedToSpeak: _handApprovedToSpeak,
      hasRaisedHand: _hasRaisedHand,
    )) {
      if (_isMyTurn || _pendingHumanTurn || _handApprovedToSpeak) {
        _showStatusToast('老师已经把这一轮发言留给你了，不需要重复举手');
      }
      return;
    }
    setState(() => _hasRaisedHand = true);
    _wsClient.sendInterrupt(speaker: widget.humanName);
    _showStatusToast('已举手，等待主持人分配发言');
  }

  bool get _isPushToTalk {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.pushToTalk ?? true;
  }

  void _onKeyEvent(KeyEvent event) {
    // 使用 HardwareKeyboard 全局处理配置热键，避免重复触发。
  }

  @override
  void dispose() {
    _disposed = true;
    _bgTaskQueue.clear();
    _turnTimer?.cancel();
    _ctrlTapTimer?.cancel();
    _speechFinalizeTimer?.cancel();
    _maxSpeechTimer?.cancel();
    _subtitleRetainTimer?.cancel();
    _humanSubtitleLockTimer?.cancel();
    _pendingHumanTurnGuardTimer?.cancel();
    _ttsPumpGuardTimer?.cancel();
    _humanResponseWatchdogTimer?.cancel();
    _deferredAutoSkipTimer?.cancel();
    _autoMicStartTimer?.cancel();
    _discussionClockTimer?.cancel();
    _statusToastTimer?.cancel();
    _statusToastEntry?.remove();
    _quickFeedbackTimer?.cancel();
    _teacherReplyWarmupTimer?.cancel();
    _ctrlHeld = false;
    _awaitingAsrFirstPacket = false;
    HardwareKeyboard.instance.removeHandler(_onHardwareKey);
    _settingsSubscription?.close();
    _asrTranscriptionSub?.cancel();
    if (_voiceServicesInitialized) {
      _ttsService.dispose();
      _browserFallbackTts?.dispose();
      _asrService.dispose();
    }
    _wsClient.dispose();
    _scrollController.dispose();
    _keyboardFocusNode.dispose();
    _candleController.dispose();
    _glowController.dispose();
    _micController.dispose();
    _thinkingController.dispose();
    _endingQuotesTransitionController.dispose();
    super.dispose();
  }

  void _showChatHistory() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.studyWall.withValues(alpha: 0.95),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.8,
        minChildSize: 0.4,
        maxChildSize: 0.95,
        expand: false,
        builder: (context, scrollController) => ChatHistoryDrawer(
          messages: _messages,
          myName: widget.humanName,
          scrollController: scrollController,
        ),
      ),
    );
  }

  String _buildLatestTtsTraceSummary() {
    final trace = _ttsPerf.lastResponseInfo;
    if (trace == null) {
      return '最近一次 /voice/tts：暂无播放记录';
    }
    final provider = formatTtsTraceValue(trace.provider);
    final usedVoice = formatTtsTraceValue(trace.usedVoice);
    final attempts = trace.attempts?.toString() ?? '—';
    final elapsed = trace.elapsedMs != null
        ? '${trace.elapsedMs!.toStringAsFixed(1)} ms'
        : '—';
    final source = trace.fromPrefetchCache ? '预取命中' : '实时回源';
    return '最近一次 /voice/tts：provider=$provider，voice=$usedVoice，attempts=$attempts，elapsed=$elapsed，来源=$source';
  }

  String _buildPerfCompareReport() {
    const baselineCount = 10;
    const recentCount = 10;

    List<double> head(List<double> src) => src.length <= baselineCount
        ? List<double>.from(src)
        : src.sublist(0, baselineCount);
    List<double> tail(List<double> src) => src.length <= recentCount
        ? List<double>.from(src)
        : src.sublist(src.length - recentCount);

    final startupBase = averageOf(head(_ttsStartupSeries));
    final startupNow = averageOf(tail(_ttsStartupSeries));
    final asrBase = averageOf(head(_asrFirstPacketSeries));
    final asrNow = averageOf(tail(_asrFirstPacketSeries));
    final hitBase = averageOf(head(_prefetchHitRateSeries));
    final hitNow = averageOf(tail(_prefetchHitRateSeries));

    String delta(double before, double after, {bool lowerIsBetter = true}) {
      if (before == 0) return 'N/A';
      final pct = ((after - before) / before) * 100;
      if (lowerIsBetter) {
        final improved = -pct;
        return '${improved >= 0 ? '+' : ''}${improved.toStringAsFixed(1)}%';
      }
      return '${pct >= 0 ? '+' : ''}${pct.toStringAsFixed(1)}%';
    }

    final now = DateTime.now().toIso8601String();
    return '''# RoundTable 并行调度优化对比报告

导出时间: $now
样本窗口: 最近30次（基线=最早10次，当前=最近10次）

## 核心指标
- 预取命中率: 基线 ${hitBase.toStringAsFixed(1)}% -> 当前 ${hitNow.toStringAsFixed(1)}% (变化 ${delta(hitBase, hitNow, lowerIsBetter: false)})
- 平均开播等待: 基线 ${startupBase.toStringAsFixed(1)}ms -> 当前 ${startupNow.toStringAsFixed(1)}ms (优化 ${delta(startupBase, startupNow)})
- ASR首包延迟: 基线 ${asrBase.toStringAsFixed(1)}ms -> 当前 ${asrNow.toStringAsFixed(1)}ms (优化 ${delta(asrBase, asrNow)})

## 运行状态
- 预取请求: ${_ttsPerf.prefetchRequested}
- 预取命中: ${_ttsPerf.prefetchHit}
- 回源次数: ${_ttsPerf.prefetchMiss}
- 开播样本数: $_ttsStartupSamples
- ASR首包样本数: $_asrFirstPacketSamples

## 最近一次服务端 TTS
- ${_buildLatestTtsTraceSummary()}

## 说明
- 命中率越高越好，开播等待与ASR首包延迟越低越好。
- 若基线样本不足，变化百分比会显示为 N/A。
''';
  }

  void _saveReportHistory(String report) {
    final entry = _PerfReportEntry(
      exportedAt: DateTime.now(),
      report: report,
      hitRate: _prefetchHitRatePercent,
      startupMs: _avgTtsStartupMs,
      asrFirstPacketMs: _avgAsrFirstPacketMs,
    );
    _reportHistory.insert(0, entry);
    if (_reportHistory.length > _maxReportHistory) {
      _reportHistory.removeLast();
    }
  }

  Future<void> _copyReport(_PerfReportEntry entry, {bool toast = true}) async {
    await Clipboard.setData(ClipboardData(text: entry.report));
    if (toast) {
      _showStatusToast('已复制报告：${entry.shortTimeLabel}');
    }
  }

  void _openReportHistoryPanel() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.studyWall.withValues(alpha: 0.96),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (context) {
        final records = List<_PerfReportEntry>.from(_reportHistory);
        return SizedBox(
          height: MediaQuery.of(context).size.height * 0.68,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.history_toggle_off,
                        color: Color(0xFF80DEEA), size: 18),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text(
                        '优化报告历史',
                        style: TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    Text(
                      '最近${records.length}条',
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.6),
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                if (records.isEmpty)
                  Expanded(
                    child: Center(
                      child: Text(
                        '暂无导出记录，先点击“导出优化对比报告”生成。',
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.65),
                          fontSize: 12,
                        ),
                      ),
                    ),
                  )
                else
                  Expanded(
                    child: ListView.separated(
                      itemCount: records.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 8),
                      itemBuilder: (context, index) {
                        final e = records[index];
                        return Container(
                          padding: const EdgeInsets.fromLTRB(10, 10, 10, 10),
                          decoration: BoxDecoration(
                            color: Colors.white.withValues(alpha: 0.05),
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(
                              color: Colors.white.withValues(alpha: 0.12),
                            ),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      e.shortTimeLabel,
                                      style: const TextStyle(
                                        color: AppColors.warmWhite,
                                        fontSize: 12,
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                    const SizedBox(height: 4),
                                    Text(
                                      '命中率 ${e.hitRate.toStringAsFixed(1)}% · 开播 ${e.startupMs.toStringAsFixed(1)}ms · 首包 ${e.asrFirstPacketMs.toStringAsFixed(1)}ms',
                                      style: TextStyle(
                                        color: Colors.white
                                            .withValues(alpha: 0.75),
                                        fontSize: 10,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 8),
                              IconButton(
                                onPressed: () => _copyReport(e),
                                icon: const Icon(Icons.copy_rounded,
                                    color: Color(0xFF80DEEA), size: 16),
                                tooltip: '一键再复制',
                                constraints: const BoxConstraints(
                                    minWidth: 28, minHeight: 28),
                                padding: EdgeInsets.zero,
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }

  Future<void> _exportPerfReport() async {
    final report = _buildPerfCompareReport();
    setState(() => _saveReportHistory(report));
    await Clipboard.setData(ClipboardData(text: report));
    _showStatusToast('优化对比报告已导出（已复制到剪贴板）');
  }

  Widget _buildPerfPanel(Size size) {
    final hitRate = _prefetchHitRatePercent;
    final hitProgress = (hitRate / 100).clamp(0.0, 1.0);
    final perf = _ttsPerf;
    final trace = perf.lastResponseInfo;
    final totalBg = _bgTaskRunning + _bgTaskQueue.length;

    return Container(
      width: min(360, size.width * 0.36),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: const Color(0xCC0F131D),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: const Color(0xFF26C6DA).withValues(alpha: 0.45),
        ),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFF26C6DA).withValues(alpha: 0.14),
            blurRadius: 20,
            spreadRadius: 1,
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            '并行调度性能面板',
            style: TextStyle(
              color: Color(0xFF80DEEA),
              fontSize: 13,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 10),
          Text(
            '预取命中率 ${hitRate.toStringAsFixed(1)}%',
            style: const TextStyle(color: Colors.white, fontSize: 12),
          ),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 8,
              value: hitProgress,
              backgroundColor: Colors.white.withValues(alpha: 0.12),
              valueColor: const AlwaysStoppedAnimation(Color(0xFF00E5A8)),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: Text(
                  '平均开播等待\n${_avgTtsStartupMs.toStringAsFixed(1)} ms',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 11,
                    height: 1.4,
                  ),
                ),
              ),
              Expanded(
                child: Text(
                  'ASR首包延迟\n${_avgAsrFirstPacketMs.toStringAsFixed(1)} ms',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 11,
                    height: 1.4,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            '预取请求 ${perf.prefetchRequested}  命中 ${perf.prefetchHit}  回源 ${perf.prefetchMiss}',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.78),
              fontSize: 10,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            '队列深度 ${_ttsQueue.length}  后台任务 $_bgTaskRunning/$totalBg',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.78),
              fontSize: 10,
            ),
          ),
          const SizedBox(height: 10),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.fromLTRB(10, 10, 10, 10),
            decoration: BoxDecoration(
              color: const Color(0xFF0C1624).withValues(alpha: 0.92),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: const Color(0xFF80DEEA).withValues(alpha: 0.22),
              ),
            ),
            child: trace == null
                ? Text(
                    '最近一次 /voice/tts：等待播放后更新',
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.72),
                      fontSize: 10,
                    ),
                  )
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '最近一次服务端 TTS',
                        style: TextStyle(
                          color: Color(0xFF80DEEA),
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _PerfTraceChip(
                            label: 'Provider',
                            value: formatTtsTraceValue(trace.provider),
                            accent: const Color(0xFF80DEEA),
                          ),
                          _PerfTraceChip(
                            label: 'Voice',
                            value: formatTtsTraceValue(trace.usedVoice),
                            accent: const Color(0xFFFFD54F),
                          ),
                          _PerfTraceChip(
                            label: 'Attempts',
                            value: trace.attempts?.toString() ?? '—',
                            accent: const Color(0xFFFFAB91),
                          ),
                          _PerfTraceChip(
                            label: 'Elapsed',
                            value: trace.elapsedMs != null
                                ? '${trace.elapsedMs!.toStringAsFixed(1)} ms'
                                : '—',
                            accent: const Color(0xFF00E5A8),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        '请求 ${formatTtsTraceValue(trace.requestedVoice)}  →  实际 ${formatTtsTraceValue(trace.usedVoice)}  ·  ${trace.fromPrefetchCache ? '预取命中' : '实时回源'}${trace.contentType != null && trace.contentType!.isNotEmpty ? '  ·  ${trace.contentType}' : ''}',
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.72),
                          fontSize: 10,
                          height: 1.45,
                        ),
                      ),
                    ],
                  ),
          ),
          const SizedBox(height: 10),
          _PerfSparkline(
            title: '预取命中率走势（30次）',
            unit: '%',
            data: _prefetchHitRateSeries,
            lineColor: const Color(0xFF00E5A8),
            preferLower: false,
          ),
          const SizedBox(height: 8),
          _PerfSparkline(
            title: '开播等待走势（30次）',
            unit: 'ms',
            data: _ttsStartupSeries,
            lineColor: const Color(0xFFFFD54F),
          ),
          const SizedBox(height: 8),
          _PerfSparkline(
            title: 'ASR首包走势（30次）',
            unit: 'ms',
            data: _asrFirstPacketSeries,
            lineColor: const Color(0xFF81D4FA),
          ),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                '历史记录 ${_reportHistory.length}/$_maxReportHistory',
                style: TextStyle(
                  color: Colors.white.withValues(alpha: 0.62),
                  fontSize: 10,
                ),
              ),
              Wrap(
                spacing: 2,
                children: [
                  TextButton.icon(
                    onPressed: _openReportHistoryPanel,
                    icon: const Icon(Icons.history,
                        color: Color(0xFF80DEEA), size: 16),
                    label: const Text(
                      '历史',
                      style: TextStyle(color: Color(0xFF80DEEA), fontSize: 11),
                    ),
                  ),
                  TextButton.icon(
                    onPressed: _exportPerfReport,
                    icon: const Icon(Icons.file_download_outlined,
                        color: Color(0xFF80DEEA), size: 16),
                    label: const Text(
                      '导出',
                      style: TextStyle(color: Color(0xFF80DEEA), fontSize: 11),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildPhasePanel(Size size) {
    final records = _phaseTelemetryHistory;
    return Container(
      width: min(400, size.width * 0.42),
      height: min(420, size.height * 0.52),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      decoration: BoxDecoration(
        color: const Color(0xCC131826),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: const Color(0xFF42A5F5).withValues(alpha: 0.45),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Phase Telemetry',
            style: TextStyle(
              color: Color(0xFF90CAF9),
              fontSize: 13,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            '最近 ${records.length} 条 · 前后端统一阶段与恢复原因',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.68),
              fontSize: 10,
            ),
          ),
          const SizedBox(height: 8),
          Expanded(
            child: records.isEmpty
                ? Center(
                    child: Text(
                      '暂无阶段事件',
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.6),
                        fontSize: 11,
                      ),
                    ),
                  )
                : ListView.separated(
                    itemCount: records.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 6),
                    itemBuilder: (context, index) {
                      final r = records[index];
                      final accent = r.recovery
                          ? const Color(0xFFFFB74D)
                          : const Color(0xFF64B5F6);
                      return Container(
                        padding: const EdgeInsets.fromLTRB(8, 7, 8, 7),
                        decoration: BoxDecoration(
                          color: Colors.white.withValues(alpha: 0.04),
                          borderRadius: BorderRadius.circular(8),
                          border:
                              Border.all(color: accent.withValues(alpha: 0.45)),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text(
                                  r.shortTime,
                                  style: TextStyle(
                                    color: Colors.white.withValues(alpha: 0.72),
                                    fontSize: 10,
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Text(
                                  r.source,
                                  style: TextStyle(color: accent, fontSize: 10),
                                ),
                                const Spacer(),
                                if (r.eventSeq != null)
                                  Text(
                                    'seq ${r.eventSeq}',
                                    style: TextStyle(
                                      color:
                                          Colors.white.withValues(alpha: 0.55),
                                      fontSize: 10,
                                    ),
                                  ),
                              ],
                            ),
                            const SizedBox(height: 2),
                            Text(
                              '${r.phase}${r.speaker.isNotEmpty ? ' · ${r.speaker}' : ''}',
                              style: const TextStyle(
                                color: AppColors.warmWhite,
                                fontSize: 11,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            const SizedBox(height: 2),
                            if (r.designateStage.isNotEmpty)
                              Container(
                                margin: const EdgeInsets.only(bottom: 4),
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 6,
                                  vertical: 2,
                                ),
                                decoration: BoxDecoration(
                                  color: const Color(0xFF90CAF9)
                                      .withValues(alpha: 0.16),
                                  borderRadius: BorderRadius.circular(999),
                                  border: Border.all(
                                    color: const Color(0xFF90CAF9)
                                        .withValues(alpha: 0.55),
                                  ),
                                ),
                                child: Text(
                                  'DESIGNATE ${r.designateStage.toUpperCase()}${r.designateTarget.isNotEmpty ? ' -> ${r.designateTarget}' : ''}',
                                  style: const TextStyle(
                                    color: Color(0xFFBBDEFB),
                                    fontSize: 10,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                            if (r.sendDropTotal > 0)
                              Container(
                                margin: const EdgeInsets.only(bottom: 4),
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 6,
                                  vertical: 2,
                                ),
                                decoration: BoxDecoration(
                                  color: const Color(0xFFFFB74D)
                                      .withValues(alpha: 0.14),
                                  borderRadius: BorderRadius.circular(999),
                                  border: Border.all(
                                    color: const Color(0xFFFFB74D)
                                        .withValues(alpha: 0.45),
                                  ),
                                ),
                                child: Text(
                                  'SEND_DROP total=${r.sendDropTotal}${r.sendDropLast.isNotEmpty ? ' · last=${r.sendDropLast}' : ''}${r.sendDropReasons.isNotEmpty ? ' · ${r.sendDropReasons}' : ''}',
                                  style: const TextStyle(
                                    color: Color(0xFFFFE0B2),
                                    fontSize: 10,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                            Text(
                              r.reason,
                              style: TextStyle(
                                color: r.recovery
                                    ? const Color(0xFFFFCC80)
                                    : Colors.white.withValues(alpha: 0.72),
                                fontSize: 10,
                              ),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  // 需求六/九：讨论结束后的金句朗读。
  // 从当前 `_voiceMap` 里挑一位思想家（zh-CN-YunzeNeural）朗读每句金句，
  // 每句读完后停顿 500ms 再读下一句；中途可取消。
  bool _quotesReadAloudActive = false;
  Future<void> _readAloudQuotes(List<String> quotes) async {
    if (_quotesReadAloudActive) return;
    _quotesReadAloudActive = true;
    final thinkerVoice = _resolveSpeakerVoice(
      '思想家',
      isThinker: true,
    );
    try {
      for (final raw in quotes) {
        if (!_quotesReadAloudActive) break;
        final line = _stripStageDirectionsForSpeech(raw).trim();
        if (line.isEmpty) continue;
        final rate = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
          '思想家',
          text: line,
          isThinker: true,
        );
        try {
          await _ttsService.speak(line, voice: thinkerVoice, rate: rate);
        } catch (_) {}
        await Future.delayed(const Duration(milliseconds: 500));
      }
    } finally {
      _quotesReadAloudActive = false;
    }
  }

  void _stopReadAloudQuotes() {
    _quotesReadAloudActive = false;
    try {
      _ttsService.stop();
    } catch (_) {}
    try {
      _browserFallbackTts?.stop();
    } catch (_) {}
  }

  Future<void> _readAloudHumanReview(String review) async {
    if (_humanReviewReadAloudActive || _humanReviewMuted) return;
    final line = _stripStageDirectionsForSpeech(review).trim();
    if (line.isEmpty) return;

    await _waitForMainTtsToSettleBeforeHumanReview(
      initialDelay: Duration.zero,
    );
    if (!mounted || _humanReviewReadAloudActive || _humanReviewMuted) return;

    _humanReviewReadAloudActive = true;
    final teacherVoice = _resolveSpeakerVoice(
      ImmersiveSessionScreen.teacherDisplayName,
    );
    final rate = ImmersiveSessionScreen.fixedSpeechRateForSpeaker(
      ImmersiveSessionScreen.teacherDisplayName,
      text: line,
    );
    try {
      await _ttsService.speak(line, voice: teacherVoice, rate: rate);
    } catch (_) {
      try {
        await _browserFallbackTts?.speak(line, voice: teacherVoice, rate: rate);
      } catch (_) {}
    } finally {
      _humanReviewReadAloudActive = false;
    }
  }

  void _stopReadAloudHumanReview() {
    _humanReviewReadAloudActive = false;
    try {
      _ttsService.stop();
    } catch (_) {}
    try {
      _browserFallbackTts?.stop();
    } catch (_) {}
  }

  void _toggleHumanReviewMute() {
    if (_humanReview.isEmpty) return;
    final nextMuted = !_humanReviewMuted;
    setState(() {
      _humanReviewMuted = nextMuted;
    });
    if (nextMuted) {
      _stopReadAloudHumanReview();
      _showStatusToast('已静音老师点评朗读');
      return;
    }
    _showStatusToast('已恢复老师点评朗读');
    unawaited(_readAloudHumanReview(_humanReview));
  }

  void _exitEndingQuotesToHome() {
    if (!mounted) {
      return;
    }
    ImmersiveSessionScreen.exitEndingQuotesToHome(context);
  }

  Widget _buildEndingQuotesOverlay(Size size) {
    return AnimatedBuilder(
      animation: _endingQuotesTransitionController,
      child: _EndingQuotesScreen(
        quotes: _goldenQuotes,
        onExit: _exitEndingQuotesToHome,
        onReadAloud: _readAloudQuotes,
        onStopReadAloud: _stopReadAloudQuotes,
      ),
      builder: (context, child) {
        final eased = Curves.easeOutCubic
            .transform(_endingQuotesTransitionController.value);
        final reverseEased = 1 - eased;
        final pageTurn = -reverseEased * 0.94;
        final shiftX = reverseEased * size.width * 0.16;
        final liftY = reverseEased * 18;

        return IgnorePointer(
          ignoring: eased <= 0.001,
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.58 * eased),
            ),
            child: Stack(
              children: [
                Positioned(
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: size.width * 0.14,
                  child: IgnorePointer(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.centerLeft,
                          end: Alignment.centerRight,
                          colors: [
                            const Color(0xFFF4D38B)
                                .withValues(alpha: 0.16 * eased),
                            Colors.transparent,
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
                Transform(
                  alignment: Alignment.centerRight,
                  transform: Matrix4.identity()
                    ..setEntry(3, 2, 0.00115)
                    ..translateByDouble(shiftX, liftY, 0, 1)
                    ..rotateY(pageTurn)
                    ..scaleByDouble(
                      0.965 + eased * 0.035,
                      0.986 + eased * 0.014,
                      1,
                      1,
                    ),
                  child: Stack(
                    children: [
                      child!,
                      Positioned(
                        top: 0,
                        bottom: 0,
                        right: 0,
                        width: 36,
                        child: IgnorePointer(
                          child: DecoratedBox(
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.centerRight,
                                end: Alignment.centerLeft,
                                colors: [
                                  Colors.black.withValues(alpha: 0.24 * eased),
                                  const Color(0xFFF4D38B)
                                      .withValues(alpha: 0.14 * eased),
                                  Colors.transparent,
                                ],
                                stops: const [0.0, 0.35, 1.0],
                              ),
                            ),
                          ),
                        ),
                      ),
                      Positioned(
                        top: 0,
                        bottom: 0,
                        left: 0,
                        width: 26,
                        child: IgnorePointer(
                          child: DecoratedBox(
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.centerLeft,
                                end: Alignment.centerRight,
                                colors: [
                                  const Color(0xFFF4D38B)
                                      .withValues(alpha: 0.18 * eased),
                                  Colors.transparent,
                                ],
                              ),
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final showEndingQuotesOverlay = _mountEndingQuotesOverlay;
    final tableRadius = min(size.width, size.height) * 0.18;
    // 需求五：字幕区宽度允许达到 85%，居中显示。
    final subtitleWidth = size.width * 0.85;

    // 需求15：圆桌整体向右移动 100px，左侧留出金句展示区
    final tableCenterX = size.width / 2 + 100;
    final tableCenterY = size.height / 2;
    // 左侧金句区宽度（从屏幕最左到圆桌左边缘减一点间距）
    final quoteAreaRight = tableCenterX - tableRadius - 60;
    // ignore: unused_local_variable
    final quoteAreaWidth = max(0.0, quoteAreaRight - 24);
    // 需求：老师点评卡比金句侧栏更窄，避免遮挡圆桌/角色图标。
    // 右边缘相对参与者圆环再退 40px，宽度上限 360。
    final reviewCardRightLimit = tableCenterX - (tableRadius + 80) - 40;
    final reviewCardWidth =
        max(0.0, min(360.0, reviewCardRightLimit - 24)).toDouble();
    // 旧版会后点评卡已替换为全屏 Overlay；保留宽度计算供其他兼容路径使用。

    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 20,
    );

    final canInterrupt = _currentSpeaker.isNotEmpty &&
        !ImmersiveSessionScreen.shouldSuppressRaiseHandRequest(
          isMyTurn: _isMyTurn,
          pendingHumanTurn: _pendingHumanTurn,
          handApprovedToSpeak: _handApprovedToSpeak,
          hasRaisedHand: _hasRaisedHand,
        );
    final showHumanTurnPromptCue =
        ImmersiveSessionScreen.shouldShowHumanTurnPromptCue(
      isMyTurn: _isMyTurn,
      isRecording: _isRecording,
      isCompletingHumanTurn: _isCompletingHumanTurn,
      isFinalizingSpeech: _isFinalizingSpeech,
    );
    final showHumanCue =
        (_pendingHumanTurn || _handApprovedToSpeak || _isMyTurn) &&
            !_discussionEnded;
    final showOpeningPreparingCue =
        _openingCueState == _OpeningCueState.preparing && !_discussionEnded;
    final showOpeningStartCue =
        _openingCueState == _OpeningCueState.ready && !_discussionEnded;
    // canSpeakNow：仅当真正轮到用户或已批准发言时才允许操作。
    // pending 阶段只保留呼吸灯提示，不允许提前点亮麦克风。
    final canSpeakNow =
        (_isMyTurn || _handApprovedToSpeak || _isRecording) && !_micLocked;
    final canTapSpeakButton = ImmersiveSessionScreen.canTapSpeakButton(
      canSpeakNow: canSpeakNow,
      ctrlHeld: _ctrlHeld,
      isRecording: _isRecording,
      recordingControlledByHoldCtrl: _recordingControlledByHoldCtrl,
    );
    // 按钮始终显示，但通过 opacity 和 IgnorePointer 控制是否可用
    // 用户回合：麦克风可用，跳过可用
    // 非用户回合但有发言者：举手可用
    // 讨论已结束则置灰不可交互
    final showActionButtons = !_discussionEnded;
    // 需求：根据窗口下沿可用空间，自适应字幕字号 / 行距 / 距底距离。
    // 圆桌底沿到屏幕底沿之间的可用区域 = h/2 - tableRadius
    final subtitleAvailable = size.height / 2 - tableRadius;
    // 极端窄窗时下调字号
    final double subtitleFontSize = subtitleAvailable < 170
        ? 16.0
        : subtitleAvailable < 220
            ? 18.0
            : 22.0;
    final subtitleLineCount = _estimateSubtitleLineCount(
      context,
      '$_centerSpeaker：$_centerMessage',
      maxWidth: subtitleWidth - 80,
      style: TextStyle(fontSize: subtitleFontSize, height: 1.9),
      maxLines: 3,
    );
    // 需求：当字幕达到 3 行时，把首行向下移动约 30px，避免遮挡圆桌人物图案。
    // 实现：减少 subtitleBottom（即整体下移），由文字 bottomCenter 对齐自然把首行下移。
    final double subtitleBottom = subtitleLineCount >= 3
        ? 10.0
        : subtitleLineCount == 2
            ? 40.0
            : 36.0;
    // 行距：3 行时压紧，避免向上溢出
    final subtitleLineHeight = subtitleLineCount >= 3
        ? 1.55
        : subtitleLineCount == 2
            ? 1.8
            : 1.7;
    // 需求19：若字幕超过 2 行，按 page 自动翻页显示
    _maybeAdvanceSubtitlePage(
      fullText: '$_centerSpeaker：$_centerMessage',
      maxWidth: subtitleWidth - 80,
      lineHeight: subtitleLineHeight,
    );

    return KeyboardListener(
      focusNode: _keyboardFocusNode,
      onKeyEvent: _onKeyEvent,
      child: Scaffold(
        body: AnimatedBuilder(
          animation: _endingQuotesTransitionController,
          child: Stack(
            children: [
              // ── 书房背景 ──
              CustomPaint(
                size: size,
                painter:
                    BookshelfPainter(lightIntensity: _isMyTurn ? 1.0 : 0.6),
              ),

              // ── 需求 8：金句不再常驻显示，改为讨论结束后通过老师点评弹窗的「查看金句」按钮查看。──
              // 旧的 _QuoteSidebar 已被移除以保持讨论页画面整洁。
              if (_discussionEnded &&
                  _humanReviewOverlayVisible &&
                  reviewCardWidth > 200 &&
                  (_humanReview.isNotEmpty || _isGeneratingHumanReview))
                // 旧版左侧紧凑点评卡已被新的全屏点评浮层取代，
                // 这里保留 sizing 计算以避免布局抖动，但不再渲染旧卡。
                const SizedBox.shrink(),

              // ── 圆桌（左移至 1/3 处）──
              Positioned(
                left: tableCenterX - tableRadius,
                top: tableCenterY - tableRadius,
                child: CustomPaint(
                  size: Size(tableRadius * 2, tableRadius * 2),
                  painter: RoundTablePainter(
                    tableRadius: tableRadius,
                    glowIntensity: _isMyTurn ? 0.9 : 0.5,
                  ),
                ),
              ),

              // ── 烛光粒子 ──
              CustomPaint(
                size: size,
                painter: CandlelightPainter(
                  particles: _particles!,
                  animationValue: _candleController.value,
                ),
              ),

              // ── 参与者圆环（跟随圆桌位置）──
              Positioned(
                left: tableCenterX - (tableRadius + 80),
                top: tableCenterY - (tableRadius + 80),
                child: SizedBox(
                  width: (tableRadius + 80) * 2,
                  height: (tableRadius + 80) * 2,
                  child: TableParticipantRing(
                    participants: _participants,
                    tableRadius: tableRadius,
                  ),
                ),
              ),

              // ── 动态麦克风 + 呼吸光效：所有发言角色均有（需求3.5）──
              if (_currentSpeaker.isNotEmpty && (_ttsPlaying || _isRecording))
                Positioned(
                  left: tableCenterX - (tableRadius + 80),
                  top: tableCenterY - (tableRadius + 80),
                  child: SizedBox(
                    width: (tableRadius + 80) * 2,
                    height: (tableRadius + 80) * 2,
                    child: _AnimatedMicOnTable(
                      speakerName: _currentSpeaker,
                      participants: _participants,
                      tableRadius: tableRadius,
                    ),
                  ),
                ),

              // ── 话题标题：圆桌上方居中 ──
              Positioned(
                top: MediaQuery.of(context).padding.top + size.height * 0.06,
                left: tableCenterX - size.width * 0.30,
                width: size.width * 0.60,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      widget.topic.title,
                      style: AppTheme.calligraphyStyleDark(fontSize: 17),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      widget.topic.category,
                      style: TextStyle(
                        color: AppColors.warmGray.withValues(alpha: 0.7),
                        fontSize: 11,
                      ),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),

              // ── Thinking indicator (f) ──
              if (_isThinking && _currentSpeaker.isNotEmpty)
                Positioned(
                  top: MediaQuery.of(context).padding.top + size.height * 0.14,
                  left: tableCenterX - 120,
                  width: 240,
                  child: Center(
                    child: AnimatedBuilder(
                      animation: _thinkingController,
                      builder: (context, _) {
                        final dotCount =
                            (_thinkingController.value * 3).floor() + 1;
                        final dots = '·' * dotCount;
                        return Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 14, vertical: 6),
                          decoration: BoxDecoration(
                            color: AppColors.studyWall.withValues(alpha: 0.8),
                            borderRadius: BorderRadius.circular(16),
                            border: Border.all(
                                color:
                                    AppColors.amberGold.withValues(alpha: 0.3)),
                          ),
                          child: Text(
                            '$_currentSpeaker 正在思考$dots',
                            style: TextStyle(
                              color: AppColors.amberGold.withValues(alpha: 0.9),
                              fontSize: 12,
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ),

              // ── 轮到用户发言时，圆桌中心显示橙色呼吸灯 ──
              if (showHumanCue && !_isRecording)
                Positioned(
                  left: tableCenterX - 18,
                  top: tableCenterY - 18,
                  width: 36,
                  height: 36,
                  child: Center(
                    child: AnimatedBuilder(
                      animation: _glowController,
                      builder: (context, _) {
                        final pulse = 0.55 + _glowController.value * 0.45;
                        final orbSize = 18.0 + _glowController.value * 18.0;
                        return Container(
                          width: orbSize,
                          height: orbSize,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            gradient: RadialGradient(
                              colors: [
                                Colors.white.withValues(alpha: 0.95),
                                const Color(0xFFFFC25B)
                                    .withValues(alpha: 0.92 * pulse),
                                const Color(0xFFFF8A2B)
                                    .withValues(alpha: 0.82 * pulse),
                                const Color(0xFFFF8A2B)
                                    .withValues(alpha: 0.06 * pulse),
                              ],
                              stops: const [0.0, 0.22, 0.62, 1.0],
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: const Color(0xFFFFA53A)
                                    .withValues(alpha: 0.45 * pulse),
                                blurRadius: 12 + _glowController.value * 14,
                                spreadRadius: 1.5 + _glowController.value * 2.5,
                              ),
                              BoxShadow(
                                color: const Color(0xFFFF6A00)
                                    .withValues(alpha: 0.18 * pulse),
                                blurRadius: 24 + _glowController.value * 18,
                                spreadRadius: _glowController.value * 4,
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
                ),

              if (showOpeningPreparingCue || showOpeningStartCue)
                Positioned(
                  left: tableCenterX - tableRadius * 0.58,
                  top: tableCenterY - 34,
                  width: tableRadius * 1.16,
                  child: IgnorePointer(
                    child: Center(
                      child: AnimatedSwitcher(
                        duration: const Duration(milliseconds: 220),
                        switchInCurve: Curves.easeOutCubic,
                        switchOutCurve: Curves.easeInCubic,
                        child: Container(
                          key: ValueKey(
                            showOpeningStartCue
                                ? 'opening-start'
                                : 'opening-preparing',
                          ),
                          padding: const EdgeInsets.symmetric(
                              horizontal: 18, vertical: 10),
                          decoration: BoxDecoration(
                            color: AppColors.studyWall.withValues(alpha: 0.76),
                            borderRadius: BorderRadius.circular(20),
                            border: Border.all(
                              color: showOpeningStartCue
                                  ? const Color(0xFFFFB37A)
                                      .withValues(alpha: 0.42)
                                  : AppColors.amberGold.withValues(alpha: 0.34),
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: Colors.black.withValues(alpha: 0.18),
                                blurRadius: 10,
                                offset: const Offset(0, 3),
                              ),
                            ],
                          ),
                          child: Text(
                            showOpeningStartCue ? '开始讨论' : '老师准备中……',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              color: showOpeningStartCue
                                  ? const Color(0xFFFFC48B)
                                  : AppColors.amberGold.withValues(alpha: 0.92),
                              fontSize: max(14.0, tableRadius * 0.105),
                              fontWeight: FontWeight.w700,
                              letterSpacing: 1.2,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),

              // ── 操作提示：圆桌下方 ──
              if (showHumanTurnPromptCue)
                Positioned(
                  top: tableCenterY + tableRadius + 16,
                  left: tableCenterX - 180,
                  width: 360,
                  child: Center(
                    child: AnimatedBuilder(
                      animation: _glowController,
                      builder: (context, _) {
                        final pulse = 0.7 + _glowController.value * 0.3;
                        return Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 18, vertical: 8),
                          decoration: BoxDecoration(
                            color: AppColors.studyWall.withValues(alpha: 0.9),
                            borderRadius: BorderRadius.circular(20),
                            border: Border.all(
                              color: AppColors.amberGold
                                  .withValues(alpha: 0.6 * pulse),
                              width: 1.5,
                            ),
                            boxShadow: [
                              BoxShadow(
                                color: AppColors.amberGold
                                    .withValues(alpha: 0.2 * pulse),
                                blurRadius: 16,
                                spreadRadius: 2,
                              ),
                            ],
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.record_voice_over,
                                  color: AppColors.amberGold, size: 18),
                              const SizedBox(width: 8),
                              Text(
                                _turnCountdown > 0
                                    ? '轮到你了，按住麦克风讲话 ($_turnCountdown s)'
                                    : '轮到你了，按住麦克风讲话',
                                style: TextStyle(
                                  color: AppColors.amberGold,
                                  fontSize: 13,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
                ),

              if (_quickFeedbackText.isNotEmpty)
                Positioned(
                  top: max(
                    MediaQuery.of(context).padding.top + 124,
                    tableCenterY - 56,
                  ),
                  left: 16,
                  width: min(280.0, size.width * 0.24),
                  child: IgnorePointer(
                    child: AnimatedSlide(
                      duration: const Duration(milliseconds: 180),
                      curve: Curves.easeOutCubic,
                      offset: _quickFeedbackVisible
                          ? Offset.zero
                          : const Offset(-0.08, 0),
                      child: AnimatedBuilder(
                        animation: _candleController,
                        builder: (context, _) {
                          final pulse = 0.72 + _candleController.value * 0.28;
                          final warmupColor = _teacherReplyWarmupVisible
                              ? const Color(0xFFFFC36D)
                              : const Color(0xFF5DE2C2);
                          return AnimatedOpacity(
                            duration: const Duration(milliseconds: 180),
                            opacity: _quickFeedbackVisible ? 1.0 : 0.0,
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 14,
                                vertical: 12,
                              ),
                              decoration: BoxDecoration(
                                color: const Color(0xD9111720),
                                borderRadius: BorderRadius.circular(18),
                                border: Border.all(
                                  color: warmupColor.withValues(
                                    alpha: _teacherReplyWarmupVisible
                                        ? 0.32 + 0.2 * pulse
                                        : 0.35,
                                  ),
                                ),
                                boxShadow: [
                                  BoxShadow(
                                    color: Colors.black.withValues(alpha: 0.16),
                                    blurRadius: 18,
                                    offset: const Offset(0, 8),
                                  ),
                                ],
                              ),
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Container(
                                    width: 8,
                                    height: 8,
                                    margin: const EdgeInsets.only(top: 5),
                                    decoration: BoxDecoration(
                                      color: warmupColor.withValues(
                                        alpha: _teacherReplyWarmupVisible
                                            ? 0.65 + 0.35 * pulse
                                            : 1,
                                      ),
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Text(
                                          _teacherReplyWarmupVisible
                                              ? '回应准备'
                                              : '即时反馈',
                                          style: GoogleFonts.notoSansSc(
                                            color: _teacherReplyWarmupVisible
                                                ? const Color(0xFFFFD9A3)
                                                : const Color(0xFF97F0DE),
                                            fontSize: 10.5,
                                            fontWeight: FontWeight.w700,
                                            letterSpacing: 0.6,
                                          ),
                                        ),
                                        const SizedBox(height: 3),
                                        Text(
                                          _quickFeedbackText,
                                          style: GoogleFonts.notoSansSc(
                                            color: const Color(0xFFF2F7F7),
                                            fontSize: 12.5,
                                            fontWeight: FontWeight.w600,
                                            height: 1.4,
                                          ),
                                          maxLines: 3,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ],
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  ),
                ),

              if (_teacherReplyWarmupVisible)
                Positioned(
                  top: MediaQuery.of(context).padding.top + size.height * 0.14,
                  left: tableCenterX - 112,
                  width: 224,
                  child: IgnorePointer(
                    child: Center(
                      child: AnimatedBuilder(
                        animation: _candleController,
                        builder: (context, _) {
                          final pulse = 0.78 + _candleController.value * 0.22;
                          final signalHeights = _teacherWarmupSignalHeights();
                          return Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 9,
                            ),
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                                colors: [
                                  const Color(0xE31D1724),
                                  const Color(0xD91A1620),
                                ],
                              ),
                              borderRadius: BorderRadius.circular(18),
                              border: Border.all(
                                color: const Color(0xFFFFC36D)
                                    .withValues(alpha: 0.35 + 0.28 * pulse),
                              ),
                              boxShadow: [
                                BoxShadow(
                                  color: const Color(0xFFFFC36D)
                                      .withValues(alpha: 0.1 + 0.08 * pulse),
                                  blurRadius: 20,
                                  spreadRadius: 1.5,
                                ),
                              ],
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: List.generate(3, (index) {
                                    return Container(
                                      width: 3,
                                      height: signalHeights[index],
                                      margin: EdgeInsets.only(
                                        right: index == 2 ? 0 : 2,
                                      ),
                                      decoration: BoxDecoration(
                                        color: const Color(0xFFFFC97A)
                                            .withValues(
                                                alpha: 0.6 + 0.35 * pulse),
                                        borderRadius: BorderRadius.circular(3),
                                      ),
                                    );
                                  }),
                                ),
                                const SizedBox(width: 10),
                                Text(
                                  '李老师正在接话${_teacherWarmupDots()}',
                                  textAlign: TextAlign.center,
                                  style: GoogleFonts.notoSansSc(
                                    color: const Color(0xFFFFD9A3),
                                    fontSize: 12,
                                    fontWeight: FontWeight.w700,
                                    letterSpacing: 0.35,
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
                    ),
                  ),
                ),

              if (_teacherReplyWarmupVisible)
                Positioned(
                  top: MediaQuery.of(context).padding.top + size.height * 0.19,
                  left: tableCenterX - 100,
                  width: 200,
                  child: IgnorePointer(
                    child: AnimatedBuilder(
                      animation: _candleController,
                      builder: (context, _) {
                        final activeWidth = 56 + _candleController.value * 124;
                        return ClipRRect(
                          borderRadius: BorderRadius.circular(999),
                          child: Container(
                            height: 3,
                            color: const Color(0x33FFC36D),
                            child: Align(
                              alignment: Alignment.centerLeft,
                              child: Container(
                                width: activeWidth,
                                decoration: BoxDecoration(
                                  gradient: const LinearGradient(
                                    colors: [
                                      Color(0x66FFC36D),
                                      Color(0xFFFFC36D),
                                      Color(0x66FFC36D),
                                    ],
                                  ),
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ),

              // ── 中间竖排按钮：麦克风 + 跳过 + 举手（圆桌与输入面板之间）──
              // 始终显示按钮，非用户回合时置灰不可操作
              Positioned(
                left: tableCenterX + tableRadius + 148,
                top: tableCenterY - tableRadius,
                child: AnimatedOpacity(
                  opacity: showActionButtons ? 1.0 : 0.25,
                  duration: const Duration(milliseconds: 300),
                  child: IgnorePointer(
                    ignoring: !showActionButtons,
                    child: _RightActionColumn(
                      showMic: canTapSpeakButton,
                      isRecording: _isRecording,
                      onStart: _onPttStart,
                      onEnd: _onPttEnd,
                      showSkip: showHumanTurnPromptCue,
                      onSkip: () => _onSkipTurn(reason: '您已跳过本次发言'),
                      hasRaisedHand: _hasRaisedHand,
                      canInterrupt: canInterrupt,
                      onRaiseHand: _onInterrupt,
                      observerSeat: widget.observerMode
                          ? _ObserverSeatBadgeData(
                              name: widget.humanName,
                              avatar: _observerSeatAvatarUrl(widget.humanName),
                              statusLabel: _observerSeatStatusLabel(),
                              hasRaisedHand: _hasRaisedHand,
                              isReservedToSpeak: _pendingHumanTurn ||
                                  _handApprovedToSpeak ||
                                  _isMyTurn,
                              isActive: _isRecording || _isMyTurn,
                            )
                          : null,
                      totalHeight: tableRadius * 2,
                    ),
                  ),
                ),
              ),

              // ── 字幕区：直接印在圆桌下方，无黑色背景，仅靠文字描边阴影保证可读 ──
              if (_centerSpeaker.isNotEmpty && _centerMessage.isNotEmpty)
                Positioned(
                  bottom: subtitleBottom + 38,
                  left: (size.width - subtitleWidth) / 2,
                  width: subtitleWidth,
                  child: IgnorePointer(
                    child: Container(
                      padding: const EdgeInsets.fromLTRB(40, 12, 40, 12),
                      child: Align(
                        alignment: Alignment.bottomCenter,
                        child: RichText(
                          textAlign: TextAlign.center,
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          text: TextSpan(
                            children: [
                              TextSpan(
                                text: '$_centerSpeaker：',
                                style: TextStyle(
                                  color: const Color(0xFF4FC3F7),
                                  fontSize: subtitleFontSize,
                                  fontWeight: FontWeight.bold,
                                  height: subtitleLineHeight,
                                  // 需求5：去掉黑色背景与大范围黑影，仅保留极细描边保持可读
                                  shadows: [
                                    Shadow(
                                        color: Colors.black
                                            .withValues(alpha: 0.55),
                                        blurRadius: 2,
                                        offset: const Offset(0, 1)),
                                  ],
                                ),
                              ),
                              TextSpan(
                                text: _displayedSubtitleMessage,
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: subtitleFontSize,
                                  height: subtitleLineHeight,
                                  shadows: [
                                    Shadow(
                                        color: Colors.black
                                            .withValues(alpha: 0.55),
                                        blurRadius: 2,
                                        offset: const Offset(0, 1)),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ),
                ),

              // ── 轮到我发光指示 ──
              if (_isMyTurn)
                Positioned(
                  left: tableCenterX - tableRadius * 0.9,
                  top: tableCenterY - tableRadius * 0.9,
                  child: AnimatedBuilder(
                    animation: _glowController,
                    builder: (context, child) {
                      final glowAlpha = 0.1 + _glowController.value * 0.2;
                      return Container(
                        width: tableRadius * 1.8,
                        height: tableRadius * 1.8,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          boxShadow: [
                            BoxShadow(
                              color: AppColors.amberGold
                                  .withValues(alpha: glowAlpha),
                              blurRadius: 40,
                              spreadRadius: 10,
                            ),
                          ],
                        ),
                      );
                    },
                  ),
                )
              else
                const SizedBox.shrink(),

              // ── 顶部状态栏（返回 + 暂停）──
              Positioned(
                top: 0,
                left: 0,
                right: 0,
                child: SafeArea(
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          AppColors.studyWall.withValues(alpha: 0.9),
                          AppColors.studyWall.withValues(alpha: 0.0),
                        ],
                      ),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        // ── 左上：返回 + 暂停 ──
                        _TopRectButton(
                          icon: Icons.arrow_back,
                          label: '返回',
                          onTap: () => Navigator.of(context).pop(),
                        ),
                        const SizedBox(width: 4),
                        _PauseButton(
                          isPaused: _isPaused,
                          onToggle: _onTogglePause,
                        ),
                        const SizedBox(width: 6),
                        _DiscussionClockChip(
                          timeText:
                              _formatDiscussionElapsed(_discussionElapsed),
                          isPaused: _isPaused,
                          started: _discussionStartedAt != null,
                        ),
                        const Spacer(),
                        IconButton(
                          icon: Icon(
                            _showPerfPanel ? Icons.speed : Icons.speed_outlined,
                            color: _showPerfPanel
                                ? const Color(0xFF80DEEA)
                                : AppColors.warmGray,
                          ),
                          onPressed: () {
                            setState(() => _showPerfPanel = !_showPerfPanel);
                          },
                          padding: EdgeInsets.zero,
                          constraints:
                              const BoxConstraints(minWidth: 40, minHeight: 40),
                        ),
                        const SizedBox(width: 6),
                        IconButton(
                          icon: Icon(
                            _showPhasePanel
                                ? Icons.route
                                : Icons.route_outlined,
                            color: _showPhasePanel
                                ? const Color(0xFF90CAF9)
                                : AppColors.warmGray,
                          ),
                          onPressed: () {
                            setState(() => _showPhasePanel = !_showPhasePanel);
                          },
                          padding: EdgeInsets.zero,
                          constraints:
                              const BoxConstraints(minWidth: 40, minHeight: 40),
                        ),
                        const SizedBox(width: 8),
                        // ── 右上：历史 ──
                        IconButton(
                          icon: const Icon(Icons.history,
                              color: AppColors.warmGray),
                          onPressed: _showChatHistory,
                          padding: EdgeInsets.zero,
                          constraints:
                              const BoxConstraints(minWidth: 40, minHeight: 40),
                        ),
                      ],
                    ),
                  ),
                ),
              ),

              // ── 系统提示：返回/暂停按钮下方，左对齐（需求3.3）──
              Positioned(
                top: MediaQuery.of(context).padding.top + 60,
                left: 16,
                width: size.width * 0.35,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.35),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    _statusText,
                    style: TextStyle(
                      color: _isMyTurn
                          ? AppColors.amberGold.withValues(alpha: 0.9)
                          : AppColors.warmGray.withValues(alpha: 0.7),
                      fontSize: 11,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),

              if (_showPerfPanel)
                Positioned(
                  top: MediaQuery.of(context).padding.top + 62,
                  right: 16,
                  child: _buildPerfPanel(size),
                ),

              if (_showPhasePanel)
                Positioned(
                  top: MediaQuery.of(context).padding.top + 62,
                  right: _showPerfPanel ? min(400, size.width * 0.42) + 24 : 16,
                  child: _buildPhasePanel(size),
                ),

              // ── 底部控制栏 ──
              Positioned(
                bottom: 0,
                left: 0,
                right: 0,
                child: GlassControlBar(
                  isMyTurn: _isMyTurn,
                  isPushToTalk: _isPushToTalk,
                  autoOpenMic: _autoOpenMic,
                  hotkeyLabel: _micHotkeyLabel,
                  isRecording: _isRecording,
                  onPttStart: _onPttStart,
                  onPttEnd: _onPttEnd,
                  canInterrupt: canInterrupt,
                  hasRaisedHand: _hasRaisedHand,
                  onInterrupt: _onInterrupt,
                  onSkipTurn: _isMyTurn ? _onSkipTurn : null,
                ),
              ),
            ],
          ),
          builder: (context, child) {
            final overlayT = Curves.easeOutCubic
                .transform(_endingQuotesTransitionController.value);
            final baseContent = child ?? const SizedBox.shrink();
            final showReviewOverlay = _discussionEnded &&
                _humanReviewOverlayVisible &&
                (_humanReview.isNotEmpty || _isGeneratingHumanReview) &&
                !showEndingQuotesOverlay;
            return Stack(
              children: [
                Transform.scale(
                  scale: 1.0 - overlayT * 0.016,
                  alignment: Alignment.center,
                  child: Opacity(
                    opacity: 1.0 - overlayT * 0.08,
                    child: baseContent,
                  ),
                ),
                if (showEndingQuotesOverlay)
                  Positioned.fill(child: _buildEndingQuotesOverlay(size)),
                if (showReviewOverlay)
                  Positioned.fill(
                    child: _PostDiscussionReviewOverlay(
                      review: _humanReview,
                      isLoading: _isGeneratingHumanReview,
                      isMuted: _humanReviewMuted,
                      onToggleMute: _toggleHumanReviewMute,
                      onExit: _exitEndingQuotesToHome,
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}

// ─── 动态麦克风 ────────────────────────────────────────────────────────────────
/// Animated mic that moves to the position of the current speaker on the table (l).
class _AnimatedMicOnTable extends StatefulWidget {
  final String speakerName;
  final List<SeatedParticipant> participants;
  final double tableRadius;

  const _AnimatedMicOnTable({
    required this.speakerName,
    required this.participants,
    required this.tableRadius,
  });

  @override
  State<_AnimatedMicOnTable> createState() => _AnimatedMicOnTableState();
}

class _AnimatedMicOnTableState extends State<_AnimatedMicOnTable>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseCtrl;
  late Animation<double> _pulse;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    )..repeat(reverse: true);
    _pulse = Tween<double>(begin: 0.8, end: 1.25)
        .animate(CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut));
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    super.dispose();
  }

  /// Reorders participants same as TableParticipantRing so angles are consistent.
  List<SeatedParticipant> _reordered(List<SeatedParticipant> list) {
    final r = List<SeatedParticipant>.from(list);
    if (r.length < 2) return r;
    final modIdx = r.indexWhere((p) => p.name == '李老师');
    if (modIdx > 0) {
      final m = r.removeAt(modIdx);
      r.insert(0, m);
    }
    final humanIdx = r.indexWhere((p) => p.isHuman);
    if (humanIdx >= 0) {
      final h = r.removeAt(humanIdx);
      final ti = (r.length / 2).round().clamp(1, r.length);
      r.insert(ti, h);
    }
    return r;
  }

  @override
  Widget build(BuildContext context) {
    final reordered = _reordered(widget.participants);
    final total = reordered.length;
    final idx = reordered.indexWhere((p) => p.name == widget.speakerName);
    if (total == 0 || idx < 0) return const SizedBox.shrink();

    final angle = (idx / total) * 2 * pi - pi / 2;
    // Place mic slightly inside the avatar ring
    final r = widget.tableRadius + 10.0;

    return LayoutBuilder(builder: (context, constraints) {
      final cx = constraints.maxWidth / 2;
      final cy = constraints.maxHeight / 2;
      final x = cx + r * cos(angle);
      final y = cy + r * sin(angle);

      return Stack(clipBehavior: Clip.none, children: [
        Positioned(
          left: x - 14,
          top: y - 14,
          child: AnimatedBuilder(
            animation: _pulse,
            builder: (context, _) {
              return Container(
                width: 28 * _pulse.value,
                height: 28 * _pulse.value,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: const Color(0xFF00FFCC).withValues(alpha: 0.15),
                  border: Border.all(
                    color: const Color(0xFF00FFCC).withValues(alpha: 0.7),
                    width: 1.5,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: const Color(0xFF00FFCC)
                          .withValues(alpha: 0.3 * _pulse.value),
                      blurRadius: 12,
                      spreadRadius: 3,
                    )
                  ],
                ),
                child:
                    const Icon(Icons.mic, color: Color(0xFF00FFCC), size: 16),
              );
            },
          ),
        ),
      ]);
    });
  }
}

class _PerfReportEntry {
  final DateTime exportedAt;
  final String report;
  final double hitRate;
  final double startupMs;
  final double asrFirstPacketMs;

  const _PerfReportEntry({
    required this.exportedAt,
    required this.report,
    required this.hitRate,
    required this.startupMs,
    required this.asrFirstPacketMs,
  });

  String get shortTimeLabel {
    String pad(int n) => n.toString().padLeft(2, '0');
    return '${pad(exportedAt.month)}-${pad(exportedAt.day)} '
        '${pad(exportedAt.hour)}:${pad(exportedAt.minute)}:${pad(exportedAt.second)}';
  }
}

class _PhaseTelemetryEntry {
  final DateTime at;
  final String source;
  final String phase;
  final String reason;
  final bool recovery;
  final String speaker;
  final int? eventSeq;
  final String designateStage;
  final String designateTarget;
  final int sendDropTotal;
  final String sendDropReasons;
  final String sendDropLast;

  const _PhaseTelemetryEntry({
    required this.at,
    required this.source,
    required this.phase,
    required this.reason,
    required this.recovery,
    required this.speaker,
    required this.eventSeq,
    required this.designateStage,
    required this.designateTarget,
    required this.sendDropTotal,
    required this.sendDropReasons,
    required this.sendDropLast,
  });

  String get shortTime {
    String pad(int n) => n.toString().padLeft(2, '0');
    return '${pad(at.hour)}:${pad(at.minute)}:${pad(at.second)}';
  }
}

class _PerfSparkline extends StatelessWidget {
  final String title;
  final String unit;
  final List<double> data;
  final Color lineColor;
  final bool preferLower;

  const _PerfSparkline({
    required this.title,
    required this.unit,
    required this.data,
    required this.lineColor,
    this.preferLower = true,
  });

  @override
  Widget build(BuildContext context) {
    final latest = data.isNotEmpty ? data.last : 0.0;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  style: const TextStyle(color: Colors.white70, fontSize: 10),
                ),
              ),
              Text(
                '${latest.toStringAsFixed(1)}$unit',
                style: TextStyle(
                  color: lineColor,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          SizedBox(
            height: 36,
            child: CustomPaint(
              painter: _SparklinePainter(
                values: data,
                lineColor: lineColor,
                preferLower: preferLower,
              ),
              child: const SizedBox.expand(),
            ),
          ),
        ],
      ),
    );
  }
}

class _PerfTraceChip extends StatelessWidget {
  final String label;
  final String value;
  final Color accent;

  const _PerfTraceChip({
    required this.label,
    required this.value,
    required this.accent,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: accent.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: accent.withValues(alpha: 0.28)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: TextStyle(
              color: accent,
              fontSize: 9,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.25,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  final List<double> values;
  final Color lineColor;
  final bool preferLower;

  _SparklinePainter({
    required this.values,
    required this.lineColor,
    required this.preferLower,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final axisPaint = Paint()
      ..color = Colors.white.withValues(alpha: 0.12)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    canvas.drawLine(
      Offset(0, size.height - 1),
      Offset(size.width, size.height - 1),
      axisPaint,
    );

    if (values.length < 2) return;

    final minVal = values.reduce(min);
    final maxVal = values.reduce(max);
    final span = (maxVal - minVal).abs() < 0.001 ? 1.0 : (maxVal - minVal);
    final dx = size.width / (values.length - 1);

    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = dx * i;
      final normalized = (values[i] - minVal) / span;
      final y = size.height - (normalized * (size.height - 4)) - 2;
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }

    final gradient = LinearGradient(
      begin: Alignment.topCenter,
      end: Alignment.bottomCenter,
      colors: [
        lineColor.withValues(alpha: 0.26),
        lineColor.withValues(alpha: 0.02),
      ],
    ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));

    final fillPath = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(
      fillPath,
      Paint()..shader = gradient,
    );

    canvas.drawPath(
      path,
      Paint()
        ..color = lineColor
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.7,
    );

    final latest = values.last;
    final prev = values.length > 1 ? values[values.length - 2] : latest;
    final improved = preferLower ? latest <= prev : latest >= prev;
    final markerColor =
        improved ? const Color(0xFF00E676) : const Color(0xFFFF5252);
    final latestNormalized = (latest - minVal) / span;
    final latestY = size.height - (latestNormalized * (size.height - 4)) - 2;
    canvas.drawCircle(
      Offset(size.width, latestY),
      2.6,
      Paint()..color = markerColor,
    );
  }

  @override
  bool shouldRepaint(covariant _SparklinePainter oldDelegate) {
    return oldDelegate.values != values ||
        oldDelegate.lineColor != lineColor ||
        oldDelegate.preferLower != preferLower;
  }
}

class _DiscussionClockChip extends StatelessWidget {
  final String timeText;
  final bool isPaused;
  final bool started;

  const _DiscussionClockChip({
    required this.timeText,
    required this.isPaused,
    required this.started,
  });

  @override
  Widget build(BuildContext context) {
    final accent = isPaused ? AppColors.amberGold : const Color(0xFF7CE0FF);
    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      width: 136,
      height: 48,
      padding: const EdgeInsets.symmetric(horizontal: 9),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF0E1629).withValues(alpha: 0.95),
            const Color(0xFF1B2847).withValues(alpha: 0.78),
          ],
        ),
        border: Border.all(color: accent.withValues(alpha: 0.72), width: 1.2),
        boxShadow: [
          BoxShadow(
            color: accent.withValues(alpha: isPaused ? 0.14 : 0.22),
            blurRadius: 12,
            spreadRadius: 1,
          ),
        ],
      ),
      child: Row(
        children: [
          Icon(
            started ? Icons.timer_outlined : Icons.timer,
            size: 16,
            color: accent,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              started ? timeText : '00:00',
              textAlign: TextAlign.center,
              style: GoogleFonts.orbitron(
                fontSize: 20,
                fontWeight: FontWeight.w700,
                color: Colors.white,
                letterSpacing: 1.1,
                height: 1,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── 暂停按钮 ──────────────────────────────────────────────────────────────────
/// Compact pause/resume button for the top-left of the session screen (m).
class _PauseButton extends StatelessWidget {
  final bool isPaused;
  final VoidCallback onToggle;
  const _PauseButton({required this.isPaused, required this.onToggle});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: isPaused ? '继续讨论' : '暂停讨论',
      child: GestureDetector(
        onTap: onToggle,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          width: 120,
          height: 48,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            color: isPaused
                ? AppColors.amberGold.withValues(alpha: 0.25)
                : Colors.white.withValues(alpha: 0.08),
            border: Border.all(
              color: isPaused
                  ? AppColors.amberGold.withValues(alpha: 0.8)
                  : Colors.white.withValues(alpha: 0.25),
              width: 1.5,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                isPaused ? Icons.play_arrow : Icons.pause,
                color: isPaused
                    ? AppColors.amberGold
                    : Colors.white.withValues(alpha: 0.75),
                size: 20,
              ),
              const SizedBox(width: 6),
              Text(
                isPaused ? '继续' : '暂停',
                style: TextStyle(
                  color: isPaused
                      ? AppColors.amberGold
                      : Colors.white.withValues(alpha: 0.85),
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _TopRectButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  const _TopRectButton({
    required this.icon,
    required this.label,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      ignoring: onTap == null,
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedOpacity(
          duration: const Duration(milliseconds: 180),
          opacity: onTap == null ? 0.55 : 1.0,
          child: Container(
            width: 120,
            height: 48,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              color: Colors.white.withValues(alpha: 0.08),
              border: Border.all(
                color: Colors.white.withValues(alpha: 0.25),
              ),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon, color: AppColors.warmWhite, size: 20),
                const SizedBox(width: 6),
                Text(
                  label,
                  style: TextStyle(
                    color: AppColors.warmWhite,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _RightActionColumn extends StatelessWidget {
  final bool showMic;
  final bool isRecording;
  final VoidCallback onStart;
  final VoidCallback onEnd;
  final bool showSkip;
  final VoidCallback? onSkip;
  final bool hasRaisedHand;
  final bool canInterrupt;
  final VoidCallback onRaiseHand;
  final _ObserverSeatBadgeData? observerSeat;
  final double totalHeight;

  const _RightActionColumn({
    required this.showMic,
    required this.isRecording,
    required this.onStart,
    required this.onEnd,
    this.showSkip = false,
    this.onSkip,
    required this.hasRaisedHand,
    required this.canInterrupt,
    required this.onRaiseHand,
    this.observerSeat,
    required this.totalHeight,
  });

  @override
  Widget build(BuildContext context) {
    final hasObserverSeat = observerSeat != null;
    final spacing = hasObserverSeat ? 10.0 : 14.0;
    final observerHeight = hasObserverSeat ? 92.0 : 0.0;
    final gapCount = hasObserverSeat ? 3.0 : 2.0;
    final btnHeight = ((totalHeight - observerHeight - spacing * gapCount) / 3)
        .clamp(48.0, 120.0);
    return SizedBox(
      height: totalHeight,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.start,
        mainAxisSize: MainAxisSize.max,
        children: [
          _SpeakButton(
            isRecording: isRecording,
            enabled: showMic,
            onStart: onStart,
            onEnd: onEnd,
            size: btnHeight,
          ),
          SizedBox(height: spacing),
          _SkipButton(onTap: showSkip ? onSkip : null, size: btnHeight),
          SizedBox(height: spacing),
          _FloatingRaiseHandButton(
            hasRaisedHand: hasRaisedHand,
            canInterrupt: canInterrupt,
            onTap: onRaiseHand,
            size: btnHeight,
          ),
          if (observerSeat != null) ...[
            SizedBox(height: spacing),
            _ObserverSeatBadge(data: observerSeat!),
          ],
        ],
      ),
    );
  }
}

class _ObserverSeatBadgeData {
  final String name;
  final String avatar;
  final String statusLabel;
  final bool hasRaisedHand;
  final bool isReservedToSpeak;
  final bool isActive;

  const _ObserverSeatBadgeData({
    required this.name,
    required this.avatar,
    required this.statusLabel,
    required this.hasRaisedHand,
    required this.isReservedToSpeak,
    required this.isActive,
  });
}

class _ObserverSeatBadge extends StatelessWidget {
  final _ObserverSeatBadgeData data;

  const _ObserverSeatBadge({required this.data});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 84,
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 10),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(18),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFF11202C).withValues(alpha: 0.94),
            const Color(0xFF1C2F42).withValues(alpha: 0.84),
          ],
        ),
        border: Border.all(
          color: data.isReservedToSpeak || data.isActive
              ? AppColors.amberGold.withValues(alpha: 0.68)
              : Colors.white.withValues(alpha: 0.16),
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.18),
            blurRadius: 16,
            offset: const Offset(0, 10),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              Container(
                width: 42,
                height: 42,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: data.isActive
                        ? AppColors.amberGold
                        : Colors.white.withValues(alpha: 0.3),
                    width: 2,
                  ),
                ),
                child: ClipOval(
                  child: Image.network(
                    data.avatar,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const Center(
                      child: Text('🙂', style: TextStyle(fontSize: 22)),
                    ),
                  ),
                ),
              ),
              if (data.hasRaisedHand)
                Positioned(
                  right: -2,
                  top: -2,
                  child: Container(
                    width: 18,
                    height: 18,
                    decoration: const BoxDecoration(
                      color: AppColors.accentWarm,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.back_hand,
                      size: 10,
                      color: Colors.white,
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            data.name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            data.statusLabel,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: data.isReservedToSpeak || data.isActive
                  ? AppColors.amberGold
                  : Colors.white.withValues(alpha: 0.62),
              fontSize: 9.5,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

// ─── 跳过发言按钮（需求4.2）──────────────────────────────────────────────────
class _SkipButton extends StatelessWidget {
  final VoidCallback? onTap;
  final double size;
  const _SkipButton({this.onTap, this.size = 48});

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 72,
        height: size,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          color: enabled
              ? Colors.white.withValues(alpha: 0.1)
              : Colors.white.withValues(alpha: 0.04),
          border: Border.all(
            color: enabled
                ? Colors.white.withValues(alpha: 0.4)
                : Colors.white.withValues(alpha: 0.15),
            width: 1.5,
          ),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.skip_next,
                color: enabled ? Colors.white70 : Colors.white24, size: 20),
            Text(
              '跳过',
              style: TextStyle(
                color: enabled ? Colors.white70 : Colors.white24,
                fontSize: 10,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ─── 浮动举手按钮 (j) ──────────────────────────────────────────────────────────
/// Large floating raise-hand button on the right side of the session screen.
class _FloatingRaiseHandButton extends StatefulWidget {
  final bool hasRaisedHand;
  final bool canInterrupt;
  final VoidCallback onTap;
  final double size;
  const _FloatingRaiseHandButton({
    required this.hasRaisedHand,
    required this.canInterrupt,
    required this.onTap,
    this.size = 72,
  });

  @override
  State<_FloatingRaiseHandButton> createState() =>
      _FloatingRaiseHandButtonState();
}

class _FloatingRaiseHandButtonState extends State<_FloatingRaiseHandButton>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseCtrl;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );
    if (widget.canInterrupt && !widget.hasRaisedHand) {
      _pulseCtrl.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(_FloatingRaiseHandButton old) {
    super.didUpdateWidget(old);
    if (widget.canInterrupt && !widget.hasRaisedHand) {
      if (!_pulseCtrl.isAnimating) _pulseCtrl.repeat(reverse: true);
    } else {
      _pulseCtrl.stop();
      _pulseCtrl.value = 0;
    }
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _pulseCtrl,
      builder: (context, _) {
        final pulse = 0.85 + _pulseCtrl.value * 0.15;
        return GestureDetector(
          onTap: (widget.canInterrupt && !widget.hasRaisedHand)
              ? widget.onTap
              : null,
          child: Transform.scale(
            scale: pulse,
            child: Container(
              width: 72,
              height: widget.size,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(18),
                color: widget.hasRaisedHand
                    ? AppColors.amberGold.withValues(alpha: 0.35)
                    : AppColors.amberGold.withValues(alpha: 0.18),
                border: Border.all(
                  color: AppColors.amberGold.withValues(alpha: 0.9),
                  width: 2,
                ),
                boxShadow: [
                  BoxShadow(
                    color: AppColors.amberGold.withValues(alpha: 0.3),
                    blurRadius: 16,
                    spreadRadius: 3,
                  ),
                ],
              ),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    widget.hasRaisedHand
                        ? Icons.back_hand
                        : Icons.back_hand_outlined,
                    color: AppColors.amberGold,
                    size: 24,
                  ),
                  Text(
                    widget.hasRaisedHand ? '已举手' : '举手',
                    style: TextStyle(
                      color: AppColors.amberGold,
                      fontSize: 9,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

// ─── 右侧讲话/结束按钮（需求5 - 鼠标点击启动麦克风）─────────────────────────────
/// 明显的大按钮，仅在轮到用户时淡出显示。
class _SpeakButton extends StatefulWidget {
  final bool isRecording;
  final bool enabled;
  final VoidCallback onStart;
  final VoidCallback onEnd;
  final double size;

  const _SpeakButton({
    required this.isRecording,
    this.enabled = true,
    required this.onStart,
    required this.onEnd,
    this.size = 72,
  });

  @override
  State<_SpeakButton> createState() => _SpeakButtonState();
}

class _SpeakButtonState extends State<_SpeakButton>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseCtrl;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    if (!widget.isRecording && widget.enabled) {
      _pulseCtrl.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(_SpeakButton old) {
    super.didUpdateWidget(old);
    if (widget.isRecording) {
      _pulseCtrl.stop();
      _pulseCtrl.value = 1.0;
    } else if (!widget.enabled) {
      _pulseCtrl.stop();
      _pulseCtrl.value = 0.0;
    } else {
      if (!_pulseCtrl.isAnimating) _pulseCtrl.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _pulseCtrl,
      builder: (context, _) {
        final pulse = widget.isRecording || !widget.enabled
            ? 1.0
            : 0.85 + _pulseCtrl.value * 0.15;
        return GestureDetector(
          onTap: !widget.enabled
              ? null
              : (widget.isRecording ? widget.onEnd : widget.onStart),
          child: Transform.scale(
            scale: pulse,
            child: Container(
              width: 72,
              height: widget.size,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(18),
                // 需求8：非可用状态采用与跳过按钮一致的浅灰低亮度
                color: widget.isRecording
                    ? const Color(0xFFFF4444).withValues(alpha: 0.25)
                    : (widget.enabled
                        ? const Color(0xFF00FFCC).withValues(alpha: 0.18)
                        : Colors.white.withValues(alpha: 0.04)),
                border: Border.all(
                  color: widget.isRecording
                      ? const Color(0xFFFF4444).withValues(alpha: 0.9)
                      : (widget.enabled
                          ? const Color(0xFF00FFCC).withValues(alpha: 0.9)
                          : Colors.white.withValues(alpha: 0.15)),
                  width: widget.enabled || widget.isRecording ? 2.5 : 1.5,
                ),
                boxShadow: widget.enabled || widget.isRecording
                    ? [
                        BoxShadow(
                          color: widget.isRecording
                              ? const Color(0xFFFF4444).withValues(alpha: 0.35)
                              : const Color(0xFF00FFCC).withValues(alpha: 0.3),
                          blurRadius: 20,
                          spreadRadius: 5,
                        ),
                      ]
                    : const [],
              ),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    widget.isRecording ? Icons.stop : Icons.mic,
                    color: widget.isRecording
                        ? const Color(0xFFFF4444)
                        : (widget.enabled
                            ? const Color(0xFF00FFCC)
                            : Colors.white24),
                    size: 28,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    widget.isRecording ? '结束' : '讲话',
                    style: TextStyle(
                      color: widget.isRecording
                          ? const Color(0xFFFF4444)
                          : (widget.enabled
                              ? const Color(0xFF00FFCC)
                              : Colors.white24),
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _PostDiscussionReviewOverlay extends StatelessWidget {
  final String review;
  final bool isLoading;
  final bool isMuted;
  final VoidCallback onToggleMute;
  final VoidCallback onExit;

  const _PostDiscussionReviewOverlay({
    required this.review,
    required this.isLoading,
    required this.isMuted,
    required this.onToggleMute,
    required this.onExit,
  });

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final dialogWidth = (size.width * 0.7).clamp(420.0, 980.0);
    final dialogHeight = (size.height * 0.7).clamp(360.0, 720.0);
    final displayText = isLoading ? '李老师正在回看你刚才的发言，准备留下一段更具体的会后点评……' : review;

    return Stack(
      fit: StackFit.expand,
      children: [
        Positioned.fill(
          child: ColoredBox(
            color: Colors.black.withValues(alpha: 0.78),
          ),
        ),
        Center(
          child: Container(
            width: dialogWidth,
            height: dialogHeight,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(36),
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  const Color(0xFF243349).withValues(alpha: 0.98),
                  const Color(0xFF13202B).withValues(alpha: 0.98),
                ],
              ),
              border: Border.all(
                color: const Color(0xFFF4D38B).withValues(alpha: 0.45),
                width: 1.4,
              ),
              boxShadow: [
                BoxShadow(
                  color: const Color(0xFFF4D38B).withValues(alpha: 0.18),
                  blurRadius: 36,
                  spreadRadius: 2,
                ),
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.45),
                  blurRadius: 60,
                  offset: const Offset(0, 28),
                ),
              ],
            ),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(40, 32, 40, 28),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.auto_awesome_rounded,
                          color: Color(0xFFFFE8B5), size: 24),
                      const SizedBox(width: 10),
                      Text(
                        '老师点评',
                        style: GoogleFonts.notoSerifSc(
                          color: const Color(0xFFFFE8B5),
                          fontSize: 28,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 2.0,
                        ),
                      ),
                      const Spacer(),
                      InkWell(
                        onTap: onToggleMute,
                        borderRadius: BorderRadius.circular(999),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 14, vertical: 8),
                          decoration: BoxDecoration(
                            color: Colors.white.withValues(alpha: 0.06),
                            borderRadius: BorderRadius.circular(999),
                            border: Border.all(
                              color: const Color(0xFFF4D38B)
                                  .withValues(alpha: 0.32),
                            ),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                isMuted
                                    ? Icons.volume_up_rounded
                                    : Icons.volume_off_rounded,
                                size: 16,
                                color: const Color(0xFFF4D38B),
                              ),
                              const SizedBox(width: 6),
                              Text(
                                isMuted ? '朗读' : '静音',
                                style: const TextStyle(
                                  color: Color(0xFFF4D38B),
                                  fontSize: 13,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  Expanded(
                    child: Container(
                      decoration: BoxDecoration(
                        color: Colors.black.withValues(alpha: 0.18),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(
                          color: Colors.white.withValues(alpha: 0.08),
                        ),
                      ),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 28, vertical: 24),
                      child: SingleChildScrollView(
                        child: Align(
                          alignment: Alignment.topLeft,
                          child: SizedBox(
                            width: double.infinity,
                            child: Text(
                              displayText,
                              style: GoogleFonts.notoSerifSc(
                                color: Colors.white
                                    .withValues(alpha: isLoading ? 0.7 : 0.94),
                                fontSize: 20,
                                height: 1.85,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 18),
                  Row(
                    children: [
                      Text(
                        isLoading ? '点评完成后可退场' : '点评已完成',
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.5),
                          fontSize: 13,
                        ),
                      ),
                      const Spacer(),
                      if (isLoading)
                        const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Color(0xFFF4D38B),
                          ),
                        ),
                      if (!isLoading)
                        FilledButton.icon(
                          onPressed: onExit,
                          icon: const Icon(Icons.logout_rounded, size: 18),
                          label: const Text('退场'),
                          style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFFF4D38B),
                            foregroundColor: const Color(0xFF1B2D40),
                            padding: const EdgeInsets.symmetric(
                                horizontal: 24, vertical: 14),
                            textStyle: const TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

// ─── 需求21/九：讨论结束后仅保留金句画面，支持"思想家朗读"开关 ────────────
class _EndingQuotesScreen extends StatefulWidget {
  final List<String> quotes;
  final VoidCallback onExit;
  final Future<void> Function(List<String> quotes) onReadAloud;
  final VoidCallback onStopReadAloud;
  const _EndingQuotesScreen({
    required this.quotes,
    required this.onExit,
    required this.onReadAloud,
    required this.onStopReadAloud,
  });

  @override
  State<_EndingQuotesScreen> createState() => _EndingQuotesScreenState();
}

class _EndingQuotesScreenState extends State<_EndingQuotesScreen> {
  bool _reading = false;

  Future<void> _toggleRead() async {
    if (_reading) {
      widget.onStopReadAloud();
      setState(() => _reading = false);
      return;
    }
    setState(() => _reading = true);
    try {
      await widget.onReadAloud(widget.quotes);
    } finally {
      if (mounted) setState(() => _reading = false);
    }
  }

  @override
  void dispose() {
    widget.onStopReadAloud();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final screenSize = MediaQuery.sizeOf(context);
    final compactLayout = screenSize.width < 900 || screenSize.height < 820;
    final display = widget.quotes.isEmpty
        ? const ['今夜的话题落下以后，真正留住人的，往往就是这一句。']
        : widget.quotes.take(4).toList(growable: false);
    final heroQuote = display.first;
    final supportingQuotes = display.skip(1).toList(growable: false);
    return Scaffold(
      backgroundColor: const Color(0xFF081018),
      body: SafeArea(
        child: Stack(
          children: [
            Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      const Color(0xFF122033),
                      const Color(0xFF081018),
                      const Color(0xFF140E18),
                    ],
                    stops: const [0.0, 0.48, 1.0],
                  ),
                ),
              ),
            ),
            Positioned(
              left: -60,
              top: 24,
              child: IgnorePointer(
                child: Container(
                  width: 260,
                  height: 260,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(
                      colors: [
                        const Color(0xFFF2C46D).withValues(alpha: 0.18),
                        Colors.transparent,
                      ],
                    ),
                  ),
                ),
              ),
            ),
            Positioned(
              left: compactLayout ? 14 : 34,
              top: compactLayout ? 14 : 28,
              bottom: compactLayout ? 14 : 28,
              child: IgnorePointer(
                child: Container(
                  width: 1,
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [
                        Colors.transparent,
                        const Color(0xFFF4D38B).withValues(alpha: 0.32),
                        Colors.transparent,
                      ],
                    ),
                  ),
                ),
              ),
            ),
            Positioned(
              right: -30,
              bottom: 10,
              child: IgnorePointer(
                child: Container(
                  width: 220,
                  height: 220,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(
                      colors: [
                        const Color(0xFF7FD7C4).withValues(alpha: 0.16),
                        Colors.transparent,
                      ],
                    ),
                  ),
                ),
              ),
            ),
            Center(
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  maxWidth: compactLayout ? 720 : 1100,
                  maxHeight: compactLayout ? screenSize.height - 28 : 780,
                ),
                child: Container(
                  margin: EdgeInsets.symmetric(
                    horizontal: compactLayout ? 18 : 24,
                    vertical: compactLayout ? 16 : 24,
                  ),
                  padding: compactLayout
                      ? const EdgeInsets.fromLTRB(20, 22, 20, 18)
                      : const EdgeInsets.fromLTRB(34, 34, 34, 26),
                  decoration: BoxDecoration(
                    borderRadius:
                        BorderRadius.circular(compactLayout ? 28 : 36),
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        const Color(0xFFF4E7C7).withValues(alpha: 0.08),
                        const Color(0xFF112235).withValues(alpha: 0.9),
                        const Color(0xFF171B24).withValues(alpha: 0.96),
                      ],
                      stops: const [0.0, 0.24, 1.0],
                    ),
                    border: Border.all(
                      color: const Color(0xFFF4D38B).withValues(alpha: 0.22),
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.26),
                        blurRadius: 42,
                        offset: const Offset(0, 24),
                      ),
                    ],
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Align(
                        alignment: Alignment.centerLeft,
                        child: compactLayout
                            ? Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  _EndingQuotesEditorialHeader(
                                    compact: compactLayout,
                                  ),
                                  const SizedBox(height: 14),
                                  _EndingQuotesActionButton(
                                    icon: _reading
                                        ? Icons.stop_circle
                                        : Icons.volume_up_rounded,
                                    label: _reading ? '停止朗读' : '思想家朗读',
                                    active: _reading,
                                    onPressed: _toggleRead,
                                  ),
                                ],
                              )
                            : Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Expanded(
                                    child: _EndingQuotesEditorialHeader(
                                      compact: compactLayout,
                                    ),
                                  ),
                                  const SizedBox(width: 16),
                                  _EndingQuotesActionButton(
                                    icon: _reading
                                        ? Icons.stop_circle
                                        : Icons.volume_up_rounded,
                                    label: _reading ? '停止朗读' : '思想家朗读',
                                    active: _reading,
                                    onPressed: _toggleRead,
                                  ),
                                ],
                              ),
                      ),
                      SizedBox(height: compactLayout ? 18 : 26),
                      Expanded(
                        child: compactLayout
                            ? ListView(
                                padding: EdgeInsets.zero,
                                physics: const BouncingScrollPhysics(),
                                children: [
                                  TweenAnimationBuilder<double>(
                                    duration: const Duration(milliseconds: 460),
                                    curve: Curves.easeOutCubic,
                                    tween: Tween(begin: 0.0, end: 1.0),
                                    builder: (_, t, child) => Opacity(
                                      opacity: t,
                                      child: Transform.translate(
                                        offset: Offset(0, (1 - t) * 18),
                                        child: child,
                                      ),
                                    ),
                                    child: _EndingQuoteHeroCard(
                                      quote: heroQuote,
                                      compact: true,
                                    ),
                                  ),
                                  if (supportingQuotes.isNotEmpty) ...[
                                    const SizedBox(height: 18),
                                    ...List<Widget>.generate(
                                      supportingQuotes.length,
                                      (index) => Padding(
                                        padding: EdgeInsets.only(
                                          bottom: index ==
                                                  supportingQuotes.length - 1
                                              ? 0
                                              : 12,
                                        ),
                                        child: TweenAnimationBuilder<double>(
                                          duration: Duration(
                                              milliseconds: 520 + index * 90),
                                          curve: Curves.easeOutCubic,
                                          tween: Tween(begin: 0.0, end: 1.0),
                                          builder: (_, t, child) => Opacity(
                                            opacity: t,
                                            child: Transform.translate(
                                              offset: Offset(0, (1 - t) * 14),
                                              child: child,
                                            ),
                                          ),
                                          child: _EndingQuoteCard(
                                            index: index + 1,
                                            quote: supportingQuotes[index],
                                            compactMode: true,
                                          ),
                                        ),
                                      ),
                                    ),
                                  ],
                                ],
                              )
                            : supportingQuotes.isEmpty
                                ? TweenAnimationBuilder<double>(
                                    duration: const Duration(milliseconds: 460),
                                    curve: Curves.easeOutCubic,
                                    tween: Tween(begin: 0.0, end: 1.0),
                                    builder: (_, t, child) => Opacity(
                                      opacity: t,
                                      child: Transform.translate(
                                        offset: Offset(0, (1 - t) * 24),
                                        child: child,
                                      ),
                                    ),
                                    child: _EndingQuoteHeroCard(
                                      quote: heroQuote,
                                      compact: false,
                                    ),
                                  )
                                : Row(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.stretch,
                                    children: [
                                      Expanded(
                                        flex: 10,
                                        child: TweenAnimationBuilder<double>(
                                          duration:
                                              const Duration(milliseconds: 460),
                                          curve: Curves.easeOutCubic,
                                          tween: Tween(begin: 0.0, end: 1.0),
                                          builder: (_, t, child) => Opacity(
                                            opacity: t,
                                            child: Transform.translate(
                                              offset: Offset(0, (1 - t) * 24),
                                              child: child,
                                            ),
                                          ),
                                          child: _EndingQuoteHeroCard(
                                            quote: heroQuote,
                                            compact: false,
                                          ),
                                        ),
                                      ),
                                      const SizedBox(width: 18),
                                      Expanded(
                                        flex: 8,
                                        child: Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                              '余韵',
                                              style: TextStyle(
                                                color: Colors.white
                                                    .withValues(alpha: 0.54),
                                                fontSize: 11,
                                                fontWeight: FontWeight.w700,
                                                letterSpacing: 2.4,
                                              ),
                                            ),
                                            const SizedBox(height: 12),
                                            Expanded(
                                              child: ListView.separated(
                                                padding: EdgeInsets.zero,
                                                physics:
                                                    const BouncingScrollPhysics(),
                                                itemCount:
                                                    supportingQuotes.length,
                                                separatorBuilder: (_, __) =>
                                                    const SizedBox(height: 14),
                                                itemBuilder: (context, index) {
                                                  return TweenAnimationBuilder<
                                                      double>(
                                                    duration: Duration(
                                                        milliseconds:
                                                            520 + index * 90),
                                                    curve: Curves.easeOutCubic,
                                                    tween: Tween(
                                                        begin: 0.0, end: 1.0),
                                                    builder: (_, t, child) =>
                                                        Opacity(
                                                      opacity: t,
                                                      child:
                                                          Transform.translate(
                                                        offset: Offset(
                                                            0, (1 - t) * 14),
                                                        child: child,
                                                      ),
                                                    ),
                                                    child: _EndingQuoteCard(
                                                      index: index + 1,
                                                      quote: supportingQuotes[
                                                          index],
                                                      compactMode: false,
                                                    ),
                                                  );
                                                },
                                              ),
                                            ),
                                          ],
                                        ),
                                      ),
                                    ],
                                  ),
                      ),
                      SizedBox(height: compactLayout ? 16 : 22),
                      SizedBox(
                        width: compactLayout ? double.infinity : 220,
                        child: FilledButton.icon(
                          onPressed: widget.onExit,
                          style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFFF4D38B),
                            foregroundColor: const Color(0xFF152334),
                            padding: EdgeInsets.symmetric(
                              horizontal: compactLayout ? 18 : 24,
                              vertical: compactLayout ? 14 : 16,
                            ),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(999),
                            ),
                          ),
                          icon: const Icon(Icons.check_circle_outline_rounded),
                          label: Text(
                            '结束',
                            style: TextStyle(
                              fontSize: compactLayout ? 15 : 16,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 1.2,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _EndingQuotesEditorialHeader extends StatelessWidget {
  final bool compact;

  const _EndingQuotesEditorialHeader({required this.compact});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '夜读摘录',
          style: AppTheme.calligraphyStyleDark(
            fontSize: compact ? 26 : 34,
            color: const Color(0xFFFFE8B5),
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          '把今晚最值得带走的话，排成一页真正能停下来看的作品。',
          style: GoogleFonts.notoSerifSc(
            color: const Color(0xFFE8F4F1).withValues(alpha: 0.78),
            fontSize: compact ? 12 : 13.5,
            height: 1.75,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _EndingQuotesMetaChip(label: '夜色书页'),
            _EndingQuotesMetaChip(label: '讨论余温'),
            _EndingQuotesMetaChip(label: '适合朗读'),
          ],
        ),
      ],
    );
  }
}

class _EndingQuotesMetaChip extends StatelessWidget {
  final String label;

  const _EndingQuotesMetaChip({required this.label});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: Colors.white.withValues(alpha: 0.72),
          fontSize: 10.5,
          fontWeight: FontWeight.w600,
          letterSpacing: 1.1,
        ),
      ),
    );
  }
}

class _EndingQuoteHeroCard extends StatelessWidget {
  final String quote;
  final bool compact;

  const _EndingQuoteHeroCard({required this.quote, required this.compact});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: compact
          ? const EdgeInsets.fromLTRB(20, 20, 20, 20)
          : const EdgeInsets.fromLTRB(28, 28, 28, 28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(compact ? 28 : 34),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFFF5DFC0).withValues(alpha: 0.16),
            const Color(0xFF1B2D40).withValues(alpha: 0.94),
            const Color(0xFF13202B).withValues(alpha: 0.98),
          ],
        ),
        border: Border.all(
          color: const Color(0xFFF4D38B).withValues(alpha: 0.22),
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.18),
            blurRadius: 28,
            offset: const Offset(0, 18),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '主句',
            style: TextStyle(
              color: const Color(0xFFFFE8B5).withValues(alpha: 0.9),
              fontSize: compact ? 10.5 : 11.5,
              fontWeight: FontWeight.w700,
              letterSpacing: 2.6,
            ),
          ),
          SizedBox(height: compact ? 18 : 24),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '“',
                style: TextStyle(
                  color: const Color(0xFFF4D38B),
                  fontSize: compact ? 42 : 56,
                  fontWeight: FontWeight.w700,
                  height: 0.8,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    quote,
                    style: GoogleFonts.notoSerifSc(
                      color: const Color(0xFFF9F6F0),
                      fontSize: compact ? 21 : 29,
                      height: compact ? 1.85 : 1.95,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
            ],
          ),
          SizedBox(height: compact ? 18 : 24),
          Row(
            children: [
              Expanded(
                child: Container(
                  height: 1,
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      colors: [
                        const Color(0xFFF4D38B).withValues(alpha: 0.0),
                        const Color(0xFFF4D38B).withValues(alpha: 0.5),
                        const Color(0xFF7FD7C4).withValues(alpha: 0.28),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Text(
                '留白也是回声',
                style: GoogleFonts.notoSerifSc(
                  color: Colors.white.withValues(alpha: 0.46),
                  fontSize: compact ? 10.5 : 11.5,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _EndingQuoteCard extends StatelessWidget {
  final int index;
  final String quote;
  final bool compactMode;

  const _EndingQuoteCard({
    required this.index,
    required this.quote,
    this.compactMode = false,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final compact = compactMode || constraints.maxWidth < 560;
        final quoteFontSize = compact ? 15.0 : 17.0;
        final lineHeight = compact ? 1.7 : 1.78;

        return Container(
          width: double.infinity,
          padding: compact
              ? const EdgeInsets.fromLTRB(16, 16, 16, 16)
              : const EdgeInsets.fromLTRB(18, 18, 18, 18),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(compact ? 20 : 24),
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                const Color(0xFFF7E4B7).withValues(alpha: 0.10),
                Colors.white.withValues(alpha: 0.04),
                const Color(0xFF7FD7C4).withValues(alpha: 0.04),
              ],
            ),
            border: Border.all(
              color: const Color(0xFFF4D38B).withValues(alpha: 0.14),
            ),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.12),
                blurRadius: 18,
                offset: const Offset(0, 10),
              ),
            ],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                padding: EdgeInsets.symmetric(
                  horizontal: compact ? 10 : 12,
                  vertical: compact ? 5 : 6,
                ),
                decoration: BoxDecoration(
                  color: const Color(0xFFF4D38B).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(
                    color: const Color(0xFFF4D38B).withValues(alpha: 0.18),
                  ),
                ),
                child: Text(
                  '余韵 ${index + 1}',
                  style: TextStyle(
                    color: const Color(0xFFFFE8B5),
                    fontSize: compact ? 10 : 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.6,
                  ),
                ),
              ),
              SizedBox(height: compact ? 12 : 14),
              Text(
                quote,
                style: GoogleFonts.notoSerifSc(
                  color: const Color(0xFFF9F6F0),
                  fontSize: quoteFontSize,
                  height: lineHeight,
                  fontWeight: FontWeight.w600,
                ),
              ),
              SizedBox(height: compact ? 12 : 14),
              Container(
                width: compact ? 72 : 84,
                height: 2,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(999),
                  gradient: LinearGradient(
                    colors: [
                      const Color(0xFFF4D38B).withValues(alpha: 0.92),
                      const Color(0xFF7FD7C4).withValues(alpha: 0.72),
                    ],
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _EndingQuotesActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool active;
  final VoidCallback onPressed;

  const _EndingQuotesActionButton({
    required this.icon,
    required this.label,
    required this.active,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        gradient: LinearGradient(
          colors: active
              ? [
                  const Color(0xFFF4D38B).withValues(alpha: 0.22),
                  const Color(0xFF7FD7C4).withValues(alpha: 0.14),
                ]
              : [
                  Colors.white.withValues(alpha: 0.06),
                  Colors.white.withValues(alpha: 0.03),
                ],
        ),
        border: Border.all(
          color: active
              ? const Color(0xFFF4D38B).withValues(alpha: 0.34)
              : Colors.white.withValues(alpha: 0.08),
        ),
      ),
      child: TextButton.icon(
        onPressed: onPressed,
        style: TextButton.styleFrom(
          foregroundColor: active
              ? const Color(0xFFFFE8B5)
              : Colors.white.withValues(alpha: 0.84),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
        ),
        icon: Icon(icon, size: 18),
        label: Text(
          label,
          style: GoogleFonts.notoSerifSc(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.6,
          ),
        ),
      ),
    );
  }
}
