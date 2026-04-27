/// Gateway 流式语音服务实现
///
/// 通过 voice-services gateway (端口 6666) 直连 Vosk/CapsWriter 本地 ASR/TTS。
/// ASR 支持 WebSocket 流式识别（低延迟），TTS 通过 gateway 代理合成。
///
/// Gateway routes:
///   POST /asr/capswriter - CapsWriter ASR
///   POST /asr/vosk       - Vosk ASR
///   POST /tts/vibevoice  - VibeVoice TTS
///   POST /tts/fireredtts - FireRedTTS TTS
///   POST /tts/openvoice  - OpenVoice TTS
///   WS   /asr/stream     - 流式 ASR (Vosk WebSocket)
// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
library;

import 'dart:html' as html;
import 'dart:async';
import 'dart:convert';
import 'dart:js' as js;
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'speech_contract.dart';

/// 默认 gateway 地址
const _defaultGatewayUrl = 'http://localhost:6666';

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

  html.WebSocket? _ws;
  html.MediaStream? _mediaStream;
  js.JsObject? _audioContext;
  js.JsObject? _processorNode;
  js.JsObject? _sourceNode;
  Completer<void>? _stopCompleter;
  Completer<void>? _finalResultCompleter;
  bool _disposed = false;
  String? _capsWriterTaskId;
  double _capsWriterStartSeconds = 0;
  int? _capsWriterMicSessionId;
  js.JsFunction? _capsWriterMicStartedCallback;
  js.JsFunction? _capsWriterMicAudioCallback;
  js.JsFunction? _capsWriterMicErrorCallback;

  // 预热的 WebSocket 连接
  html.WebSocket? _warmWs;
  Timer? _warmupTimer;

  GatewayStreamingAsrService({
    this.gatewayUrl = _defaultGatewayUrl,
    this.service = 'vosk',
    this.wsPath = '/asr/stream',
    this.wsProtocols = const <String>[],
    this.capsWriterJsonProtocol = false,
  }) {
    // 启动时预热 WebSocket 连接
    _preWarmConnection();
  }

  html.WebSocket _createWebSocket() {
    if (wsProtocols.isEmpty) {
      return html.WebSocket(_buildWsUrl());
    }
    return html.WebSocket(_buildWsUrl(), wsProtocols);
  }

  Future<void> _connectWebSocketForStreaming() async {
    if (_warmWs != null && _warmWs!.readyState == html.WebSocket.OPEN) {
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

  void _ensureCapsWriterMicBridge() {
    if (js.context.hasProperty('roundTableCapsWriterMic')) return;

    js.context.callMethod('eval', [
      r'''
(function () {
  if (window.roundTableCapsWriterMic) return;
  window.roundTableCapsWriterMic = {
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
    ]);
  }

  Future<void> _startCapsWriterMicBridge() async {
    _ensureCapsWriterMicBridge();
    final bridge = js.context['roundTableCapsWriterMic'] as js.JsObject;
    final started = Completer<void>();

    _capsWriterMicStartedCallback =
        js.JsFunction.withThis((thisArg, dynamic sessionId) {
      if (sessionId is num) {
        _capsWriterMicSessionId = sessionId.toInt();
      }
      if (!started.isCompleted) started.complete();
    });

    _capsWriterMicAudioCallback =
        js.JsFunction.withThis((thisArg, dynamic channelData) {
      try {
        if (channelData is js.JsObject) {
          final length = (channelData['length'] as num).toInt();
          if (length > 0) _sendCapsWriterAudioChunk(channelData, length);
        } else if (channelData is Float32List) {
          _sendCapsWriterAudioList(channelData);
        }
      } catch (error) {
        if (!_controller.isClosed) {
          _controller.addError('CapsWriter 音频块处理失败: $error');
        }
      }
    });

    _capsWriterMicErrorCallback =
        js.JsFunction.withThis((thisArg, dynamic error) {
      final message = '麦克风流初始化失败: ${error?.toString() ?? 'unknown error'}';
      if (!started.isCompleted) {
        started.completeError(StateError(message));
      } else if (!_controller.isClosed) {
        _controller.addError(message);
      }
    });

    bridge.callMethod('start', [
      _capsWriterMicStartedCallback,
      _capsWriterMicAudioCallback,
      _capsWriterMicErrorCallback,
      16000,
    ]);

    await started.future.timeout(const Duration(seconds: 8));
  }

  void _stopCapsWriterMicBridge() {
    final sessionId = _capsWriterMicSessionId;
    if (sessionId != null &&
        js.context.hasProperty('roundTableCapsWriterMic')) {
      try {
        (js.context['roundTableCapsWriterMic'] as js.JsObject)
            .callMethod('stop', [sessionId]);
      } catch (_) {}
    }
    _capsWriterMicSessionId = null;
    _capsWriterMicStartedCallback = null;
    _capsWriterMicAudioCallback = null;
    _capsWriterMicErrorCallback = null;
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
    _stopCapsWriterMicBridge();
    try {
      _processorNode?.callMethod('disconnect');
    } catch (_) {}
    try {
      _sourceNode?.callMethod('disconnect');
    } catch (_) {}
    try {
      _audioContext?.callMethod('close');
    } catch (_) {}
    try {
      _mediaStream?.getTracks().forEach((track) => track.stop());
    } catch (_) {}
    try {
      _ws?.close();
    } catch (_) {}

    _processorNode = null;
    _sourceNode = null;
    _audioContext = null;
    _mediaStream = null;
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

  /// 预热 WebSocket 连接，减少首次录音延迟
  void _preWarmConnection() {
    if (_disposed) return;
    if (_warmWs != null &&
        (_warmWs!.readyState == html.WebSocket.OPEN ||
            _warmWs!.readyState == html.WebSocket.CONNECTING)) {
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

      if (capsWriterJsonProtocol) {
        await _connectWebSocketForStreaming();
        await _startCapsWriterMicBridge();
      } else {
        // 1. 获取麦克风
        final mediaDevices = html.window.navigator.mediaDevices;
        if (mediaDevices == null) {
          throw StateError('当前浏览器不支持麦克风采集');
        }
        _mediaStream = await mediaDevices.getUserMedia({
          'audio': {
            'sampleRate': 16000,
            'channelCount': 1,
            'echoCancellation': true,
            'noiseSuppression': true,
          }
        });
        if (_mediaStream == null || _mediaStream!.getAudioTracks().isEmpty) {
          throw StateError('未获取到有效的麦克风音轨');
        }

        await _connectWebSocketForStreaming();

        // 3. 设置 AudioContext + ScriptProcessor 来获取 PCM 数据
        _setupAudioPipeline();
      }

      // 4. 监听 WebSocket 消息
      _ws!.onMessage.listen((event) {
        if (_controller.isClosed) return;
        try {
          final data = jsonDecode(event.data as String) as Map<String, dynamic>;
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

  String _encodeFloat32Base64(js.JsObject channelData, int length) {
    final byteData = ByteData(length * 4);
    for (var i = 0; i < length; i++) {
      final sample =
          (channelData[i] as num).toDouble().clamp(-1.0, 1.0).toDouble();
      byteData.setFloat32(i * 4, sample, Endian.little);
    }
    return base64Encode(byteData.buffer.asUint8List());
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

  void _sendCapsWriterAudioChunk(js.JsObject channelData, int length) {
    if (_ws == null || _ws!.readyState != html.WebSocket.OPEN) return;
    _capsWriterTaskId ??= _newCapsWriterTaskId();
    if (_capsWriterStartSeconds <= 0) {
      _capsWriterStartSeconds = DateTime.now().millisecondsSinceEpoch / 1000.0;
    }

    _ws!.sendString(jsonEncode({
      'task_id': _capsWriterTaskId,
      'seg_duration': 60,
      'seg_overlap': 4,
      'is_final': false,
      'time_start': _capsWriterStartSeconds,
      'time_frame': DateTime.now().millisecondsSinceEpoch / 1000.0,
      'source': 'mic',
      'data': _encodeFloat32Base64(channelData, length),
      'context': '',
    }));
  }

  void _sendCapsWriterAudioList(Float32List channelData) {
    if (_ws == null || _ws!.readyState != html.WebSocket.OPEN) return;
    if (channelData.isEmpty) return;
    _capsWriterTaskId ??= _newCapsWriterTaskId();
    if (_capsWriterStartSeconds <= 0) {
      _capsWriterStartSeconds = DateTime.now().millisecondsSinceEpoch / 1000.0;
    }

    _ws!.sendString(jsonEncode({
      'task_id': _capsWriterTaskId,
      'seg_duration': 60,
      'seg_overlap': 4,
      'is_final': false,
      'time_start': _capsWriterStartSeconds,
      'time_frame': DateTime.now().millisecondsSinceEpoch / 1000.0,
      'source': 'mic',
      'data': _encodeFloat32ListBase64(channelData),
      'context': '',
    }));
  }

  /// 构建 Web Audio 管道：Mic → AudioContext → ScriptProcessor → WebSocket
  void _setupAudioPipeline() {
    final mediaStream = _mediaStream;
    if (mediaStream == null) {
      throw StateError('麦克风流初始化失败');
    }

    final ctx = js.context;

    // 创建 AudioContext (16kHz)
    final audioContextClass = ctx.hasProperty('AudioContext')
        ? ctx['AudioContext']
        : ctx['webkitAudioContext'];
    try {
      _audioContext = js.JsObject(audioContextClass as js.JsFunction, [
        js.JsObject.jsify({'sampleRate': 16000})
      ]);
    } catch (_) {
      _audioContext = js.JsObject(audioContextClass as js.JsFunction);
    }

    // 创建 MediaStreamSource
    try {
      _sourceNode = _audioContext!.callMethod(
        'createMediaStreamSource',
        [mediaStream],
      );
    } catch (firstError) {
      try {
        _sourceNode = _audioContext!.callMethod(
          'createMediaStreamSource',
          [js.JsObject.fromBrowserObject(mediaStream)],
        );
      } catch (secondError) {
        try {
          final createMethod = _audioContext!['createMediaStreamSource'];
          if (createMethod is! js.JsFunction) {
            throw StateError('createMediaStreamSource is not callable');
          }
          _sourceNode = createMethod.apply(
            [mediaStream],
            thisArg: _audioContext,
          ) as js.JsObject;
        } catch (_) {
          throw StateError(
            'createMediaStreamSource failed: $firstError / $secondError',
          );
        }
      }
    }

    // 创建 ScriptProcessor (bufferSize=4096, inputChannels=1, outputChannels=1)
    _processorNode = _audioContext!.callMethod(
      'createScriptProcessor',
      [4096, 1, 1],
    );

    // onaudioprocess：将 float32 转 int16 PCM 并发送
    _processorNode!['onaudioprocess'] =
        js.JsFunction.withThis((thisArg, event) {
      if (_ws == null || _ws!.readyState != html.WebSocket.OPEN) return;

      final inputBuffer = (event as js.JsObject)['inputBuffer'];
      final channelData = inputBuffer.callMethod('getChannelData', [0]);

      // 获取 Float32Array 的长度和数据
      final length = (channelData['length'] as num).toInt();
      if (capsWriterJsonProtocol) {
        _sendCapsWriterAudioChunk(channelData, length);
        return;
      }

      final int16Data = Int16List(length);

      for (var i = 0; i < length; i++) {
        final sample = (channelData[i] as num).toDouble();
        // Clamp to [-1, 1] and convert to int16
        final clamped = sample.clamp(-1.0, 1.0);
        int16Data[i] = (clamped * 32767).round();
      }

      // 发送 PCM 二进制数据
      _ws!.sendTypedData(int16Data);
    });

    // 连接管道
    _sourceNode!.callMethod('connect', [_processorNode]);
    _processorNode!.callMethod('connect', [_audioContext!['destination']]);
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening) return;

    try {
      // 断开音频管道
      _stopCapsWriterMicBridge();
      _processorNode?.callMethod('disconnect');
      _sourceNode?.callMethod('disconnect');
      _audioContext?.callMethod('close');
      _processorNode = null;
      _sourceNode = null;
      _audioContext = null;

      // 停止麦克风
      _mediaStream?.getTracks().forEach((track) => track.stop());
      _mediaStream = null;

      // 发送 eof 获取最终结果
      if (_ws != null && _ws!.readyState == html.WebSocket.OPEN) {
        if (service == 'vosk') {
          _ws!.sendString('eof');
        } else if (capsWriterJsonProtocol) {
          _capsWriterTaskId ??= _newCapsWriterTaskId();
          if (_capsWriterStartSeconds <= 0) {
            _capsWriterStartSeconds =
                DateTime.now().millisecondsSinceEpoch / 1000.0;
          }
          _ws!.sendString(jsonEncode({
            'task_id': _capsWriterTaskId,
            'seg_duration': 60,
            'seg_overlap': 4,
            'is_final': true,
            'time_start': _capsWriterStartSeconds,
            'time_frame': DateTime.now().millisecondsSinceEpoch / 1000.0,
            'source': 'mic',
            'data': '',
            'context': '',
          }));
        } else {
          _ws!.sendString(jsonEncode({'type': 'eof'}));
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

/// Gateway TTS 实现：通过 gateway 代理访问本地 TTS 服务
///
/// 支持: vibevoice, fireredtts, openvoice
/// TTS 预加载：可提前合成下一段文本
class GatewayTtsService implements TtsService {
  final String gatewayUrl;
  final String service; // 'vibevoice', 'fireredtts', 'openvoice'
  final Dio _dio;
  bool _isSpeaking = false;
  html.AudioElement? _audioElement;
  Completer<void>? _pendingCompleter;
  String? _activeObjectUrl;

  // TTS 预加载缓存
  final Map<String, Uint8List> _prefetchCache = {};
  final Map<String, Future<void>> _prefetchInFlight = {};
  static const int _maxCacheSize = 5;
  int _prefetchRequested = 0;
  int _prefetchHit = 0;
  int _prefetchMiss = 0;

  GatewayTtsService({
    this.gatewayUrl = _defaultGatewayUrl,
    this.service = 'vibevoice',
  }) : _dio = Dio(BaseOptions(
          baseUrl: gatewayUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 10),
          responseType: ResponseType.bytes,
        ));

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  TtsPerfSnapshot getPerfSnapshot() {
    return TtsPerfSnapshot(
      prefetchHit: _prefetchHit,
      prefetchMiss: _prefetchMiss,
      prefetchRequested: _prefetchRequested,
    );
  }

  String _cacheKey(String text, {String? voice}) {
    return '${voice ?? 'default'}::$text';
  }

  /// 预加载一段文本的 TTS（后台合成，缓存结果）
  @override
  Future<void> prefetch(String text, {String? voice}) async {
    _prefetchRequested += 1;
    final key = _cacheKey(text, voice: voice);
    if (_prefetchCache.containsKey(key)) return;
    final existing = _prefetchInFlight[key];
    if (existing != null) {
      await existing;
      return;
    }

    final job = () async {
      try {
        final audioBytes = await _synthesize(text, voice: voice);
        if (audioBytes.isNotEmpty) {
          // 限制缓存大小
          if (_prefetchCache.length >= _maxCacheSize) {
            _prefetchCache.remove(_prefetchCache.keys.first);
          }
          _prefetchCache[key] = audioBytes;
        }
      } catch (_) {
        // Ignore prefetch failures; playback path will retry on demand.
      } finally {
        _prefetchInFlight.remove(key);
      }
    }();

    _prefetchInFlight[key] = job;
    await job;
  }

  @override
  Future<void> prefetchBatch(
    List<({String text, String? voice})> items, {
    int maxConcurrent = 2,
  }) async {
    if (items.isEmpty) return;

    final queue = List<({String text, String? voice})>.from(items);
    final workers = maxConcurrent.clamp(1, 4);

    Future<void> worker() async {
      while (queue.isNotEmpty) {
        final item = queue.removeLast();
        await prefetch(item.text, voice: item.voice);
      }
    }

    await Future.wait(List.generate(workers, (_) => worker()));
  }

  Future<Uint8List> _synthesize(String text, {String? voice}) async {
    final response = await _dio.post<List<int>>(
      '/tts/$service',
      data: {
        'text': text,
        'speaker': voice ?? 'default',
      },
      options: Options(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(response.data!);
  }

  @override
  Future<void> speak(
    String text, {
    String? voice,
    double rate = 1.0,
    void Function()? onStart,
  }) async {
    await stop();

    try {
      _isSpeaking = true;
      var started = false;

      // 优先使用预加载缓存
      Uint8List audioBytes;
      final key = _cacheKey(text, voice: voice);
      if (_prefetchCache.containsKey(key)) {
        _prefetchHit += 1;
        audioBytes = _prefetchCache.remove(key)!;
      } else {
        _prefetchMiss += 1;
        audioBytes = await _synthesize(text, voice: voice);
      }

      // Gateway 返回 WAV 格式
      final blob = html.Blob([audioBytes], 'audio/wav');
      final url = html.Url.createObjectUrlFromBlob(blob);
      _activeObjectUrl = url;

      _audioElement = html.AudioElement()
        ..src = url
        ..playbackRate = rate
        ..autoplay = true;

      final completer = Completer<void>();
      _pendingCompleter = completer;

      void markStarted() {
        if (started) return;
        started = true;
        onStart?.call();
      }

      _audioElement!.onPlaying.listen((_) => markStarted());

      _audioElement!.onEnded.listen((_) {
        _isSpeaking = false;
        if (_activeObjectUrl == url) {
          _activeObjectUrl = null;
        }
        html.Url.revokeObjectUrl(url);
        _audioElement = null;
        _pendingCompleter = null;
        if (!completer.isCompleted) completer.complete();
      });

      _audioElement!.onError.listen((_) {
        _isSpeaking = false;
        if (_activeObjectUrl == url) {
          _activeObjectUrl = null;
        }
        html.Url.revokeObjectUrl(url);
        _audioElement = null;
        _pendingCompleter = null;
        if (!completer.isCompleted) {
          completer.completeError('TTS playback error');
        }
      });

      await _audioElement!.play();
      markStarted();
      await completer.future;
    } catch (e) {
      _isSpeaking = false;
      rethrow;
    }
  }

  @override
  Future<void> stop() async {
    _isSpeaking = false;
    _audioElement?.pause();
    _audioElement = null;
    final url = _activeObjectUrl;
    _activeObjectUrl = null;
    if (url != null) {
      html.Url.revokeObjectUrl(url);
    }
    if (_pendingCompleter != null && !_pendingCompleter!.isCompleted) {
      _pendingCompleter!.complete();
      _pendingCompleter = null;
    }
  }

  @override
  void dispose() {
    stop();
    _prefetchCache.clear();
    _dio.close();
  }
}
