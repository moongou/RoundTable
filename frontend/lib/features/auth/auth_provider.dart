import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../services/api_client.dart';
import '../../services/auth_service.dart';
import '../../state/settings_provider.dart';

final authServiceProvider = Provider<AuthService>((ref) => AuthService());

final authStateProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  return AuthNotifier(
      ref.read(authServiceProvider), ref.read(apiClientProvider));
});

class AuthState {
  final bool isLoggedIn;
  final Map<String, dynamic>? user;
  final String? error;
  final bool isLoading;

  const AuthState(
      {this.isLoggedIn = false, this.user, this.error, this.isLoading = false});

  AuthState copyWith(
      {bool? isLoggedIn,
      Map<String, dynamic>? user,
      String? error,
      bool? isLoading}) {
    return AuthState(
        isLoggedIn: isLoggedIn ?? this.isLoggedIn,
        user: user ?? this.user,
        error: error,
        isLoading: isLoading ?? this.isLoading);
  }
}

class AuthNotifier extends StateNotifier<AuthState> {
  final AuthService _service;
  final ApiClient _apiClient;

  AuthNotifier(this._service, this._apiClient) : super(const AuthState()) {
    _checkSession();
  }

  Future<void> _checkSession() async {
    final user = await _service.me();
    if (user != null) {
      _apiClient.setAuthToken(_service.token);
      state = AuthState(isLoggedIn: true, user: user);
    }
  }

  Future<void> login(String username, String password) async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final user = await _service.login(username, password);
      _apiClient.setAuthToken(_service.token);
      state = AuthState(isLoggedIn: true, user: user);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  Future<void> register(
      String username, String password, String displayName) async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final user = await _service.register(username, password, displayName);
      _apiClient.setAuthToken(_service.token);
      state = AuthState(isLoggedIn: true, user: user);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  Future<void> logout() async {
    await _service.logout();
    _apiClient.setAuthToken(null);
    state = const AuthState();
  }

  Future<bool> updateNickname(String nickname) async {
    final normalized = nickname.trim();
    if (normalized.isEmpty) {
      state = state.copyWith(error: '昵称不能为空');
      return false;
    }
    state = state.copyWith(isLoading: true, error: null);
    try {
      final updatedUser = await _service.updateNickname(normalized);
      state = AuthState(isLoggedIn: true, user: updatedUser);
      return true;
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
      return false;
    }
  }

  Future<Map<String, dynamic>?> loadProfileOverview() async {
    if (!state.isLoggedIn) return null;
    try {
      final payload = await _service.getProfileOverview();
      final userRaw = payload['user'];
      if (userRaw is Map) {
        final user = Map<String, dynamic>.from(userRaw);
        state = AuthState(isLoggedIn: true, user: user);
      }
      return payload;
    } catch (_) {
      return null;
    }
  }
}
