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
  Future<void> speak(String text, {String? voice, double rate = 1.0}) async {
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
  Future<void> warmup() async {}

  @override
  bool get isAvailable => _isAvailable;

  @override
  bool get isListening => _isListening;

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<void> startListening() async {
    if (_isListening || !_isAvailable) return;

    try {
      final context = js.context;
      final speechRecognitionCtor =
          context.hasProperty('webkitSpeechRecognition')
              ? context['webkitSpeechRecognition']
              : context['SpeechRecognition'];

      _recognition = js.JsObject(speechRecognitionCtor as js.JsFunction);
      _recognition!['continuous'] = true;
      _recognition!['interimResults'] = true;
      _recognition!['lang'] = 'zh-CN';

      // 绑定结果事件
      _recognition!['onresult'] = (js.JsObject event) {
        try {
          final results = event['results'];
          final len = (results['length'] as num).toInt();
          if (len > 0 && !_controller.isClosed) {
            for (var i = 0; i < len; i++) {
              final item = results[i];
              final transcript =
                  ((item[0]['transcript'] ?? '') as String).trim();
              if (transcript.isEmpty) continue;
              final isFinal = item['isFinal'] == true;
              _controller.add(AsrResult(text: transcript, isFinal: isFinal));
            }
          }
        } catch (_) {}
      };

      _recognition!['onend'] = () {
        _isListening = false;
      };

      _recognition!['onerror'] = (dynamic event) {
        _isListening = false;
        if (!_controller.isClosed) {
          final err = event != null ? event.toString() : 'browser_asr_error';
          _controller.addError('浏览器语音识别错误: $err');
        }
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
