import 'package:dio/dio.dart';

/// REST API 客户端，用于与后端通信
class ApiClient {
  late final Dio _dio;

  ApiClient({String baseUrl = 'http://localhost:8000'}) {
    _dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));
  }

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

  /// 获取所有角色模板
  Future<List<Map<String, dynamic>>> getCharacters() async {
    final response = await _dio.get('/api/v1/characters/');
    return List<Map<String, dynamic>>.from(response.data);
  }

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

  /// 构建 WebSocket URL
  String getWebSocketUrl(String sessionId) {
    return _dio.options.baseUrl
        .replace('http', 'ws')
        .replace('https', 'wss');
  }
}