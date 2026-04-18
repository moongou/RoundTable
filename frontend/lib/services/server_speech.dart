/// 服务器端语音服务实现
///
/// 通过后端 API 代理调用 TTS/ASR 服务（Edge TTS / CosyVoice / FunASR / OpenAI）。
///
/// 注意：此文件使用 dart:html（仅限 Flutter Web）。
/// 在非 Web 平台，此文件将无法编译，需要条件导入替代实现。
library;

// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'speech_service.dart';

/// 服务器端 TTS 实现：调用 POST /api/v1/voice/tts 获取音频并播放
class ServerTtsService implements TtsService {
  final String serverUrl;
  final Dio _dio;
  bool _isSpeaking = false;
  html.AudioElement? _audioElement;

  ServerTtsService({this.serverUrl = 'http://localhost:8001'})
      : _dio = Dio(BaseOptions(
          baseUrl: serverUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 30),
          responseType: ResponseType.bytes,
        ));

  @override
  bool get isSpeaking => _isSpeaking;

  @override
  Future<void> speak(String text, {String? voice}) async {
    await stop();

    try {
      _isSpeaking = true;
      final response = await _dio.post<List<int>>(
        '/api/v1/voice/tts',
        data: {'text': text, 'voice': voice ?? 'alloy'},
        options: Options(responseType: ResponseType.bytes),
      );

      final audioBytes = Uint8List.fromList(response.data!);
      final blob = html.Blob([audioBytes], 'audio/mpeg');
      final url = html.Url.createObjectUrlFromBlob(blob);

      _audioElement = html.AudioElement()
        ..src = url
        ..autoplay = true;

      final completer = Completer<void>();

      _audioElement!.onEnded.listen((_) {
        _isSpeaking = false;
        html.Url.revokeObjectUrl(url);
        if (!completer.isCompleted) completer.complete();
      });

      _audioElement!.onError.listen((_) {
        _isSpeaking = false;
        html.Url.revokeObjectUrl(url);
        if (!completer.isCompleted)
          completer.completeError('TTS playback error');
      });

      await _audioElement!.play();
      await completer.future;
    } catch (e) {
      _isSpeaking = false;
    }
  }

  @override
  Future<void> stop() async {
    _isSpeaking = false;
    _audioElement?.pause();
    _audioElement = null;
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
  final Dio _dio;
  bool _isListening = false;
  final StreamController<String> _controller =
      StreamController<String>.broadcast();
  html.MediaRecorder? _mediaRecorder;
  final List<html.Blob> _chunks = [];

  ServerAsrService({this.serverUrl = 'http://localhost:8001'})
      : _dio = Dio(BaseOptions(
          baseUrl: serverUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 60),
        ));

  @override
  bool get isListening => _isListening;

  @override
  bool get isAvailable => true; // 假设服务器可用

  @override
  Stream<String> get transcriptionStream => _controller.stream;

  @override
  Future<void> startListening() async {
    if (_isListening) return;

    try {
      // 请求麦克风权限并开始录音
      final mediaDevices = html.window.navigator.mediaDevices;
      if (mediaDevices == null) return;

      final stream = await mediaDevices.getUserMedia({'audio': true});
      _mediaRecorder = html.MediaRecorder(stream);
      _chunks.clear();

      // 使用 addEventListener 监听数据
      _mediaRecorder!.addEventListener('dataavailable', (html.Event event) {
        final blobEvent = event as html.BlobEvent;
        if (blobEvent.data != null) {
          _chunks.add(blobEvent.data!);
        }
      });

      _mediaRecorder!.addEventListener('stop', (html.Event _) async {
        await _sendForTranscription();
        _isListening = false;
      });

      // 每秒收集一次数据
      _mediaRecorder!.start(1000);
      _isListening = true;
    } catch (e) {
      _isListening = false;
    }
  }

  @override
  Future<void> stopListening() async {
    if (!_isListening || _mediaRecorder == null) return;
    try {
      _mediaRecorder!.stop();
    } catch (_) {
      _isListening = false;
    }
  }

  Future<void> _sendForTranscription() async {
    if (_chunks.isEmpty) return;

    try {
      // 合并音频块为单个 Blob
      final mergedBlob = html.Blob(_chunks, 'audio/webm');

      // 读取 Blob 为字节
      final reader = html.FileReader();
      reader.readAsArrayBuffer(mergedBlob);
      await reader.onLoadEnd.first;
      final audioData = reader.result as Uint8List;

      // 上传到 ASR 端点
      final formData = FormData.fromMap({
        'audio': MultipartFile.fromBytes(audioData, filename: 'audio.webm'),
        'format': 'webm',
      });

      final response = await _dio.post('/api/v1/voice/asr', data: formData);
      final text = response.data['text'] as String? ?? '';

      if (text.isNotEmpty && !_controller.isClosed) {
        _controller.add(text);
      }
    } catch (e) {
      if (!_controller.isClosed) {
        _controller.addError('语音识别失败: $e');
      }
    } finally {
      _chunks.clear();
    }
  }

  @override
  void dispose() {
    stopListening();
    _controller.close();
    _dio.close();
  }
}
