/// Speech service contracts shared by web and non-web implementations.
library;

/// ASR single transcript segment.
class AsrResult {
  final String text;
  final bool isFinal;

  const AsrResult({required this.text, required this.isFinal});
}

/// TTS service abstraction.
abstract class TtsService {
  Future<void> speak(String text, {String? voice, double rate = 1.0});

  Future<void> stop();

  bool get isSpeaking;

  void dispose();
}

/// ASR service abstraction.
abstract class AsrService {
  Future<void> startListening();

  Future<void> stopListening();

  Stream<AsrResult> get transcriptionStream;

  Future<String> refineTranscript(String text);

  bool get isListening;

  bool get isAvailable;

  void dispose();
}
