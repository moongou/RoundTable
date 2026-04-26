/// 浏览器原生语音服务实现
///
/// 使用 Web Speech API（SpeechSynthesis / SpeechRecognition）
/// 通过 dart:js 和 dart:html 在 Flutter Web 中调用。
///
/// 注意：SpeechRecognition 仅 Chrome/Edge 完全支持。
// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
library;

import 'dart:html' as html;
import 'dart:async';
import 'dart:js' as js;

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
      final synth = html.window.speechSynthesis;
      if (synth == null) return;

      final utterance = html.SpeechSynthesisUtterance(text);
      utterance.lang = 'zh-CN';
      utterance.rate = rate;
      utterance.pitch = 1.0;
      utterance.volume = 1.0;

      _isSpeaking = true;
      var started = false;
      final completer = Completer<void>();

      utterance.onStart.listen((_) {
        if (started) return;
        started = true;
        onStart?.call();
      });

      utterance.onEnd.listen((_) {
        _isSpeaking = false;
        if (!completer.isCompleted) completer.complete();
      });

      utterance.onError.listen((_) {
        _isSpeaking = false;
        if (!completer.isCompleted) completer.completeError('TTS error');
      });

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
      html.window.speechSynthesis?.cancel();
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
  js.JsObject? _recognition;
  String _lastTranscript = '';
  bool _lastTranscriptIsFinal = false;
  bool _stopRequested = false;
  Completer<void>? _stopCompleter;

  BrowserAsrService() {
    _checkAvailability();
  }

  String _describeRecognitionError(dynamic event) {
    try {
      final jsEvent = event as js.JsObject?;
      final errorCode = (jsEvent?['error'] ?? '').toString();
      final message = (jsEvent?['message'] ?? '').toString().trim();
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
    final mediaDevices = html.window.navigator.mediaDevices;
    if (mediaDevices == null) {
      throw StateError('当前浏览器不支持麦克风访问');
    }
    final stream = await mediaDevices.getUserMedia({'audio': true});
    stream.getTracks().forEach((track) => track.stop());
  }

  void _checkAvailability() {
    try {
      final context = js.context;
      _isAvailable = context.hasProperty('webkitSpeechRecognition') ||
          context.hasProperty('SpeechRecognition');
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

      final context = js.context;
      final speechRecognitionCtor =
          context.hasProperty('webkitSpeechRecognition')
              ? context['webkitSpeechRecognition']
              : context['SpeechRecognition'];

      _recognition = js.JsObject(speechRecognitionCtor as js.JsFunction);
      _recognition!['continuous'] = true;
      _recognition!['interimResults'] = true;
      _recognition!['lang'] = 'zh-CN';
      _recognition!['maxAlternatives'] = 1;
      _lastTranscript = '';
      _lastTranscriptIsFinal = false;
      _stopRequested = false;
      _stopCompleter = null;

      // 绑定结果事件
      _recognition!['onresult'] = js.JsFunction.withThis((_, dynamic event) {
        try {
          final jsEvent = event as js.JsObject?;
          final results = jsEvent?['results'];
          final len = ((results?['length'] ?? 0) as num).toInt();
          final startIndex = ((jsEvent?['resultIndex'] ?? 0) as num).toInt();
          if (len > 0) {
            for (var i = startIndex; i < len; i++) {
              final item = results[i];
              final transcript =
                  ((item[0]['transcript'] ?? '') as String).trim();
              if (transcript.isEmpty) continue;
              final isFinal = item['isFinal'] == true;
              _emitTranscript(transcript, isFinal: isFinal);
            }
          }
        } catch (_) {}
      });

      _recognition!['onend'] = js.JsFunction.withThis((_, dynamic event) {
        _completeRecognitionCycle(flushPending: true);
      });

      _recognition!['onerror'] = js.JsFunction.withThis((_, dynamic event) {
        final jsEvent = event as js.JsObject?;
        final errorCode = (jsEvent?['error'] ?? '').toString();
        final isExpectedAbort = _stopRequested && errorCode == 'aborted';
        _completeRecognitionCycle(flushPending: _stopRequested);
        if (!isExpectedAbort && !_controller.isClosed) {
          _controller.addError(_describeRecognitionError(event));
        }
      });

      _recognition!.callMethod('start');
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
      recognition.callMethod('stop');
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
