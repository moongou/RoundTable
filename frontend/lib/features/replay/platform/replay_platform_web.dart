import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

import 'replay_platform_types.dart';

Future<PickedReplayZip?> pickReplayZipFile() async {
  final input = web.HTMLInputElement()
    ..type = 'file'
    ..accept = '.zip,application/zip,application/x-zip-compressed'
    ..multiple = false;
  input.style.display = 'none';
  web.document.body?.append(input);
  input.click();
  try {
    await input.onChange.first.timeout(
      const Duration(minutes: 3),
      onTimeout: () => web.Event('timeout'),
    );
    final files = input.files;
    if (files == null || files.length == 0) {
      return null;
    }
    final file = files.item(0);
    if (file == null) {
      return null;
    }
    final bytes = await _readFileAsBytes(file);
    return PickedReplayZip(name: file.name, bytes: bytes);
  } finally {
    input.remove();
  }
}

Future<Uint8List> _readFileAsBytes(web.File file) async {
  try {
    final buffer = await file.arrayBuffer().toDart;
    return Uint8List.view(buffer.toDart);
  } catch (_) {
    throw Exception('无法读取 ZIP 内容');
  }
}

ReplayAudioController createReplayAudioController() =>
    _WebReplayAudioController();

class _WebReplayAudioController implements ReplayAudioController {
  web.HTMLAudioElement? _audioElement;
  String? _activeObjectUrl;
  Completer<void>? _playCompleter;

  @override
  Future<void> playBytes(
    Uint8List bytes, {
    required String mime,
    required double playbackRate,
  }) async {
    stop();
    final blobParts = <JSAny>[bytes.toJS];
    final blob = web.Blob(
      blobParts.toJS,
      web.BlobPropertyBag(type: mime),
    );
    final url = web.URL.createObjectURL(blob);
    _activeObjectUrl = url;
    final audioElement = web.HTMLAudioElement()
      ..src = url
      ..playbackRate = playbackRate
      ..autoplay = true;
    _audioElement = audioElement;

    final completer = Completer<void>();
    _playCompleter = completer;

    audioElement.onEnded.listen((_) {
      if (!completer.isCompleted) {
        completer.complete();
      }
    });
    audioElement.onError.listen((_) {
      if (!completer.isCompleted) {
        completer.complete();
      }
    });

    try {
      await audioElement.play().toDart;
    } catch (_) {
      // 某些浏览器会拒绝自动播放，交给 ended/error 收敛。
    }

    await completer.future;

    if (_activeObjectUrl == url) {
      web.URL.revokeObjectURL(url);
      _activeObjectUrl = null;
    }
    _audioElement = null;
    if (_playCompleter == completer) {
      _playCompleter = null;
    }
  }

  @override
  void stop() {
    try {
      _audioElement?.pause();
    } catch (_) {}
    if (_activeObjectUrl != null) {
      web.URL.revokeObjectURL(_activeObjectUrl!);
      _activeObjectUrl = null;
    }
    _audioElement = null;
    final completer = _playCompleter;
    if (completer != null && !completer.isCompleted) {
      completer.complete();
    }
    _playCompleter = null;
  }

  @override
  void dispose() {
    stop();
  }
}
