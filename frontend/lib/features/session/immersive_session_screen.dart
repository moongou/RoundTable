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
  final String humanName;

  const ImmersiveSessionScreen({
    super.key,
    required this.topic,
    required this.characterIds,
    required this.humanName,
  });

  @override
  ConsumerState<ImmersiveSessionScreen> createState() => _ImmersiveSessionScreenState();
}

class _ImmersiveSessionScreenState extends ConsumerState<ImmersiveSessionScreen>
    with TickerProviderStateMixin {
  final DiscussionWebSocket _wsClient = DiscussionWebSocket();
  final TextEditingController _inputController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final FocusNode _keyboardFocusNode = FocusNode();

  // 讨论状态
  List<ChatMessage> _messages = [];
  String _currentSpeaker = '';
  bool _isMyTurn = false;
  String _statusText = '连接中...';
  bool _hasRaisedHand = false;

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
  List<CandleParticle>? _particles;

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
        humanNames: [widget.humanName],
      );

      setState(() => _statusText = '已连接');

      // 监听事件
      _wsClient.events.listen(_handleEvent);
    } catch (e) {
      setState(() => _statusText = '连接失败: $e');
    }
  }

  void _buildParticipants() {
    // 构建参与者列表：AI 角色 + 人类
    final names = <String>{};
    final avatars = <String, String>{};

    // 从已有消息中提取参与者
    for (final msg in _messages) {
      if (msg.type != 'system') {
        names.add(msg.source);
      }
    }

    // 从当前发言者中添加
    if (_currentSpeaker.isNotEmpty) {
      names.add(_currentSpeaker);
    }

    // 话题中的角色信息 - 使用 characterIds 映射
    const avatarMap = {
      'moderator': '👩‍🏫',
      'explorer': '🔍',
      'skeptic': '🤔',
      'peacemaker': '🕊️',
      'storyteller': '📖',
    };

    const nameMap = {
      'moderator': '李老师',
      'explorer': '小探',
      'skeptic': '小疑',
      'peacemaker': '小和',
      'storyteller': '小说',
    };

    // 先添加 AI 角色
    for (final charId in widget.characterIds) {
      final name = nameMap[charId] ?? charId;
      names.add(name);
      avatars[name] = avatarMap[charId] ?? '🤖';
    }

    // 添加人类参与者
    names.add(widget.humanName);
    avatars[widget.humanName] = '🙋';

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
            _messages.add(ChatMessage(source: source, content: content, type: msgType));
            _centerMessage = content;
            _centerSpeaker = source;
          });

          // 如果是 AI 角色/主持人消息，自动 TTS 朗读
          if (msgType != 'system' && source != widget.humanName) {
            _ttsService.speak(content);
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
              _statusText = '轮到你发言了！';
              _glowController.repeat(reverse: true);
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
          _statusText = '轮到你发言了！';
          _glowController.repeat(reverse: true);
        });
      case WsEventType.error:
        final data = event.data;
        setState(() {
          _statusText = '错误: ${data?['message'] ?? '未知错误'}';
        });
      case WsEventType.ended:
        setState(() {
          _statusText = '讨论已结束';
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
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _asrService.startListening();
  }

  void _onPttEnd() {
    setState(() => _isRecording = false);
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _asrService.stopListening();
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
    } else if (event is KeyUpEvent && event.logicalKey == LogicalKeyboardKey.space) {
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

    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 20,
    );

    final canInterrupt = !_isMyTurn && _currentSpeaker.isNotEmpty && !_hasRaisedHand;

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

            // ── 中心发言气泡 ──
            if (_centerSpeaker.isNotEmpty && _centerMessage.isNotEmpty)
              Center(
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: tableRadius * 0.3),
                  child: SpeakingBubble(
                    speaker: _centerSpeaker,
                    content: _centerMessage,
                    speakerColor: AppColors.getParticipantColor(_centerSpeaker),
                    isVisible: true,
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
                            color: AppColors.amberGold.withValues(alpha: glowAlpha),
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
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
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
                        icon: const Icon(Icons.arrow_back, color: AppColors.warmWhite),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              widget.topic.title,
                              style: AppTheme.calligraphyStyleDark(fontSize: 16),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            Text(
                              _statusText,
                              style: TextStyle(
                                color: _isMyTurn ? AppColors.amberGold : AppColors.warmGray,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.history, color: AppColors.warmGray),
                        onPressed: _showChatHistory,
                      ),
                    ],
                  ),
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
              ),
            ),
          ],
        ),
      ),
    );
  }
}