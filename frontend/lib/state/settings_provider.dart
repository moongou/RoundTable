import 'package:flutter/foundation.dart';
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

bool _hasStoredString(SharedPreferences prefs, String key) {
  final value = prefs.getString(key);
  return value != null && value.trim().isNotEmpty;
}

String _normalizeMicControlMode(String? mode) {
  return mode?.trim() == 'hold_ctrl' ? 'hold_ctrl' : 'hold_ctrl';
}

@visibleForTesting
LocalSettings resolveInitialLocalSettings({
  required String serverUrl,
  required String? storedLlmProvider,
  required String? storedAsrProvider,
  required String? storedTtsProvider,
  required bool hasStoredPushToTalk,
  required bool? storedPushToTalk,
  required String? storedMicControlMode,
  CurrentConfig? remoteConfig,
}) {
  final normalizedLlmProvider = storedLlmProvider?.trim();
  final normalizedAsrProvider = storedAsrProvider?.trim();
  final normalizedTtsProvider = storedTtsProvider?.trim();
  final normalizedMicControlMode = storedMicControlMode?.trim();

  return LocalSettings(
    serverUrl: serverUrl,
    llmProvider: normalizedLlmProvider?.isNotEmpty == true
        ? normalizedLlmProvider!
        : 'openai',
    asrProvider: normalizedAsrProvider?.isNotEmpty == true
        ? normalizedAsrProvider!
        : (remoteConfig?.asrProvider.trim().isNotEmpty == true
            ? remoteConfig!.asrProvider.trim()
            : 'funasr'),
    ttsProvider: normalizedTtsProvider?.isNotEmpty == true
        ? normalizedTtsProvider!
        : (remoteConfig?.ttsProvider.trim().isNotEmpty == true
            ? remoteConfig!.ttsProvider.trim()
            : 'edge_tts'),
    pushToTalk: hasStoredPushToTalk
        ? (storedPushToTalk ?? true)
        : (remoteConfig?.pushToTalk ?? true),
    micControlMode: _normalizeMicControlMode(normalizedMicControlMode),
  );
}

Future<CurrentConfig?> _loadRemoteCurrentConfig(String serverUrl) async {
  try {
    final apiClient = ApiClient(baseUrl: serverUrl);
    return await apiClient.getCurrentConfig();
  } catch (_) {
    return null;
  }
}

/// 本地设置 Provider（异步加载）
final localSettingsProvider =
    AsyncNotifierProvider<LocalSettingsNotifier, LocalSettings>(
  LocalSettingsNotifier.new,
);

class LocalSettingsNotifier extends AsyncNotifier<LocalSettings> {
  @override
  Future<LocalSettings> build() async {
    final prefs = await SharedPreferences.getInstance();
    final serverUrl =
        prefs.getString(_prefsKeyServerUrl) ?? 'http://localhost:8001';
    final hasStoredAsrProvider = _hasStoredString(prefs, _prefsKeyAsrProvider);
    final hasStoredTtsProvider = _hasStoredString(prefs, _prefsKeyTtsProvider);
    final hasStoredPushToTalk = prefs.containsKey(_prefsKeyPushToTalk);

    CurrentConfig? remoteConfig;
    if (!hasStoredAsrProvider ||
        !hasStoredTtsProvider ||
        !hasStoredPushToTalk) {
      remoteConfig = await _loadRemoteCurrentConfig(serverUrl);
    }

    return resolveInitialLocalSettings(
      serverUrl: serverUrl,
      storedLlmProvider: prefs.getString(_prefsKeyLlmProvider),
      storedAsrProvider: prefs.getString(_prefsKeyAsrProvider),
      storedTtsProvider: prefs.getString(_prefsKeyTtsProvider),
      hasStoredPushToTalk: hasStoredPushToTalk,
      storedPushToTalk: prefs.getBool(_prefsKeyPushToTalk),
      storedMicControlMode: prefs.getString(_prefsKeyMicControlMode),
      remoteConfig: remoteConfig,
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

  /// 更新麦克风控制模式（当前统一为 hold_ctrl）
  Future<void> setMicControlMode(String mode) async {
    final prefs = await SharedPreferences.getInstance();
    final normalizedMode = _normalizeMicControlMode(mode);
    await prefs.setString(_prefsKeyMicControlMode, normalizedMode);
    state = AsyncData(state.value!.copyWith(micControlMode: normalizedMode));
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
