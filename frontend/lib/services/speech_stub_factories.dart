library;

import 'dart:async';

import 'speech_contract.dart';

class _NoopTtsService implements TtsService {
  bool _isSpeaking = false;

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  Future<void> speak(String text, {String? voice, double rate = 1.0}) async {
    _isSpeaking = true;
    _isSpeaking = false;
  }

  @override
  Future<void> stop() async {
    _isSpeaking = false;
  }

  @override
  void dispose() {
    _isSpeaking = false;
  }
}

class _NoopAsrService implements AsrService {
  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();
  bool _isListening = false;

  @override
  bool get isListening => _isListening;

  @override
  bool get isAvailable => false;

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<void> startListening() async {
    _isListening = true;
  }

  @override
  Future<void> stopListening() async {
    _isListening = false;
  }

  @override
  Future<String> refineTranscript(String text) async {
    return text.trim();
  }

  @override
  void dispose() {
    _isListening = false;
    _controller.close();
  }
}

TtsService createWebTtsService(String providerId, {required String serverUrl}) {
  return _NoopTtsService();
}

AsrService createWebAsrService(String providerId, {required String serverUrl}) {
  return _NoopAsrService();
}
