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
  interrupt,
  disconnected,
  resumed;

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
  StreamSubscription? _channelSub;
  final StreamController<WsEvent> _eventController =
      StreamController<WsEvent>.broadcast();
  bool _connected = false;
  int _interruptRequestSeq = 0;

  // --- 断线重连/恢复状态 ---
  String? _wsUrl;
  String? _sessionId;
  String? _topicId;
  List<String> _characterIds = const [];
  List<String> _humanNames = const [];
  List<String> _thinkerIds = const [];
  bool _observerMode = false;
  int _maxTurns = 24;
  int? _userId;

  /// 客户端最后收到的事件序号，重连时用于请求服务端补发遗漏事件。
  int _lastEventSeq = 0;

  /// 是否收到过服务端明确的 `ended`（真正结束，不应再重连）。
  bool _serverEnded = false;

  /// 是否由本地主动断开（dispose/disconnect），主动断开不重连。
  bool _manualClose = false;

  bool _reconnecting = false;
  int _reconnectAttempts = 0;
  static const int _maxReconnectAttempts = 8;
  Timer? _reconnectTimer;

  /// 事件流
  Stream<WsEvent> get events => _eventController.stream;

  bool get isConnected => _connected;

  /// 是否正在尝试断线重连（UI 可据此显示“连接中断，正在恢复”提示）。
  bool get isReconnecting => _reconnecting;

  /// 连接到讨论 WebSocket
  Future<void> connect({
    required String wsUrl,
    required String sessionId,
    required String topicId,
    required List<String> characterIds,
    required List<String> humanNames,
    List<String> thinkerIds = const [],
    bool observerMode = false,
    int maxTurns = 24,
    int? userId,
  }) async {
    _wsUrl = wsUrl;
    _sessionId = sessionId;
    _topicId = topicId;
    _characterIds = characterIds;
    _humanNames = humanNames;
    _thinkerIds = thinkerIds;
    _observerMode = observerMode;
    _maxTurns = maxTurns;
    _userId = userId;

    _lastEventSeq = 0;
    _serverEnded = false;
    _manualClose = false;
    _reconnecting = false;
    _reconnectAttempts = 0;
    _reconnectTimer?.cancel();

    _openChannel(resume: false);
  }

  /// 打开（或重新打开）WebSocket 通道。`resume=true` 时携带 resume_seq 让服务端
  /// 接管后台仍在运行的讨论并补发遗漏事件。
  void _openChannel({required bool resume}) {
    if (_wsUrl == null || _sessionId == null) return;

    final uri = Uri.parse('$_wsUrl/api/v1/ws/discussion/$_sessionId');
    _channel = WebSocketChannel.connect(uri);
    _connected = true;

    final config = <String, dynamic>{
      'topic_id': _topicId,
      'character_ids': _characterIds,
      'thinker_ids': _thinkerIds,
      'human_names': _humanNames,
      'max_turns': _maxTurns,
      'observer_mode': _observerMode,
    };
    if (_userId != null) {
      config['user_id'] = _userId;
    }
    if (resume) {
      config['resume'] = true;
      config['resume_seq'] = _lastEventSeq;
    }
    _channel!.sink.add(jsonEncode(config));

    _channelSub?.cancel();
    _channelSub = _channel!.stream.listen(
      (message) {
        try {
          final decoded = jsonDecode(message as String);
          if (decoded is! Map) {
            throw FormatException('WebSocket payload is not a JSON object');
          }
          final json = Map<String, dynamic>.from(decoded);
          final event = WsEvent.fromJson(json);

          // 跟踪事件序号，供断线重连补发。
          if (event.eventSeq != null && event.eventSeq! > _lastEventSeq) {
            _lastEventSeq = event.eventSeq!;
          }
          if (event.eventType == WsEventType.ended) {
            _serverEnded = true;
            _reconnecting = false;
            _reconnectTimer?.cancel();
          } else if (event.eventType == WsEventType.resumed) {
            // 重连成功，复位重连计数。
            _reconnecting = false;
            _reconnectAttempts = 0;
          }
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
      onDone: _handleDone,
      onError: _handleError,
    );
  }

  void _handleDone() {
    _connected = false;
    if (_manualClose || _serverEnded) {
      // 真正结束或主动断开：服务端 `ended` 事件已转发给 UI，无需再处理。
      if (_serverEnded && !_manualClose) {
        _eventController.add(const WsEvent(eventType: WsEventType.ended));
      }
      return;
    }
    // 意外断开（切标签/短暂掉线）：通知 UI 进入“恢复中”，并尝试重连。
    _eventController.add(const WsEvent(eventType: WsEventType.disconnected));
    _scheduleReconnect();
  }

  void _handleError(Object error) {
    _connected = false;
    if (_manualClose || _serverEnded) {
      _eventController.add(WsEvent(
        eventType: WsEventType.error,
        data: {'message': error.toString()},
      ));
      return;
    }
    // 连接出错同样进入重连流程，而不是直接判定为结束。
    _eventController.add(const WsEvent(eventType: WsEventType.disconnected));
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_manualClose || _serverEnded) return;
    if (_reconnectAttempts >= _maxReconnectAttempts) {
      // 多次重连失败，放弃并按结束处理。
      _eventController.add(const WsEvent(
        eventType: WsEventType.ended,
        data: {'reason': 'reconnect_failed'},
      ));
      return;
    }
    _reconnecting = true;
    _reconnectAttempts++;
    final delayMs = (700 * _reconnectAttempts).clamp(700, 5000);
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(milliseconds: delayMs), () {
      if (_manualClose || _serverEnded) return;
      try {
        _openChannel(resume: true);
      } catch (_) {
        _scheduleReconnect();
      }
    });
  }

  /// 立即尝试一次重连（例如页面重新可见时由 UI 主动触发）。
  void requestReconnectNow() {
    if (_manualClose || _serverEnded || _connected) return;
    _reconnectTimer?.cancel();
    _openChannel(resume: true);
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

  void sendEndDiscussion({
    required String speaker,
    String reason = 'button',
  }) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'end_discussion',
      'speaker': speaker,
      'reason': reason,
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

  void sendClientMetric({
    required String name,
    required int valueMs,
    String? speaker,
    String? phase,
    int? eventSeq,
    String? detail,
  }) {
    if (!_connected || _channel == null) return;

    _channel!.sink.add(jsonEncode({
      'type': 'client_metric',
      'name': name,
      'value_ms': valueMs,
      if (speaker != null && speaker.isNotEmpty) 'speaker': speaker,
      if (phase != null && phase.isNotEmpty) 'phase': phase,
      if (eventSeq != null) 'event_seq': eventSeq,
      if (detail != null && detail.isNotEmpty) 'detail': detail,
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
  void sendInterrupt({required String speaker, String? requestId}) {
    if (!_connected || _channel == null) return;

    final resolvedRequestId = (requestId != null && requestId.isNotEmpty)
        ? requestId
        : 'int-${DateTime.now().microsecondsSinceEpoch}-${++_interruptRequestSeq}';

    _channel!.sink.add(jsonEncode({
      'type': 'interrupt',
      'speaker': speaker,
      'request_id': resolvedRequestId,
    }));
  }

  /// 断开连接
  void disconnect() {
    _manualClose = true;
    _reconnecting = false;
    _reconnectTimer?.cancel();
    _channelSub?.cancel();
    _channelSub = null;
    _channel?.sink.close();
    _connected = false;
  }

  void dispose() {
    disconnect();
    _eventController.close();
  }
}
