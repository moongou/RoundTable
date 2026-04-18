/// 浏览器原生语音服务实现
///
/// 使用 Web Speech API（SpeechSynthesis / SpeechRecognition）
/// 通过 dart:js 和 dart:html 在 Flutter Web 中调用。
///
/// 注意：SpeechRecognition 仅 Chrome/Edge 完全支持。
library;

// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'dart:async';
import 'dart:js' as js;

import 'speech_service.dart';

/// 浏览器原生 TTS 实现
class BrowserTtsService implements TtsService {
  bool _isSpeaking = false;

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  Future<void> speak(String text, {String? voice}) async {
    await stop();

    try {
      final synth = html.window.speechSynthesis;
      if (synth == null) return;

      final utterance = html.SpeechSynthesisUtterance(text);
      utterance.lang = 'zh-CN';
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      utterance.volume = 1.0;

      _isSpeaking = true;
      final completer = Completer<void>();

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
  final StreamController<String> _controller =
      StreamController<String>.broadcast();
  js.JsObject? _recognition;

  BrowserAsrService() {
    _checkAvailability();
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
  bool get isAvailable => _isAvailable;

  @override
  bool get isListening => _isListening;

  @override
  Stream<String> get transcriptionStream => _controller.stream;

  @override
  Future<void> startListening() async {
    if (_isListening || !_isAvailable) return;

    try {
      final context = js.context;
      final SpeechRecognition = context.hasProperty('webkitSpeechRecognition')
          ? context['webkitSpeechRecognition']
          : context['SpeechRecognition'];

      _recognition = js.JsObject(SpeechRecognition as js.JsFunction);
      _recognition!['continuous'] = false;
      _recognition!['interimResults'] = false;
      _recognition!['lang'] = 'zh-CN';

      // 绑定结果事件
      _recognition!['onresult'] = (js.JsObject event) {
        try {
          final results = event['results'];
          final len = (results['length'] as num).toInt();
          if (len > 0) {
            final lastResult = results[len - 1];
            final transcript = (lastResult[0]['transcript'] ?? '') as String;
            if (transcript.isNotEmpty && !_controller.isClosed) {
              _controller.add(transcript);
            }
          }
        } catch (_) {}
      };

      _recognition!['onend'] = () {
        _isListening = false;
      };

      _recognition!['onerror'] = () {
        _isListening = false;
      };

      _recognition!.callMethod('start');
      _isListening = true;
    } catch (e) {
      _isListening = false;
    }
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening || _recognition == null) return;
    try {
      _recognition!.callMethod('stop');
    } catch (_) {}
    _isListening = false;
  }

  @override
  void dispose() {
    stopListening();
    _controller.close();
  }
}
