/// 浏览器原生语音服务实现
///
/// 使用 Web Speech API（SpeechSynthesis / SpeechRecognition）。
///
/// 注意：SpeechRecognition 仅 Chrome/Edge 完全支持。
library;

import 'dart:async';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';

import 'package:web/web.dart' as web;

import 'speech_contract.dart';

/// 浏览器原生 TTS 实现
class BrowserTtsService implements TtsService {
  bool _isSpeaking = false;

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  TtsPerfSnapshot getPerfSnapshot() => const TtsPerfSnapshot();

  @override
  Future<void> prefetch(String text, {String? voice}) async {}

  @override
  Future<void> prefetchBatch(
    List<({String text, String? voice})> items, {
    int maxConcurrent = 2,
  }) async {}

  @override
  Future<void> speak(
    String text, {
    String? voice,
    double rate = 1.0,
    void Function()? onStart,
  }) async {
    await stop();

    try {
      final synth = web.window.speechSynthesis;
      final utterance = web.SpeechSynthesisUtterance(text)
        ..lang = 'zh-CN'
        ..rate = rate
        ..pitch = 1.0
        ..volume = 1.0;

      _isSpeaking = true;
      var started = false;
      final completer = Completer<void>();

      utterance.onstart = ((web.Event _) {
        if (started) return;
        started = true;
        onStart?.call();
      }).toJS;

      utterance.onend = ((web.Event _) {
        _isSpeaking = false;
        if (!completer.isCompleted) completer.complete();
      }).toJS;

      utterance.onerror = ((web.Event _) {
        _isSpeaking = false;
        if (!completer.isCompleted) completer.completeError('TTS error');
      }).toJS;

      synth.speak(utterance);
      await completer.future;
    } catch (e) {
      _isSpeaking = false;
      rethrow;
    }
  }

  @override
  Future<void> stop() async {
    try {
      web.window.speechSynthesis.cancel();
    } catch (_) {}
    _isSpeaking = false;
  }

  @override
  void dispose() {
    stop();
  }
}

/// 浏览器原生 ASR 实现
class BrowserAsrService implements AsrService {
  bool _isListening = false;
  bool _isAvailable = false;
  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();
  JSObject? _recognition;
  String _lastTranscript = '';
  bool _lastTranscriptIsFinal = false;
  bool _stopRequested = false;
  Completer<void>? _stopCompleter;

  BrowserAsrService() {
    _checkAvailability();
  }

  String _jsAnyToString(JSAny? value) {
    if (value == null) {
      return '';
    }
    try {
      return (value as JSString).toDart;
    } catch (_) {
      return '';
    }
  }

  String _describeRecognitionError(JSAny? event) {
    try {
      final jsEvent = event as JSObject?;
      final errorCode = _jsAnyToString(jsEvent?['error']);
      final message = _jsAnyToString(jsEvent?['message']).trim();
      final suffix = message.isEmpty ? '' : '：$message';
      switch (errorCode) {
        case 'not-allowed':
        case 'service-not-allowed':
          return '麦克风或浏览器语音识别权限被拒绝$suffix';
        case 'audio-capture':
          return '没有检测到可用麦克风$suffix';
        case 'no-speech':
          return '没有检测到语音输入，请靠近麦克风后重试$suffix';
        case 'network':
          return '浏览器语音识别网络异常$suffix';
        case 'aborted':
          return '浏览器语音识别被中断$suffix';
        case 'bad-grammar':
          return '浏览器语音识别语法配置错误$suffix';
      }
      if (errorCode.isNotEmpty) {
        return '浏览器语音识别错误($errorCode)$suffix';
      }
    } catch (_) {}
    return '浏览器语音识别错误';
  }

  Future<void> _ensureMicrophoneAccess() async {
    final stream = await web.window.navigator.mediaDevices
        .getUserMedia(web.MediaStreamConstraints(audio: true.toJS))
        .toDart;
    stream.getTracks().toDart.forEach((track) => track.stop());
  }

  void _checkAvailability() {
    try {
      final context = globalContext;
      _isAvailable = context.has('webkitSpeechRecognition') ||
          context.has('SpeechRecognition');
    } catch (_) {
      _isAvailable = false;
    }
  }

  @override
  Future<void> warmup() async {}

  @override
  bool get isAvailable => _isAvailable;

  @override
  bool get isListening => _isListening;

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<AsrAudioCapture?> takeLastCapture() async => null;

  void _emitTranscript(String transcript, {required bool isFinal}) {
    final value = transcript.trim();
    if (value.isEmpty || _controller.isClosed) {
      return;
    }
    _lastTranscript = value;
    _lastTranscriptIsFinal = isFinal;
    _controller.add(AsrResult(text: value, isFinal: isFinal));
  }

  void _flushPendingTranscript() {
    if (_lastTranscript.isEmpty ||
        _lastTranscriptIsFinal ||
        _controller.isClosed) {
      return;
    }
    _lastTranscriptIsFinal = true;
    _controller.add(AsrResult(text: _lastTranscript, isFinal: true));
  }

  void _completeRecognitionCycle({bool flushPending = false}) {
    if (flushPending) {
      _flushPendingTranscript();
    }
    _isListening = false;
    _stopRequested = false;
    _recognition = null;
    final completer = _stopCompleter;
    _stopCompleter = null;
    if (completer != null && !completer.isCompleted) {
      completer.complete();
    }
  }

  @override
  Future<void> startListening() async {
    if (_isListening) return;
    if (!_isAvailable) {
      throw StateError('当前浏览器不支持原生语音识别，请改用 CapsWriter、Vosk 或 FunASR');
    }

    try {
      await _ensureMicrophoneAccess();

      final context = globalContext;
      JSFunction? speechRecognitionCtor;
      try {
        speechRecognitionCtor = (context.has('webkitSpeechRecognition')
            ? context['webkitSpeechRecognition']
            : context['SpeechRecognition']) as JSFunction?;
      } catch (_) {
        speechRecognitionCtor = null;
      }
      if (speechRecognitionCtor == null) {
        throw StateError('当前浏览器不支持 SpeechRecognition');
      }

      _recognition = speechRecognitionCtor.callAsConstructor<JSObject>();
      _recognition!['continuous'] = true.toJS;
      _recognition!['interimResults'] = true.toJS;
      _recognition!['lang'] = 'zh-CN'.toJS;
      _recognition!['maxAlternatives'] = 1.toJS;
      _lastTranscript = '';
      _lastTranscriptIsFinal = false;
      _stopRequested = false;
      _stopCompleter = null;

      _recognition!['onresult'] = ((JSAny? event) {
        try {
          final jsEvent = event as JSObject?;
          final results = jsEvent?['results'] as JSObject?;
          final len = ((results?['length'] as JSNumber?)?.toDartInt ?? 0);
          final startIndex =
              ((jsEvent?['resultIndex'] as JSNumber?)?.toDartInt ?? 0);
          if (len > 0) {
            for (var i = startIndex; i < len; i++) {
              final item = results?.getProperty<JSAny?>(i.toJS) as JSObject?;
              final alt = item?.getProperty<JSAny?>(0.toJS) as JSObject?;
              final transcript = _jsAnyToString(alt?['transcript']).trim();
              if (transcript.isEmpty) continue;
              final isFinal = (item?['isFinal'] as JSBoolean?)?.toDart ?? false;
              _emitTranscript(transcript, isFinal: isFinal);
            }
          }
        } catch (_) {}
      }).toJS;

      _recognition!['onend'] = ((JSAny? _) {
        _completeRecognitionCycle(flushPending: true);
      }).toJS;

      _recognition!['onerror'] = ((JSAny? event) {
        final jsEvent = event as JSObject?;
        final errorCode = _jsAnyToString(jsEvent?['error']);
        final isExpectedAbort = _stopRequested && errorCode == 'aborted';
        _completeRecognitionCycle(flushPending: _stopRequested);
        if (!isExpectedAbort && !_controller.isClosed) {
          _controller.addError(_describeRecognitionError(event));
        }
      }).toJS;

      _recognition!.callMethod<JSAny?>('start'.toJS);
      _isListening = true;
    } catch (e) {
      _completeRecognitionCycle();
      if (!_controller.isClosed) {
        _controller.addError(e.toString());
      }
      rethrow;
    }
  }

  @override
  Future<void> stopListening() async {
    final recognition = _recognition;
    if (recognition == null) {
      _completeRecognitionCycle(flushPending: true);
      return;
    }

    _isListening = false;
    _stopRequested = true;
    final completer = _stopCompleter ??= Completer<void>();
    try {
      recognition.callMethod<JSAny?>('stop'.toJS);
    } catch (_) {
      _completeRecognitionCycle(flushPending: true);
      return;
    }

    await completer.future.timeout(
      const Duration(milliseconds: 400),
      onTimeout: () {
        _completeRecognitionCycle(flushPending: true);
      },
    );
  }

  @override
  Future<String> refineTranscript(String text) async {
    var v = text.trim();
    if (v.isEmpty) return '';
    // 轻量后处理：去重复空白、修正常见重复词、补句末标点
    v = v.replaceAll(RegExp(r'\s+'), ' ');
    v = v.replaceAll(RegExp(r'(我觉得){2,}'), '我觉得');
    v = v.replaceAll(RegExp(r'(然后){2,}'), '然后');
    if (!RegExp(r'[。！？!?]$').hasMatch(v)) {
      v = '$v。';
    }
    return v;
  }

  @override
  void dispose() {
    stopListening();
    _controller.close();
  }
}
