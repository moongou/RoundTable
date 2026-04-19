import 'dart:async';
import 'dart:math';

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
import 'speaking_bubble.dart';
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
  final List<({String text, String? voice})> _ttsQueue = [];
  bool _ttsPlaying = false;
  // 错误追踪：如果先收到错误事件，结束时显示错误原因而非"讨论已结束"
  String? _lastErrorMessage;
  // Thinking indicator state (f)
  bool _isThinking = false;
  // Streaming STT text (bug 3)
  String _sttPartialText = '';
  // User turn countdown timer (bug 3)
  int _turnCountdown = 0;

  // 参与者
  List<SeatedParticipant> _participants = [];

  // 中心消息
  String _centerMessage = '';
  String _centerSpeaker = '';

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
    // Global hardware keyboard listener for spacebar PTT (works even when text field is focused)
    HardwareKeyboard.instance.addHandler(_onHardwareKey);
    _initVoiceServices();
    _startDiscussion();
  }

  /// Global key handler for spacebar push-to-talk (l: spacebar fix)
  bool _onHardwareKey(KeyEvent event) {
    if (!_isPushToTalk) return false;
    if (event is KeyDownEvent && event.logicalKey == LogicalKeyboardKey.space) {
      if (_isMyTurn && !_isRecording && !_isPaused) {
        _onPttStart();
        return true; // consume the event
      }
    } else if (event is KeyUpEvent &&
        event.logicalKey == LogicalKeyboardKey.space) {
      if (_isRecording) {
        _onPttEnd();
        return true;
      }
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

    // 监听 ASR 转录结果
    _asrService.transcriptionStream.listen((text) {
      if (text.isNotEmpty) {
        // Show partial STT text before sending (bug 3)
        setState(() => _sttPartialText = text);
        _wsClient.sendHumanInput(speaker: widget.humanName, content: text);
        setState(() {
          _messages.add(ChatMessage(source: widget.humanName, content: text));
          _isMyTurn = false;
          _sttPartialText = '';
          _statusText = '等待其他人发言...';
        });
      }
    });
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
            final allVoices = [
              ..._maleThinkerVoices,
              ..._femaleThinkerVoices
            ];
            final voice =
                allVoices[thinkerVoiceIndex % allVoices.length];
            return voice;
          });
          thinkerVoiceIndex++;
        }
      }
    }

    setState(() {
      _participants = names.map((name) {
        final isHuman = name == widget.humanName;
        final isSpeaking = name == _currentSpeaker;
        final isCurrentSpeaker = name == _currentSpeaker;
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

          setState(() {
            _messages.add(
                ChatMessage(source: source, content: content, type: msgType));
            _centerMessage = content;
            _centerSpeaker = source;
            // Clear thinking state when message arrives (f)
            if (source == _currentSpeaker) {
              _isThinking = false;
              _thinkingController.stop();
            }
          });

          // 如果是 AI 角色/主持人消息，排队 TTS 朗读（顺序播放，i）
          if (msgType != 'system' && source != widget.humanName) {
            final voice = _voiceMap[source];
            _enqueueTts(content, voice);
          }

          _buildParticipants();
        }
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;
          _cancelTurnCountdown();
          setState(() {
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == widget.humanName;
            _hasRaisedHand = false;
            _sttPartialText = '';
            if (_isMyTurn) {
              _isThinking = false;
              _thinkingController.stop();
              _statusText = '轮到你了，请按住空格键说话';
              _glowController.repeat(reverse: true);
              _keyboardFocusNode.requestFocus();
              _startTurnCountdown();
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
          _buildParticipants();
        }
      case WsEventType.stream:
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
        _cancelTurnCountdown();
        setState(() {
          _isMyTurn = true;
          _isThinking = false;
          _thinkingController.stop();
          _sttPartialText = '';
          _statusText = '轮到你了，请按住空格键说话';
          _glowController.repeat(reverse: true);
        });
        _keyboardFocusNode.requestFocus();
        _startTurnCountdown();
      case WsEventType.error:
        final data = event.data;
        final errMsg = data?['message'] ?? data?['original_error'] ?? '未知错误';
        setState(() {
          _lastErrorMessage = errMsg.toString();
          _statusText = '错误: $_lastErrorMessage';
        });
        // Show error dialog
        if (mounted) {
          _showErrorDialog(_lastErrorMessage!);
        }
      case WsEventType.ended:
        final endedWithError = _lastErrorMessage != null;
        setState(() {
          _statusText =
              endedWithError ? '会话已中断: ${_lastErrorMessage!}' : '讨论已结束';
          _isMyTurn = false;
          _glowController.stop();
        });
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
    }
  }

  // ── 文本输入 ────────────────────────────────────────────────────────────────

  void _sendMessage() {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;

    _cancelTurnCountdown();
    _wsClient.sendHumanInput(speaker: widget.humanName, content: text);

    setState(() {
      _messages.add(ChatMessage(source: widget.humanName, content: text));
      _isMyTurn = false;
      _statusText = '等待其他人发言...';
      _centerMessage = text;
      _centerSpeaker = widget.humanName;
    });

    _inputController.clear();
  }

  // ── Push-to-Talk ────────────────────────────────────────────────────────────

  void _onPttStart() {
    _cancelTurnCountdown();
    setState(() => _isRecording = true);
    _micController.repeat(reverse: true);
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _asrService.startListening();
  }

  void _onPttEnd() {
    setState(() => _isRecording = false);
    _micController.stop();
    _micController.reset();
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _asrService.stopListening();
  }

  // ── 跳过本轮发言 ────────────────────────────────────────────────────────────

  void _onSkipTurn() {
    _cancelTurnCountdown();
    _wsClient.sendHumanInput(speaker: widget.humanName, content: '（跳过）');
    setState(() {
      _messages.add(ChatMessage(
        source: widget.humanName,
        content: '（跳过）',
        type: 'text',
      ));
      _isMyTurn = false;
      _statusText = '等待其他人发言...';
    });
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
      setState(() => _turnCountdown--);
      if (_turnCountdown <= 0) {
        timer.cancel();
        _onSkipTurn();
      }
    });
  }

  void _cancelTurnCountdown() {
    _turnTimer?.cancel();
    _turnTimer = null;
    if (_turnCountdown > 0) setState(() => _turnCountdown = 0);
  }

  // ── TTS 顺序播放队列 (i) ────────────────────────────────────────────────────

  /// Add text to the TTS queue and start playback if not already playing.
  void _enqueueTts(String text, String? voice) {
    _ttsQueue.add((text: text, voice: voice));
    if (!_ttsPlaying) _playNextTts();
  }

  /// Simple emotion-based speech rate (h): calm/sad → slower, excited/angry → faster.
  double _emotionSpeed(String text) {
    const excitedWords = ['太棒了', '好极了', '厉害', '激动', '加油', '哇', '真的吗', '不可思议', '冲啊'];
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
      return;
    }
    _ttsPlaying = true;
    final item = _ttsQueue.removeAt(0);
    final speed = _emotionSpeed(item.text);
    await _ttsService.speak(item.text, voice: item.voice, rate: speed);
    // After speak() resolves, play the next item (if any)
    if (mounted) _playNextTts();
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
  }

  bool get _isPushToTalk {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.pushToTalk ?? true;
  }

  void _onKeyEvent(KeyEvent event) {
    if (!_isPushToTalk) return;

    if (event is KeyDownEvent && event.logicalKey == LogicalKeyboardKey.space) {
      if (_isMyTurn && !_isRecording) {
        _onPttStart();
      }
    } else if (event is KeyUpEvent &&
        event.logicalKey == LogicalKeyboardKey.space) {
      if (_isRecording) {
        _onPttEnd();
      }
    }
  }

  @override
  void dispose() {
    _turnTimer?.cancel();
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

  void _showErrorDialog(String errorMessage) {
    if (!mounted) return;
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF2A2015),
        title: const Row(children: [
          Icon(Icons.error_outline, color: Colors.red, size: 22),
          SizedBox(width: 8),
          Text('无法开始讨论',
              style: TextStyle(color: Color(0xFFF5DEB3), fontSize: 16)),
        ]),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.red.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.red.withValues(alpha: 0.3)),
              ),
              child: Text(
                errorMessage,
                style: const TextStyle(color: Colors.redAccent, fontSize: 13),
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '请检查设置页面中的 AI 模型配置，确保已选择正确的提供商并填写有效的 API Key。',
              style: TextStyle(color: Color(0xFFAA9977), fontSize: 12),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.of(ctx).pop();
              Navigator.of(context).pushReplacementNamed('/settings');
            },
            child:
                const Text('前往设置', style: TextStyle(color: Color(0xFFD4A017))),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('关闭', style: TextStyle(color: Color(0xFFAA9977))),
          ),
        ],
      ),
    );
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

            // ── 圆桌 ──
            Center(
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

            // ── 参与者圆环 ──
            Center(
              child: SizedBox(
                width: (tableRadius + 80) * 2,
                height: (tableRadius + 80) * 2,
                child: TableParticipantRing(
                  participants: _participants,
                  tableRadius: tableRadius,
                ),
              ),
            ),

            // ── 动态麦克风：随发言者移动 (l) ──
            if (_currentSpeaker.isNotEmpty)
              Center(
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

            // ── 话题标题：圆桌上方居中 (c/bug2) ──
            Positioned(
              top: MediaQuery.of(context).padding.top + size.height * 0.06,
              left: 0,
              right: 0,
              child: Center(
                child: ConstrainedBox(
                  constraints: BoxConstraints(maxWidth: size.width * 0.60),
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
              ),
            ),

            // ── Thinking indicator (f) ──
            if (_isThinking && _currentSpeaker.isNotEmpty)
              Positioned(
                top: MediaQuery.of(context).padding.top + size.height * 0.14,
                left: 0,
                right: 0,
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
                              color: AppColors.amberGold.withValues(alpha: 0.3)),
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

            // ── "轮到你了" prompt + countdown (bug 3) ──
            if (_isMyTurn && !_isRecording)
              Positioned(
                top: MediaQuery.of(context).padding.top + size.height * 0.14,
                left: 0,
                right: 0,
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
                                  ? '轮到你了，请按住空格键说话 ($_turnCountdown s)'
                                  : '轮到你了，请按住空格键说话',
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

            // ── 底部字幕条 ──
            if (_centerSpeaker.isNotEmpty && _centerMessage.isNotEmpty)
              Positioned(
                bottom: 100,
                left: 0,
                right: 0,
                child: Center(
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.52,
                    ),
                    child: SpeakingBubble(
                      speaker: _centerSpeaker,
                      content: _centerMessage,
                      speakerColor:
                          AppColors.getParticipantColor(_centerSpeaker),
                      isVisible: true,
                    ),
                  ),
                ),
              ),

            // ── 轮到我发光指示 ──
            if (_isMyTurn)
              Center(
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

            // ── 顶部状态栏 ──
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
                      // ── 左上：返回 + 暂停 (m) ──
                      IconButton(
                        icon: const Icon(Icons.arrow_back,
                            color: AppColors.warmWhite),
                        onPressed: () => Navigator.of(context).pop(),
                        padding: EdgeInsets.zero,
                        constraints:
                            const BoxConstraints(minWidth: 36, minHeight: 36),
                      ),
                      const SizedBox(width: 4),
                      // Pause button (m)
                      _PauseButton(
                        isPaused: _isPaused,
                        onToggle: _onTogglePause,
                      ),
                      const Spacer(),
                      // ── 顶部中央：状态文本 ──
                      Flexible(
                        flex: 3,
                        child: Text(
                          _statusText,
                          style: TextStyle(
                            color: _isMyTurn
                                ? AppColors.amberGold
                                : AppColors.warmGray,
                            fontSize: 12,
                          ),
                          textAlign: TextAlign.center,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const Spacer(),
                      // ── 右上：历史 (m) ──
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

            // ── 录音霓虹麦克风指示 + STT 文本 (bug 3) ──
            if (_isRecording)
              Positioned(
                bottom: 130,
                left: 0,
                right: 0,
                child: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (_sttPartialText.isNotEmpty)
                        Container(
                          margin: const EdgeInsets.only(bottom: 8),
                          padding: const EdgeInsets.symmetric(
                              horizontal: 14, vertical: 6),
                          decoration: BoxDecoration(
                            color: const Color(0xFF0D1B2A).withValues(alpha: 0.9),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(
                                color: const Color(0xFF00FFCC)
                                    .withValues(alpha: 0.3)),
                          ),
                          child: Text(
                            _sttPartialText,
                            style: const TextStyle(
                              color: Color(0xFF00FFCC),
                              fontSize: 13,
                            ),
                          ),
                        ),
                      AnimatedBuilder(
                        animation: _micController,
                        builder: (context, child) {
                          final pulse = 0.6 + _micController.value * 0.4;
                          return Container(
                            width: 72,
                            height: 72,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: const Color(0xFF0D1B2A),
                              boxShadow: [
                                BoxShadow(
                                  color: const Color(0xFF00FFCC)
                                      .withValues(alpha: 0.5 * pulse),
                                  blurRadius: 24 * pulse,
                                  spreadRadius: 6 * pulse,
                                ),
                                BoxShadow(
                                  color: const Color(0xFF00BFFF)
                                      .withValues(alpha: 0.3 * pulse),
                                  blurRadius: 40 * pulse,
                                  spreadRadius: 10 * pulse,
                                ),
                              ],
                              border: Border.all(
                                color: const Color(0xFF00FFCC)
                                    .withValues(alpha: 0.8 * pulse),
                                width: 2,
                              ),
                            ),
                            child: const Icon(
                              Icons.mic,
                              color: Color(0xFF00FFCC),
                              size: 32,
                            ),
                          );
                        },
                      ),
                    ],
                  ),
                ),
              ),

            // ── 右侧浮动举手按钮 (j) ──
            if (canInterrupt || _hasRaisedHand)
              Positioned(
                right: 16,
                top: size.height * 0.42,
                child: _FloatingRaiseHandButton(
                  hasRaisedHand: _hasRaisedHand,
                  canInterrupt: canInterrupt,
                  onTap: _onInterrupt,
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
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
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
          child: Icon(
            isPaused ? Icons.play_arrow : Icons.pause,
            color: isPaused
                ? AppColors.amberGold
                : Colors.white.withValues(alpha: 0.75),
            size: 18,
          ),
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
  const _FloatingRaiseHandButton({
    required this.hasRaisedHand,
    required this.canInterrupt,
    required this.onTap,
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
          onTap:
              (widget.canInterrupt && !widget.hasRaisedHand) ? widget.onTap : null,
          child: Transform.scale(
            scale: pulse,
            child: Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
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
