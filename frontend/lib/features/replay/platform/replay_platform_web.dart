// ignore_for_file: avoid_web_libraries_in_flutter, deprecated_member_use

import 'dart:async';
import 'dart:html' as html;
import 'dart:typed_data';

import 'replay_platform_types.dart';

Future<PickedReplayZip?> pickReplayZipFile() async {
  final input = html.FileUploadInputElement()
    ..accept = '.zip,application/zip,application/x-zip-compressed'
    ..multiple = false;
  input.style.display = 'none';
  html.document.body?.append(input);
  input.click();
  try {
    await input.onChange.first.timeout(
      const Duration(minutes: 3),
      onTimeout: () => html.Event('timeout'),
    );
    final files = input.files;
    if (files == null || files.isEmpty) {
      return null;
    }
    final file = files.first;
    final bytes = await _readFileAsBytes(file);
    return PickedReplayZip(name: file.name, bytes: bytes);
  } finally {
    input.remove();
  }
}

Future<Uint8List> _readFileAsBytes(html.File file) async {
  final reader = html.FileReader();
  final loadFuture = reader.onLoad.first;
  final errorFuture = reader.onError.first.then<void>((_) {
    throw Exception('无法读取 ZIP 内容');
  });
  final abortFuture = reader.onAbort.first.then<void>((_) {
    throw Exception('已取消读取 ZIP 内容');
  });

  reader.readAsArrayBuffer(file);
  await Future.any([loadFuture, errorFuture, abortFuture]);

  final result = reader.result;
  if (result is ByteBuffer) {
    return Uint8List.view(result);
  }
  if (result is Uint8List) {
    return result;
  }
  if (result is List<int>) {
    return Uint8List.fromList(result);
  }
  throw Exception('无法解析 ZIP 内容');
}

ReplayAudioController createReplayAudioController() =>
    _WebReplayAudioController();

class _WebReplayAudioController implements ReplayAudioController {
  html.AudioElement? _audioElement;
  String? _activeObjectUrl;
  Completer<void>? _playCompleter;

  @override
  Future<void> playBytes(
    Uint8List bytes, {
    required String mime,
    required double playbackRate,
  }) async {
    stop();
    final blob = html.Blob([bytes], mime);
    final url = html.Url.createObjectUrlFromBlob(blob);
    _activeObjectUrl = url;
    final audioElement = html.AudioElement()
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
      await audioElement.play();
    } catch (_) {
      // 某些浏览器会拒绝自动播放，交给 ended/error 收敛。
    }

    await completer.future;

    if (_activeObjectUrl == url) {
      html.Url.revokeObjectUrl(url);
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
      html.Url.revokeObjectUrl(_activeObjectUrl!);
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
