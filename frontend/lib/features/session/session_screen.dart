import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/config_models.dart';
import '../../models/discussion_models.dart';
import '../../services/speech_service.dart';
import '../../services/websocket_client.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import 'push_to_talk_button.dart';

/// 讨论房间 - 核心界面
class SessionScreen extends ConsumerStatefulWidget {
  static const String defaultServerUrl = 'http://localhost:8001';
  static const String defaultAsrProvider = 'funasr';
  static const String defaultTtsProvider = 'edge_tts';

  final Topic topic;
  final List<String> characterIds;
  final String humanName;

  const SessionScreen({
    super.key,
    required this.topic,
    required this.characterIds,
    required this.humanName,
  });

  @override
  ConsumerState<SessionScreen> createState() => _SessionScreenState();

  static String preferAvailableChatTts(
    String requestedProvider,
    SpeechConfig? speechConfig,
  ) {
    if (requestedProvider != defaultTtsProvider) {
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
}

class _SessionScreenState extends ConsumerState<SessionScreen> {
  // Ctrl 键计数与定时器
  int _ctrlKeyCount = 0;
  DateTime? _lastCtrlKeyTime;
  Timer? _ctrlKeyTimer;
  Timer? _speechTimeoutTimer;
  bool _hasSpokenDuringRecording = false;

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

  // 语音状态
  bool _isRecording = false;
  late TtsService _ttsService;
  late AsrService _asrService;
  bool _voiceServicesInitialized = false;
  String _speechServerUrl = SessionScreen.defaultServerUrl;
  String _ttsProviderId = SessionScreen.defaultTtsProvider;
  String _asrProviderId = SessionScreen.defaultAsrProvider;
  String _ttsProviderUrl = '';
  String _asrProviderUrl = '';
  StreamSubscription<AsrResult>? _asrTranscriptionSub;
  ProviderSubscription<AsyncValue<LocalSettings>>? _settingsSubscription;

  @override
  void initState() {
    super.initState();
    final settings =
        ref.read(localSettingsProvider).valueOrNull ?? const LocalSettings();
    _bindVoiceServices(
      serverUrl: settings.serverUrl,
      ttsProvider: settings.ttsProvider,
      asrProvider: settings.asrProvider,
    );
    unawaited(_refreshVoiceServices(settingsOverride: settings));
    _settingsSubscription = ref.listenManual<AsyncValue<LocalSettings>>(
      localSettingsProvider,
      (previous, next) {
        final nextSettings = next.valueOrNull;
        if (nextSettings == null) {
          return;
        }
        _bindVoiceServices(
          serverUrl: nextSettings.serverUrl,
          ttsProvider: nextSettings.ttsProvider,
          asrProvider: nextSettings.asrProvider,
        );
        unawaited(_refreshVoiceServices(settingsOverride: nextSettings));
      },
    );
    _startDiscussion();
  }

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

  void _bindVoiceServices({
    required String serverUrl,
    required String ttsProvider,
    required String asrProvider,
    String ttsProviderUrl = '',
    String asrProviderUrl = '',
  }) {
    if (_voiceServicesInitialized &&
        _speechServerUrl == serverUrl &&
        _ttsProviderId == ttsProvider &&
        _asrProviderId == asrProvider &&
        _ttsProviderUrl == ttsProviderUrl &&
        _asrProviderUrl == asrProviderUrl) {
      return;
    }

    if (_voiceServicesInitialized) {
      _asrTranscriptionSub?.cancel();
      try {
        _ttsService.stop();
      } catch (_) {}
      try {
        _asrService.stopListening();
      } catch (_) {}
      _ttsService.dispose();
      _asrService.dispose();
    }

    _speechServerUrl = serverUrl;
    _ttsProviderId = ttsProvider;
    _asrProviderId = asrProvider;
    _ttsProviderUrl = ttsProviderUrl;
    _asrProviderUrl = asrProviderUrl;
    _ttsService = createTtsService(
      ttsProvider,
      serverUrl: serverUrl,
      providerUrl: ttsProviderUrl,
    );
    _asrService = createAsrService(
      asrProvider,
      serverUrl: serverUrl,
      providerUrl: asrProviderUrl,
    );
    _asrTranscriptionSub?.cancel();
    _asrTranscriptionSub =
        _asrService.transcriptionStream.listen(_handleAsrResult);
    _voiceServicesInitialized = true;
  }

  Future<void> _refreshVoiceServices({LocalSettings? settingsOverride}) async {
    final settings = settingsOverride ??
        ref.read(localSettingsProvider).valueOrNull ??
        const LocalSettings();
    SpeechConfig? speechConfig = ref.read(speechConfigProvider).valueOrNull;
    if (speechConfig == null) {
      try {
        speechConfig = await ref.read(speechConfigProvider.future);
      } catch (_) {
        speechConfig = null;
      }
    }

    final ttsProvider = SessionScreen.preferAvailableChatTts(
      settings.ttsProvider,
      speechConfig,
    );
    _bindVoiceServices(
      serverUrl: settings.serverUrl,
      ttsProvider: ttsProvider,
      asrProvider: settings.asrProvider,
      ttsProviderUrl:
          _resolveProviderUrl(speechConfig, ttsProvider, asr: false),
      asrProviderUrl:
          _resolveProviderUrl(speechConfig, settings.asrProvider, asr: true),
    );
  }

  void _handleAsrResult(AsrResult result) {
    if (!result.isFinal) return;
    final normalized = result.text.trim();
    if (normalized.isNotEmpty) {
      _hasSpokenDuringRecording = true;
      _wsClient.sendHumanInput(speaker: widget.humanName, content: normalized);
      setState(() {
        _messages
            .add(ChatMessage(source: widget.humanName, content: normalized));
        _isMyTurn = false;
        _statusText = '等待其他人发言...';
      });
      _scrollToBottom();
    }
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
        freeTopic: widget.topic.id == 'free_topic' ? widget.topic.title : '',
        freeTopicDetail:
            widget.topic.id == 'free_topic' ? widget.topic.description : '',
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
          });

          // 如果是 AI 角色/主持人消息，自动 TTS 朗读
          if (msgType != 'system' && source != widget.humanName) {
            _ttsService.speak(content);
          }

          _scrollToBottom();
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
            } else if (isHuman) {
              _statusText = '$speaker 正在发言...';
            } else {
              _statusText = '$speaker 正在思考...';
            }
          });
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
        final speaker =
            ((event.data?['speaker'] ?? '') as Object).toString().trim();
        if (speaker.isNotEmpty && speaker != widget.humanName) {
          break;
        }
        setState(() {
          _isMyTurn = true;
          _statusText = '轮到你发言了！';
        });
      case WsEventType.apiError:
        final apiData = event.data;
        setState(() {
          _statusText =
              '错误: ${apiData?['message'] ?? apiData?['original_error'] ?? 'AI 服务异常'}';
        });
      case WsEventType.phaseTelemetry:
        break;
      case WsEventType.error:
        final data = event.data;
        final errMsg = (data?['message'] ?? '').toString().trim();
        // 过滤 ASGI 内部噪声错误，不显示给用户
        if (errMsg.isEmpty ||
            errMsg.contains('Unexpected ASGI message') ||
            errMsg.contains('websocket.send')) {
          break;
        }
        setState(() {
          _statusText = '错误: $errMsg';
        });
      case WsEventType.ended:
        setState(() {
          _statusText = '讨论已结束';
          _isMyTurn = false;
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
    });

    _inputController.clear();
    _scrollToBottom();
  }

  // ── Push-to-Talk ────────────────────────────────────────────────────────────

  void _onPttStart() {
    _speechTimeoutTimer?.cancel();
    _hasSpokenDuringRecording = false;
    setState(() => _isRecording = true);
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _asrService.startListening();

    // 10 秒未检测到语音则自动结束并跳过。
    _speechTimeoutTimer = Timer(const Duration(seconds: 10), () {
      if (!mounted || !_isRecording) return;
      _onPttEnd();
      if (!_hasSpokenDuringRecording) {
        _wsClient.sendHumanInput(speaker: widget.humanName, content: '（跳过）');
        setState(() {
          _statusText = '10秒未检测到语音，已自动跳过';
          _isMyTurn = false;
        });
      }
    });
  }

  void _onPttEnd() {
    _speechTimeoutTimer?.cancel();
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

  // ── 滚动 ────────────────────────────────────────────────────────────────────

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  bool get _isPushToTalk {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.pushToTalk ?? true;
  }

  @override
  void dispose() {
    _ctrlKeyTimer?.cancel();
    _speechTimeoutTimer?.cancel();
    _settingsSubscription?.close();
    _asrTranscriptionSub?.cancel();
    if (_voiceServicesInitialized) {
      _ttsService.dispose();
      _asrService.dispose();
    }
    _wsClient.dispose();
    _inputController.dispose();
    _scrollController.dispose();
    _keyboardFocusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isPushToTalk = _isPushToTalk;

    return KeyboardListener(
      focusNode: _keyboardFocusNode,
      onKeyEvent: _onKeyEvent,
      child: Scaffold(
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.topic.title,
                  maxLines: 1, overflow: TextOverflow.ellipsis),
              Text(_statusText, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
        body: Column(
          children: [
            // 参与者条
            _ParticipantBar(
              currentSpeaker: _currentSpeaker,
              humanName: widget.humanName,
              isMyTurn: _isMyTurn,
              isRecording: _isRecording,
            ),

            // 消息列表
            Expanded(
              child: ListView.builder(
                controller: _scrollController,
                padding: const EdgeInsets.all(16),
                itemCount: _messages.length,
                itemBuilder: (context, index) {
                  final msg = _messages[index];
                  return _MessageBubble(message: msg, myName: widget.humanName);
                },
              ),
            ),

            // 输入区域
            _InputArea(
              isMyTurn: _isMyTurn,
              isPushToTalk: isPushToTalk,
              isRecording: _isRecording,
              inputController: _inputController,
              onSendMessage: _sendMessage,
              onPttStart: _onPttStart,
              onPttEnd: _onPttEnd,
              canInterrupt:
                  !_isMyTurn && _currentSpeaker.isNotEmpty && !_hasRaisedHand,
              hasRaisedHand: _hasRaisedHand,
              onInterrupt: _onInterrupt,
            ),
          ],
        ),
      ),
    );
  }

  void _onKeyEvent(KeyEvent event) {
    // 仅 Push-to-Talk 模式下Ctrl键触发
    if (!_isPushToTalk) return;

    // 监听Ctrl键
    final isCtrl = event.logicalKey == LogicalKeyboardKey.controlLeft ||
        event.logicalKey == LogicalKeyboardKey.controlRight;

    if (event is KeyDownEvent && isCtrl) {
      final now = DateTime.now();
      if (_lastCtrlKeyTime == null ||
          now.difference(_lastCtrlKeyTime!) >
              const Duration(milliseconds: 1200)) {
        _ctrlKeyCount = 1;
      } else {
        _ctrlKeyCount++;
      }
      _lastCtrlKeyTime = now;

      _ctrlKeyTimer?.cancel();
      _ctrlKeyTimer = Timer(const Duration(milliseconds: 1200), () {
        _ctrlKeyCount = 0;
      });

      // 三击Ctrl启动语音识别
      if (_ctrlKeyCount == 3 && _isMyTurn && !_isRecording) {
        _onPttStart();
        _ctrlKeyCount = 0;
      }
      // 双击Ctrl结束语音识别
      else if (_ctrlKeyCount == 2 && _isRecording) {
        _onPttEnd();
        _ctrlKeyCount = 0;
      }
    }
  }
}

// ── 子组件 ──────────────────────────────────────────────────────────────────

class _ParticipantBar extends StatelessWidget {
  final String currentSpeaker;
  final String humanName;
  final bool isMyTurn;
  final bool isRecording;

  const _ParticipantBar({
    required this.currentSpeaker,
    required this.humanName,
    required this.isMyTurn,
    required this.isRecording,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
      ),
      child: Row(
        children: [
          if (isMyTurn || isRecording)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: isRecording
                    ? Colors.red
                    : Theme.of(context).colorScheme.primary,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    isRecording ? Icons.mic : Icons.mic_none,
                    size: 14,
                    color: Colors.white,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    isRecording ? '录音中...' : '轮到你发言',
                    style: const TextStyle(color: Colors.white, fontSize: 12),
                  ),
                ],
              ),
            )
          else if (currentSpeaker.isNotEmpty)
            Text(
              '$currentSpeaker 正在发言',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          const Spacer(),
          Text(
            '你: $humanName',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _InputArea extends StatelessWidget {
  final bool isMyTurn;
  final bool isPushToTalk;
  final bool isRecording;
  final TextEditingController inputController;
  final VoidCallback onSendMessage;
  final VoidCallback onPttStart;
  final VoidCallback onPttEnd;
  final bool canInterrupt;
  final bool hasRaisedHand;
  final VoidCallback onInterrupt;

  const _InputArea({
    required this.isMyTurn,
    required this.isPushToTalk,
    required this.isRecording,
    required this.inputController,
    required this.onSendMessage,
    required this.onPttStart,
    required this.onPttEnd,
    required this.canInterrupt,
    required this.hasRaisedHand,
    required this.onInterrupt,
  });

  @override
  Widget build(BuildContext context) {
    if (!isMyTurn && !canInterrupt) return const SizedBox.shrink();

    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.1),
            blurRadius: 4,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        child: isPushToTalk && isMyTurn
            ? _buildPttInput(context)
            : isMyTurn
                ? _buildTextInput(context)
                : _buildInterruptOnly(context),
      ),
    );
  }

  Widget _buildPttInput(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        // PTT 按钮
        PushToTalkButton(
          isEnabled: true,
          onRecordStart: onPttStart,
          onRecordEnd: onPttEnd,
          isRecording: isRecording,
        ),
        const SizedBox(width: 12),
        // 文本输入切换按钮
        TextButton.icon(
          onPressed: onSendMessage,
          icon: const Icon(Icons.keyboard, size: 18),
          label: const Text('文字输入'),
        ),
        const SizedBox(width: 8),
        // 打断按钮
        if (canInterrupt)
          InterruptButton(
            isVisible: true,
            onPressed: onInterrupt,
            hasRaisedHand: hasRaisedHand,
          ),
      ],
    );
  }

  Widget _buildTextInput(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: inputController,
            decoration: InputDecoration(
              hintText: '说出你的想法...',
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
              ),
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 16,
                vertical: 10,
              ),
            ),
            onSubmitted: (_) => onSendMessage(),
          ),
        ),
        const SizedBox(width: 8),
        // 麦克风按钮（切换到 PTT）
        if (isPushToTalk)
          IconButton(
            onPressed: onPttStart,
            icon: const Icon(Icons.mic),
            style: IconButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.primaryContainer,
            ),
          ),
        FilledButton(
          onPressed: onSendMessage,
          style: FilledButton.styleFrom(
            shape: const CircleBorder(),
            padding: const EdgeInsets.all(12),
          ),
          child: const Icon(Icons.send),
        ),
      ],
    );
  }

  Widget _buildInterruptOnly(BuildContext context) {
    return Center(
      child: InterruptButton(
        isVisible: canInterrupt,
        onPressed: onInterrupt,
        hasRaisedHand: hasRaisedHand,
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  final ChatMessage message;
  final String myName;

  const _MessageBubble({required this.message, required this.myName});

  @override
  Widget build(BuildContext context) {
    final isMe = message.source == myName;
    final isSystem = message.type == 'system';
    final isModerator = message.source == '老师';

    if (isSystem) {
      return Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: Colors.grey[200],
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(
            message.content,
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: Colors.grey[700]),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    final participantColor = AppColors.getParticipantColor(message.source);

    return Align(
      alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.all(12),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.7,
        ),
        decoration: BoxDecoration(
          color: isMe
              ? Theme.of(context).colorScheme.primary
              : isModerator
                  ? participantColor.withValues(alpha: 0.1)
                  : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(12),
            topRight: const Radius.circular(12),
            bottomLeft: isMe ? const Radius.circular(12) : Radius.zero,
            bottomRight: isMe ? Radius.zero : const Radius.circular(12),
          ),
          border: isModerator && !isMe
              ? Border(left: BorderSide(color: participantColor, width: 3))
              : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: participantColor,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  message.source,
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                        color: isMe
                            ? Theme.of(context).colorScheme.onPrimary
                            : null,
                        fontWeight: FontWeight.bold,
                      ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              message.content,
              style: TextStyle(
                color: isMe ? Theme.of(context).colorScheme.onPrimary : null,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
