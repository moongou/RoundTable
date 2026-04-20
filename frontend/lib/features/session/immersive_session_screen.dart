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
import 'table_participant_ring.dart';

/// 沉浸式讨论界面 - 圆桌围坐体验
class ImmersiveSessionScreen extends ConsumerStatefulWidget {
  final Topic topic;
  final List<String> characterIds;
  final List<String> thinkerIds;
  final String humanName;

  const ImmersiveSessionScreen({
    super.key,
    required this.topic,
    required this.characterIds,
    this.thinkerIds = const [],
    required this.humanName,
  });

  @override
  ConsumerState<ImmersiveSessionScreen> createState() =>
      _ImmersiveSessionScreenState();
}

class _ImmersiveSessionScreenState extends ConsumerState<ImmersiveSessionScreen>
    with TickerProviderStateMixin {
  final DiscussionWebSocket _wsClient = DiscussionWebSocket();
  final TextEditingController _inputController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final FocusNode _keyboardFocusNode = FocusNode();

  // 讨论状态
  final List<ChatMessage> _messages = [];
  String _currentSpeaker = '';
  bool _isMyTurn = false;
  String _statusText = '连接中...';
  bool _hasRaisedHand = false;
  bool _isPaused = false;
  // TTS 顺序播放队列 (i)
  final List<({String source, String text, String? voice})> _ttsQueue = [];
  bool _ttsPlaying = false;
  // 错误追踪：如果先收到错误事件，结束时显示错误原因而非"讨论已结束"
  String? _lastErrorMessage;
  // Thinking indicator state (f)
  bool _isThinking = false;
  // Streaming STT text (bug 3)
  String _sttPartialText = '';
  int _ctrlTapCount = 0;
  DateTime? _lastCtrlTapAt;
  Timer? _ctrlTapTimer;
  bool _ctrlHeld = false;
  Timer? _speechFinalizeTimer;
  bool _pendingHumanTurn = false;
  String _pendingHumanSpeaker = '';
  // User turn countdown timer (bug 3)
  int _turnCountdown = 0;
  // 3分钟最大发言计时器（需求2）
  Timer? _maxSpeechTimer;
  DateTime? _speechStartTime;
  // 文字输入面板状态（需求2）
  bool _showTextInputPanel = false;
  // 用户发言后字幕保留计时器（需求2）
  Timer? _subtitleRetainTimer;

  // 参与者
  List<SeatedParticipant> _participants = [];

  // 中心消息
  String _centerMessage = '';
  String _centerSpeaker = '';
  int _subtitleToken = 0;
  String _subtitleOwner = '';

  // 语音状态
  bool _isRecording = false;
  late TtsService _ttsService;
  late AsrService _asrService;

  // 动画
  late AnimationController _candleController;
  late AnimationController _glowController;
  late AnimationController _micController;
  late AnimationController _thinkingController; // (f) thinking dots
  Timer? _turnTimer; // (bug 3) user turn countdown
  List<CandleParticle>? _particles;
  OverlayEntry? _statusToastEntry;
  Timer? _statusToastTimer;

  // 角色显示名 → Edge TTS 音色映射
  static const _nameToVoice = <String, String>{
    '李老师': 'zh-CN-XiaoxiaoNeural', // 温暖女声·教师
    '小探': 'zh-CN-YunxiNeural', // 少年男声·探索
    '小疑': 'zh-CN-YunzeNeural', // 深沉男声·质疑
    '小和': 'zh-CN-XiaoyiNeural', // 柔和女声·和平
    '小说': 'zh-CN-XiaohanNeural', // 活泼女声·讲故事
    '小明': 'zh-CN-YunjieNeural', // 阳光男声·乐观
    '小思': 'zh-CN-YunxiaNeural', // 明亮男声·提问
    '小理': 'zh-CN-YunyangNeural', // 正式男声·理性
    '小爱': 'zh-CN-XiaohanNeural', // 温柔女声·共情
    '小想': 'zh-CN-YunfengNeural', // 稳健男声·创新
    '小行': 'zh-CN-YunjianNeural', // 强劲男声·务实
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
    _inputController.addListener(_onTextInputChanged);
    _initVoiceServices();
    _startDiscussion();
  }

  // PTT 触发逻辑：双击 Ctrl 开关录音，或按住 Ctrl 录音（可在设置切换）。
  bool _onHardwareKey(KeyEvent event) {
    if (!_isPushToTalk || _isPaused) return false;
    final isCtrl = event.logicalKey == LogicalKeyboardKey.controlLeft ||
        event.logicalKey == LogicalKeyboardKey.controlRight;
    if (!isCtrl) return false;

    if (_micControlMode == 'hold_ctrl') {
      if (event is KeyDownEvent && !_ctrlHeld) {
        _ctrlHeld = true;
        if (_isMyTurn && !_isRecording) {
          _onPttStart();
          return true;
        }
      }
      if (event is KeyUpEvent) {
        _ctrlHeld = false;
        if (_isRecording) {
          _onPttEnd();
          return true;
        }
      }
      return false;
    }

    if (event is! KeyDownEvent) return false;

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

    _ttsService = createTtsService(ttsProvider, serverUrl: serverUrl);
    _asrService = createAsrService(asrProvider, serverUrl: serverUrl);

    // 监听 ASR 转录结果：流式写入草稿，最终片段用于收尾。
    _asrService.transcriptionStream.listen((result) {
      final chunk = result.text.trim();
      if (chunk.isEmpty) return;
      setState(() {
        _sttPartialText = result.isFinal
            ? chunk
            : _mergeStreamingDraft(_sttPartialText, chunk);
        if (_isRecording) {
          _centerSpeaker = widget.humanName;
          _centerMessage = _sttPartialText;
        }
      });
      _debugSubtitleLog(triggerRole: widget.humanName, note: 'stt streaming');
    });
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

  bool _hasOngoingSpeechPlayback() {
    return _ttsPlaying || _ttsService.isSpeaking || _isRecording;
  }

  bool get _debugSubtitleLogEnabled => kDebugMode;

  String get _micControlMode {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.micControlMode ?? 'double_ctrl';
  }

  void _debugSubtitleLog({required String triggerRole, required String note}) {
    if (!_debugSubtitleLogEnabled) return;
    debugPrint(
      '[SubtitleSync] trigger=$triggerRole current=$_currentSpeaker owner=$_subtitleOwner token=$_subtitleToken note=$note',
    );
  }

  void _acquireSubtitleToken(String owner) {
    _subtitleToken += 1;
    _subtitleOwner = owner;
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
    setState(() {
      _pendingHumanTurn = false;
      _pendingHumanSpeaker = '';
      _isMyTurn = true;
      _isThinking = false;
      _thinkingController.stop();
      _sttPartialText = '';
      _clearSubtitleBeforeSpeakerSwitch(
          speaker.isEmpty ? widget.humanName : speaker);
      _showTextInputPanel = true; // 显示文字输入面板
      _statusText = '轮到你了：点击麦克风说话，或在右侧输入文字';
      _glowController.repeat(reverse: true);
      if (speaker.isNotEmpty) {
        _currentSpeaker = speaker;
      }
    });
    _keyboardFocusNode.requestFocus();
    _startTurnCountdown();
  }

  void _deferHumanTurn({String speaker = ''}) {
    setState(() {
      _pendingHumanTurn = true;
      _pendingHumanSpeaker = speaker;
      _statusText = '等待上一位发言播放完成，即将轮到你...';
      _isMyTurn = false;
    });
  }

  void _tryActivatePendingHumanTurn() {
    if (!_pendingHumanTurn) return;
    if (_hasOngoingSpeechPlayback()) return;
    _activateHumanTurnNow(speaker: _pendingHumanSpeaker);
  }

  String _friendlyError(String raw) {
    final s = raw.trim();
    if (s.contains('输入队列不存在') || s.contains('Failed to get user input')) {
      return '用户输入通道暂时不可用，系统已自动跳过本轮并继续讨论。';
    }
    if (s.contains('timeout') || s.contains('超时')) {
      return '等待输入超时，系统已自动进入下一位发言。';
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
    switch (event.eventType) {
      case WsEventType.message:
        final data = event.data;
        if (data != null) {
          final source = data['source'] ?? '未知';
          final content = data['content'] ?? '';
          final msgType = data['msg_type'] ?? 'text';
          final shouldSpeak = msgType != 'system' && source != widget.humanName;

          setState(() {
            _messages.add(
                ChatMessage(source: source, content: content, type: msgType));

            // 关键同步策略：
            // - 需要TTS的消息，不在接收时抢先更新字幕；
            // - 在真正开始播放时再更新中心字幕，确保音字同时出现。
            if (!shouldSpeak) {
              _centerMessage = content;
              _centerSpeaker = source;
            }

            // Clear thinking state when message arrives (f)
            if (source == _currentSpeaker) {
              _isThinking = false;
              _thinkingController.stop();
            }
          });

          // 如果是 AI 角色/主持人消息，排队 TTS 朗读（顺序播放，i）
          if (shouldSpeak) {
            final voice = _voiceMap[source];
            _enqueueTts(source: source, text: content, voice: voice);
          }

          _buildParticipants();
        }
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;

          if (isHuman &&
              speaker == widget.humanName &&
              _hasOngoingSpeechPlayback()) {
            _deferHumanTurn(speaker: speaker);
            _buildParticipants();
            return;
          }

          _cancelTurnCountdown();
          _cancelMaxSpeechTimer();
          setState(() {
            _clearSubtitleBeforeSpeakerSwitch(speaker);
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == widget.humanName;
            _hasRaisedHand = false;
            _sttPartialText = '';
            if (_isMyTurn) {
              _isThinking = false;
              _thinkingController.stop();
              _showTextInputPanel = true;
              _statusText = '轮到你了：点击麦克风说话，或在右侧输入文字';
              _glowController.repeat(reverse: true);
              _keyboardFocusNode.requestFocus();
              _startTurnCountdown();
            } else if (isHuman) {
              _isThinking = false;
              _thinkingController.stop();
              _showTextInputPanel = false;
              _statusText = '$speaker 正在发言...';
              _glowController.stop();
            } else {
              _showTextInputPanel = false;
              _isThinking = true;
              _thinkingController.repeat();
              _statusText = '$speaker 正在思考...';
              _glowController.stop();
            }
          });
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
      case WsEventType.stream:
        final data = event.data;
        if (data != null) {
          final source = (data['source'] ?? '').toString();
          final content = (data['content'] ?? '').toString();
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
          if (newState == 'interrupted') {
            setState(() => _statusText = '有人请求打断...');
          } else {
            setState(() => _statusText = '状态: $newState');
          }
        }
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
      case WsEventType.humanInputRequested:
        if (_hasOngoingSpeechPlayback()) {
          _deferHumanTurn(speaker: widget.humanName);
        } else {
          _activateHumanTurnNow(speaker: widget.humanName);
        }
      case WsEventType.apiError:
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? 'AI 服务错误';
        final friendly = _friendlyError(errMsg.toString());
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
        setState(() {
          _lastErrorMessage = friendly;
          _statusText = '错误: $_lastErrorMessage';
        });
        _showStatusToast(_lastErrorMessage!, isError: true);
        break;
      case WsEventType.ended:
        final endedWithError = _lastErrorMessage != null;
        setState(() {
          _statusText =
              endedWithError ? '会话已中断: ${_lastErrorMessage!}' : '讨论已结束';
          _isMyTurn = false;
          _glowController.stop();
        });
        break;
      case WsEventType.interrupt:
        final data = event.data;
        if (data != null) {
          final interrupter = data['interrupter'] ?? '';
          setState(() {
            _messages.add(ChatMessage(
              source: '系统',
              content: '$interrupter 举手请求发言',
              type: 'system',
            ));
          });
          _buildParticipants();
        }
        break;
    }
  }

  // ── 文本输入 ────────────────────────────────────────────────────────────────

  void _sendMessage() {
    final text = _inputController.text.trim();
    if (text.isEmpty) {
      _onSkipTurn(reason: '输入为空，已自动跳过本轮');
      return;
    }

    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
    _wsClient.sendHumanInput(speaker: widget.humanName, content: text);

    setState(() {
      _messages.add(ChatMessage(source: widget.humanName, content: text));
      _isMyTurn = false;
      _showTextInputPanel = false;
      _statusText = '等待其他人发言...';
      _centerMessage = text;
      _centerSpeaker = widget.humanName;
    });

    _inputController.clear();

    // 字幕保留（需求2）：保留时间 = min(2~4秒, 发言时长×0.5)，不少于1秒
    _startSubtitleRetain(text);
  }

  // ── Push-to-Talk ────────────────────────────────────────────────────────────

  void _onPttStart() {
    _cancelTurnCountdown();
    _speechFinalizeTimer?.cancel();

    // 需求2：如果有未提交的文字，切换到语音模式时自动清除
    if (_inputController.text.trim().isNotEmpty) {
      _inputController.clear();
    }

    setState(() {
      _isRecording = true;
      _centerSpeaker = widget.humanName;
      _centerMessage = '';
      _showTextInputPanel = false; // 语音模式隐藏文字输入
    });
    _acquireSubtitleToken(widget.humanName);
    _micController.repeat(reverse: true);
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _asrService.startListening();
    _speechStartTime = DateTime.now();
    _startMaxSpeechTimer();
  }

  void _onPttEnd() {
    if (!_isRecording) return;
    _cancelMaxSpeechTimer();
    setState(() => _isRecording = false);
    _micController.stop();
    _micController.reset();
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _asrService.stopListening();

    _speechFinalizeTimer?.cancel();
    _speechFinalizeTimer = Timer(const Duration(milliseconds: 800), () async {
      if (!mounted) return;

      final rawText = _sttPartialText;
      final refined = await _refineTranscript(rawText);
      if (!mounted) return;
      setState(() => _sttPartialText = '');

      // 只要有任何文本（流式草稿或精炼结果），就提交；都为空才跳过
      if (refined.trim().isEmpty && rawText.trim().isEmpty) {
        _onSkipTurn(reason: '未识别到有效语音，已自动跳过本轮', addUserMessage: false);
        return;
      }
      final submitText = refined.trim().isNotEmpty ? refined : rawText.trim();

      _wsClient.sendHumanInput(speaker: widget.humanName, content: submitText);
      setState(() {
        _messages
            .add(ChatMessage(source: widget.humanName, content: submitText));
        _isMyTurn = false;
        _showTextInputPanel = false;
        _statusText = '等待其他人发言...';
        _centerMessage = submitText;
        _centerSpeaker = widget.humanName;
      });

      // 字幕保留
      _startSubtitleRetain(submitText);
    });
  }

  // ── 跳过本轮发言 ────────────────────────────────────────────────────────────

  void _onSkipTurn({String? reason, bool addUserMessage = true}) {
    _cancelTurnCountdown();
    _cancelMaxSpeechTimer();
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
      _showTextInputPanel = false;
      _statusText = reason ?? '等待其他人发言...';
    });
    if (reason != null && reason.isNotEmpty) {
      _showStatusToast(reason);
    }
    // 跳过后确保 TTS 队列继续处理（修复跳过后无声音的 bug）
    if (_ttsQueue.isNotEmpty && !_ttsPlaying) {
      Future.delayed(const Duration(milliseconds: 500), () {
        if (mounted && _ttsQueue.isNotEmpty && !_ttsPlaying) {
          _playNextTts();
        }
      });
    }
  }

  // ── User turn countdown (bug 3) ────────────────────────────────────────────

  void _startTurnCountdown() {
    _cancelTurnCountdown();
    setState(() => _turnCountdown = 10);
    _turnTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _turnCountdown--);
      if (_turnCountdown <= 0) {
        timer.cancel();
        _onSkipTurn(reason: '10秒未开始语音发言，已自动跳过');
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
      } else if (_showTextInputPanel &&
          _inputController.text.trim().isNotEmpty) {
        // 自动提交文字
        _sendMessage();
        setState(() {
          _statusText = '输入超过3分钟，已自动提交';
        });
        _showStatusToast('输入超过3分钟，已自动提交');
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

  // ── 文字输入开始时取消10秒计时（需求2）────────────────────────────────────────

  void _onTextInputChanged() {
    if (_inputController.text.trim().isNotEmpty && _turnCountdown > 0) {
      _cancelTurnCountdown();
      _startMaxSpeechTimer(); // 开始3分钟倒计时
    }
  }

  // ── TTS 顺序播放队列 (i) ────────────────────────────────────────────────────

  /// Add text to the TTS queue and start playback if not already playing.
  void _enqueueTts(
      {required String source, required String text, String? voice}) {
    _ttsQueue.add((source: source, text: text, voice: voice));
    if (!_ttsPlaying) _playNextTts();
  }

  /// TTS 预加载：提前合成下一段文本，减少播放间隔（Req4）
  void _prefetchTts(String text, {String? voice}) {
    // GatewayTtsService supports prefetch; other TTS services don't.
    try {
      (_ttsService as dynamic).prefetch(text, voice: voice);
    } catch (_) {
      // Not supported by this TTS service
    }
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
    if (_ttsQueue.isEmpty || _isPaused) {
      _ttsPlaying = false;
      _tryActivatePendingHumanTurn();
      return;
    }
    _ttsPlaying = true;
    final item = _ttsQueue.removeAt(0);
    _acquireSubtitleToken(item.source);

    // TTS 预加载：在播放当前条目时，提前合成下一条和再下一条（Req5 优化）
    if (_ttsQueue.isNotEmpty) {
      final next = _ttsQueue.first;
      _prefetchTts(next.text, voice: next.voice);
      if (_ttsQueue.length > 1) {
        final nextNext = _ttsQueue[1];
        _prefetchTts(nextNext.text, voice: nextNext.voice);
      }
    }

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
    try {
      // 超时保护：按文字长度动态计算，每个字约0.3秒，最少20秒，最多120秒
      final timeoutSec = (item.text.length * 0.3).clamp(20, 120).toInt();
      await _ttsService
          .speak(item.text, voice: item.voice, rate: speed)
          .timeout(Duration(seconds: timeoutSec));
    } catch (e) {
      // TTS 失败或超时，跳过并提示，但不中断队列
      if (mounted) {
        setState(() {
          _messages.add(ChatMessage(
            source: '系统',
            content: '${item.source} 语音播放异常，已跳过',
            type: 'system',
          ));
        });
      }
    }
    // After speak() resolves, play the next item (if any)
    if (!mounted) return;
    // 确保 TTS 完全停止后再播放下一条
    await Future.delayed(const Duration(milliseconds: 300));
    if (_ttsQueue.isEmpty) {
      _ttsPlaying = false;
      _buildParticipants();
      _tryActivatePendingHumanTurn();
      return;
    }
    _playNextTts();
  }

  // ── 暂停 / 继续 ─────────────────────────────────────────────────────────────

  void _onTogglePause() {
    if (_isPaused) {
      setState(() {
        _isPaused = false;
        _statusText = '继续讨论...';
      });
      _wsClient.sendResume();
      // Resume TTS queue
      if (_ttsQueue.isNotEmpty && !_ttsPlaying) _playNextTts();
    } else {
      setState(() {
        _isPaused = true;
        _statusText = '已暂停';
      });
      _ttsService.stop();
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
    _turnTimer?.cancel();
    _ctrlTapTimer?.cancel();
    _speechFinalizeTimer?.cancel();
    _maxSpeechTimer?.cancel();
    _subtitleRetainTimer?.cancel();
    _statusToastTimer?.cancel();
    _statusToastEntry?.remove();
    _ctrlHeld = false;
    _inputController.removeListener(_onTextInputChanged);
    HardwareKeyboard.instance.removeHandler(_onHardwareKey);
    _ttsService.dispose();
    _asrService.dispose();
    _wsClient.dispose();
    _inputController.dispose();
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

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final tableRadius = min(size.width, size.height) * 0.18;

    // 圆桌圆心位于屏幕水平 1/3 处（需求3.2）
    final tableCenterX = size.width / 3;
    final tableCenterY = size.height / 2;

    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 20,
    );

    final canInterrupt =
        !_isMyTurn && _currentSpeaker.isNotEmpty && !_hasRaisedHand;

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
            if (_isMyTurn && !_isRecording && !_showTextInputPanel)
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
                                  ? '轮到你了，点击麦克风或右侧输入文字 ($_turnCountdown s)'
                                  : '轮到你了，点击麦克风或右侧输入文字',
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
            if (_isMyTurn || canInterrupt || _hasRaisedHand)
              Positioned(
                left: tableCenterX + tableRadius + 148,
                top: tableCenterY - tableRadius,
                child: AnimatedOpacity(
                  opacity: 1.0,
                  duration: const Duration(milliseconds: 300),
                  child: _RightActionColumn(
                    showMic: _isMyTurn,
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

            // ── 右侧文字输入面板 ──
            if (_isMyTurn && !_isRecording)
              Positioned(
                right: 116,
                top: tableCenterY - tableRadius,
                child: AnimatedOpacity(
                  opacity: _showTextInputPanel ? 1.0 : 0.0,
                  duration: const Duration(milliseconds: 350),
                  child: IgnorePointer(
                    ignoring: !_showTextInputPanel,
                    child: _TextInputPanel(
                      controller: _inputController,
                      onSend: _sendMessage,
                      onClose: () =>
                          setState(() => _showTextInputPanel = false),
                      turnCountdown: _turnCountdown,
                      tableHeight: tableRadius * 2,
                    ),
                  ),
                ),
              ),

            // ── 底部字幕区（电影风格，20号字，确保完整显示）──
            if (_centerSpeaker.isNotEmpty && _centerMessage.isNotEmpty)
              Positioned(
                bottom: 0,
                left: 0,
                right: 0,
                height: size.height * 0.22,
                child: IgnorePointer(
                  child: Container(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          Colors.transparent,
                          Colors.black.withValues(alpha: 0.7),
                          Colors.black.withValues(alpha: 0.85),
                        ],
                        stops: const [0.0, 0.3, 1.0],
                      ),
                    ),
                    padding: const EdgeInsets.fromLTRB(48, 24, 48, 24),
                    child: Align(
                      alignment: Alignment.bottomCenter,
                      child: RichText(
                        textAlign: TextAlign.center,
                        maxLines: 5,
                        overflow: TextOverflow.ellipsis,
                        text: TextSpan(
                          children: [
                            TextSpan(
                              text: '$_centerSpeaker：',
                              style: const TextStyle(
                                color: Color(0xFF4FC3F7),
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                                height: 1.6,
                                shadows: [
                                  Shadow(color: Colors.black, blurRadius: 8),
                                  Shadow(color: Colors.black, blurRadius: 16),
                                ],
                              ),
                            ),
                            TextSpan(
                              text: _centerMessage,
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 20,
                                height: 1.6,
                                shadows: [
                                  Shadow(color: Colors.black, blurRadius: 8),
                                  Shadow(color: Colors.black, blurRadius: 16),
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

            // ── 底部控制栏 ──
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: GlassControlBar(
                isMyTurn: _isMyTurn,
                isPushToTalk: _isPushToTalk,
                isRecording: _isRecording,
                inputController: _inputController,
                onSendMessage: _sendMessage,
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

// ─── 左侧文字输入面板（需求2）──────────────────────────────────────────────────
/// 圆桌左侧的大面积文字输入面板，宽高比 3:2，面积接近圆桌一半。
class _TextInputPanel extends StatelessWidget {
  final TextEditingController controller;
  final VoidCallback onSend;
  final VoidCallback onClose;
  final int turnCountdown;
  final double tableHeight;

  const _TextInputPanel({
    required this.controller,
    required this.onSend,
    required this.onClose,
    required this.turnCountdown,
    required this.tableHeight,
  });

  @override
  Widget build(BuildContext context) {
    // 宽度为屏幕 25%，高度等于圆桌直径
    final panelWidth = MediaQuery.of(context).size.width * 0.25;
    final panelHeight = tableHeight;

    return Container(
      width: panelWidth,
      height: panelHeight,
      decoration: BoxDecoration(
        color: AppColors.studyWall.withValues(alpha: 0.95),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: AppColors.amberGold.withValues(alpha: 0.4),
          width: 1.5,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.4),
            blurRadius: 20,
            spreadRadius: 4,
          ),
        ],
      ),
      child: Column(
        children: [
          // 标题栏
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              border: Border(
                bottom: BorderSide(
                  color: AppColors.amberGold.withValues(alpha: 0.2),
                ),
              ),
            ),
            child: Row(
              children: [
                Icon(Icons.edit, color: AppColors.amberGold, size: 16),
                const SizedBox(width: 6),
                Text(
                  turnCountdown > 0 ? '输入你的发言 ($turnCountdown s)' : '输入你的发言',
                  style: TextStyle(
                    color: AppColors.amberGold,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const Spacer(),
                GestureDetector(
                  onTap: onClose,
                  child: Icon(Icons.close, color: AppColors.warmGray, size: 18),
                ),
              ],
            ),
          ),
          // 文字输入区
          Expanded(
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: TextField(
                controller: controller,
                maxLines: null,
                expands: true,
                textAlignVertical: TextAlignVertical.top,
                style: const TextStyle(
                  color: AppColors.warmWhite,
                  fontSize: 14,
                  height: 1.5,
                ),
                decoration: InputDecoration(
                  hintText: '在这里输入你的想法...\n输入完成后点击下方"发送"',
                  hintStyle: TextStyle(
                    color: AppColors.warmGray.withValues(alpha: 0.6),
                    fontSize: 13,
                  ),
                  border: InputBorder.none,
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ),
          ),
          // 发送按钮
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            child: Row(
              children: [
                const Spacer(),
                SizedBox(
                  width: 60,
                  height: 48,
                  child: FilledButton(
                    onPressed: onSend,
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.amberGold,
                      foregroundColor: AppColors.scrollTitle,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(14),
                      ),
                    ),
                    child: const Icon(Icons.send, size: 18),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
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
    if (!widget.isRecording) {
      _pulseCtrl.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(_SpeakButton old) {
    super.didUpdateWidget(old);
    if (widget.isRecording) {
      _pulseCtrl.stop();
      _pulseCtrl.value = 1.0;
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
        final pulse = widget.isRecording ? 1.0 : 0.85 + _pulseCtrl.value * 0.15;
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
                color: widget.isRecording
                    ? const Color(0xFFFF4444).withValues(alpha: 0.25)
                    : (widget.enabled
                        ? const Color(0xFF00FFCC).withValues(alpha: 0.18)
                        : Colors.white.withValues(alpha: 0.08)),
                border: Border.all(
                  color: widget.isRecording
                      ? const Color(0xFFFF4444).withValues(alpha: 0.9)
                      : (widget.enabled
                          ? const Color(0xFF00FFCC).withValues(alpha: 0.9)
                          : Colors.white.withValues(alpha: 0.3)),
                  width: 2.5,
                ),
                boxShadow: [
                  BoxShadow(
                    color: widget.isRecording
                        ? const Color(0xFFFF4444).withValues(alpha: 0.35)
                        : (widget.enabled
                            ? const Color(0xFF00FFCC).withValues(alpha: 0.3)
                            : Colors.black.withValues(alpha: 0.15)),
                    blurRadius: 20,
                    spreadRadius: 5,
                  ),
                ],
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
                            : Colors.white70),
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
                              : Colors.white70),
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
