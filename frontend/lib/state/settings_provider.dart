import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/config_models.dart';
import '../services/api_client.dart';

// ── ApiClient Provider ─────────────────────────────────────────────────────

/// 提供 ApiClient 单例，baseUrl 从本地设置读取
final apiClientProvider = Provider<ApiClient>((ref) {
  final asyncSettings = ref.watch(localSettingsProvider);
  // 异步设置未加载完时用默认值
  final serverUrl = asyncSettings.valueOrNull?.serverUrl ?? 'http://localhost:8001';
  return ApiClient(baseUrl: serverUrl);
});

// ── 本地设置（SharedPreferences 持久化）───────────────────────────────────

const _prefsKeyServerUrl = 'server_url';
const _prefsKeyLlmProvider = 'llm_provider';
const _prefsKeyAsrProvider = 'asr_provider';
const _prefsKeyTtsProvider = 'tts_provider';
const _prefsKeyPushToTalk = 'push_to_talk';

/// 本地设置 Provider（异步加载）
final localSettingsProvider =
    AsyncNotifierProvider<LocalSettingsNotifier, LocalSettings>(
  LocalSettingsNotifier.new,
);

class LocalSettingsNotifier extends AsyncNotifier<LocalSettings> {
  @override
  Future<LocalSettings> build() async {
    final prefs = await SharedPreferences.getInstance();
    return LocalSettings(
      serverUrl: prefs.getString(_prefsKeyServerUrl) ?? 'http://localhost:8001',
      llmProvider: prefs.getString(_prefsKeyLlmProvider) ?? 'openai',
      asrProvider: prefs.getString(_prefsKeyAsrProvider) ?? 'browser',
      ttsProvider: prefs.getString(_prefsKeyTtsProvider) ?? 'browser',
      pushToTalk: prefs.getBool(_prefsKeyPushToTalk) ?? true,
    );
  }

  /// 更新服务器地址
  Future<void> setServerUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKeyServerUrl, url);
    state = AsyncData(state.value!.copyWith(serverUrl: url));
  }

  /// 更新选中的 LLM 提供商
  Future<void> setLlmProvider(String providerId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKeyLlmProvider, providerId);
    state = AsyncData(state.value!.copyWith(llmProvider: providerId));
  }

  /// 更新 ASR 提供商
  Future<void> setAsrProvider(String providerId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKeyAsrProvider, providerId);
    state = AsyncData(state.value!.copyWith(asrProvider: providerId));
  }

  /// 更新 TTS 提供商
  Future<void> setTtsProvider(String providerId) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKeyTtsProvider, providerId);
    state = AsyncData(state.value!.copyWith(ttsProvider: providerId));
  }

  /// 更新 Push-to-Talk 开关
  Future<void> setPushToTalk(bool enabled) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_prefsKeyPushToTalk, enabled);
    state = AsyncData(state.value!.copyWith(pushToTalk: enabled));
  }
}

// ── 远端配置 Provider（从后端 API 读取）─────────────────────────────────────

/// LLM 提供商列表
final providersProvider = FutureProvider<List<ProviderInfo>>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.getConfigProviders();
});

/// 语音服务配置
final speechConfigProvider = FutureProvider<SpeechConfig>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.getSpeechConfig();
});

/// 本地服务健康状态
final healthStatusProvider =
    FutureProvider<Map<String, ServiceHealth>>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.checkServicesHealth();
});

/// 当前生效配置
final currentConfigProvider = FutureProvider<CurrentConfig>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.getCurrentConfig();
});

/// 配置验证结果
final configValidationProvider =
    FutureProvider<ConfigValidation?>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.validateConfig();
});