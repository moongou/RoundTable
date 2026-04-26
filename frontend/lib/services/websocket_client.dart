import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

/// WebSocket 事件类型
enum WsEventType {
  message,
  turnChange,
  stream,
  stateChange,
  phaseTelemetry,
  system,
  humanInputRequested,
  apiError,
  error,
  ended,
  interrupt;

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
        .map((i, part) => MapEntry(
            i, i == 0 ? part : '${part[0].toUpperCase()}${part.substring(1)}'))
        .values
        .join('');
  }
}

/// WebSocket 事件
class WsEvent {
  final WsEventType eventType;
  final Map<String, dynamic>? data;
  final int? eventSeq;

  const WsEvent({required this.eventType, this.data, this.eventSeq});

  factory WsEvent.fromJson(Map<String, dynamic> json) {
    final eventTypeRaw = json['event_type']?.toString() ?? 'error';
    final dataRaw = json['data'];
    final dataMap = dataRaw is Map
        ? Map<String, dynamic>.from(dataRaw)
        : <String, dynamic>{};

    return WsEvent(
      eventType: WsEventType.fromString(eventTypeRaw),
      data: dataMap,
      eventSeq: (json['event_seq'] as num?)?.toInt(),
    );
  }
}

/// WebSocket 客户端，用于接收讨论事件和发送人类输入
class DiscussionWebSocket {
  WebSocketChannel? _channel;
  final StreamController<WsEvent> _eventController =
      StreamController<WsEvent>.broadcast();
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
    List<String> thinkerIds = const [],
    bool observerMode = false,
    int maxTurns = 30,
  }) async {
    final uri = Uri.parse('$wsUrl/api/v1/ws/discussion/$sessionId');
    _channel = WebSocketChannel.connect(uri);

    _connected = true;

    // 发送初始配置
    _channel!.sink.add(jsonEncode({
      'topic_id': topicId,
      'character_ids': characterIds,
      'thinker_ids': thinkerIds,
      'human_names': humanNames,
      'max_turns': maxTurns,
      'observer_mode': observerMode,
    }));

    // 监听消息
    _channel!.stream.listen(
      (message) {
        try {
          final decoded = jsonDecode(message as String);
          if (decoded is! Map) {
            throw FormatException('WebSocket payload is not a JSON object');
          }
          final json = Map<String, dynamic>.from(decoded);
          final event = WsEvent.fromJson(json);
          _eventController.add(event);
        } catch (e) {
          _eventController.add(WsEvent(
            eventType: WsEventType.error,
            data: {
              'message': 'WebSocket 事件解析失败: $e',
            },
          ));
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
  void sendHumanInput({
    required String speaker,
    required String content,
    Map<String, dynamic>? recording,
  }) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'human_input',
      'speaker': speaker,
      'content': content,
      if (recording != null) 'recording': recording,
    }));
  }

  /// 发送 Push-to-Talk 开始信号
  void sendPushToTalkStart({required String speaker}) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'push_to_talk_start',
      'speaker': speaker,
    }));
  }

  /// 发送 Push-to-Talk 结束信号
  void sendPushToTalkEnd({required String speaker}) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'push_to_talk_end',
      'speaker': speaker,
    }));
  }

  /// 上报前端 ASR 引擎工作状态，便于后端日志与时序观测。
  void sendAsrStatus({
    required String speaker,
    required String provider,
    required String status,
    bool? available,
    bool? listening,
    int? textLen,
    bool? isFinal,
    String? error,
  }) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'asr_status',
      'speaker': speaker,
      'provider': provider,
      'status': status,
      if (available != null) 'available': available,
      if (listening != null) 'listening': listening,
      if (textLen != null) 'text_len': textLen,
      if (isFinal != null) 'is_final': isFinal,
      if (error != null && error.isNotEmpty) 'error': error,
    }));
  }

  /// 发送暂停信号
  void sendPause() {
    if (!_connected || _channel == null) return;
    _channel!.sink.add(jsonEncode({'type': 'pause'}));
  }

  /// 发送继续信号
  void sendResume() {
    if (!_connected || _channel == null) return;
    _channel!.sink.add(jsonEncode({'type': 'resume'}));
  }

  /// 发送打断请求（举手）
  void sendInterrupt({required String speaker}) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'interrupt',
      'speaker': speaker,
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
