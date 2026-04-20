/// Speech service contracts shared by web and non-web implementations.
library;

/// ASR single transcript segment.
class AsrResult {
  final String text;
  final bool isFinal;

  const AsrResult({required this.text, required this.isFinal});
}

/// Runtime TTS metrics snapshot used by UI telemetry panels.
class TtsPerfSnapshot {
  final int prefetchHit;
  final int prefetchMiss;
  final int prefetchRequested;

  const TtsPerfSnapshot({
    this.prefetchHit = 0,
    this.prefetchMiss = 0,
    this.prefetchRequested = 0,
  });

  int get playbackCount => prefetchHit + prefetchMiss;
}

/// TTS service abstraction.
abstract class TtsService {
  Future<void> speak(String text, {String? voice, double rate = 1.0});

  /// Optional background prefetch for upcoming lines.
  Future<void> prefetch(String text, {String? voice}) async {}

  /// Optional batched prefetch with bounded concurrency.
  Future<void> prefetchBatch(
    List<({String text, String? voice})> items, {
    int maxConcurrent = 2,
  }) async {
    for (final item in items) {
      await prefetch(item.text, voice: item.voice);
    }
  }

  Future<void> stop();

  bool get isSpeaking;

  /// Optional telemetry snapshot for runtime performance panels.
  TtsPerfSnapshot getPerfSnapshot() => const TtsPerfSnapshot();

  void dispose();
}

/// ASR service abstraction.
abstract class AsrService {
  /// Optional pre-warm stage for reducing first-start latency.
  Future<void> warmup() async {}

  Future<void> startListening();

  Future<void> stopListening();

  Stream<AsrResult> get transcriptionStream;

  Future<String> refineTranscript(String text);

  bool get isListening;

  bool get isAvailable;

  void dispose();
}
