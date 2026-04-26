/// 服务器端语音服务实现
///
/// 通过后端 API 代理调用 TTS/ASR 服务（Edge TTS / CosyVoice / FunASR / OpenAI）。
///
/// 注意：此文件使用 dart:html（仅限 Flutter Web）。
/// 在非 Web 平台，此文件将无法编译，需要条件导入替代实现。
// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
library;

import 'dart:html' as html;
import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'speech_contract.dart';

class _ServerTtsAudioPayload {
  final Uint8List bytes;
  final String contentType;
  final TtsLastResponseInfo responseInfo;

  const _ServerTtsAudioPayload({
    required this.bytes,
    required this.contentType,
    required this.responseInfo,
  });
}

/// 服务器端 TTS 实现：调用 POST /api/v1/voice/tts 获取音频并播放
class ServerTtsService implements TtsService {
  final String serverUrl;
  final String providerId;
  final Dio _dio;
  bool _isSpeaking = false;
  html.AudioElement? _audioElement;
  Completer<void>? _pendingCompleter;
  String? _activeObjectUrl;
  final Map<String, _ServerTtsAudioPayload> _prefetchCache = {};
  final Map<String, Future<void>> _prefetchInFlight = {};
  static const int _maxCacheSize = 5;
  int _prefetchRequested = 0;
  int _prefetchHit = 0;
  int _prefetchMiss = 0;
  TtsLastResponseInfo? _lastResponseInfo;

  ServerTtsService({
    this.serverUrl = 'http://localhost:8001',
    this.providerId = 'edge_tts',
  }) : _dio = Dio(BaseOptions(
          baseUrl: serverUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 10),
          responseType: ResponseType.bytes,
        ));

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  TtsPerfSnapshot getPerfSnapshot() => TtsPerfSnapshot(
        prefetchHit: _prefetchHit,
        prefetchMiss: _prefetchMiss,
        prefetchRequested: _prefetchRequested,
        lastResponseInfo: _lastResponseInfo,
      );

  String _cacheKey(String text, {String? voice}) {
    return '${voice ?? 'default'}::$text';
  }

  Future<_ServerTtsAudioPayload> _fetchAudio(String text,
      {String? voice}) async {
    final response = await _dio.post<List<int>>(
      '/api/v1/voice/tts',
      data: {
        'text': text,
        'provider': providerId,
        'voice': voice ?? 'alloy',
      },
      options: Options(responseType: ResponseType.bytes),
    );

    return _ServerTtsAudioPayload(
      bytes: Uint8List.fromList(response.data!),
      contentType: response.headers.value('content-type') ?? 'audio/mpeg',
      responseInfo: TtsLastResponseInfo(
        provider: response.headers.value('x-tts-provider') ?? providerId,
        requestedVoice: response.headers.value('x-voice-requested') ?? voice,
        usedVoice: response.headers.value('x-voice-used') ?? voice,
        attempts: int.tryParse(response.headers.value('x-tts-attempts') ?? ''),
        elapsedMs:
            double.tryParse(response.headers.value('x-tts-elapsed-ms') ?? ''),
        contentType: response.headers.value('content-type') ?? 'audio/mpeg',
      ),
    );
  }

  @override
  Future<void> prefetch(String text, {String? voice}) async {
    _prefetchRequested += 1;
    final key = _cacheKey(text, voice: voice);
    if (_prefetchCache.containsKey(key)) {
      return;
    }

    final existing = _prefetchInFlight[key];
    if (existing != null) {
      await existing;
      return;
    }

    final job = () async {
      try {
        final payload = await _fetchAudio(text, voice: voice);
        if (payload.bytes.isEmpty) {
          return;
        }
        if (_prefetchCache.length >= _maxCacheSize) {
          _prefetchCache.remove(_prefetchCache.keys.first);
        }
        _prefetchCache[key] = payload;
      } catch (_) {
        // Ignore prefetch failures; playback path will retry on demand.
      } finally {
        _prefetchInFlight.remove(key);
      }
    }();

    _prefetchInFlight[key] = job;
    await job;
  }

  @override
  Future<void> prefetchBatch(
    List<({String text, String? voice})> items, {
    int maxConcurrent = 2,
  }) async {
    if (items.isEmpty) return;

    final queue = List<({String text, String? voice})>.from(items);
    final workers = maxConcurrent.clamp(1, 4);

    Future<void> worker() async {
      while (queue.isNotEmpty) {
        final item = queue.removeLast();
        await prefetch(item.text, voice: item.voice);
      }
    }

    await Future.wait(List.generate(workers, (_) => worker()));
  }

  @override
  Future<void> speak(String text, {String? voice, double rate = 1.0}) async {
    await stop();

    try {
      _isSpeaking = true;
      final key = _cacheKey(text, voice: voice);
      _ServerTtsAudioPayload payload;
      if (_prefetchCache.containsKey(key)) {
        _prefetchHit += 1;
        payload = _prefetchCache.remove(key)!;
        _lastResponseInfo =
            payload.responseInfo.copyWith(fromPrefetchCache: true);
      } else {
        _prefetchMiss += 1;
        payload = await _fetchAudio(text, voice: voice);
        _lastResponseInfo =
            payload.responseInfo.copyWith(fromPrefetchCache: false);
      }

      final blob = html.Blob([payload.bytes], payload.contentType);
      final url = html.Url.createObjectUrlFromBlob(blob);
      _activeObjectUrl = url;

      _audioElement = html.AudioElement()
        ..src = url
        ..playbackRate = rate
        ..autoplay = true;

      final completer = Completer<void>();
      _pendingCompleter = completer;

      _audioElement!.onEnded.listen((_) {
        _isSpeaking = false;
        if (_activeObjectUrl == url) {
          _activeObjectUrl = null;
        }
        html.Url.revokeObjectUrl(url);
        _audioElement = null;
        _pendingCompleter = null;
        if (!completer.isCompleted) completer.complete();
      });

      _audioElement!.onError.listen((_) {
        _isSpeaking = false;
        if (_activeObjectUrl == url) {
          _activeObjectUrl = null;
        }
        html.Url.revokeObjectUrl(url);
        _audioElement = null;
        _pendingCompleter = null;
        if (!completer.isCompleted) {
          completer.completeError('TTS playback error');
        }
      });

      await _audioElement!.play();
      await completer.future;
    } catch (e) {
      _isSpeaking = false;
      rethrow;
    }
  }

  @override
  Future<void> stop() async {
    _isSpeaking = false;
    _audioElement?.pause();
    _audioElement = null;
    final url = _activeObjectUrl;
    _activeObjectUrl = null;
    if (url != null) {
      html.Url.revokeObjectUrl(url);
    }
    // 完成 pending completer 以立即中断 speak() 中的等待
    if (_pendingCompleter != null && !_pendingCompleter!.isCompleted) {
      _pendingCompleter!.complete();
      _pendingCompleter = null;
    }
  }

  @override
  void dispose() {
    stop();
    _dio.close();
  }
}

/// 服务器端 ASR 实现：录音后上传到 POST /api/v1/voice/asr 获取转录文本
class ServerAsrService implements AsrService {
  final String serverUrl;
  final String? providerId;
  final Dio _dio;
  bool _isListening = false;
  final StreamController<AsrResult> _controller =
      StreamController<AsrResult>.broadcast();
  html.MediaRecorder? _mediaRecorder;
  html.MediaStream? _mediaStream;
  final List<html.Blob> _chunks = [];
  DateTime? _recordingStartTime;

  ServerAsrService({
    this.serverUrl = 'http://localhost:8001',
    this.providerId,
  }) : _dio = Dio(BaseOptions(
          baseUrl: serverUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 10),
        ));

  @override
  Future<void> warmup() async {}

  @override
  bool get isListening => _isListening;

  @override
  bool get isAvailable => true; // 假设服务器可用

  @override
  Stream<AsrResult> get transcriptionStream => _controller.stream;

  @override
  Future<void> startListening() async {
    if (_isListening) return;

    // 清理上一轮可能残留的资源
    await _forceCleanup();

    try {
      final mediaDevices = html.window.navigator.mediaDevices;
      if (mediaDevices == null) {
        _controller.addError('浏览器不支持麦克风访问');
        return;
      }

      final stream = await mediaDevices.getUserMedia({'audio': true});
      _mediaStream = stream;
      _mediaRecorder = html.MediaRecorder(stream);
      _chunks.clear();
      _recordingStartTime = DateTime.now();

      _mediaRecorder!.addEventListener('dataavailable', (html.Event event) {
        final blobEvent = event as html.BlobEvent;
        if (blobEvent.data != null) {
          _chunks.add(blobEvent.data!);
        }
      });

      _mediaRecorder!.addEventListener('stop', (html.Event _) async {
        await _sendForTranscription();
      });

      // 每秒收集一次数据
      _mediaRecorder!.start(1000);
      _isListening = true;
    } catch (e) {
      _isListening = false;
      _controller.addError('麦克风启动失败: $e');
    }
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening) return;

    // 立即标记为非监听状态，防止重复触发
    _isListening = false;

    if (_mediaRecorder == null) return;

    try {
      // 如果录音时间太短（< 300ms），大概率没有有效音频，直接清空
      final elapsedMs = _recordingStartTime != null
          ? DateTime.now().difference(_recordingStartTime!).inMilliseconds
          : 0;
      if (elapsedMs < 300) {
        _mediaRecorder!.stop();
        _chunks.clear();
        return;
      }

      _mediaRecorder!.stop();

      // 保险：若 3 秒内 stop 事件未触发（某些浏览器偶发），强制发送
      await Future.delayed(const Duration(seconds: 3));
      if (_chunks.isNotEmpty && !_isListening) {
        await _sendForTranscription();
      }
    } catch (e) {
      _isListening = false;
      _chunks.clear();
      _controller.addError('停止录音失败: $e');
    }
  }

  Future<void> _sendForTranscription() async {
    if (_chunks.isEmpty) return;

    // 避免并发发送
    final chunksToSend = List<html.Blob>.from(_chunks);
    _chunks.clear();

    try {
      // 合并音频块为单个 Blob
      final mergedBlob = html.Blob(chunksToSend, 'audio/webm');

      // 读取 Blob 为字节
      final reader = html.FileReader();
      reader.readAsArrayBuffer(mergedBlob);
      await reader.onLoadEnd.first;
      final audioData = reader.result as Uint8List;

      // 空音频检查
      if (audioData.isEmpty || audioData.length < 1024) {
        _controller.addError('录音数据过小，请确认麦克风正常工作并靠近麦克风说话');
        return;
      }

      // 上传到 ASR 端点，带 10 秒超时
      final formData = FormData.fromMap({
        'audio': MultipartFile.fromBytes(audioData, filename: 'audio.webm'),
        'format': 'webm',
        if (providerId?.trim().isNotEmpty == true)
          'provider': providerId!.trim(),
      });

      final response = await _dio
          .post('/api/v1/voice/asr', data: formData)
          .timeout(const Duration(seconds: 10), onTimeout: () {
        throw TimeoutException('语音识别请求超时（10秒），请检查网络或 ASR 服务状态');
      });

      final text = response.data['text'] as String? ?? '';

      if (text.isNotEmpty && !_controller.isClosed) {
        _controller.add(AsrResult(text: text.trim(), isFinal: true));
      } else if (!_controller.isClosed) {
        _controller.addError('未识别到有效语音，请重试录音');
      }
    } on DioException catch (e) {
      String errMsg;
      if (e.response?.statusCode == 503) {
        errMsg = '语音识别服务当前不可用，请在设置中切换其他 ASR 服务或确认本地服务已启动';
      } else if (e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.sendTimeout ||
          e.type == DioExceptionType.receiveTimeout) {
        errMsg = '语音识别请求超时，请检查网络或 ASR 服务状态';
      } else {
        errMsg = '语音识别请求失败: ${e.message}';
      }
      if (!_controller.isClosed) {
        _controller.addError(errMsg);
      }
    } on TimeoutException catch (e) {
      if (!_controller.isClosed) {
        _controller.addError(e.message ?? '语音识别超时');
      }
    } catch (e) {
      if (!_controller.isClosed) {
        _controller.addError('语音识别失败: $e');
      }
    }
  }

  Future<void> _forceCleanup() async {
    _chunks.clear();
    _recordingStartTime = null;
    if (_mediaRecorder != null) {
      try {
        if (_mediaRecorder!.state == 'recording') {
          _mediaRecorder!.stop();
        }
      } catch (_) {
        // 忽略清理错误
      }
      _mediaRecorder = null;
    }
    if (_mediaStream != null) {
      try {
        _mediaStream!.getTracks().forEach((track) => track.stop());
      } catch (_) {
        // 忽略清理错误
      }
      _mediaStream = null;
    }
    _isListening = false;
  }

  @override
  Future<String> refineTranscript(String text) async {
    final normalized = text.trim();
    if (normalized.isEmpty) return '';
    final locallyNormalized = normalized.replaceAllMapped(
      RegExp(r'([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])'),
      (match) => match.group(1) ?? '',
    );
    try {
      final response = await _dio.post(
        '/api/v1/voice/asr/refine',
        data: {'text': locallyNormalized},
      );
      final refined =
          (response.data['text'] as String?)?.trim() ?? locallyNormalized;
      return refined.isEmpty ? locallyNormalized : refined;
    } catch (_) {
      return locallyNormalized;
    }
  }

  @override
  void dispose() {
    _forceCleanup();
    _controller.close();
    _dio.close();
  }
}
