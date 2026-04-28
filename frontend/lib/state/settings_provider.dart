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
const _prefsKeyTtsVoiceAssignments = 'tts_voice_assignments';
const _prefsKeyPushToTalk = 'push_to_talk';
const _prefsKeyStreamUserSubtitles = 'stream_user_subtitles';
const _prefsKeyAsrStreamingEnabled = 'asr_streaming_enabled';
const _prefsKeyMicActivationMode = 'mic_activation_mode';
const _prefsKeyMicControlMode = 'mic_control_mode';
const _prefsKeyMicHotkey = 'mic_hotkey';

const _kAllowedMicHotkeys = <String>{
  'right_alt',
  'left_alt',
  'any_alt',
  'f12',
  'right_ctrl',
  'left_ctrl',
  'space',
};

String _normalizeMicHotkey(String? key) {
  final trimmed = key?.trim();
  if (trimmed == null || trimmed.isEmpty) return 'right_alt';
  if (_kAllowedMicHotkeys.contains(trimmed)) return trimmed;
  return 'right_alt';
}

String _normalizeMicControlMode(String? mode) {
  return mode?.trim() == 'hold_ctrl' ? 'hold_ctrl' : 'hold_ctrl';
}

String _normalizeMicActivationMode(String? mode) {
  return mode?.trim() == 'auto' ? 'auto' : 'manual';
}

String _normalizeSpeechProvider(String? providerId) {
  final normalized = providerId?.trim();
  if (normalized == null || normalized.isEmpty) {
    return '';
  }
  return normalized;
}

String _normalizeTtsProvider(String? providerId) {
  final normalized = _normalizeSpeechProvider(providerId);
  if (normalized.isEmpty) {
    return '';
  }
  if (normalized == 'openai_tts') {
    return 'edge_tts';
  }
  return normalized;
}

@visibleForTesting
LocalSettings resolveInitialLocalSettings({
  required String serverUrl,
  required String? storedLlmProvider,
  required String? storedAsrProvider,
  required String? storedTtsProvider,
  String? storedTtsVoiceAssignments,
  required bool hasStoredPushToTalk,
  required bool? storedPushToTalk,
  bool hasStoredStreamUserSubtitles = false,
  bool? storedStreamUserSubtitles,
  bool hasStoredAsrStreamingEnabled = false,
  bool? storedAsrStreamingEnabled,
  required String? storedMicActivationMode,
  required String? storedMicControlMode,
  String? storedMicHotkey,
  CurrentConfig? remoteConfig,
}) {
  final normalizedLlmProvider = storedLlmProvider?.trim();
  final normalizedAsrProvider = _normalizeSpeechProvider(storedAsrProvider);
  final normalizedTtsProvider = _normalizeTtsProvider(storedTtsProvider);
  final normalizedMicActivationMode =
      _normalizeMicActivationMode(storedMicActivationMode);
  final normalizedMicControlMode = storedMicControlMode?.trim();
  final remoteAsrProvider = _normalizeSpeechProvider(remoteConfig?.asrProvider);
  final remoteTtsProvider = _normalizeTtsProvider(remoteConfig?.ttsProvider);

  return LocalSettings(
    serverUrl: serverUrl,
    llmProvider: normalizedLlmProvider?.isNotEmpty == true
        ? normalizedLlmProvider!
        : 'openai',
    // Speech provider selection is meant to reflect the backend runtime.
    // If local prefs drift from /config/current, prefer the remote runtime.
    asrProvider: remoteAsrProvider.isNotEmpty
        ? remoteAsrProvider
        : (normalizedAsrProvider.isNotEmpty ? normalizedAsrProvider : 'funasr'),
    ttsProvider: remoteTtsProvider.isNotEmpty
        ? remoteTtsProvider
        : (normalizedTtsProvider.isNotEmpty
            ? normalizedTtsProvider
            : 'edge_tts'),
    ttsVoiceAssignments:
        decodeStoredVoiceAssignments(storedTtsVoiceAssignments),
    pushToTalk: hasStoredPushToTalk
        ? (storedPushToTalk ?? true)
        : (remoteConfig?.pushToTalk ?? true),
    streamUserSubtitles: hasStoredStreamUserSubtitles
        ? (storedStreamUserSubtitles ?? true)
        : true,
    asrStreamingEnabled: hasStoredAsrStreamingEnabled
        ? (storedAsrStreamingEnabled ?? true)
        : true,
    micActivationMode: normalizedMicActivationMode,
    micControlMode: _normalizeMicControlMode(normalizedMicControlMode),
    micHotkey: _normalizeMicHotkey(storedMicHotkey),
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
    final hasStoredPushToTalk = prefs.containsKey(_prefsKeyPushToTalk);
    final hasStoredStreamUserSubtitles =
        prefs.containsKey(_prefsKeyStreamUserSubtitles);
    final hasStoredAsrStreamingEnabled =
        prefs.containsKey(_prefsKeyAsrStreamingEnabled);

    final storedAsrProvider = prefs.getString(_prefsKeyAsrProvider);
    final storedTtsProvider = prefs.getString(_prefsKeyTtsProvider);
    final remoteConfig = await _loadRemoteCurrentConfig(serverUrl);

    final resolved = resolveInitialLocalSettings(
      serverUrl: serverUrl,
      storedLlmProvider: prefs.getString(_prefsKeyLlmProvider),
      storedAsrProvider: storedAsrProvider,
      storedTtsProvider: storedTtsProvider,
      storedTtsVoiceAssignments: prefs.getString(_prefsKeyTtsVoiceAssignments),
      hasStoredPushToTalk: hasStoredPushToTalk,
      storedPushToTalk: prefs.getBool(_prefsKeyPushToTalk),
      hasStoredStreamUserSubtitles: hasStoredStreamUserSubtitles,
      storedStreamUserSubtitles: prefs.getBool(_prefsKeyStreamUserSubtitles),
      hasStoredAsrStreamingEnabled: hasStoredAsrStreamingEnabled,
      storedAsrStreamingEnabled: prefs.getBool(_prefsKeyAsrStreamingEnabled),
      storedMicActivationMode: prefs.getString(_prefsKeyMicActivationMode),
      storedMicControlMode: prefs.getString(_prefsKeyMicControlMode),
      storedMicHotkey: prefs.getString(_prefsKeyMicHotkey),
      remoteConfig: remoteConfig,
    );

    if (_normalizeSpeechProvider(storedAsrProvider) != resolved.asrProvider) {
      await prefs.setString(_prefsKeyAsrProvider, resolved.asrProvider);
    }
    if (_normalizeTtsProvider(storedTtsProvider) != resolved.ttsProvider) {
      await prefs.setString(_prefsKeyTtsProvider, resolved.ttsProvider);
    }

    return resolved;
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
    final normalizedProviderId = _normalizeTtsProvider(providerId);
    await prefs.setString(_prefsKeyTtsProvider, normalizedProviderId);
    state = AsyncData(
      state.value!.copyWith(ttsProvider: normalizedProviderId),
    );
  }

  Future<void> saveTtsVoiceAssignments(Map<String, String> assignments) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _prefsKeyTtsVoiceAssignments,
      encodeStoredVoiceAssignments(assignments),
    );
    state = AsyncData(
      state.value!.copyWith(ttsVoiceAssignments: assignments),
    );
  }

  Future<void> setTtsVoiceAssignment({
    required String providerId,
    required String speaker,
    required String voice,
  }) async {
    final current = state.valueOrNull ?? const LocalSettings();
    final next = current.withVoiceAssignment(
      providerId: providerId,
      speaker: speaker,
      voice: voice,
    );
    await saveTtsVoiceAssignments(next.ttsVoiceAssignments);
  }

  Future<void> resetTtsVoiceAssignmentsForProvider(String providerId) async {
    final current = state.valueOrNull ?? const LocalSettings();
    final next = current.resetVoiceAssignmentsForProvider(providerId);
    await saveTtsVoiceAssignments(next.ttsVoiceAssignments);
  }

  /// 更新 Push-to-Talk 开关
  Future<void> setPushToTalk(bool enabled) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_prefsKeyPushToTalk, enabled);
    state = AsyncData(state.value!.copyWith(pushToTalk: enabled));
  }

  /// 更新是否实时显示用户字幕
  Future<void> setStreamUserSubtitles(bool enabled) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_prefsKeyStreamUserSubtitles, enabled);
    state = AsyncData(state.value!.copyWith(streamUserSubtitles: enabled));
  }

  Future<void> setAsrStreamingEnabled(bool enabled) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_prefsKeyAsrStreamingEnabled, enabled);
    state = AsyncData(state.value!.copyWith(asrStreamingEnabled: enabled));
  }

  Future<void> setMicActivationMode(String mode) async {
    final prefs = await SharedPreferences.getInstance();
    final normalizedMode = _normalizeMicActivationMode(mode);
    await prefs.setString(_prefsKeyMicActivationMode, normalizedMode);
    state = AsyncData(
      state.value!.copyWith(micActivationMode: normalizedMode),
    );
  }

  /// 更新麦克风控制模式（当前统一为 hold_ctrl）
  Future<void> setMicControlMode(String mode) async {
    final prefs = await SharedPreferences.getInstance();
    final normalizedMode = _normalizeMicControlMode(mode);
    await prefs.setString(_prefsKeyMicControlMode, normalizedMode);
    state = AsyncData(state.value!.copyWith(micControlMode: normalizedMode));
  }

  /// 更新麦克风热键
  Future<void> setMicHotkey(String hotkey) async {
    final prefs = await SharedPreferences.getInstance();
    final normalized = _normalizeMicHotkey(hotkey);
    await prefs.setString(_prefsKeyMicHotkey, normalized);
    state = AsyncData(state.value!.copyWith(micHotkey: normalized));
  }

  Future<void> applySnapshot(Map<String, dynamic> snapshot) async {
    final prefs = await SharedPreferences.getInstance();
    final current = state.valueOrNull ?? const LocalSettings();

    final rawServerUrl = (snapshot['server_url'] as String? ?? '').trim();
    final rawLlmProvider = (snapshot['llm_provider'] as String? ?? '').trim();
    final rawAsrProvider = (snapshot['asr_provider'] as String? ?? '').trim();
    final rawTtsProvider = (snapshot['tts_provider'] as String? ?? '').trim();
    final rawMicActivationMode = snapshot['mic_activation_mode'] as String?;
    final rawMicControlMode = snapshot['mic_control_mode'] as String?;
    final rawMicHotkey = snapshot['mic_hotkey'] as String?;
    final rawVoiceAssignments = snapshot['tts_voice_assignments'];

    Map<String, String> snapshotVoiceAssignments = current.ttsVoiceAssignments;
    if (rawVoiceAssignments is Map) {
      final normalized = <String, String>{};
      rawVoiceAssignments.forEach((key, value) {
        final normalizedKey = key.toString().trim();
        final normalizedValue = value?.toString().trim() ?? '';
        if (normalizedKey.isEmpty || normalizedValue.isEmpty) {
          return;
        }
        normalized[normalizedKey] = normalizedValue;
      });
      snapshotVoiceAssignments = normalized;
    }

    final next = current.copyWith(
      serverUrl: rawServerUrl.isNotEmpty ? rawServerUrl : current.serverUrl,
      llmProvider:
          rawLlmProvider.isNotEmpty ? rawLlmProvider : current.llmProvider,
      asrProvider:
          rawAsrProvider.isNotEmpty ? rawAsrProvider : current.asrProvider,
      ttsProvider: rawTtsProvider.isNotEmpty
          ? _normalizeTtsProvider(rawTtsProvider)
          : current.ttsProvider,
      ttsVoiceAssignments: snapshotVoiceAssignments,
      pushToTalk: snapshot['push_to_talk'] as bool? ?? current.pushToTalk,
      streamUserSubtitles: snapshot['stream_user_subtitles'] as bool? ??
          current.streamUserSubtitles,
      asrStreamingEnabled: snapshot['asr_streaming_enabled'] as bool? ??
          current.asrStreamingEnabled,
      micActivationMode: rawMicActivationMode != null
          ? _normalizeMicActivationMode(rawMicActivationMode)
          : current.micActivationMode,
      micControlMode: rawMicControlMode != null
          ? _normalizeMicControlMode(rawMicControlMode)
          : current.micControlMode,
      micHotkey: rawMicHotkey != null
          ? _normalizeMicHotkey(rawMicHotkey)
          : current.micHotkey,
    );

    await prefs.setString(_prefsKeyServerUrl, next.serverUrl);
    await prefs.setString(_prefsKeyLlmProvider, next.llmProvider);
    await prefs.setString(_prefsKeyAsrProvider, next.asrProvider);
    await prefs.setString(_prefsKeyTtsProvider, next.ttsProvider);
    await prefs.setString(
      _prefsKeyTtsVoiceAssignments,
      encodeStoredVoiceAssignments(next.ttsVoiceAssignments),
    );
    await prefs.setBool(_prefsKeyPushToTalk, next.pushToTalk);
    await prefs.setBool(_prefsKeyStreamUserSubtitles, next.streamUserSubtitles);
    await prefs.setBool(_prefsKeyAsrStreamingEnabled, next.asrStreamingEnabled);
    await prefs.setString(_prefsKeyMicActivationMode, next.micActivationMode);
    await prefs.setString(_prefsKeyMicControlMode, next.micControlMode);
    await prefs.setString(_prefsKeyMicHotkey, next.micHotkey);

    state = AsyncData(next);
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

final configProfilesProvider =
    FutureProvider<List<SavedConfigProfile>>((ref) async {
  final apiClient = ref.watch(apiClientProvider);
  return apiClient.listConfigProfiles();
});
