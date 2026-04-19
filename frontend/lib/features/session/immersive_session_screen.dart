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
  // 错误追踪：如果先收到错误事件，结束时显示错误原因而非"讨论已结束"
  String? _lastErrorMessage;

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
    _initVoiceServices();
    _startDiscussion();
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
        _wsClient.sendHumanInput(speaker: widget.humanName, content: text);
        setState(() {
          _messages.add(ChatMessage(source: widget.humanName, content: text));
          _isMyTurn = false;
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

    // 旧角色模板的头像映射 - 使用 DiceBear 网络头像（PNG，64px）
    // 每个名字/ID 对应一个固定的 DiceBear 头像 URL
    // 备用 emoji 只在网络失败时显示（errorBuilder 里）
    const templateAvatarMap = {
      'moderator': '👩‍🏫',
      '李老师': '👩‍🏫',
      'explorer': '🔍',
      '小探': '🔍',
      'skeptic': '🤔',
      '小疑': '🤔',
      'peacemaker': '🕊️',
      '小和': '🕊️',
      'storyteller': '📖',
      '小说': '📖',
      'optimist': '☀️',
      '小明': '☀️',
      'questioner': '🤨',
      '小思': '🤨',
      'rationalist': '🧮',
      '小理': '🧮',
      'empath': '💗',
      '小爱': '💗',
      'innovator': '💡',
      '小想': '💡',
      'pragmatist': '🔧',
      '小行': '🔧',
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
        // AI 角色和思想家都使用 DiceBear 网络头像
        avatars[name] = diceBearUrl(name);
        if (templateAvatarMap.containsKey(name)) {
          _voiceMap.putIfAbsent(
              name, () => _nameToVoice[name] ?? 'zh-CN-XiaoxiaoNeural');
        } else {
          // 思想家 - 轮转分配男声
          _voiceMap.putIfAbsent(
              name,
              () => _maleThinkerVoices[
                  thinkerVoiceIndex % _maleThinkerVoices.length]);
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
          });

          // 如果是 AI 角色/主持人消息，自动 TTS 朗读
          if (msgType != 'system' && source != widget.humanName) {
            final voice = _voiceMap[source];
            _ttsService.speak(content, voice: voice);
          }

          _buildParticipants();
        }
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;
          setState(() {
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == widget.humanName;
            _hasRaisedHand = false;
            if (_isMyTurn) {
              _statusText = '轮到你！按空格键发言 或 输入文字';
              _glowController.repeat(reverse: true);
              _keyboardFocusNode.requestFocus();
            } else if (isHuman) {
              _statusText = '$speaker 正在发言...';
              _glowController.stop();
            } else {
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
        setState(() {
          _isMyTurn = true;
          _statusText = '轮到你！按空格键发言 或 输入文字';
          _glowController.repeat(reverse: true);
        });
        _keyboardFocusNode.requestFocus();
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
    _ttsService.dispose();
    _asrService.dispose();
    _wsClient.dispose();
    _inputController.dispose();
    _scrollController.dispose();
    _keyboardFocusNode.dispose();
    _candleController.dispose();
    _glowController.dispose();
    _micController.dispose();
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
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
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
                    children: [
                      IconButton(
                        icon: const Icon(Icons.arrow_back,
                            color: AppColors.warmWhite),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              widget.topic.title,
                              style:
                                  AppTheme.calligraphyStyleDark(fontSize: 16),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            Text(
                              _statusText,
                              style: TextStyle(
                                color: _isMyTurn
                                    ? AppColors.amberGold
                                    : AppColors.warmGray,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.history,
                            color: AppColors.warmGray),
                        onPressed: _showChatHistory,
                      ),
                    ],
                  ),
                ),
              ),
            ),

            // ── 录音霓虹麦克风指示 ──
            if (_isRecording)
              Positioned(
                bottom: 130,
                left: 0,
                right: 0,
                child: Center(
                  child: AnimatedBuilder(
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
