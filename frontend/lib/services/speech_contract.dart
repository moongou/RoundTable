/// Speech service contracts shared by web and non-web implementations.
library;

/// ASR single transcript segment.
class AsrResult {
  final String text;
  final bool isFinal;

  const AsrResult({required this.text, required this.isFinal});
}

/// Runtime TTS metrics snapshot used by UI telemetry panels.
class TtsLastResponseInfo {
  final String? provider;
  final String? requestedVoice;
  final String? usedVoice;
  final int? attempts;
  final double? elapsedMs;
  final String? contentType;
  final bool fromPrefetchCache;

  const TtsLastResponseInfo({
    this.provider,
    this.requestedVoice,
    this.usedVoice,
    this.attempts,
    this.elapsedMs,
    this.contentType,
    this.fromPrefetchCache = false,
  });

  TtsLastResponseInfo copyWith({
    String? provider,
    String? requestedVoice,
    String? usedVoice,
    int? attempts,
    double? elapsedMs,
    String? contentType,
    bool? fromPrefetchCache,
  }) {
    return TtsLastResponseInfo(
      provider: provider ?? this.provider,
      requestedVoice: requestedVoice ?? this.requestedVoice,
      usedVoice: usedVoice ?? this.usedVoice,
      attempts: attempts ?? this.attempts,
      elapsedMs: elapsedMs ?? this.elapsedMs,
      contentType: contentType ?? this.contentType,
      fromPrefetchCache: fromPrefetchCache ?? this.fromPrefetchCache,
    );
  }
}

class TtsPerfSnapshot {
  final int prefetchHit;
  final int prefetchMiss;
  final int prefetchRequested;
  final TtsLastResponseInfo? lastResponseInfo;

  const TtsPerfSnapshot({
    this.prefetchHit = 0,
    this.prefetchMiss = 0,
    this.prefetchRequested = 0,
    this.lastResponseInfo,
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
