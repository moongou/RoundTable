import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;

import 'auth_token_storage.dart' as auth_token_storage;

const _tokenStorageKey = 'roundtable_auth_token';

class AuthService {
  final Dio _dio;

  AuthService({String baseUrl = 'http://localhost:8001'})
      : _dio = Dio(BaseOptions(
          baseUrl: kIsWeb ? Uri.base.origin : baseUrl,
          connectTimeout: const Duration(seconds: 10),
          headers: {'Content-Type': 'application/json'},
        )) {
    _restoreToken();
  }

  String? _token;

  String? get token => _token;

  void setToken(String? t) {
    _token = t;
  }

  void _persistToken() {
    final token = _token;
    if (token == null) {
      return;
    }
    auth_token_storage.writeToken(_tokenStorageKey, token);
  }

  void _restoreToken() {
    _token = auth_token_storage.readToken(_tokenStorageKey);
  }

  void _clearToken() {
    auth_token_storage.removeToken(_tokenStorageKey);
  }

  Map<String, String> _authHeaders() =>
      _token != null ? {'Authorization': 'Bearer $_token'} : {};

  Future<Map<String, dynamic>> register(
      String username, String password, String displayName) async {
    final r = await _dio.post('/api/v1/auth/register', data: {
      'username': username,
      'password': password,
      'display_name': displayName,
      'nickname': displayName,
    });
    _token = r.data['token'];
    _persistToken();
    return r.data['user'];
  }

  Future<Map<String, dynamic>> login(String username, String password) async {
    final r = await _dio.post('/api/v1/auth/login',
        data: {'username': username, 'password': password});
    _token = r.data['token'];
    _persistToken();
    return r.data['user'];
  }

  Future<void> logout() async {
    if (_token != null) {
      await _dio.post('/api/v1/auth/logout',
          options: Options(headers: _authHeaders()));
    }
    _token = null;
    _clearToken();
  }

  Future<Map<String, dynamic>?> me() async {
    if (_token == null) return null;
    final r = await _dio.get('/api/v1/auth/me',
        options: Options(headers: _authHeaders()));
    return r.data['user'];
  }

  Future<Map<String, dynamic>> getProfileOverview() async {
    final r = await _dio.get('/api/v1/auth/profile',
        options: Options(headers: _authHeaders()));
    return Map<String, dynamic>.from(r.data as Map);
  }

  Future<Map<String, dynamic>> updateNickname(String nickname) async {
    final r = await _dio.patch(
      '/api/v1/auth/profile',
      data: {'nickname': nickname},
      options: Options(headers: _authHeaders()),
    );
    return Map<String, dynamic>.from(r.data['user'] as Map);
  }
}
