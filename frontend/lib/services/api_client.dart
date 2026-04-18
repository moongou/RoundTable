import 'package:dio/dio.dart';

import '../models/config_models.dart';

/// REST API 客户端，用于与后端通信
class ApiClient {
  late final Dio _dio;

  ApiClient({String baseUrl = 'http://localhost:8001'}) {
    _dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));
  }

  /// 更新 baseUrl（设置页修改服务器地址后调用）
  void updateBaseUrl(String url) {
    _dio.options.baseUrl = url;
  }

  String get baseUrl => _dio.options.baseUrl;

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
    int maxTurns = 30,
  }) async {
    final response = await _dio.post('/api/v1/sessions/', data: {
      'topic_id': topicId,
      'character_ids': characterIds,
      'human_names': humanNames,
      'max_turns': maxTurns,
    });
    return Map<String, dynamic>.from(response.data);
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
    return data.map((name, json) =>
        MapEntry(name, ServiceHealth.fromJson(name, json as Map<String, dynamic>)));
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

  // ── WebSocket ─────────────────────────────────────────────────────────────

  /// 构建 WebSocket URL
  String getWebSocketUrl(String sessionId) {
    return _dio.options.baseUrl
        .replaceAll('http', 'ws')
        .replaceAll('https', 'wss');
  }
}