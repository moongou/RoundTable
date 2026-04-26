import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'dart:typed_data';

import '../models/config_models.dart';
import '../models/discussion_models.dart';

/// REST API 客户端，用于与后端通信
class ApiClient {
  late final Dio _dio;

  ApiClient({String baseUrl = 'http://localhost:8001'}) {
    // Web 环境：自动使用浏览器当前 origin，避免 localhost vs 127.0.0.1 的跨域问题
    final effectiveUrl = kIsWeb ? Uri.base.origin : baseUrl;
    _dio = Dio(BaseOptions(
      baseUrl: effectiveUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));

    // 错误拦截器：将 Dio 底层错误转为用户友好的中文提示
    _dio.interceptors.add(InterceptorsWrapper(
      onError: (DioException e, ErrorInterceptorHandler handler) {
        handler.next(e.copyWith(message: _friendlyError(e)));
      },
    ));
  }

  /// 更新 baseUrl（设置页修改服务器地址后调用）
  void updateBaseUrl(String url) {
    _dio.options.baseUrl = url;
  }

  /// 暴露 Dio 实例供基准测试等直接调用
  Dio get dio => _dio;

  String get baseUrl => _dio.options.baseUrl;

  /// 将 DioException 转为用户友好的中文错误信息
  static String _friendlyError(DioException e) {
    switch (e.type) {
      case DioExceptionType.connectionError:
        return '无法连接服务器，请确认后端服务已启动 (${e.requestOptions.baseUrl})';
      case DioExceptionType.connectionTimeout:
        return '连接超时，请检查服务器地址是否正确';
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return '请求超时，服务器响应过慢';
      case DioExceptionType.badResponse:
        final code = e.response?.statusCode ?? 0;
        if (code == 404) return '接口不存在 (404)，请检查后端版本';
        if (code >= 500) return '服务器内部错误 ($code)';
        return '服务器返回错误 ($code)';
      case DioExceptionType.cancel:
        return '请求已取消';
      default:
        return '网络错误: ${e.message ?? "未知"}';
    }
  }

  // ── 话题 API ──────────────────────────────────────────────────────────────

  /// 获取所有话题
  Future<List<Map<String, dynamic>>> getTopics({String? category}) async {
    final response = await _dio.get(
      '/api/v1/topics/',
      queryParameters: category != null ? {'category': category} : null,
    );
    return List<Map<String, dynamic>>.from(response.data);
  }

  /// 获取单个话题
  Future<Map<String, dynamic>> getTopic(String topicId) async {
    final response = await _dio.get('/api/v1/topics/$topicId');
    return Map<String, dynamic>.from(response.data);
  }

  // ── 角色 API ──────────────────────────────────────────────────────────────

  /// 获取所有角色模板
  Future<List<Map<String, dynamic>>> getCharacters() async {
    final response = await _dio.get('/api/v1/characters/');
    return List<Map<String, dynamic>>.from(response.data);
  }

  // ── 会话 API ──────────────────────────────────────────────────────────────

  /// 创建讨论会话
  Future<Map<String, dynamic>> createSession({
    required String topicId,
    required List<String> characterIds,
    required List<String> humanNames,
    List<String> thinkerIds = const [],
    int maxTurns = 30,
    String freeTopic = '',
    String freeTopicDetail = '',
  }) async {
    final data = <String, dynamic>{
      'topic_id': topicId,
      'character_ids': characterIds,
      'human_names': humanNames,
      'thinker_ids': thinkerIds,
      'max_turns': maxTurns,
    };
    if (freeTopic.isNotEmpty) {
      data['free_topic'] = freeTopic;
      if (freeTopicDetail.isNotEmpty) {
        data['free_topic_detail'] = freeTopicDetail;
      }
    }
    final response = await _dio.post('/api/v1/sessions/', data: data);
    return Map<String, dynamic>.from(response.data);
  }

  /// 把自由话题的长描述提炼成一句更适合展示的标题。
  Future<Map<String, dynamic>> refineFreeTopic(String text) async {
    final response = await _dio.post(
      '/api/v1/topics/refine-free-topic',
      data: {'text': text},
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 获取会话详情
  Future<Map<String, dynamic>> getSession(String sessionId) async {
    final response = await _dio.get('/api/v1/sessions/$sessionId');
    return Map<String, dynamic>.from(response.data);
  }

  /// 获取所有会话
  Future<List<Map<String, dynamic>>> listSessions() async {
    final response = await _dio.get('/api/v1/sessions/');
    return List<Map<String, dynamic>>.from(response.data);
  }

  /// 删除会话
  Future<void> deleteSession(String sessionId) async {
    await _dio.delete('/api/v1/sessions/$sessionId');
  }

  /// 将用户本轮原始录音挂到会话历史，便于后续回放与复盘。
  Future<Map<String, dynamic>> uploadMeetingRecording({
    required String sessionId,
    required String speaker,
    required Uint8List audioBytes,
    required String fileExtension,
    required String contentType,
    int? durationMs,
    String transcript = '',
  }) async {
    final response = await _dio.post(
      '/api/v1/history/sessions/$sessionId/recordings',
      data: FormData.fromMap({
        'speaker': speaker,
        'transcript': transcript,
        if (durationMs != null) 'duration_ms': durationMs,
        'audio': MultipartFile.fromBytes(
          audioBytes,
          filename:
              'recording.${fileExtension.isEmpty ? 'bin' : fileExtension}',
        ),
      }),
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 获取某场讨论已经持久化的完整剧本。
  Future<Map<String, dynamic>> getMeetingScript(String sessionId) async {
    final response =
        await _dio.get('/api/v1/history/sessions/$sessionId/script');
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 让后端使用当前 LLM 配置提炼“今日金句”。
  Future<List<String>> generateGoldenQuotes({
    required String topic,
    required List<ChatMessage> messages,
    int maxQuotes = 4,
  }) async {
    final response = await _dio.post('/api/v1/sessions/golden-quotes', data: {
      'topic': topic,
      'messages': messages
          .map((message) => {
                'source': message.source,
                'content': message.content,
                'type': message.type,
              })
          .toList(growable: false),
      'max_quotes': maxQuotes,
    });
    final data = Map<String, dynamic>.from(response.data as Map);
    return List<String>.from(data['quotes'] ?? const <String>[]);
  }

  /// 让后端为真人用户生成一段会后点评。
  Future<String> generateHumanReview({
    required String topic,
    required String humanName,
    required List<ChatMessage> messages,
  }) async {
    final response = await _dio.post('/api/v1/sessions/human-review', data: {
      'topic': topic,
      'human_name': humanName,
      'messages': messages
          .map((message) => {
                'source': message.source,
                'content': message.content,
                'type': message.type,
              })
          .toList(growable: false),
    });
    final data = Map<String, dynamic>.from(response.data as Map);
    return (data['review'] ?? '').toString();
  }

  // ── 配置 API ──────────────────────────────────────────────────────────────

  /// 获取所有 LLM 提供商列表及配置状态
  Future<List<ProviderInfo>> getConfigProviders() async {
    final response = await _dio.get('/api/v1/config/providers');
    return (response.data as List)
        .map((e) => ProviderInfo.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 获取语音服务配置（ASR/TTS 提供商 + push_to_talk）
  Future<SpeechConfig> getSpeechConfig() async {
    final response = await _dio.get('/api/v1/config/speech');
    return SpeechConfig.fromJson(response.data as Map<String, dynamic>);
  }

  /// 检查本地服务健康状态
  Future<Map<String, ServiceHealth>> checkServicesHealth() async {
    final response = await _dio.get('/api/v1/config/health');
    final data = response.data as Map<String, dynamic>;
    return data.map((name, json) => MapEntry(
        name, ServiceHealth.fromJson(name, json as Map<String, dynamic>)));
  }

  /// 获取当前生效配置
  Future<CurrentConfig> getCurrentConfig() async {
    final response = await _dio.get('/api/v1/config/current');
    return CurrentConfig.fromJson(response.data as Map<String, dynamic>);
  }

  /// 验证当前 LLM 配置是否有效
  Future<ConfigValidation> validateConfig() async {
    final response = await _dio.post('/api/v1/config/validate');
    return ConfigValidation.fromJson(response.data as Map<String, dynamic>);
  }

  /// 更新运行时配置（LLM 提供商、API Key、模型等）
  Future<Map<String, dynamic>> updateConfig(
      Map<String, dynamic> updates) async {
    final response = await _dio.post('/api/v1/config/update', data: updates);
    return Map<String, dynamic>.from(response.data);
  }

  /// 将配置持久化写入 .env 文件
  Future<Map<String, dynamic>> saveConfig(Map<String, dynamic> updates) async {
    final response = await _dio.post('/api/v1/config/save', data: updates);
    return Map<String, dynamic>.from(response.data);
  }

  /// 列出所有已保存的配置集
  Future<List<SavedConfigProfile>> listConfigProfiles() async {
    final response = await _dio.get('/api/v1/config/profiles');
    final data = Map<String, dynamic>.from(response.data as Map);
    return List<Map<String, dynamic>>.from(
            data['profiles'] ?? const <Map<String, dynamic>>[])
        .map(SavedConfigProfile.fromJson)
        .toList();
  }

  /// 保存当前完整配置为一个配置集
  Future<Map<String, dynamic>> saveConfigProfile({
    required String name,
    String description = '',
    required LocalSettings localSettings,
  }) async {
    final response = await _dio.post(
      '/api/v1/config/profiles',
      data: {
        'name': name,
        'description': description,
        'local_settings': localSettings.toJson(),
      },
    );
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 载入一个已保存的配置集
  Future<Map<String, dynamic>> loadConfigProfile(String profileId) async {
    final response = await _dio.post('/api/v1/config/profiles/$profileId/load');
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 删除一个已保存的配置集
  Future<Map<String, dynamic>> deleteConfigProfile(String profileId) async {
    final response = await _dio.delete('/api/v1/config/profiles/$profileId');
    return Map<String, dynamic>.from(response.data as Map);
  }

  /// 测试 LLM 提供商连接，返回可用模型列表
  Future<ProviderTestResult> testProvider({
    required String providerId,
    String? apiKey,
    String? baseUrl,
    String? model,
  }) async {
    final response = await _dio.post('/api/v1/config/test-provider', data: {
      'provider_id': providerId,
      if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
      if (baseUrl != null && baseUrl.isNotEmpty) 'base_url': baseUrl,
      if (model != null && model.isNotEmpty) 'model': model,
    });
    return ProviderTestResult.fromJson(response.data as Map<String, dynamic>);
  }

  /// 测试语音服务连接，返回可用音色列表
  Future<VoiceServiceTestResult> testVoiceService({
    required String service,
    String? url,
    String? apiKey,
    String? model,
    String? voice,
  }) async {
    final response =
        await _dio.post('/api/v1/config/test-voice-service', data: {
      'service': service,
      if (url != null && url.isNotEmpty) 'url': url,
      if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
      if (model != null && model.isNotEmpty) 'model': model,
      if (voice != null && voice.isNotEmpty) 'voice': voice,
    });
    return VoiceServiceTestResult.fromJson(
        response.data as Map<String, dynamic>);
  }

  /// 获取网络搜索配置
  Future<WebSearchConfig> getWebSearchConfig() async {
    final response = await _dio.get('/api/v1/config/web-search');
    return WebSearchConfig.fromJson(response.data as Map<String, dynamic>);
  }

  /// 测试网络搜索 API 连接
  Future<Map<String, dynamic>> testWebSearch({String? apiKey}) async {
    final response = await _dio.post('/api/v1/config/test-web-search', data: {
      if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
    });
    return Map<String, dynamic>.from(response.data);
  }

  /// 获取所有思想家
  Future<List<Map<String, dynamic>>> getThinkers({String? domain}) async {
    final response = await _dio.get(
      '/api/v1/thinkers/',
      queryParameters: {
        if (domain != null) 'domain': domain,
        'size': 100,
      },
    );
    final data = response.data;
    if (data is Map && data.containsKey('items')) {
      return List<Map<String, dynamic>>.from(data['items']);
    }
    return List<Map<String, dynamic>>.from(data);
  }

  /// 获取话题分类列表
  Future<List<Map<String, dynamic>>> getTopicCategories() async {
    final response = await _dio.get('/api/v1/topics/categories/');
    return List<Map<String, dynamic>>.from(response.data);
  }

  // ── WebSocket ─────────────────────────────────────────────────────────────

  /// 构建 WebSocket URL
  String getWebSocketUrl(String sessionId) {
    final base = _dio.options.baseUrl.isNotEmpty
        ? _dio.options.baseUrl
        : (kIsWeb ? Uri.base.origin : 'http://localhost:8001');
    return base.replaceAll('https', 'wss').replaceAll('http', 'ws');
  }
}
