import 'dart:typed_data';

class PickedReplayZip {
  final String name;
  final Uint8List bytes;

  const PickedReplayZip({required this.name, required this.bytes});
}

abstract class ReplayAudioController {
  Future<void> playBytes(
    Uint8List bytes, {
    required String mime,
    required double playbackRate,
  });

  void stop();

  void dispose();
}
