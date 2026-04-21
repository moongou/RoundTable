import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
import 'session_frontend_commander.dart';
import 'table_participant_ring.dart';

/// 沉浸式讨论界面 - 圆桌围坐体验
class ImmersiveSessionScreen extends ConsumerStatefulWidget {
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
  bool _hasRaisedHand = false;
  bool _isPaused = false;
  // 需求4：用户发言完毕后，麦克风应立即置灰，直到下轮发言或举手经同意
  bool _micLocked = false;
  // 需求15：AI 总结的金句列表（侧边栏展示）
  final List<String> _liveQuotes = [];
  // 需求21：讨论结束后仅显示金句画面
  bool _discussionEnded = false;
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

  /// Debug-only accessor so static analysis sees a read of the latent field.
  // ignore: unused_element
  String get _activeTtsSource => _activeTtsItem?.source ?? '';
  int _ttsSessionSeq = 0;
  String _activeTtsSessionId = '';
  bool _ttsPumpRunning = false;
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
  Timer? _speechFinalizeTimer;
  bool _isFinalizingSpeech = false; // 防止多次快速按 Ctrl 导致并发 finalize
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
  Timer? _deferredAutoSkipTimer;
  DateTime? _humanTurnActivatedAt;
  static const Duration _minHumanTurnAutoSkipWindow = Duration(seconds: 10);

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
  late TtsService _ttsService;
  late AsrService _asrService;
  TtsService? _browserFallbackTts;

  // 动画
  late AnimationController _candleController;
  late AnimationController _glowController;
  late AnimationController _micController;
  late AnimationController _thinkingController; // (f) thinking dots
  Timer? _turnTimer; // (bug 3) user turn countdown
  List<CandleParticle>? _particles;
  OverlayEntry? _statusToastEntry;
  Timer? _statusToastTimer;
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
  String _asrProviderId = 'browser';
  final List<double> _ttsStartupSeries = [];
  final List<double> _asrFirstPacketSeries = [];
  final List<double> _prefetchHitRateSeries = [];
  static const int _maxReportHistory = 12;
  final List<_PerfReportEntry> _reportHistory = [];
  static const int _maxPhaseTelemetryHistory = 80;
  final List<_PhaseTelemetryEntry> _phaseTelemetryHistory = [];

  // 角色显示名 → Edge TTS 音色映射（与后端 YAML 配置严格同步）
  static const _nameToVoice = <String, String>{
    '李老师': 'zh-CN-XiaoxiaoNeural', // 温暖女声·教师（成人音色）
    '小探': 'zh-CN-YunxiNeural', // 少年男声·探索
    '小疑': 'zh-CN-YunyeNeural', // 年轻男声·质疑
    '小和': 'zh-CN-XiaoyiNeural', // 柔和女声·和平
    '小说': 'zh-CN-XiaohanNeural', // 活泼女声·讲故事
    '小明': 'zh-CN-YunjieNeural', // 阳光男声·乐观
    '小思': 'zh-CN-YunxiaNeural', // 明亮男声·提问
    '小理': 'zh-CN-YunyangNeural', // 正式男声·理性
    '小爱': 'zh-CN-XiaomengNeural', // 可爱女声·共情
    '小想': 'zh-CN-YunfengNeural', // 稳健男声·创新
    '小行': 'zh-CN-YunhaoNeural', // 年轻男声·务实
  };

  // 每次会话分配的参与者音色表（用于思想家的哈希分配）
  final Map<String, String> _voiceMap = {};

  // Edge TTS 男声列表（供思想家轮转使用）
  static const _maleThinkerVoices = <String>[
    'zh-CN-YunyangNeural',
    'zh-CN-YunxiNeural',
    'zh-CN-YunzeNeural',
    'zh-CN-YunjieNeural',
    'zh-CN-YunfengNeural',
    'zh-CN-YunjianNeural',
    'zh-CN-YunxiaNeural',
  ];

  // Edge TTS 女声列表（供女性思想家轮转使用）(g)
  static const _femaleThinkerVoices = <String>[
    'zh-CN-XiaoxiaoNeural',
    'zh-CN-XiaoyiNeural',
    'zh-CN-XiaohanNeural',
    'zh-CN-XiaomengNeural',
    'zh-CN-XiaochenNeural',
    'zh-CN-XiaoshuangNeural',
    'zh-CN-XiaoxuanNeural',
    'zh-CN-XiaoruiNeural',
    'zh-CN-XiaozhenNeural',
    'zh-CN-XiaoyanNeural',
  ];

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
    // Global hardware keyboard listener for Ctrl gesture PTT.
    HardwareKeyboard.instance.addHandler(_onHardwareKey);
    _initVoiceServices();
    _startDiscussion();
  }

  // PTT 触发逻辑：双击 Ctrl 开关录音，或按住 Ctrl 录音（可在设置切换）。
  bool _onHardwareKey(KeyEvent event) {
    if (!_isPushToTalk || _isPaused) return false;
    final isCtrl = event.logicalKey == LogicalKeyboardKey.controlLeft ||
        event.logicalKey == LogicalKeyboardKey.controlRight;
    if (!isCtrl) return false;

    if (event is KeyUpEvent) {
      _ctrlHeld = false;
      if (_micControlMode == 'hold_ctrl' && _isRecording) {
        _onPttEnd();
        return true;
      }
      return false;
    }

    if (event is! KeyDownEvent) return false;
    if (_ctrlHeld) return false;
    _ctrlHeld = true;

    if (_micControlMode == 'hold_ctrl') {
      if (_isMyTurn && !_isRecording) {
        _onPttStart();
        return true;
      }
      return false;
    }

    // double_ctrl 模式下也允许单击 Ctrl 直接开关录音，降低时序丢失导致的“按了没反应”。
    if ((_isMyTurn || _handApprovedToSpeak || _pendingHumanTurn) &&
        !_isRecording) {
      _onPttStart();
      return true;
    }
    if (_isRecording) {
      _onPttEnd();
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
      _onPttEnd();
      return true;
    }
    return false;
  }

  void _initVoiceServices() {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    final serverUrl = settings?.serverUrl ?? 'http://localhost:8001';
    final ttsProvider = settings?.ttsProvider ?? 'browser';
    final asrProvider = settings?.asrProvider ?? 'browser';
    _asrProviderId = asrProvider;

    _ttsService = createTtsService(ttsProvider, serverUrl: serverUrl);
    _asrService = createAsrService(asrProvider, serverUrl: serverUrl);
    if (ttsProvider != 'browser') {
      _browserFallbackTts = createTtsService('browser', serverUrl: serverUrl);
    } else {
      _browserFallbackTts = null;
    }
    _reportAsrStatus(
      'initialized',
      available: _asrService.isAvailable,
      listening: _asrService.isListening,
    );

    // 监听 ASR 转录结果：流式写入草稿，最终片段用于收尾。
    _asrService.transcriptionStream.listen((result) {
      final chunk = result.text.trim();
      if (chunk.isEmpty) return;
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
            : _mergeStreamingDraft(_sttPartialText, chunk);
        if (_sttPartialText.trim().isNotEmpty) {
          _lastNonEmptySttText = _sttPartialText.trim();
        }
        if (_isRecording) {
          _centerSpeaker = widget.humanName;
          _centerMessage = _sttPartialText;
        }
      });
      _debugSubtitleLog(triggerRole: widget.humanName, note: 'stt streaming');
    }, onError: (error) {
      _reportAsrStatus(
        'stream_error',
        listening: _asrService.isListening,
        error: error.toString(),
      );
    });

    _prepareUpcomingPipeline(reason: 'voice-init', includeAsrWarmup: true);
  }

  String _mergeStreamingDraft(String current, String incoming) {
    if (current.isEmpty) return incoming;
    if (incoming.startsWith(current)) return incoming;
    if (current.startsWith(incoming)) return current;
    if (current.endsWith(incoming)) return current;
    return '$current $incoming';
  }

  // VIBEVOICE 风格二阶段：流式草稿 + 发言结束后纠正。
  String _polishTranscript(String raw) {
    var text = raw.trim();
    if (text.isEmpty) return '';

    text = text.replaceAll(RegExp(r'\s+'), ' ');
    text = text.replaceAll(RegExp(r'([，。！？；,.!?;])\1+'), r'$1');
    text = text.replaceAll(RegExp(r'(嗯|呃|啊|那个|就是)(\s*\1)+'), r'$1');

    final parts = text.split(RegExp(r'[，。！？；,.!?;]+'));
    final dedup = <String>[];
    String prev = '';
    for (final p in parts) {
      final v = p.trim();
      if (v.isEmpty || v == prev) continue;
      dedup.add(v);
      prev = v;
    }

    text = dedup.join('，').trim();
    if (text.isEmpty) return '';
    if (!RegExp(r'[。！？!?]$').hasMatch(text)) {
      text = '$text。';
    }
    return text;
  }

  Future<String> _refineTranscript(String raw) async {
    final polished = _polishTranscript(raw);
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

  bool _hasOngoingSpeechPlayback() {
    return _ttsPlaying || _ttsService.isSpeaking || _isRecording;
  }

  bool _hasBlockingPlaybackForHumanTurn() {
    return _ttsPlaying || _ttsService.isSpeaking || _ttsQueue.isNotEmpty;
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
        await _ttsService.prefetchBatch(upcoming, maxConcurrent: 2);
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
    return settings?.micControlMode ?? 'double_ctrl';
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
  // 需求15/21：从讨论历史中提炼 5~8 句"金句"（简单启发式：挑选 12~60 字、
  // 含有表达性关键词、非系统消息的短句）
  List<String> _deriveGoldenQuotes() {
    if (_messages.isEmpty) return const [];
    final seen = <String>{};
    final quotes = <String>[];
    const stopPrefixes = ['请', '好的', '嗯', '哦', '谢谢', '我来', '那么', '让我'];
    for (final m in _messages.reversed) {
      if (m.type == 'system') continue;
      for (final raw in _splitIntoSentences(m.content)) {
        final s = _stripStageDirectionsForSpeech(raw.trim());
        if (s.length < 10 || s.length > 60) continue;
        if (stopPrefixes.any(s.startsWith)) continue;
        if (seen.contains(s)) continue;
        seen.add(s);
        quotes.add(s);
        if (quotes.length >= 8) return quotes;
      }
    }
    return quotes.take(8).toList();
  }

  // 需求六：实时金句提炼。对新到的消息做快速筛选，挑出 10~20 字、
  // 去掉表情/括号备注后内容独立的短句，去重后追加到 _liveQuotes，
  // 上限 12 条，避免把整屏金句全塞出来。
  static const int _kMaxLiveQuotes = 12;
  void _extractAndAppendLiveQuotes(String content) {
    if (content.trim().isEmpty) return;
    const stopPrefixes = ['请', '好的', '嗯', '哦', '谢谢', '我来', '那么', '让我'];
    final seen = _liveQuotes.toSet();
    for (final raw in _splitIntoSentences(content)) {
      if (_liveQuotes.length >= _kMaxLiveQuotes) return;
      final s = _stripStageDirectionsForSpeech(raw.trim());
      if (s.length < 8 || s.length > 20) continue;
      if (stopPrefixes.any(s.startsWith)) continue;
      if (seen.contains(s)) continue;
      seen.add(s);
      _liveQuotes.add(s);
    }
  }

  List<String> _splitIntoSentences(String text) {
    if (text.isEmpty) return const [];
    final re = RegExp(r'[^。！？!?\n]+[。！？!?]?');
    return re.allMatches(text).map((m) => m.group(0) ?? '').toList();
  }

  String _subtitlePageCacheKey = '';
  List<String> _subtitlePages = const [];
  int _subtitlePageIndex = 0;
  Timer? _subtitlePageTimer;

  String get _displayedSubtitleMessage {
    if (_subtitlePages.isEmpty) return _centerMessage;
    final idx = _subtitlePageIndex.clamp(0, _subtitlePages.length - 1);
    return _subtitlePages[idx];
  }

  void _maybeAdvanceSubtitlePage({
    required String fullText,
    required double maxWidth,
    required double lineHeight,
  }) {
    final cacheKey = '$fullText|${maxWidth.toStringAsFixed(1)}';
    if (cacheKey == _subtitlePageCacheKey) return;
    _subtitlePageCacheKey = cacheKey;
    _subtitlePages = _paginateSubtitle(
      text: _centerMessage,
      maxWidth: maxWidth,
      lineHeight: lineHeight,
    );
    _subtitlePageIndex = 0;
    _subtitlePageTimer?.cancel();
    if (_subtitlePages.length > 1) {
      _subtitlePageTimer =
          Timer.periodic(const Duration(milliseconds: 2600), (t) {
        if (!mounted || _subtitlePages.length <= 1) {
          t.cancel();
          return;
        }
        setState(() {
          _subtitlePageIndex = (_subtitlePageIndex + 1) % _subtitlePages.length;
        });
      });
    }
  }

  List<String> _paginateSubtitle({
    required String text,
    required double maxWidth,
    required double lineHeight,
    int linesPerPage = 3,
  }) {
    if (text.trim().isEmpty) return const [];
    final style = TextStyle(fontSize: 20, height: lineHeight);
    // 估算一行能容纳多少字符，按句/标点切分
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: maxWidth);
    final lineMetrics = tp.computeLineMetrics();
    if (lineMetrics.length <= linesPerPage) return [text];
    // 按可见字符位置切
    final pages = <String>[];
    int cursor = 0;
    while (cursor < text.length) {
      final sub = text.substring(cursor);
      final pageTp = TextPainter(
        text: TextSpan(text: sub, style: style),
        textDirection: TextDirection.ltr,
        maxLines: linesPerPage,
        ellipsis: null,
      )..layout(maxWidth: maxWidth);
      final endOffset =
          pageTp.getPositionForOffset(Offset(maxWidth, pageTp.height - 1));
      var take = endOffset.offset.clamp(1, sub.length);
      // 向回寻找更自然的断点（标点/空格）
      if (take < sub.length) {
        const punct = '，。；！？,.?!;、 ';
        for (int i = take; i > max(0, take - 12); i--) {
          if (punct.contains(sub[i - 1])) {
            take = i;
            break;
          }
        }
      }
      pages.add(sub.substring(0, take).trim());
      cursor += take;
    }
    return pages.where((p) => p.isNotEmpty).toList(growable: false);
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

  void _activateHumanTurnNow({String speaker = ''}) {
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _cancelPendingHumanTurnGuard();
    final activateSpeaker = speaker.isEmpty ? widget.humanName : speaker;
    _commander.markHumanTurnActivated(speaker: activateSpeaker);
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
    _humanTurnActivatedAt = DateTime.now();
    setState(() {
      _isMyTurn = true;
      // 需求4：新一轮/举手批准，解锁麦克风
      _micLocked = false;
      _isThinking = false;
      _thinkingController.stop();
      _sttPartialText = '';
      _clearSubtitleBeforeSpeakerSwitch(
          speaker.isEmpty ? widget.humanName : speaker);
      // 需求20：暂停时不提示"请按住麦克风讲话"
      _statusText = _isPaused ? '暂停中...' : '轮到你了：请按住麦克风讲话';
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
  }

  void _deferHumanTurn({String speaker = ''}) {
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _commander.onHumanInputRequested(
      speaker: speaker,
      hasOngoingSpeechPlayback: true,
    );
    setState(() {
      _statusText = '等待上一位发言播放完成，即将轮到你...';
      _isMyTurn = false;
    });
    _schedulePendingHumanTurnGuard();
    _prepareUpcomingPipeline(
        reason: 'defer-human-turn', includeAsrWarmup: true);
  }

  Duration _remainingAutoSkipGuard() {
    final started = _humanTurnActivatedAt;
    if (started == null) return Duration.zero;
    final elapsed = DateTime.now().difference(started);
    if (elapsed >= _minHumanTurnAutoSkipWindow) return Duration.zero;
    return _minHumanTurnAutoSkipWindow - elapsed;
  }

  bool _scheduleAutoSkipWhenGuardReady({
    required String reason,
    bool addUserMessage = true,
  }) {
    final remain = _remainingAutoSkipGuard();
    if (remain <= Duration.zero) {
      return false;
    }
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = Timer(remain, () {
      if (!mounted) return;
      if (_isMyTurn && !_isRecording) {
        _onSkipTurn(
          reason: '$reason（保底等待后执行）',
          addUserMessage: addUserMessage,
          isAuto: false,
        );
      }
    });
    _showStatusToast('已进入保底发言窗口，${remain.inSeconds}秒后才会自动跳过');
    return true;
  }

  void _schedulePendingHumanTurnGuard(
      {Duration delay = const Duration(seconds: 5)}) {
    _pendingHumanTurnGuardTimer?.cancel();
    _pendingHumanTurnGuardTimer = Timer(delay, () {
      if (!mounted) return;

      // Already in user's turn or actively recording — no recovery needed
      if (_isMyTurn || _isRecording) {
        _cancelPendingHumanTurnGuard();
        return;
      }

      if (_commander.shouldForceActivatePending(
        hasOngoingSpeechPlayback: _hasBlockingPlaybackForHumanTurn(),
      )) {
        final speaker = _pendingHumanSpeaker.isEmpty
            ? widget.humanName
            : _pendingHumanSpeaker;
        _pushPhaseTelemetry(
          source: 'frontend',
          phase: 'human_speaking',
          reason: 'pending_human_turn_guard_force_activate',
          recovery: true,
          speaker: speaker,
        );
        _activateHumanTurnNow(speaker: speaker);
        return;
      }

      if (_pendingHumanTurn) {
        _schedulePendingHumanTurnGuard(delay: const Duration(seconds: 2));
      }
    });
  }

  void _cancelPendingHumanTurnGuard() {
    _pendingHumanTurnGuardTimer?.cancel();
    _pendingHumanTurnGuardTimer = null;
  }

  void _scheduleTtsPumpGuard(
      {Duration delay = const Duration(milliseconds: 900)}) {
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
    if (_hasBlockingPlaybackForHumanTurn()) return;
    _activateHumanTurnNow(speaker: _pendingHumanSpeaker);
  }

  String _friendlyError(String raw) {
    final s = raw.trim();
    // 空消息或默认"未知错误" → 静默忽略，不打扰用户
    if (s.isEmpty || s == '未知错误') {
      return '';
    }
    // ASGI 内部消息/连接关闭后的发送尝试 → 静默忽略
    if (s.contains("Unexpected ASGI message") || s.contains("websocket.send")) {
      return '';
    }
    // WebSocket 解析失败（前端产生的包装错误） → 静默忽略
    if (s.contains('WebSocket 事件解析失败')) {
      return '';
    }
    // 讨论流程中的队列/输入通道问题 → 友好提示
    if (s.contains('输入队列不存在') || s.contains('Failed to get user input')) {
      return '用户输入通道暂时不可用，系统已自动跳过本轮并继续讨论。';
    }
    if (s.contains('timeout') || s.contains('超时')) {
      return '等待输入超时，系统已自动进入下一位发言。';
    }
    // 讨论出现异常 → 简洁提示（避免展示长堆栈）
    if (s.contains('讨论出现异常')) {
      return '讨论过程出现短暂异常，系统正在自动恢复。';
    }
    // 流程停滞恢复通知 → 忽略（不是真正错误）
    if (s.contains('自动恢复调度') || s.contains('自动跳过并继续')) {
      return '';
    }
    return s;
  }

  Future<void> _startDiscussion() async {
    setState(() => _statusText = '创建讨论会话...');

    try {
      final apiClient = ref.read(apiClientProvider);

      // 创建会话
      final sessionData = await apiClient.createSession(
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        thinkerIds: widget.thinkerIds,
        humanNames: [widget.humanName],
        freeTopic: widget.topic.id == 'free_topic' ? widget.topic.title : '',
      );

      final sessionId = sessionData['session_id'] as String;
      final wsUrl = apiClient.getWebSocketUrl(sessionId);

      // 连接 WebSocket
      await _wsClient.connect(
        wsUrl: wsUrl,
        sessionId: sessionId,
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        thinkerIds: widget.thinkerIds,
        humanNames: [widget.humanName],
        observerMode: widget.observerMode,
      );

      setState(() => _statusText = '已连接');

      // 连接后立即预填参与者，确保主持人(老师)和所有角色从一开始就显示在圆桌上
      _prePopulateParticipants();

      // 监听事件
      _wsClient.events.listen(_handleEvent);
    } catch (e) {
      setState(() => _statusText = '连接失败: $e');
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
  };

  /// 连接成功后立即预填参与者，确保老师和所有角色出现在圆桌上
  void _prePopulateParticipants() {
    // 始终包含老师（即使 characterIds 里没有 moderator，也强制加入）
    final names = <String>{'李老师'};

    // 所有选中的角色
    for (final id in widget.characterIds) {
      final name = _charIdToName[id] ?? id;
      if (name != '李老师') names.add(name); // 避免重复
    }

    // 思想家直接使用 ID（后端会用 display_name，与 thinker yaml 里的 name 一致）
    for (final id in widget.thinkerIds) {
      names.add(id);
    }

    // 人类参与者
    names.add(widget.humanName);

    _knownParticipants = names.toList();
    _buildParticipants();
  }

  void _buildParticipants() {
    // 构建参与者列表：AI 角色 + 思想家 + 人类
    final names = <String>{};
    final avatars = <String, String>{};

    // 从系统事件的参与者列表中获取
    for (final name in _knownParticipants) {
      names.add(name);
    }

    // 从已有消息中补充参与者
    for (final msg in _messages) {
      if (msg.type != 'system') {
        names.add(msg.source);
      }
    }

    // 从当前发言者中添加
    if (_currentSpeaker.isNotEmpty) {
      names.add(_currentSpeaker);
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
    };

    /// DiceBear avatar URL for a given seed name
    String diceBearUrl(String seed, {String style = 'notionists-neutral'}) {
      final encoded = Uri.encodeComponent(seed);
      return 'https://api.dicebear.com/7.x/$style/png?seed=$encoded&size=128&backgroundColor=b6e3f4,c0aede,d1d4f9,ffd5dc';
    }

    // 为所有参与者分配头像并建立音色映射
    int thinkerVoiceIndex = 0;
    for (final name in names) {
      if (name == widget.humanName) {
        avatars[name] = diceBearUrl(name, style: 'personas');
        // 人类不需要 TTS 音色
      } else {
        // AI 角色使用 character-specific DiceBear styles (d)
        final style = charDiceBearStyle[name] ?? 'notionists-neutral';
        avatars[name] = diceBearUrl(name, style: style);
        if (templateAvatarMap.containsKey(name)) {
          _voiceMap.putIfAbsent(
              name, () => _nameToVoice[name] ?? 'zh-CN-XiaoxiaoNeural');
        } else {
          // 思想家 - 轮转分配, alternate male/female voices (g)
          _voiceMap.putIfAbsent(name, () {
            final allVoices = [..._maleThinkerVoices, ..._femaleThinkerVoices];
            final voice = allVoices[thinkerVoiceIndex % allVoices.length];
            return voice;
          });
          thinkerVoiceIndex++;
        }
      }
    }

    setState(() {
      _participants = names.map((name) {
        final isHuman = name == widget.humanName;
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

  void _handleEvent(WsEvent event) {
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
          final source = data['source'] ?? '未知';
          final content = data['content'] ?? '';
          final msgType = data['msg_type'] ?? 'text';
          final shouldSpeak = msgType != 'system' && source != widget.humanName;

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
                  (!_isMyTurn || source == widget.humanName)) {
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

            // 需求六：金句在讨论过程中实时生成，像打字机一样逐句弹出。
            // 对非系统消息内容做启发式提炼（与收尾一致），直接追加到侧边栏。
            if (msgType != 'system' && source != '系统') {
              _extractAndAppendLiveQuotes(content);
            }
          });

          // 如果是 AI 角色/主持人消息，排队 TTS 朗读（顺序播放，i）
          if (shouldSpeak) {
            final voice = _voiceMap[source];
            _enqueueTts(source: source, text: content, voice: voice);
          }

          _buildParticipants();
        }
        break;
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;
          final sessionMatched = _isRealtimeSessionMatched(
              data: data, triggerRole: speaker, eventType: 'turn_change');

          if (!sessionMatched) {
            setState(() {
              _statusText = '$speaker 排队发言中...';
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
              speaker == widget.humanName &&
              _hasBlockingPlaybackForHumanTurn()) {
            _deferHumanTurn(speaker: speaker);
            _buildParticipants();
            return;
          }

          // 严格语音/字幕同步：若上一位仍在播报或队列未清空，不提前切换到下一位 AI 的视觉状态。
          if (!isHuman &&
              (_hasOngoingSpeechPlayback() || _ttsQueue.isNotEmpty)) {
            setState(() {
              _statusText = '$speaker 排队发言中...';
              _isThinking = true;
              _thinkingController.repeat();
            });
            _prepareUpcomingPipeline(
              reason: 'turn-change-queued:$speaker',
              includeAsrWarmup: true,
            );
            return;
          }

          _cancelTurnCountdown();
          _cancelMaxSpeechTimer();
          setState(() {
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == widget.humanName;
            _hasRaisedHand = false;
            _sttPartialText = '';
            if (_isMyTurn) {
              // 需求4：新一轮轮到我，解锁麦克风
              _micLocked = false;
              _isThinking = false;
              _thinkingController.stop();
              // 需求20：暂停时不提示"请按住麦克风讲话"
              _statusText = _isPaused ? '暂停中...' : '轮到你了：请按住麦克风讲话';
              if (!_isPaused) {
                _glowController.repeat(reverse: true);
                _keyboardFocusNode.requestFocus();
                _startTurnCountdown();
              }
            } else if (isHuman) {
              _isThinking = false;
              _thinkingController.stop();
              _statusText = '$speaker 正在发言...';
              _glowController.stop();
            } else {
              _isThinking = true;
              _thinkingController.repeat();
              _statusText = '$speaker 正在思考...';
              _glowController.stop();
            }
          });
          _prepareUpcomingPipeline(
            reason: 'turn-change:$speaker',
            includeAsrWarmup: !isHuman,
          );
          _buildParticipants();
          // 确保 TTS 队列在角色切换后继续播放（修复跳过后无声音 bug）
          if (!_ttsPlaying && _ttsQueue.isNotEmpty) {
            Future.delayed(const Duration(milliseconds: 300), () {
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
          final source = (data['source'] ?? '').toString();
          final content = (data['content'] ?? '').toString();
          // 语音与字幕同步：AI 流式文本不提前渲染，统一在 TTS 开始时显示。
          if (source != widget.humanName) {
            _isRealtimeSessionMatched(
              data: data,
              triggerRole: source,
              eventType: 'stream',
            );
            _debugSubtitleLog(
                triggerRole: source, note: 'stream blocked for non-human');
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
          if (newState == 'interrupted') {
            setState(() => _statusText = '有人请求打断...');
          } else if (newState == 'human_turn_waiting' && _isPaused) {
            // 需求20：暂停状态下不显示"轮到你了"之类的提示
          } else {
            setState(() => _statusText = '状态：$newLabel');
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
            _knownParticipants = participants.cast<String>();
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
        // If already in user's turn or actively recording, ignore duplicate requests
        if (_isMyTurn || _isRecording) {
          break;
        }
        final command = _commander.onHumanInputRequested(
          speaker: widget.humanName,
          hasOngoingSpeechPlayback: _hasBlockingPlaybackForHumanTurn(),
        );
        if (command == HumanTurnCommand.defer) {
          _deferHumanTurn(speaker: widget.humanName);
        } else if (command == HumanTurnCommand.activateNow) {
          _activateHumanTurnNow(speaker: widget.humanName);
        }
        break;
      case WsEventType.apiError:
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? 'AI 服务错误';
        final friendly = _friendlyError(errMsg.toString());
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
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? '未知错误';
        final friendly = _friendlyError(errMsg.toString());
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
        final endedWithError = _lastErrorMessage != null;
        // 需求21：讨论结束后生成金句并切换到纯金句画面
        final summaryQuotes = _deriveGoldenQuotes();
        setState(() {
          _statusText =
              endedWithError ? '会话已中断: ${_lastErrorMessage!}' : '讨论已结束';
          _isMyTurn = false;
          _glowController.stop();
          if (summaryQuotes.isNotEmpty) {
            _liveQuotes
              ..clear()
              ..addAll(summaryQuotes);
          }
          _discussionEnded = true;
        });
        break;
      case WsEventType.interrupt:
        final data = event.data;
        if (data != null) {
          final interrupter = data['interrupter'] ?? '';
          final approvedBy = (data['approved_by'] ?? '李老师').toString();
          final approved = data['approved'] == true;
          final mineApproved = approved && interrupter == widget.humanName;
          final command = _commander.onInterruptApprovedForHuman(
            mineApproved: mineApproved,
            speaker: widget.humanName,
            hasOngoingSpeechPlayback: _hasBlockingPlaybackForHumanTurn(),
          );
          setState(() {
            _messages.add(ChatMessage(
              source: approvedBy,
              content: '$interrupter 同学，请先发言。',
              type: 'system',
            ));
            if (mineApproved && command == HumanTurnCommand.defer) {
              _statusText = '李老师已同意你先发言，请准备讲话';
            }
          });
          if (mineApproved && command == HumanTurnCommand.activateNow) {
            _activateHumanTurnNow(speaker: widget.humanName);
          } else if (mineApproved && command == HumanTurnCommand.defer) {
            _schedulePendingHumanTurnGuard();
          }
          _buildParticipants();
        }
        break;
    }
  }

  // ── 文本输入已完全移除：本会话为纯语音模式 ─────────────────────────────────

  // ── Push-to-Talk ────────────────────────────────────────────────────────────

  void _onPttStart() {
    if (!(_isMyTurn || _handApprovedToSpeak || _pendingHumanTurn)) {
      _showStatusToast('当前还未轮到你发言');
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
        _statusText = '当前语音识别服务不可用，请在设置中切换可用 ASR';
      });
      _showStatusToast('语音识别服务不可用，请先在设置页完成可用性测试');
      return;
    }
    _cancelTurnCountdown();
    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = null;
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;

    // 需求2：纯语音模式，不存在文字输入

    setState(() {
      _isRecording = true;
      _centerSpeaker = widget.humanName;
      _centerMessage = '';
      _sttPartialText = '';
      _lastNonEmptySttText = '';
      // 需求四：用户按下 Ctrl/点击麦克风启动后，立即撤掉
      // "轮到你了，按住麦克风讲话"的残留提示，改成"正在聆听..."。
      _statusText = '正在聆听...';
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
        setState(() {
          _isRecording = false;
          _statusText = '语音识别未成功启动，请重试录音';
        });
        _showStatusToast('语音识别启动失败，请重试录音');
      }
    }());
    _asrListenStartAt = DateTime.now();
    _awaitingAsrFirstPacket = true;
    _speechStartTime = DateTime.now();
    _startMaxSpeechTimer();
  }

  void _onPttEnd() {
    if (!_isRecording) return;
    if (_isFinalizingSpeech) return; // 防止并发 finalize
    _isFinalizingSpeech = true;
    _cancelMaxSpeechTimer();
    setState(() {
      _isRecording = false;
      // 需求5：录音结束、字幕还没出来时，立刻清除"请按住麦克风讲话"的提示
      if (_statusText.contains('请按住麦克风讲话') || _statusText.contains('轮到你了')) {
        _statusText = '识别中...';
      }
    });
    _micController.stop();
    _micController.reset();
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _reportAsrStatus('stop_requested', listening: _asrService.isListening);
    try {
      _asrService.stopListening();
    } catch (e) {
      if (kDebugMode) debugPrint('[ASR] stopListening error: $e');
    }
    _reportAsrStatus('stop_called', listening: _asrService.isListening);
    _awaitingAsrFirstPacket = false;

    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = Timer(const Duration(milliseconds: 800), () async {
      try {
        await _finalizeSpeechWithRetry();
      } finally {
        if (mounted) {
          _isFinalizingSpeech = false;
        }
      }
    });
  }

  Future<void> _finalizeSpeechWithRetry() async {
    if (!mounted) return;
    String rawText = (_sttPartialText.trim().isNotEmpty
            ? _sttPartialText.trim()
            : _lastNonEmptySttText.trim())
        .trim();

    // 给 ASR 最终包更长等待窗口，避免”录完即判空”导致误跳过。
    final deadline = DateTime.now().add(const Duration(seconds: 8));
    while (rawText.isEmpty && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 350));
      if (!mounted) return;
      rawText = (_sttPartialText.trim().isNotEmpty
              ? _sttPartialText.trim()
              : _lastNonEmptySttText.trim())
          .trim();
    }

    final refined = rawText.isNotEmpty ? await _refineTranscript(rawText) : '';
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
        _isMyTurn = true;
        _statusText = '未识别到语音，请重新录音';
      });
      _showStatusToast('未识别到有效语音，请重试录音');
      _startTurnCountdown();
      return;
    }
    final submitText = refined.trim().isNotEmpty ? refined.trim() : rawText;
    _reportAsrStatus(
      'submitted',
      listening: _asrService.isListening,
      textLen: submitText.length,
      isFinal: true,
    );

    _wsClient.sendHumanInput(speaker: widget.humanName, content: submitText);
    setState(() {
      // 不直接添加到 _messages，避免后端 message 事件重复添加
      // 后端收到 human_input 后会发送 message 事件，前端 _handleEvent 中会添加
      _isMyTurn = false;
      // 需求4：发言完毕，锁定麦克风为灰色，直到下轮或举手批准
      _micLocked = true;
      _statusText = '等待其他人发言...';
      _centerMessage = submitText;
      _centerSpeaker = widget.humanName;
    });
    _commander.markHumanTurnCompleted();
    _cancelPendingHumanTurnGuard();

    // 字幕保留
    _startSubtitleRetain(submitText);
    _holdAiUntilHumanSubtitleDone(submitText);
  }

  // ── 跳过本轮发言 ────────────────────────────────────────────────────────────

  void _onSkipTurn({
    String? reason,
    bool addUserMessage = true,
    bool isAuto = false,
  }) {
    if (isAuto) {
      final blocked = _scheduleAutoSkipWhenGuardReady(
        reason: reason ?? '自动跳过',
        addUserMessage: addUserMessage,
      );
      if (blocked) {
        return;
      }
    }
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _deferredAutoSkipTimer?.cancel();
    _deferredAutoSkipTimer = null;
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
      _statusText = reason ?? '等待其他人发言...';
    });
    if (reason != null && reason.isNotEmpty) {
      _showStatusToast(reason);
    }
    _humanTurnActivatedAt = null;
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
      if (_isRecording || _sttPartialText.trim().isNotEmpty) {
        return;
      }
      setState(() => _turnCountdown--);
      if (_turnCountdown <= 0) {
        timer.cancel();
        _onSkipTurn(reason: '30秒未开始语音发言，已自动跳过', isAuto: true);
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
        _onPttEnd();
        setState(() {
          _statusText = '发言超过3分钟，已自动提交';
        });
        _showStatusToast('发言超过3分钟，已自动提交');
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
    final chars = text.trim().runes.length;
    final holdMs = (chars * 45).clamp(1000, 3000).toInt();
    _humanSubtitleLockUntil =
        DateTime.now().add(Duration(milliseconds: holdMs));
    _humanSubtitleLockTimer?.cancel();
    _humanSubtitleLockTimer = Timer(Duration(milliseconds: holdMs), () {
      if (!mounted) return;
      if (!_isMyTurn && !_isRecording && !_ttsPlaying && _ttsQueue.isNotEmpty) {
        _playNextTts();
      }
    });
  }

  // ── TTS 顺序播放队列 (i) ────────────────────────────────────────────────────

  /// Add text to the TTS queue and start playback if not already playing.
  void _enqueueTts(
      {required String source, required String text, String? voice}) {
    // 需求8：暂停期间直接丢弃所有 TTS 请求，恢复后从新消息开始播放
    if (_isPaused) return;
    final speechText = _stripStageDirectionsForSpeech(text);
    if (speechText.isEmpty) return;
    final playbackSessionId = _nextTtsPlaybackSessionId();
    _ttsQueue.add((
      source: source,
      text: speechText,
      voice: voice,
      playbackSessionId: playbackSessionId,
      enqueuedAt: DateTime.now(),
    ));
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

  /// Simple emotion-based speech rate (h): calm/sad → slower, excited/angry → faster.
  double _emotionSpeed(String text) {
    const excitedWords = [
      '太棒了',
      '好极了',
      '厉害',
      '激动',
      '加油',
      '哇',
      '真的吗',
      '不可思议',
      '冲啊'
    ];
    const calmWords = ['慢慢来', '想一想', '仔细', '平静', '安静', '沉思', '也许', '或许', '思考'];
    int exciteCount = 0;
    int calmCount = 0;
    for (final w in excitedWords) {
      if (text.contains(w)) exciteCount++;
    }
    for (final w in calmWords) {
      if (text.contains(w)) calmCount++;
    }
    if (exciteCount > calmCount) return 1.1;
    if (calmCount > exciteCount) return 0.85;
    return 1.0;
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
        final waitMs = lockUntil.difference(DateTime.now()).inMilliseconds;
        Future.delayed(Duration(milliseconds: waitMs.clamp(80, 3000)), () {
          if (mounted && !_ttsPlaying && _ttsQueue.isNotEmpty && !_isMyTurn) {
            _playNextTts();
          }
        });
        return;
      }
      _ttsPlaying = true;
      final item = _ttsQueue.removeAt(0);
      _activeTtsItem = item;
      _activeTtsSessionId = item.playbackSessionId;
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

      // 字幕与语音严格同步：TTS 播放时强制显示对应发言者的字幕。
      if (mounted) {
        setState(() {
          _currentSpeaker = item.source;
          _centerSpeaker = item.source;
          _centerMessage = item.text;
          _isThinking = false;
          _thinkingController.stop();
        });
        _debugSubtitleLog(triggerRole: item.source, note: 'tts render start');
        _buildParticipants();
      }

      final speed = _emotionSpeed(item.text);
      bool played = false;
      try {
        // 超时保护：按文字长度动态计算，每个字约0.3秒，最少20秒，最多120秒
        final timeoutSec = (item.text.length * 0.3).clamp(20, 120).toInt();
        Object? lastErr;
        for (var attempt = 0; attempt < 2 && !played; attempt++) {
          try {
            await _ttsService
                .speak(item.text, voice: item.voice, rate: speed)
                .timeout(Duration(seconds: timeoutSec));
            played = true;
          } catch (e) {
            lastErr = e;
            if (attempt == 0) {
              await Future<void>.delayed(const Duration(milliseconds: 200));
            }
          }
        }
        if (!played && lastErr != null) {
          final fallback = _browserFallbackTts;
          if (fallback != null) {
            await fallback
                .speak(item.text, voice: item.voice, rate: speed)
                .timeout(Duration(seconds: timeoutSec));
            played = true;
            if (mounted) {
              _showStatusToast('主 TTS 异常，已切换浏览器语音兜底');
            }
          } else {
            throw lastErr;
          }
        }
      } catch (e) {
        // TTS 失败时暂停队列并保留当前文本，避免“字幕闪过+语音缺失”。
        _ttsQueue.insert(0, item);
        _activeTtsItem = null;
        _activeTtsSessionId = '';
        _ttsPlaying = false;
        if (mounted) {
          setState(() {
            _isPaused = true;
            _statusText = '语音播报失败，已暂停自动轮播，请检查 TTS 配置后继续';
            _messages.add(ChatMessage(
              source: '系统',
              content: '${item.source} 语音播放异常，已暂停等待修复后重试',
              type: 'system',
            ));
          });
          _showStatusToast('语音播报失败：$e', isError: true);
        }
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
      // After speak() resolves, record current prefetch hit rate and play next.
      _pushSeriesSample(_prefetchHitRateSeries, _prefetchHitRatePercent);
      if (!mounted) return;
      // 确保 TTS 完全停止后再播放下一条
      await Future.delayed(const Duration(milliseconds: 300));
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
    } finally {
      _ttsPumpRunning = false;
      final shouldContinue = mounted &&
          !_isPaused &&
          !_ttsPlaying &&
          _ttsQueue.isNotEmpty &&
          !_isMyTurn &&
          !_isRecording;
      if (shouldContinue) {
        _scheduleTtsPumpGuard(delay: const Duration(milliseconds: 600));
        Future.microtask(_playNextTts);
      }
    }
  }

  // ── 暂停 / 继续 ─────────────────────────────────────────────────────────────

  void _onTogglePause() {
    if (_isPaused) {
      setState(() {
        _isPaused = false;
        _statusText = '继续讨论...';
      });
      // 需求8：恢复前强制清空残留队列和活动项，避免“许多语料”被集中重放
      _ttsQueue.clear();
      _activeTtsItem = null;
      _activeTtsSessionId = '';
      _ttsPlaying = false;
      _wsClient.sendResume();
    } else {
      setState(() {
        _isPaused = true;
        _statusText = '已暂停（已冻结语音与轮次）';
        _isThinking = false;
      });
      _commander.markHumanTurnCompleted();
      _cancelPendingHumanTurnGuard();
      _cancelTurnCountdown();
      _cancelMaxSpeechTimer();
      _speechFinalizeTimer?.cancel();
      if (_isRecording) {
        _asrService.stopListening();
        _isRecording = false;
      }
      // 清空 TTS 队列，停止当前播放，避免暂停后继续播放
      _ttsQueue.clear();
      _activeTtsItem = null;
      _activeTtsSessionId = '';
      _ttsPlaying = false;
      _ttsService.stop();
      _wsClient.sendInterrupt(speaker: widget.humanName);
      _wsClient.sendPause();
    }
  }

  // ── 打断 ────────────────────────────────────────────────────────────────────

  void _onInterrupt() {
    if (_hasRaisedHand) return;
    setState(() => _hasRaisedHand = true);
    _wsClient.sendInterrupt(speaker: widget.humanName);
    _showStatusToast('已举手，等待主持人分配发言');
  }

  bool get _isPushToTalk {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.pushToTalk ?? true;
  }

  void _onKeyEvent(KeyEvent event) {
    // 使用 HardwareKeyboard 全局处理 Ctrl 连击，避免重复触发。
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
    _deferredAutoSkipTimer?.cancel();
    _statusToastTimer?.cancel();
    _statusToastEntry?.remove();
    _ctrlHeld = false;
    _awaitingAsrFirstPacket = false;
    HardwareKeyboard.instance.removeHandler(_onHardwareKey);
    _ttsService.dispose();
    _browserFallbackTts?.dispose();
    _asrService.dispose();
    _wsClient.dispose();
    _scrollController.dispose();
    _keyboardFocusNode.dispose();
    _candleController.dispose();
    _glowController.dispose();
    _micController.dispose();
    _thinkingController.dispose();
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

  double _averageOf(List<double> values) {
    if (values.isEmpty) return 0;
    var total = 0.0;
    for (final v in values) {
      total += v;
    }
    return total / values.length;
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

    final startupBase = _averageOf(head(_ttsStartupSeries));
    final startupNow = _averageOf(tail(_ttsStartupSeries));
    final asrBase = _averageOf(head(_asrFirstPacketSeries));
    final asrNow = _averageOf(tail(_asrFirstPacketSeries));
    final hitBase = _averageOf(head(_prefetchHitRateSeries));
    final hitNow = _averageOf(tail(_prefetchHitRateSeries));

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
    String thinkerVoice = 'zh-CN-YunzeNeural';
    for (final v in _voiceMap.values) {
      if (v.contains('Yunze') || v.contains('Yunxi') || v.contains('Yunjian')) {
        thinkerVoice = v;
        break;
      }
    }
    try {
      for (final raw in quotes) {
        if (!_quotesReadAloudActive) break;
        final line = _stripStageDirectionsForSpeech(raw).trim();
        if (line.isEmpty) continue;
        try {
          await _ttsService.speak(line, voice: thinkerVoice, rate: 1.0);
        } catch (_) {
          try {
            await _browserFallbackTts?.speak(line,
                voice: thinkerVoice, rate: 1.0);
          } catch (_) {}
        }
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

  @override
  Widget build(BuildContext context) {
    // 需求21：讨论结束后清空桌面元素，仅保留金句画面
    if (_discussionEnded) {
      return _EndingQuotesScreen(
        quotes: _liveQuotes,
        onExit: () {
          if (Navigator.of(context).canPop()) {
            Navigator.of(context).pop();
          }
        },
        // 需求九：朗读由"思想家角色"完成，句间有小停顿。
        onReadAloud: _readAloudQuotes,
        onStopReadAloud: _stopReadAloudQuotes,
      );
    }
    final size = MediaQuery.of(context).size;
    final tableRadius = min(size.width, size.height) * 0.18;
    // 需求五：字幕区宽度 +12%（0.70 -> 0.784）。
    final subtitleWidth = size.width * 0.784;

    // 需求15：圆桌整体向右移动 100px，左侧留出金句展示区
    final tableCenterX = size.width / 2 + 100;
    final tableCenterY = size.height / 2;
    // 左侧金句区宽度（从屏幕最左到圆桌左边缘减一点间距）
    final quoteAreaRight = tableCenterX - tableRadius - 60;
    final quoteAreaWidth = max(0.0, quoteAreaRight - 24);

    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 20,
    );

    final canInterrupt = !_isMyTurn &&
        _currentSpeaker.isNotEmpty &&
        !_hasRaisedHand &&
        !_handApprovedToSpeak;
    // canSpeakNow：仅当真正轮到用户或已批准发言时才允许操作
    // 排除 _pendingHumanTurn，避免"human_input_requested"发出后、TTS 未停时提前允许录音
    // 需求4：发言完毕后 _micLocked=true，麦克风立即灰化，需下轮或举手批准才重新可用
    final canSpeakNow =
        (_isMyTurn || _handApprovedToSpeak || _isRecording) && !_micLocked;
    // 按钮始终显示，但通过 opacity 和 IgnorePointer 控制是否可用
    // 用户回合：麦克风可用，跳过可用
    // 非用户回合但有发言者：举手可用
    // 讨论已结束则置灰不可交互
    final showActionButtons = !_discussionEnded;
    final subtitleLineCount = _estimateSubtitleLineCount(
      context,
      '$_centerSpeaker：$_centerMessage',
      maxWidth: subtitleWidth - 80,
      style: const TextStyle(fontSize: 20, height: 1.9),
      maxLines: 3,
    );
    // 需求10：第一行字幕区域高度降低 30px；两行时整体再向下 10px 以拉大行距
    final subtitleBottom = subtitleLineCount <= 1 ? 56.0 : 60.0;
    // 需求10：两行行距 +10px（通过 TextStyle.height 增加）
    final subtitleLineHeight = subtitleLineCount <= 1 ? 1.7 : 1.9;
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
        body: Stack(
          children: [
            // ── 书房背景 ──
            CustomPaint(
              size: size,
              painter: BookshelfPainter(lightIntensity: _isMyTurn ? 1.0 : 0.6),
            ),

            // ── 需求15：左侧金句区（暗色虚线分隔）──
            if (quoteAreaWidth > 80)
              Positioned(
                left: 24,
                top: 80,
                width: quoteAreaWidth,
                height: size.height - 160,
                child: _QuoteSidebar(
                  width: quoteAreaWidth,
                  quotes: _liveQuotes,
                ),
              ),

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

            // ── "请XX发言" 提示：轮到用户时显示在圆桌中央 ──
            if (_isMyTurn)
              Positioned(
                left: tableCenterX - tableRadius * 0.6,
                top: tableCenterY - 20,
                width: tableRadius * 1.2,
                child: Center(
                  child: AnimatedBuilder(
                    animation: _glowController,
                    builder: (context, _) {
                      final pulse = 0.7 + _glowController.value * 0.3;
                      return Text(
                        '请${widget.humanName}发言',
                        style: TextStyle(
                          color: AppColors.amberGold.withValues(alpha: pulse),
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                          shadows: const [
                            Shadow(color: Colors.black, blurRadius: 8),
                            Shadow(color: Colors.black, blurRadius: 16),
                          ],
                        ),
                        textAlign: TextAlign.center,
                      );
                    },
                  ),
                ),
              ),

            // ── 操作提示：圆桌下方 ──
            if (_isMyTurn && !_isRecording)
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
                    showMic: canSpeakNow,
                    isRecording: _isRecording,
                    onStart: _onPttStart,
                    onEnd: _onPttEnd,
                    showSkip: _isMyTurn && !_isRecording,
                    onSkip: () => _onSkipTurn(reason: '您已跳过本次发言'),
                    hasRaisedHand: _hasRaisedHand,
                    canInterrupt: canInterrupt,
                    onRaiseHand: _onInterrupt,
                    totalHeight: tableRadius * 2,
                  ),
                ),
              ),
            ),

            // ── 字幕区：直接印在圆桌下方，无黑色背景，仅靠文字描边阴影保证可读 ──
            if (_centerSpeaker.isNotEmpty && _centerMessage.isNotEmpty)
              Positioned(
                bottom: subtitleBottom + 8,
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
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                                height: subtitleLineHeight,
                                // 需求5：去掉黑色背景与大范围黑影，仅保留极细描边保持可读
                                shadows: [
                                  Shadow(
                                      color:
                                          Colors.black.withValues(alpha: 0.55),
                                      blurRadius: 2,
                                      offset: const Offset(0, 1)),
                                ],
                              ),
                            ),
                            TextSpan(
                              text: _displayedSubtitleMessage,
                              style: TextStyle(
                                color: Colors.white,
                                fontSize: 20,
                                height: subtitleLineHeight,
                                shadows: [
                                  Shadow(
                                      color:
                                          Colors.black.withValues(alpha: 0.55),
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
                            const BoxConstraints(minWidth: 36, minHeight: 36),
                      ),
                      IconButton(
                        icon: Icon(
                          _showPhasePanel ? Icons.route : Icons.route_outlined,
                          color: _showPhasePanel
                              ? const Color(0xFF90CAF9)
                              : AppColors.warmGray,
                        ),
                        onPressed: () {
                          setState(() => _showPhasePanel = !_showPhasePanel);
                        },
                        padding: EdgeInsets.zero,
                        constraints:
                            const BoxConstraints(minWidth: 36, minHeight: 36),
                      ),
                      const SizedBox(width: 2),
                      // ── 右上：历史 ──
                      IconButton(
                        icon: const Icon(Icons.history,
                            color: AppColors.warmGray),
                        onPressed: _showChatHistory,
                        padding: EdgeInsets.zero,
                        constraints:
                            const BoxConstraints(minWidth: 36, minHeight: 36),
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
  final VoidCallback onTap;

  const _TopRectButton({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 120,
        height: 48,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          color: Colors.white.withValues(alpha: 0.08),
          border: Border.all(color: Colors.white.withValues(alpha: 0.25)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: AppColors.warmWhite, size: 20),
            const SizedBox(width: 6),
            Text(
              label,
              style: const TextStyle(
                color: AppColors.warmWhite,
                fontSize: 14,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
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
    required this.totalHeight,
  });

  @override
  Widget build(BuildContext context) {
    // 3 buttons share totalHeight (= table diameter)
    final btnCount = 3;
    final spacing = 12.0;
    final btnHeight = ((totalHeight - spacing * (btnCount - 1)) / btnCount)
        .clamp(48.0, 120.0);
    return SizedBox(
      height: totalHeight,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        mainAxisSize: MainAxisSize.max,
        children: [
          _SpeakButton(
            isRecording: isRecording,
            enabled: showMic,
            onStart: onStart,
            onEnd: onEnd,
            size: btnHeight,
          ),
          _SkipButton(onTap: showSkip ? onSkip : null, size: btnHeight),
          _FloatingRaiseHandButton(
            hasRaisedHand: hasRaisedHand,
            canInterrupt: canInterrupt,
            onTap: onRaiseHand,
            size: btnHeight,
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

// ─── 需求15/需求六：左侧金句侧边栏（暗色虚线分隔）───────────────────────
/// 金句列表以 1、2、3 的序号形式展示，字体较小，最新一条淡入（打字机感）。
class _QuoteSidebar extends StatelessWidget {
  final double width;
  final List<String> quotes;
  const _QuoteSidebar({required this.width, required this.quotes});

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _DashedDividerPainter(),
      // 需求六：虚线区域（即右边竖虚线）相较之前向左移动 100px，
      // 通过 painter 里把 x 偏移 -100 来达到（见 painter），同时 padding 收窄。
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 12, 124, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.format_quote,
                    color: Color(0xFFE5B25D), size: 16),
                const SizedBox(width: 8),
                Text('金句回响',
                    style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.85),
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 2)),
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: quotes.isEmpty
                  ? Center(
                      child: Text(
                        '讨论中的精彩瞬间\n会在这里被记下…',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            color: Colors.white.withValues(alpha: 0.22),
                            fontSize: 11,
                            height: 1.8),
                      ),
                    )
                  : ListView.separated(
                      itemCount: quotes.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 8),
                      itemBuilder: (_, i) {
                        final isLatest = i == quotes.length - 1;
                        final child = Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SizedBox(
                              width: 20,
                              child: Text('${i + 1}.',
                                  style: TextStyle(
                                      color: const Color(0xFFE5B25D)
                                          .withValues(alpha: 0.65),
                                      fontSize: 11,
                                      fontWeight: FontWeight.w600,
                                      height: 1.6)),
                            ),
                            Expanded(
                              child: Text(
                                quotes[i],
                                style: TextStyle(
                                  color: Colors.white.withValues(alpha: 0.78),
                                  fontSize: 11,
                                  height: 1.6,
                                ),
                                softWrap: true,
                              ),
                            ),
                          ],
                        );
                        if (!isLatest) return child;
                        // 最新一条：淡入 + 轻微向上位移，形成"打字机弹出"感。
                        return TweenAnimationBuilder<double>(
                          key: ValueKey('quote-${quotes.length}'),
                          duration: const Duration(milliseconds: 420),
                          curve: Curves.easeOut,
                          tween: Tween(begin: 0.0, end: 1.0),
                          builder: (_, t, c) => Opacity(
                            opacity: t,
                            child: Transform.translate(
                              offset: Offset(0, (1 - t) * 6),
                              child: c,
                            ),
                          ),
                          child: child,
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DashedDividerPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.white.withValues(alpha: 0.12)
      ..strokeWidth = 1.0
      ..style = PaintingStyle.stroke;
    const dash = 6.0;
    const gap = 5.0;
    // 需求六：虚线向左移动 100px。
    final x = size.width - 100;
    if (x <= 0) return;
    double y = 0;
    while (y < size.height) {
      canvas.drawLine(Offset(x, y), Offset(x, y + dash), paint);
      y += dash + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _DashedDividerPainter oldDelegate) => false;
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
    final display = widget.quotes.isEmpty
        ? const ['今天的讨论已经落幕，但思辨的星光将一直在心里闪烁。']
        : widget.quotes;
    return Scaffold(
      backgroundColor: const Color(0xFF0A0E1A),
      body: SafeArea(
        child: Stack(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(56, 60, 56, 60),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      const Text(
                        '今日金句',
                        style: TextStyle(
                          color: Color(0xFFE5B25D),
                          fontSize: 28,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 12,
                        ),
                      ),
                      const Spacer(),
                      // 需求六/九：朗读开关，点击后由思想家朗读每一句。
                      TextButton.icon(
                        onPressed: _toggleRead,
                        style: TextButton.styleFrom(
                          foregroundColor: _reading
                              ? const Color(0xFFE5B25D)
                              : Colors.white70,
                          padding: const EdgeInsets.symmetric(
                              horizontal: 16, vertical: 10),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(24),
                            side: BorderSide(
                              color: (_reading
                                      ? const Color(0xFFE5B25D)
                                      : Colors.white24)
                                  .withValues(alpha: 0.6),
                            ),
                          ),
                        ),
                        icon: Icon(
                            _reading ? Icons.stop_circle : Icons.volume_up),
                        label: Text(_reading ? '停止朗读' : '思想家朗读'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Container(
                    height: 1,
                    color: Colors.white.withValues(alpha: 0.12),
                  ),
                  const SizedBox(height: 40),
                  Expanded(
                    child: ListView.separated(
                      itemCount: display.length,
                      separatorBuilder: (_, __) => const SizedBox(height: 28),
                      itemBuilder: (_, i) => Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('${i + 1}.',
                              style: TextStyle(
                                  color: Colors.white.withValues(alpha: 0.4),
                                  fontSize: 20)),
                          const SizedBox(width: 18),
                          Expanded(
                            child: Text(
                              '"${display[i]}"',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 22,
                                height: 1.9,
                                fontStyle: FontStyle.italic,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Positioned(
              right: 24,
              top: 24,
              child: IconButton(
                icon: const Icon(Icons.close, color: Colors.white70),
                onPressed: widget.onExit,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
