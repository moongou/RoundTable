import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/config_models.dart';
import '../services/api_client.dart';

// ── ApiClient Provider ─────────────────────────────────────────────────────

/// 提供 ApiClient 单例，baseUrl 从本地设置读取
/// Web 环境下自动使用浏览器 origin 避免跨域问题
final apiClientProvider = Provider<ApiClient>((ref) {
  final asyncSettings = ref.watch(localSettingsProvider);
  final serverUrl =
      asyncSettings.valueOrNull?.serverUrl ?? 'http://localhost:8001';
  // Web: ApiClient 构造器内部会自动使用 Uri.base.origin
  // 非 Web: 使用用户配置的 serverUrl
  return ApiClient(baseUrl: serverUrl);
});

// ── 本地设置（SharedPreferences 持久化）───────────────────────────────────

const _prefsKeyServerUrl = 'server_url';
const _prefsKeyLlmProvider = 'llm_provider';
const _prefsKeyAsrProvider = 'asr_provider';
const _prefsKeyTtsProvider = 'tts_provider';
const _prefsKeyPushToTalk = 'push_to_talk';
const _prefsKeyMicControlMode = 'mic_control_mode';

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
      // 默认 FunASR 流式（ws://localhost:10095）—— 浏览器原生 Web Speech API
      // 在本地/无网情况下会立即触发 onend，造成“麦克风一打开就关”。
      asrProvider: prefs.getString(_prefsKeyAsrProvider) ?? 'funasr',
      ttsProvider: prefs.getString(_prefsKeyTtsProvider) ?? 'edge_tts',
      pushToTalk: prefs.getBool(_prefsKeyPushToTalk) ?? true,
      micControlMode: prefs.getString(_prefsKeyMicControlMode) ?? 'double_ctrl',
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

  /// 更新麦克风控制模式（double_ctrl | hold_ctrl）
  Future<void> setMicControlMode(String mode) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKeyMicControlMode, mode);
    state = AsyncData(state.value!.copyWith(micControlMode: mode));
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
final configValidationProvider = FutureProvider<ConfigValidation?>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.validateConfig();
});
