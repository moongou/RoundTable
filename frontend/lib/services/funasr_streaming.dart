/// FunASR 直连流式 ASR 客户端
///
/// 浏览器直接连接 FunASR WebSocket 服务（默认 ws://localhost:10095），
/// 使用 Web Audio API 采集麦克风 PCM（16kHz 单声道 int16），逐块发送，
/// 实时获取 text_online（在线增量）与 text_offline（最终整句）。
///
/// 对应本项目需求 5b："从 LOCALHOST:9999 面板上找到合适作为流式语音识别的
/// 工具，然后改造本项目为流式输入输出"。
library;

import 'dart:async';
import 'dart:convert';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

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

  web.WebSocket? _ws;
  web.MediaStream? _mediaStream;
  JSObject? _audioContext;
  JSObject? _sourceNode;
  JSObject? _processorNode;
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
      final probe = web.WebSocket(wsUrl, 'binary'.toJS);
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
      _mediaStream = await web.window.navigator.mediaDevices
          .getUserMedia(web.MediaStreamConstraints(audio: true.toJS))
          .toDart;

      _ws = web.WebSocket(wsUrl, 'binary'.toJS);
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

      _ws!.send(
        jsonEncode({
          'chunk_size': [5, 10, 5],
          'wav_name': 'roundtable',
          'is_speaking': true,
          'chunk_interval': chunkIntervalMs ~/ 10,
          'itn': itn,
          'mode': mode,
          'wav_format': 'PCM',
          'audio_fs': 16000,
        }).toJS,
      );

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
    final ctx = globalContext;
    JSFunction? audioContextCtor;
    try {
      audioContextCtor = (ctx.has('AudioContext')
          ? ctx['AudioContext']
          : ctx['webkitAudioContext']) as JSFunction?;
    } catch (_) {
      audioContextCtor = null;
    }
    if (audioContextCtor == null) {
      throw StateError('浏览器不支持 AudioContext');
    }

    JSFunction? objectCtor;
    try {
      objectCtor = ctx['Object'] as JSFunction?;
    } catch (_) {
      objectCtor = null;
    }
    JSObject? options;
    if (objectCtor != null) {
      options = objectCtor.callAsConstructor<JSObject>();
      options['sampleRate'] = 16000.toJS;
    }

    _audioContext = options == null
        ? audioContextCtor.callAsConstructor<JSObject>()
        : audioContextCtor.callAsConstructor<JSObject>(options);

    if (_mediaStream == null) {
      throw StateError('麦克风流未初始化');
    }

    _sourceNode = _audioContext!.callMethod<JSObject>(
      'createMediaStreamSource'.toJS,
      _mediaStream!,
    );
    _processorNode = _audioContext!.callMethod<JSObject>(
      'createScriptProcessor'.toJS,
      4096.toJS,
      1.toJS,
      1.toJS,
    );

    _processorNode!['onaudioprocess'] = ((JSAny? event) {
      if (_ws == null || _ws!.readyState != web.WebSocket.OPEN) {
        return;
      }
      JSObject? eventObj;
      JSObject? inputBuffer;
      JSFloat32Array? channelData;
      try {
        eventObj = event as JSObject?;
        inputBuffer = eventObj?['inputBuffer'] as JSObject?;
        channelData = inputBuffer?.callMethod<JSAny?>(
            'getChannelData'.toJS, 0.toJS) as JSFloat32Array?;
      } catch (_) {
        channelData = null;
      }
      if (channelData == null) {
        return;
      }

      final samples = channelData.toDart;
      final int16Data = Int16List(samples.length);
      for (var i = 0; i < samples.length; i++) {
        final s = samples[i].clamp(-1.0, 1.0);
        int16Data[i] = (s * 32767).round();
      }

      try {
        _ws!.send(int16Data.toJS);
      } catch (_) {}
    }).toJS;

    _sourceNode!.callMethod<JSAny?>('connect'.toJS, _processorNode!);
    final destination = _audioContext!['destination'];
    if (destination != null) {
      _processorNode!.callMethod<JSAny?>('connect'.toJS, destination);
    }
  }

  String? _extractMessageText(web.MessageEvent ev) {
    final data = ev.data;
    if (data == null) {
      return null;
    }
    try {
      return (data as JSString).toDart;
    } catch (_) {
      return null;
    }
  }

  void _onWsMessage(web.MessageEvent ev) {
    final rawText = _extractMessageText(ev);
    if (rawText == null) return;

    Map<String, dynamic> payload;
    try {
      payload = jsonDecode(rawText) as Map<String, dynamic>;
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
      if (_ws?.readyState == web.WebSocket.OPEN) {
        _ws!.send(jsonEncode({'is_speaking': false}).toJS);
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
      _processorNode?.callMethod<JSAny?>('disconnect'.toJS);
    } catch (_) {}
    try {
      _sourceNode?.callMethod<JSAny?>('disconnect'.toJS);
    } catch (_) {}
    try {
      _audioContext?.callMethod<JSAny?>('close'.toJS);
    } catch (_) {}
    try {
      _mediaStream?.getTracks().toDart.forEach((track) => track.stop());
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
