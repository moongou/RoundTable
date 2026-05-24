/// Gateway 流式语音服务实现
///
/// 直连各自独立端口上的 Vosk/CapsWriter 本地 ASR 服务。
/// ASR 支持 WebSocket 流式识别（低延迟）。
///
/// Local endpoints:
///   CapsWriter: ws://localhost:6016/ws
///   Vosk:       ws://localhost:6702/stream
library;

import 'dart:async';
import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:web/web.dart' as web;

import 'speech_contract.dart';

const _defaultCapsWriterUrl = 'ws://localhost:6016';
const _defaultVoskUrl = 'http://localhost:6702';

String _defaultAsrBaseUrl(String service) {
  return service.trim().toLowerCase() == 'capswriter'
      ? _defaultCapsWriterUrl
      : _defaultVoskUrl;
}

String _defaultAsrWsPath(String service) {
  return service.trim().toLowerCase() == 'capswriter' ? '/ws' : '/stream';
}

/// Gateway 流式 ASR 实现：WebSocket 实时流式语音识别
///
/// 工作流程:
/// 1. startListening() → 获取麦克风权限，连接 WebSocket
/// 2. 通过 AudioWorklet/ScriptProcessor 实时发送 PCM 数据
/// 3. 接收流式识别结果 (partial/result/final)
/// 4. stopListening() → 发送 eof，获取最终结果
class GatewayStreamingAsrService implements AsrService {
  final String gatewayUrl;
  final String service; // 'vosk' or 'capswriter'
  final String wsPath;
  final List<String> wsProtocols;
  final bool capsWriterJsonProtocol;
  bool _isListening = false;
  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();

  web.WebSocket? _ws;
  Completer<void>? _stopCompleter;
  Completer<void>? _finalResultCompleter;
  bool _disposed = false;
  String? _capsWriterTaskId;
  double _capsWriterStartSeconds = 0;
  int? _micSessionId;
  JSFunction? _micStartedCallback;
  JSFunction? _micAudioCallback;
  JSFunction? _micErrorCallback;

  // 预热的 WebSocket 连接
  web.WebSocket? _warmWs;
  Timer? _warmupTimer;

  GatewayStreamingAsrService({
    String? gatewayUrl,
    String service = 'vosk',
    String? wsPath,
    this.wsProtocols = const <String>[],
    this.capsWriterJsonProtocol = false,
  })  : service =
            service.trim().isEmpty ? 'vosk' : service.trim().toLowerCase(),
        gatewayUrl = gatewayUrl != null && gatewayUrl.trim().isNotEmpty
            ? gatewayUrl.trim()
            : _defaultAsrBaseUrl(service),
        wsPath = wsPath != null && wsPath.trim().isNotEmpty
            ? wsPath.trim()
            : _defaultAsrWsPath(service) {
    // 启动时预热 WebSocket 连接
    _preWarmConnection();
  }

  web.WebSocket _createWebSocket() {
    if (wsProtocols.isEmpty) {
      return web.WebSocket(_buildWsUrl());
    }
    return web.WebSocket(_buildWsUrl(), wsProtocols.first.toJS);
  }

  Future<void> _connectWebSocketForStreaming() async {
    if (_warmWs != null && _warmWs!.readyState == web.WebSocket.OPEN) {
      _ws = _warmWs;
      _warmWs = null;
      _warmupTimer?.cancel();
      return;
    }

    _warmWs?.close();
    _warmWs = null;
    _ws = _createWebSocket();
    _ws!.binaryType = 'arraybuffer';
    await _ws!.onOpen.first.timeout(const Duration(seconds: 5));
  }

  void _ensureMicBridge() {
    if (globalContext.has('roundTableStreamingAsrMic')) return;

    globalContext.callMethod<JSAny?>(
        'eval'.toJS,
        r'''
(function () {
  if (window.roundTableStreamingAsrMic) return;
  window.roundTableStreamingAsrMic = {
    nextId: 1,
    sessions: {},
    start: function (onStarted, onAudio, onError, sampleRate) {
      var id = this.nextId++;
      var self = this;
      var report = function (error) {
        var message = '';
        try {
          if (error && error.name) {
            message = error.name + (error.message ? ': ' + error.message : '');
          } else if (error && error.message) {
            message = error.message;
          } else {
            message = String(error || 'unknown error');
          }
        } catch (_) {
          message = 'unknown error';
        }
        try { onError(message); } catch (_) {}
      };

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        report('getUserMedia unavailable');
        return id;
      }

      var constraints = {
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true
        }
      };
      if (sampleRate) constraints.audio.sampleRate = sampleRate;

      navigator.mediaDevices.getUserMedia(constraints).then(function (stream) {
        try {
          var AudioContextClass = window.AudioContext || window.webkitAudioContext;
          if (!AudioContextClass) throw new Error('AudioContext unavailable');

          var audioContext;
          try {
            audioContext = new AudioContextClass(sampleRate ? { sampleRate: sampleRate } : undefined);
          } catch (_) {
            audioContext = new AudioContextClass();
          }

          var source = audioContext.createMediaStreamSource(stream);
          var processor = audioContext.createScriptProcessor(4096, 1, 1);
          processor.onaudioprocess = function (event) {
            try {
              var channelData = event.inputBuffer.getChannelData(0);
              if (channelData && channelData.length) onAudio(channelData);
            } catch (error) {
              report(error);
            }
          };

          source.connect(processor);
          processor.connect(audioContext.destination);
          self.sessions[id] = {
            stream: stream,
            audioContext: audioContext,
            source: source,
            processor: processor
          };

          if (audioContext.state === 'suspended' && audioContext.resume) {
            audioContext.resume().catch(function () {});
          }
          onStarted(id);
        } catch (error) {
          try { stream.getTracks().forEach(function (track) { track.stop(); }); } catch (_) {}
          report(error);
        }
      }).catch(report);

      return id;
    },
    stop: function (id) {
      var session = this.sessions[id];
      if (!session) return;
      delete this.sessions[id];
      try {
        if (session.processor) {
          session.processor.onaudioprocess = null;
          session.processor.disconnect();
        }
      } catch (_) {}
      try { if (session.source) session.source.disconnect(); } catch (_) {}
      try { if (session.audioContext) session.audioContext.close(); } catch (_) {}
      try {
        if (session.stream) {
          session.stream.getTracks().forEach(function (track) { track.stop(); });
        }
      } catch (_) {}
    }
  };
}());
'''
            .toJS);
  }

  Future<void> _startMicBridge() async {
    _ensureMicBridge();
    final bridge = globalContext['roundTableStreamingAsrMic'] as JSObject;
    final started = Completer<void>();

    _micStartedCallback = ((JSAny? sessionId) {
      final id = (sessionId as JSNumber?)?.toDartInt;
      if (id != null) {
        _micSessionId = id;
      }
      if (!started.isCompleted) started.complete();
    }).toJS;

    _micAudioCallback = ((JSAny? channelData) {
      try {
        final samples = _toFloat32Samples(channelData);
        if (samples == null || samples.isEmpty) {
          return;
        }
        if (capsWriterJsonProtocol) {
          _sendCapsWriterAudioList(samples);
        } else {
          _sendPcmAudioList(samples);
        }
      } catch (error) {
        if (!_controller.isClosed) {
          _controller.addError('${service.toUpperCase()} 音频块处理失败: $error');
        }
      }
    }).toJS;

    _micErrorCallback = ((JSAny? error) {
      final message = '麦克风流初始化失败: ${_jsAnyToString(error) ?? 'unknown error'}';
      if (!started.isCompleted) {
        started.completeError(StateError(message));
      } else if (!_controller.isClosed) {
        _controller.addError(message);
      }
    }).toJS;

    bridge.callMethodVarArgs<JSAny?>('start'.toJS, [
      _micStartedCallback,
      _micAudioCallback,
      _micErrorCallback,
      16000.toJS,
    ]);

    await started.future.timeout(const Duration(seconds: 8));
  }

  void _stopMicBridge() {
    final sessionId = _micSessionId;
    if (sessionId != null && globalContext.has('roundTableStreamingAsrMic')) {
      try {
        (globalContext['roundTableStreamingAsrMic'] as JSObject)
            .callMethod<JSAny?>('stop'.toJS, sessionId.toJS);
      } catch (_) {}
    }
    _micSessionId = null;
    _micStartedCallback = null;
    _micAudioCallback = null;
    _micErrorCallback = null;
  }

  String _describeStartError(Object error) {
    if (error is TimeoutException) {
      return '${service.toUpperCase()} 连接超时，请确认本地服务地址可达';
    }

    final raw = error.toString();
    if (raw.contains('createMediaStreamSource') &&
        raw.contains('MediaStream')) {
      return '麦克风流初始化失败，请刷新页面并重新授权麦克风后重试';
    }
    if (raw.contains('NotAllowedError')) {
      return '麦克风权限被拒绝，请允许浏览器访问麦克风';
    }
    if (raw.contains('NotFoundError')) {
      return '未检测到可用的麦克风设备';
    }
    if (raw.contains('NotReadableError')) {
      return '麦克风当前不可读，可能正被其他程序占用';
    }
    if (raw.contains('SecurityError')) {
      return '当前页面不允许访问麦克风，请使用 localhost 或受信任来源打开';
    }
    return '语音识别启动失败: $raw';
  }

  void _cleanupAfterStartFailure() {
    _stopMicBridge();
    try {
      _ws?.close();
    } catch (_) {}
    _ws = null;
  }

  String _buildWsUrl() {
    final normalizedPath = wsPath.startsWith('/') ? wsPath : '/$wsPath';
    final parsed = Uri.tryParse(gatewayUrl);
    if (parsed == null || parsed.host.isEmpty) {
      final wsBase = gatewayUrl
          .replaceFirst('https://', 'wss://')
          .replaceFirst('http://', 'ws://');
      return '$wsBase$normalizedPath';
    }
    final scheme = parsed.scheme == 'https' ? 'wss' : 'ws';
    return parsed
        .replace(
          scheme: scheme,
          path: normalizedPath,
          query: null,
          fragment: null,
        )
        .toString();
  }

  String? _jsAnyToString(JSAny? value) {
    if (value == null) return null;
    try {
      return (value as JSString).toDart;
    } catch (_) {
      return null;
    }
  }

  String? _messageEventText(web.MessageEvent event) =>
      _jsAnyToString(event.data);

  Float32List? _toFloat32Samples(JSAny? channelData) {
    if (channelData == null) return null;

    try {
      return (channelData as JSFloat32Array).toDart;
    } catch (_) {}

    try {
      final jsData = channelData as JSObject;
      final length = (jsData['length'] as JSNumber?)?.toDartInt ?? 0;
      if (length <= 0) {
        return null;
      }
      final samples = Float32List(length);
      for (var i = 0; i < length; i++) {
        final sample =
            (jsData.getProperty<JSAny?>(i.toJS) as JSNumber?)?.toDartDouble ??
                0.0;
        samples[i] = sample;
      }
      return samples;
    } catch (_) {
      return null;
    }
  }

  /// 预热 WebSocket 连接，减少首次录音延迟
  void _preWarmConnection() {
    if (_disposed) return;
    if (_warmWs != null &&
        (_warmWs!.readyState == web.WebSocket.OPEN ||
            _warmWs!.readyState == web.WebSocket.CONNECTING)) {
      return;
    }
    try {
      _warmWs = _createWebSocket();
      _warmWs!.binaryType = 'arraybuffer';
      // 30秒后关闭预热连接避免资源浪费
      _warmupTimer = Timer(const Duration(seconds: 30), () {
        _warmWs?.close();
        _warmWs = null;
      });
    } catch (_) {}
  }

  @override
  Future<void> warmup() async {
    _preWarmConnection();
  }

  @override
  bool get isAvailable => true;

  @override
  bool get isListening => _isListening;

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<AsrAudioCapture?> takeLastCapture() async => null;

  @override
  Future<void> startListening() async {
    if (_isListening || _disposed) return;

    try {
      _isListening = true;
      _stopCompleter = Completer<void>();
      _finalResultCompleter = Completer<void>();

      await _connectWebSocketForStreaming();
      await _startMicBridge();

      // 4. 监听 WebSocket 消息
      _ws!.onMessage.listen((event) {
        if (_controller.isClosed) return;
        try {
          final payload = _messageEventText(event);
          if (payload == null || payload.isEmpty) {
            return;
          }
          final data = jsonDecode(payload) as Map<String, dynamic>;
          if (data.containsKey('is_final')) {
            final isFinal = data['is_final'] == true;
            final text = ((data['text_accu'] as String?) ??
                    (data['text'] as String?) ??
                    '')
                .trim();
            if (text.isNotEmpty) {
              _controller.add(AsrResult(text: text, isFinal: isFinal));
            }
            if (isFinal && !(_finalResultCompleter?.isCompleted ?? true)) {
              _finalResultCompleter!.complete();
            }
            return;
          }

          final type = data['type'] as String? ?? '';
          final text = (data['text'] as String? ?? '').trim();
          if (type == 'error') {
            _controller.addError(data['error'] ?? '语音识别服务返回错误');
            if (!(_finalResultCompleter?.isCompleted ?? true)) {
              _finalResultCompleter!.complete();
            }
            return;
          }
          if (text.isEmpty) return;

          switch (type) {
            case 'partial':
              _controller.add(AsrResult(text: text, isFinal: false));
              break;
            case 'result':
            case 'final':
              _controller.add(AsrResult(text: text, isFinal: true));
              if (!(_finalResultCompleter?.isCompleted ?? true)) {
                _finalResultCompleter!.complete();
              }
              break;
          }
        } catch (_) {}
      });

      _ws!.onClose.listen((_) {
        _isListening = false;
        if (!(_finalResultCompleter?.isCompleted ?? true)) {
          _finalResultCompleter!.complete();
        }
        if (_stopCompleter != null && !_stopCompleter!.isCompleted) {
          _stopCompleter!.complete();
        }
      });

      _ws!.onError.listen((_) {
        _isListening = false;
        if (!(_finalResultCompleter?.isCompleted ?? true)) {
          _finalResultCompleter!.complete();
        }
      });
    } catch (e) {
      _isListening = false;
      _cleanupAfterStartFailure();
      final message = _describeStartError(e);
      if (!_controller.isClosed) {
        _controller.addError(message);
      }
      throw StateError(message);
    }
  }

  String _encodeFloat32ListBase64(Float32List channelData) {
    final byteData = ByteData(channelData.length * 4);
    for (var i = 0; i < channelData.length; i++) {
      final sample = channelData[i].clamp(-1.0, 1.0).toDouble();
      byteData.setFloat32(i * 4, sample, Endian.little);
    }
    return base64Encode(byteData.buffer.asUint8List());
  }

  String _newCapsWriterTaskId() =>
      'roundtable-${DateTime.now().microsecondsSinceEpoch}';

  void _sendCapsWriterAudioList(Float32List channelData) {
    if (_ws == null || _ws!.readyState != web.WebSocket.OPEN) return;
    if (channelData.isEmpty) return;
    _capsWriterTaskId ??= _newCapsWriterTaskId();
    if (_capsWriterStartSeconds <= 0) {
      _capsWriterStartSeconds = DateTime.now().millisecondsSinceEpoch / 1000.0;
    }

    _ws!.send(
      jsonEncode({
        'task_id': _capsWriterTaskId,
        'seg_duration': 60,
        'seg_overlap': 4,
        'is_final': false,
        'time_start': _capsWriterStartSeconds,
        'time_frame': DateTime.now().millisecondsSinceEpoch / 1000.0,
        'source': 'mic',
        'data': _encodeFloat32ListBase64(channelData),
        'context': '',
      }).toJS,
    );
  }

  void _sendPcmAudioList(Float32List channelData) {
    if (_ws == null || _ws!.readyState != web.WebSocket.OPEN) return;
    if (channelData.isEmpty) return;
    final int16Data = Int16List(channelData.length);
    for (var i = 0; i < channelData.length; i++) {
      final sample = channelData[i].clamp(-1.0, 1.0).toDouble();
      int16Data[i] = (sample * 32767).round();
    }
    _ws!.send(int16Data.toJS);
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening) return;

    try {
      _stopMicBridge();

      // 发送 eof 获取最终结果
      if (_ws != null && _ws!.readyState == web.WebSocket.OPEN) {
        if (service == 'vosk') {
          _ws!.send('eof'.toJS);
        } else if (capsWriterJsonProtocol) {
          _capsWriterTaskId ??= _newCapsWriterTaskId();
          if (_capsWriterStartSeconds <= 0) {
            _capsWriterStartSeconds =
                DateTime.now().millisecondsSinceEpoch / 1000.0;
          }
          _ws!.send(
            jsonEncode({
              'task_id': _capsWriterTaskId,
              'seg_duration': 60,
              'seg_overlap': 4,
              'is_final': true,
              'time_start': _capsWriterStartSeconds,
              'time_frame': DateTime.now().millisecondsSinceEpoch / 1000.0,
              'source': 'mic',
              'data': '',
              'context': '',
            }).toJS,
          );
        } else {
          _ws!.send(jsonEncode({'type': 'eof'}).toJS);
        }
        await _finalResultCompleter?.future.timeout(
          const Duration(milliseconds: 1800),
          onTimeout: () {},
        );
        // 等待服务端主动收尾，避免在最终文本返回前就关闭连接。
        await _stopCompleter?.future.timeout(
          const Duration(milliseconds: 1200),
          onTimeout: () {},
        );
      }
    } catch (_) {}

    _ws?.close();
    _ws = null;
    _isListening = false;
    _capsWriterTaskId = null;
    _capsWriterStartSeconds = 0;
  }

  @override
  Future<String> refineTranscript(String text) async {
    var v = text.trim();
    if (v.isEmpty) return '';
    // 本地简单后处理
    v = v.replaceAll(RegExp(r'\s+'), ' ');
    v = v.replaceAllMapped(
      RegExp(r'([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])'),
      (match) => match.group(1) ?? '',
    );
    v = v.replaceAll(RegExp(r'(我觉得){2,}'), '我觉得');
    v = v.replaceAll(RegExp(r'(然后){2,}'), '然后');
    if (!RegExp(r'[。！？!?]$').hasMatch(v)) {
      v = '$v。';
    }
    // 如果后端可用，也尝试服务端润色
    try {
      final serverUrl = Uri.base.origin;
      final dio = Dio(BaseOptions(
        baseUrl: serverUrl,
        connectTimeout: const Duration(seconds: 3),
        receiveTimeout: const Duration(seconds: 5),
      ));
      final response = await dio.post(
        '/api/v1/voice/asr/refine',
        data: {'text': v},
      );
      final refined = (response.data['text'] as String?)?.trim();
      if (refined != null && refined.isNotEmpty) return refined;
    } catch (_) {}
    return v;
  }

  @override
  void dispose() {
    _disposed = true;
    _warmupTimer?.cancel();
    _warmWs?.close();
    stopListening();
    _controller.close();
  }
}
