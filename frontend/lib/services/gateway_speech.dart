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
  bool _isListening = false;
  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();

  html.WebSocket? _ws;
  html.MediaStream? _mediaStream;
  js.JsObject? _audioContext;
  js.JsObject? _processorNode;
  js.JsObject? _sourceNode;
  Completer<void>? _stopCompleter;
  bool _disposed = false;

  // 预热的 WebSocket 连接
  html.WebSocket? _warmWs;
  Timer? _warmupTimer;

  GatewayStreamingAsrService({
    this.gatewayUrl = _defaultGatewayUrl,
    this.service = 'vosk',
  }) {
    // 启动时预热 WebSocket 连接
    _preWarmConnection();
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
      final wsUrl = gatewayUrl.replaceFirst('http', 'ws');
      _warmWs = html.WebSocket('$wsUrl/asr/stream');
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
  Future<void> startListening() async {
    if (_isListening || _disposed) return;

    try {
      _isListening = true;
      _stopCompleter = Completer<void>();

      // 1. 获取麦克风
      final mediaDevices = html.window.navigator.mediaDevices;
      if (mediaDevices == null) {
        _isListening = false;
        return;
      }
      _mediaStream = await mediaDevices.getUserMedia({
        'audio': {
          'sampleRate': 16000,
          'channelCount': 1,
          'echoCancellation': true,
          'noiseSuppression': true,
        }
      });

      // 2. 连接 WebSocket（复用预热连接或新建）
      if (_warmWs != null && _warmWs!.readyState == html.WebSocket.OPEN) {
        _ws = _warmWs;
        _warmWs = null;
        _warmupTimer?.cancel();
      } else {
        _warmWs?.close();
        _warmWs = null;
        final wsUrl = gatewayUrl.replaceFirst('http', 'ws');
        _ws = html.WebSocket('$wsUrl/asr/stream');
        _ws!.binaryType = 'arraybuffer';
        // 等待连接打开
        await _ws!.onOpen.first.timeout(const Duration(seconds: 5));
      }

      // 3. 设置 AudioContext + ScriptProcessor 来获取 PCM 数据
      _setupAudioPipeline();

      // 4. 监听 WebSocket 消息
      _ws!.onMessage.listen((event) {
        if (_controller.isClosed) return;
        try {
          final data = jsonDecode(event.data as String) as Map<String, dynamic>;
          final type = data['type'] as String? ?? '';
          final text = (data['text'] as String? ?? '').trim();
          if (text.isEmpty) return;

          switch (type) {
            case 'partial':
              _controller.add(AsrResult(text: text, isFinal: false));
              break;
            case 'result':
            case 'final':
              _controller.add(AsrResult(text: text, isFinal: true));
              break;
          }
        } catch (_) {}
      });

      _ws!.onClose.listen((_) {
        _isListening = false;
        if (_stopCompleter != null && !_stopCompleter!.isCompleted) {
          _stopCompleter!.complete();
        }
      });

      _ws!.onError.listen((_) {
        _isListening = false;
      });
    } catch (e) {
      _isListening = false;
    }
  }

  /// 构建 Web Audio 管道：Mic → AudioContext → ScriptProcessor → PCM → WebSocket
  void _setupAudioPipeline() {
    final ctx = js.context;

    // 创建 AudioContext (16kHz)
    final audioContextClass = ctx.hasProperty('AudioContext')
        ? ctx['AudioContext']
        : ctx['webkitAudioContext'];
    _audioContext = js.JsObject(audioContextClass as js.JsFunction, [
      js.JsObject.jsify({'sampleRate': 16000})
    ]);

    // 创建 MediaStreamSource
    _sourceNode = _audioContext!.callMethod(
      'createMediaStreamSource',
      [js.JsObject.fromBrowserObject(_mediaStream!)],
    );

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
        _ws!.sendString('eof');
        // 等待最终结果(最多2秒)
        await _stopCompleter?.future.timeout(
          const Duration(seconds: 2),
          onTimeout: () {},
        );
      }
    } catch (_) {}

    _ws?.close();
    _ws = null;
    _isListening = false;
  }

  @override
  Future<String> refineTranscript(String text) async {
    var v = text.trim();
    if (v.isEmpty) return '';
    // 本地简单后处理
    v = v.replaceAll(RegExp(r'\s+'), ' ');
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
          receiveTimeout: const Duration(seconds: 60),
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
  Future<void> speak(String text, {String? voice, double rate = 1.0}) async {
    await stop();

    try {
      _isSpeaking = true;

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

      _audioElement = html.AudioElement()
        ..src = url
        ..playbackRate = rate
        ..autoplay = true;

      final completer = Completer<void>();

      _audioElement!.onEnded.listen((_) {
        _isSpeaking = false;
        html.Url.revokeObjectUrl(url);
        if (!completer.isCompleted) completer.complete();
      });

      _audioElement!.onError.listen((_) {
        _isSpeaking = false;
        html.Url.revokeObjectUrl(url);
        if (!completer.isCompleted) {
          completer.completeError('TTS playback error');
        }
      });

      await _audioElement!.play();
      await completer.future;
    } catch (e) {
      _isSpeaking = false;
    }
  }

  @override
  Future<void> stop() async {
    _isSpeaking = false;
    _audioElement?.pause();
    _audioElement = null;
  }

  @override
  void dispose() {
    stop();
    _prefetchCache.clear();
    _dio.close();
  }
}
