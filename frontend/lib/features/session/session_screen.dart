import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../services/api_client.dart';
import '../../services/websocket_client.dart';

/// 讨论房间 - 核心界面
class SessionScreen extends StatefulWidget {
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
  State<SessionScreen> createState() => _SessionScreenState();
}

class _SessionScreenState extends State<SessionScreen> {
  final ApiClient _apiClient = ApiClient();
  final DiscussionWebSocket _wsClient = DiscussionWebSocket();
  final TextEditingController _inputController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  // 讨论状态
  List<ChatMessage> _messages = [];
  String _currentSpeaker = '';
  bool _isHumanTurn = false;
  bool _isMyTurn = false;
  bool _connected = false;
  String _statusText = '连接中...';

  @override
  void initState() {
    super.initState();
    _startDiscussion();
  }

  Future<void> _startDiscussion() async {
    setState(() => _statusText = '创建讨论会话...');

    try {
      // 创建会话
      final sessionData = await _apiClient.createSession(
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        humanNames: [widget.humanName],
      );

      final sessionId = sessionData['session_id'] as String;
      final wsUrl = _apiClient.getWebSocketUrl(sessionId);

      // 连接 WebSocket
      await _wsClient.connect(
        wsUrl: wsUrl,
        sessionId: sessionId,
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        humanNames: [widget.humanName],
      );

      setState(() {
        _connected = true;
        _statusText = '已连接';
      });

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
          setState(() {
            _messages.add(ChatMessage(
              source: data['source'] ?? '未知',
              content: data['content'] ?? '',
              type: data['msg_type'] ?? 'text',
            ));
          });
          _scrollToBottom();
        }
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;
          setState(() {
            _currentSpeaker = speaker;
            _isHumanTurn = isHuman;
            _isMyTurn = isHuman && speaker == widget.humanName;
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
        // 流式文本 - 在 Phase 1 可以累积显示
        final data = event.data;
        if (data != null) {
          // 简单处理：将流式块添加为消息（后续可以优化为更新最后一条消息）
          // Phase 1 先忽略流式，只显示完整消息
        }
      case WsEventType.stateChange:
        final data = event.data;
        if (data != null) {
          final newState = data['new_state'] ?? '';
          setState(() {
            _statusText = '状态: $newState';
          });
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
        });
      case WsEventType.error:
        final data = event.data;
        setState(() {
          _statusText = '错误: ${data?['message'] ?? '未知错误'}';
        });
      case WsEventType.ended:
        setState(() {
          _connected = false;
          _statusText = '讨论已结束';
          _isMyTurn = false;
        });
    }
  }

  void _sendMessage() {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;

    _wsClient.sendHumanInput(
      speaker: widget.humanName,
      content: text,
    );

    setState(() {
      _messages.add(ChatMessage(
        source: widget.humanName,
        content: text,
      ));
      _isMyTurn = false;
      _statusText = '等待其他人发言...';
    });

    _inputController.clear();
    _scrollToBottom();
  }

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

  @override
  void dispose() {
    _wsClient.dispose();
    _inputController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.topic.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(_statusText),
      ),
      body: Column(
        children: [
          // 参与者条
          _ParticipantBar(
            currentSpeaker: _currentSpeaker,
            humanName: widget.humanName,
            isMyTurn: _isMyTurn,
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
          if (_isMyTurn)
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surface,
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withOpacity(0.1),
                    blurRadius: 4,
                    offset: const Offset(0, -2),
                  ),
                ],
              ),
              child: SafeArea(
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _inputController,
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
                        onSubmitted: (_) => _sendMessage(),
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton(
                      onPressed: _sendMessage,
                      style: FilledButton.styleFrom(
                        shape: const CircleBorder(),
                        padding: const EdgeInsets.all(12),
                      ),
                      child: const Icon(Icons.send),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ParticipantBar extends StatelessWidget {
  final String currentSpeaker;
  final String humanName;
  final bool isMyTurn;

  const _ParticipantBar({
    required this.currentSpeaker,
    required this.humanName,
    required this.isMyTurn,
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
          if (isMyTurn)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.primary,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                '轮到你发言',
                style: TextStyle(color: Theme.of(context).colorScheme.onPrimary),
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
            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: Colors.grey[700]),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

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
                  ? Theme.of(context).colorScheme.secondaryContainer
                  : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(12),
            topRight: const Radius.circular(12),
            bottomLeft: isMe ? const Radius.circular(12) : Radius.zero,
            bottomRight: isMe ? Radius.zero : const Radius.circular(12),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              message.source,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: isMe ? Theme.of(context).colorScheme.onPrimary : null,
                    fontWeight: FontWeight.bold,
                  ),
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