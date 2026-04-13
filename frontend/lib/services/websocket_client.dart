import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

/// WebSocket 事件类型
enum WsEventType {
  message,
  turnChange,
  stream,
  stateChange,
  system,
  humanInputRequested,
  error,
  ended;

  static WsEventType fromString(String type) {
    return WsEventType.values.firstWhere(
      (e) => e.name == _toCamelCase(type),
      orElse: () => WsEventType.error,
    );
  }

  static String _toCamelCase(String snakeCase) {
    return snakeCase
        .split('_')
        .asMap()
        .map((i, part) => MapEntry(i, i == 0 ? part : part.capitalize()))
        .values
        .join('');
  }
}

/// WebSocket 事件
class WsEvent {
  final WsEventType eventType;
  final Map<String, dynamic>? data;

  const WsEvent({required this.eventType, this.data});

  factory WsEvent.fromJson(Map<String, dynamic> json) => WsEvent(
        eventType: WsEventType.fromString(json['event_type'] as String),
        data: json['data'] as Map<String, dynamic>?,
      );
}

/// WebSocket 客户端，用于接收讨论事件和发送人类输入
class DiscussionWebSocket {
  WebSocketChannel? _channel;
  final StreamController<WsEvent> _eventController = StreamController<WsEvent>.broadcast();
  bool _connected = false;

  /// 事件流
  Stream<WsEvent> get events => _eventController.stream;

  bool get isConnected => _connected;

  /// 连接到讨论 WebSocket
  Future<void> connect({
    required String wsUrl,
    required String sessionId,
    required String topicId,
    required List<String> characterIds,
    required List<String> humanNames,
  }) async {
    final uri = Uri.parse('$wsUrl/api/v1/ws/discussion/$sessionId');
    _channel = WebSocketChannel.connect(uri);

    _connected = true;

    // 发送初始配置
    _channel!.sink.add(jsonEncode({
      'topic_id': topicId,
      'character_ids': characterIds,
      'human_names': humanNames,
    }));

    // 监听消息
    _channel!.stream.listen(
      (message) {
        try {
          final json = jsonDecode(message as String) as Map<String, dynamic>;
          final event = WsEvent.fromJson(json);
          _eventController.add(event);
        } catch (e) {
          // 忽略解析错误
        }
      },
      onDone: () {
        _connected = false;
        _eventController.add(const WsEvent(eventType: WsEventType.ended));
      },
      onError: (error) {
        _connected = false;
        _eventController.add(WsEvent(
          eventType: WsEventType.error,
          data: {'message': error.toString()},
        ));
      },
    );
  }

  /// 发送人类输入
  void sendHumanInput({required String speaker, required String content}) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'human_input',
      'speaker': speaker,
      'content': content,
    }));
  }

  /// 断开连接
  void disconnect() {
    _channel?.sink.close();
    _connected = false;
  }

  void dispose() {
    disconnect();
    _eventController.close();
  }
}