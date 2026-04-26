/// FunASR 直连流式 ASR 客户端
///
/// 浏览器直接连接 FunASR WebSocket 服务（默认 ws://localhost:10095），
/// 使用 Web Audio API 采集麦克风 PCM（16kHz 单声道 int16），逐块发送，
/// 实时获取 text_online（在线增量）与 text_offline（最终整句）。
///
/// 对应本项目需求 5b："从 LOCALHOST:9999 面板上找到合适作为流式语音识别的
/// 工具，然后改造本项目为流式输入输出"。
// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
library;

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:js' as js;
import 'dart:typed_data';

import 'speech_contract.dart';

class FunasrStreamingAsrService implements AsrService {
  final String wsUrl;
  final String mode;
  final bool itn;
  final int chunkIntervalMs;

  FunasrStreamingAsrService({
    this.wsUrl = 'ws://localhost:10095',
    this.mode = '2pass',
    this.itn = true,
    this.chunkIntervalMs = 60,
  });

  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();
  bool _isListening = false;
  bool _available = true;

  html.WebSocket? _ws;
  html.MediaStream? _mediaStream;
  js.JsObject? _audioContext;
  js.JsObject? _sourceNode;
  js.JsObject? _processorNode;
  Completer<void>? _finalResultCompleter;

  final StringBuffer _onlineBuf = StringBuffer();
  String _lastOffline = '';

  @override
  bool get isAvailable => _available;

  @override
  bool get isListening => _isListening;

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<AsrAudioCapture?> takeLastCapture() async => null;

  @override
  Future<void> warmup() async {
    try {
      final probe = html.WebSocket(wsUrl, 'binary');
      final c = Completer<bool>();
      probe.onOpen.first.then((_) {
        if (!c.isCompleted) c.complete(true);
        try {
          probe.close();
        } catch (_) {}
      });
      probe.onError.first.then((_) {
        if (!c.isCompleted) c.complete(false);
      });
      _available = await c.future
          .timeout(const Duration(seconds: 2), onTimeout: () => false);
    } catch (_) {
      _available = false;
    }
  }

  @override
  Future<void> startListening() async {
    if (_isListening) return;
    _onlineBuf.clear();
    _lastOffline = '';
    _finalResultCompleter = Completer<void>();

    try {
      final md = html.window.navigator.mediaDevices;
      if (md == null) {
        _controller.addError('浏览器不支持麦克风访问');
        return;
      }
      _mediaStream = await md.getUserMedia({
        'audio': {
          'channelCount': 1,
          'echoCancellation': true,
          'noiseSuppression': true,
        }
      });

      _ws = html.WebSocket(wsUrl, 'binary');
      _ws!.binaryType = 'arraybuffer';

      final openC = Completer<void>();
      _ws!.onOpen.first.then((_) {
        if (!openC.isCompleted) openC.complete();
      });
      _ws!.onError.first.then((_) {
        if (!openC.isCompleted) {
          openC.completeError('FunASR WebSocket 连接失败');
        }
        if (!_controller.isClosed) {
          _controller.addError('FunASR WebSocket 错误');
        }
      });
      _ws!.onMessage.listen(_onWsMessage);
      _ws!.onClose.listen((_) {
        _isListening = false;
        if (!(_finalResultCompleter?.isCompleted ?? true)) {
          _finalResultCompleter!.complete();
        }
      });

      await openC.future.timeout(const Duration(seconds: 5));

      _ws!.sendString(jsonEncode({
        'chunk_size': [5, 10, 5],
        'wav_name': 'roundtable',
        'is_speaking': true,
        'chunk_interval': chunkIntervalMs ~/ 10,
        'itn': itn,
        'mode': mode,
        'wav_format': 'PCM',
        'audio_fs': 16000,
      }));

      _setupAudioPipeline();
      _isListening = true;
    } catch (e) {
      await _cleanupAudio();
      _isListening = false;
      if (!_controller.isClosed) {
        _controller.addError('流式 ASR 启动失败: $e');
      }
    }
  }

  void _setupAudioPipeline() {
    final ctx = js.context;
    final audioContextClass = ctx.hasProperty('AudioContext')
        ? ctx['AudioContext']
        : ctx['webkitAudioContext'];
    _audioContext = js.JsObject(audioContextClass as js.JsFunction, [
      js.JsObject.jsify({'sampleRate': 16000})
    ]);
    _sourceNode = _audioContext!.callMethod(
      'createMediaStreamSource',
      [js.JsObject.fromBrowserObject(_mediaStream!)],
    );
    _processorNode = _audioContext!.callMethod(
      'createScriptProcessor',
      [4096, 1, 1],
    );

    _processorNode!['onaudioprocess'] =
        js.JsFunction.withThis((thisArg, event) {
      if (_ws == null || _ws!.readyState != html.WebSocket.OPEN) return;
      final inputBuffer = (event as js.JsObject)['inputBuffer'];
      final channelData = inputBuffer.callMethod('getChannelData', [0]);
      final length = (channelData['length'] as num).toInt();
      final int16Data = Int16List(length);
      for (var i = 0; i < length; i++) {
        final s = (channelData[i] as num).toDouble().clamp(-1.0, 1.0);
        int16Data[i] = (s * 32767).round();
      }
      try {
        _ws!.sendTypedData(int16Data);
      } catch (_) {}
    });

    _sourceNode!.callMethod('connect', [_processorNode]);
    _processorNode!.callMethod('connect', [_audioContext!['destination']]);
  }

  void _onWsMessage(html.MessageEvent ev) {
    final data = ev.data;
    if (data is! String) return;
    Map<String, dynamic> payload;
    try {
      payload = jsonDecode(data) as Map<String, dynamic>;
    } catch (_) {
      return;
    }

    final modeField = (payload['mode'] ?? '').toString();
    final text = (payload['text'] ?? '').toString();
    final isFinal = payload['is_final'] == true;

    if (modeField.contains('online') && text.isNotEmpty) {
      _onlineBuf.write(text);
      final merged = '$_lastOffline${_onlineBuf.toString()}';
      if (!_controller.isClosed) {
        _controller.add(AsrResult(text: merged, isFinal: false));
      }
    } else if (modeField.contains('offline') || modeField.contains('2pass')) {
      if (text.isNotEmpty) {
        _lastOffline = '$_lastOffline$text';
        _onlineBuf.clear();
        if (!_controller.isClosed) {
          _controller.add(AsrResult(text: _lastOffline, isFinal: true));
        }
        if (!(_finalResultCompleter?.isCompleted ?? true)) {
          _finalResultCompleter!.complete();
        }
      } else if (isFinal && _lastOffline.isNotEmpty) {
        if (!_controller.isClosed) {
          _controller.add(AsrResult(text: _lastOffline, isFinal: true));
        }
        if (!(_finalResultCompleter?.isCompleted ?? true)) {
          _finalResultCompleter!.complete();
        }
      }
    }
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening) return;
    _isListening = false;

    try {
      if (_ws?.readyState == html.WebSocket.OPEN) {
        _ws!.sendString(jsonEncode({'is_speaking': false}));
      }
    } catch (_) {}

    await _finalResultCompleter?.future.timeout(
      const Duration(milliseconds: 1800),
      onTimeout: () {},
    );
    await Future.delayed(const Duration(milliseconds: 150));
    await _cleanupAudio();

    try {
      _ws?.close();
    } catch (_) {}
    _ws = null;
  }

  Future<void> _cleanupAudio() async {
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
      _mediaStream?.getTracks().forEach((t) => t.stop());
    } catch (_) {}
    _processorNode = null;
    _sourceNode = null;
    _audioContext = null;
    _mediaStream = null;
  }

  @override
  Future<String> refineTranscript(String text) async => text.trim();

  @override
  void dispose() {
    stopListening();
    if (!_controller.isClosed) _controller.close();
  }
}
