import 'dart:typed_data';

import 'replay_platform_types.dart';

Future<PickedReplayZip?> pickReplayZipFile() async {
  throw UnsupportedError('当前平台不支持浏览器文件选择');
}

ReplayAudioController createReplayAudioController() =>
    _StubReplayAudioController();

class _StubReplayAudioController implements ReplayAudioController {
  @override
  Future<void> playBytes(
    Uint8List bytes, {
    required String mime,
    required double playbackRate,
  }) async {
    throw UnsupportedError('当前平台不支持浏览器音频回放');
  }

  @override
  void stop() {}

  @override
  void dispose() {}
}
