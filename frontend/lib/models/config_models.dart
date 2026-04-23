// 配置相关数据模型，与后端 config API 对齐

/// LLM 提供商信息
class ProviderInfo {
  final String id;
  final String name;
  final String baseUrl;
  final String model;
  final bool hasApiKey;
  final bool isActive;
  final bool needsApiKey;

  const ProviderInfo({
    required this.id,
    required this.name,
    required this.baseUrl,
    required this.model,
    required this.hasApiKey,
    required this.isActive,
    required this.needsApiKey,
  });

  factory ProviderInfo.fromJson(Map<String, dynamic> json) => ProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        baseUrl: json['base_url'] as String? ?? '',
        model: json['model'] as String? ?? '',
        hasApiKey: json['has_api_key'] as bool? ?? false,
        isActive: json['is_active'] as bool? ?? false,
        needsApiKey: json['needs_api_key'] as bool? ?? true,
      );

  /// 提供商图标/emoji
  String get icon {
    switch (id) {
      case 'openai':
        return '🌐';
      case 'qwen':
        return '🔮';
      case 'deepseek':
        return '🔍';
      case 'ollama':
        return '🦙';
      case 'ollama_cloud':
        return '☁️';
      case 'doubao':
        return '🫘';
      case 'volcengine':
        return '🌋';
      case 'bailian':
        return '🔥';
      case 'zhipu':
        return '🧠';
      case 'anthropic':
        return '🤖';
      case 'gemini':
        return '💎';
      default:
        return '🤖';
    }
  }

  /// 是否为本地部署（不需要云端 API Key）
  bool get isLocal => id == 'ollama';
}

/// 提供商连接测试结果
class ProviderTestResult {
  final bool success;
  final List<String> models;
  final String? error;

  const ProviderTestResult({
    required this.success,
    required this.models,
    this.error,
  });

  factory ProviderTestResult.fromJson(Map<String, dynamic> json) =>
      ProviderTestResult(
        success: json['success'] as bool? ?? false,
        models: List<String>.from(json['models'] as List? ?? []),
        error: json['error'] as String?,
      );
}

/// 语音识别（ASR）提供商
class SpeechProviderInfo {
  final String id;
  final String name;
  final bool isActive;
  final bool available;
  final String url;
  final String defaultUrl;
  final bool needsApiKey;
  final bool hasApiKey;

  const SpeechProviderInfo({
    required this.id,
    required this.name,
    required this.isActive,
    this.available = false,
    this.url = '',
    this.defaultUrl = '',
    this.needsApiKey = false,
    this.hasApiKey = false,
  });

  factory SpeechProviderInfo.fromJson(Map<String, dynamic> json) =>
      SpeechProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        isActive: json['is_active'] as bool? ?? false,
        available: json['available'] as bool? ?? false,
        url: json['url'] as String? ?? '',
        defaultUrl: json['default_url'] as String? ?? '',
        needsApiKey: json['needs_api_key'] as bool? ?? false,
        hasApiKey: json['has_api_key'] as bool? ?? false,
      );
}

/// 语音服务连接测试结果
class VoiceServiceTestResult {
  final bool success;
  final List<String> voices;
  final String? error;
  final String url;
  final int? statusCode;
  final double? latencyMs;

  const VoiceServiceTestResult({
    required this.success,
    required this.voices,
    required this.url,
    this.statusCode,
    this.latencyMs,
    this.error,
  });

  factory VoiceServiceTestResult.fromJson(Map<String, dynamic> json) =>
      VoiceServiceTestResult(
        success: json['success'] as bool? ?? false,
        voices: List<String>.from(json['voices'] as List? ?? []),
        url: json['url'] as String? ?? '',
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
        error: json['error'] as String?,
      );
}

/// 语音配置（包含 ASR/TTS 提供商列表和 push_to_talk）
class SpeechConfig {
  final List<SpeechProviderInfo> asrProviders;
  final List<SpeechProviderInfo> ttsProviders;
  final bool pushToTalk;
  final String ttsVoice;
  final String cosyvoiceVoice;

  const SpeechConfig({
    required this.asrProviders,
    required this.ttsProviders,
    required this.pushToTalk,
    this.ttsVoice = 'zh-CN-XiaoxiaoNeural',
    this.cosyvoiceVoice = 'default',
  });

  factory SpeechConfig.fromJson(Map<String, dynamic> json) => SpeechConfig(
        asrProviders: (json['asr'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        ttsProviders: (json['tts'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        pushToTalk: json['push_to_talk'] as bool? ?? true,
        ttsVoice: json['tts_voice'] as String? ?? 'zh-CN-XiaoxiaoNeural',
        cosyvoiceVoice: json['cosyvoice_voice'] as String? ?? 'default',
      );
}

/// 本地服务健康状态
class ServiceHealth {
  final String name;
  final String url;
  final bool reachable;
  final int? statusCode;
  final double? latencyMs;
  final String? detail;

  const ServiceHealth({
    required this.name,
    required this.url,
    required this.reachable,
    this.statusCode,
    this.latencyMs,
    this.detail,
  });

  factory ServiceHealth.fromJson(String name, Map<String, dynamic> json) =>
      ServiceHealth(
        name: name,
        url: json['url'] as String? ?? '',
        reachable: json['reachable'] as bool? ?? false,
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
        detail: json['detail'] as String?,
      );
}

class ValidationCheck {
  final String name;
  final bool ok;
  final String detail;
  final int? statusCode;
  final double? latencyMs;

  const ValidationCheck({
    required this.name,
    required this.ok,
    required this.detail,
    this.statusCode,
    this.latencyMs,
  });

  factory ValidationCheck.fromJson(Map<String, dynamic> json) =>
      ValidationCheck(
        name: json['name'] as String? ?? '未命名项目',
        ok: json['ok'] as bool? ?? false,
        detail: json['detail'] as String? ?? '',
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
      );
}

/// 当前生效配置
class CurrentConfig {
  final String llmProvider;
  final String llmProviderName;
  final String apiKeyMasked;
  final String model;
  final String asrProvider;
  final String ttsProvider;
  final bool pushToTalk;
  final bool webSearchEnabled;
  final bool tavilyConfigured;

  const CurrentConfig({
    required this.llmProvider,
    required this.llmProviderName,
    required this.apiKeyMasked,
    required this.model,
    required this.asrProvider,
    required this.ttsProvider,
    required this.pushToTalk,
    this.webSearchEnabled = false,
    this.tavilyConfigured = false,
  });

  factory CurrentConfig.fromJson(Map<String, dynamic> json) => CurrentConfig(
        llmProvider: json['llm_provider'] as String,
        llmProviderName: json['llm_provider_name'] as String,
        apiKeyMasked: json['api_key_masked'] as String? ?? '',
        model: json['model'] as String,
        asrProvider: json['asr_provider'] as String? ?? 'funasr',
        ttsProvider: json['tts_provider'] as String? ?? 'edge_tts',
        pushToTalk: json['push_to_talk'] as bool? ?? true,
        webSearchEnabled: json['web_search_enabled'] as bool? ?? false,
        tavilyConfigured: json['tavily_configured'] as bool? ?? false,
      );
}

/// 配置验证结果
class ConfigValidation {
  final bool valid;
  final String message;
  final List<ValidationCheck> checks;

  const ConfigValidation({
    required this.valid,
    required this.message,
    this.checks = const [],
  });

  factory ConfigValidation.fromJson(Map<String, dynamic> json) =>
      ConfigValidation(
        valid: json['valid'] as bool,
        message: json['message'] as String,
        checks: (json['checks'] as List<dynamic>? ?? const [])
            .whereType<Map>()
            .map((e) => ValidationCheck.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
      );
}

/// 本地持久化设置（保存在 SharedPreferences）
class LocalSettings {
  final String serverUrl;
  final String llmProvider;
  final String asrProvider;
  final String ttsProvider;
  final bool pushToTalk;
  final String micControlMode;

  const LocalSettings({
    this.serverUrl = 'http://localhost:8001',
    this.llmProvider = 'openai',
    this.asrProvider = 'funasr',
    this.ttsProvider = 'edge_tts',
    this.pushToTalk = true,
    this.micControlMode = 'double_ctrl',
  });

  LocalSettings copyWith({
    String? serverUrl,
    String? llmProvider,
    String? asrProvider,
    String? ttsProvider,
    bool? pushToTalk,
    String? micControlMode,
  }) =>
      LocalSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        llmProvider: llmProvider ?? this.llmProvider,
        asrProvider: asrProvider ?? this.asrProvider,
        ttsProvider: ttsProvider ?? this.ttsProvider,
        pushToTalk: pushToTalk ?? this.pushToTalk,
        micControlMode: micControlMode ?? this.micControlMode,
      );
}

/// 网络搜索配置
class WebSearchConfig {
  final bool enabled;
  final bool hasApiKey;
  final String apiKeyMasked;
  final String baseUrl;

  const WebSearchConfig({
    required this.enabled,
    required this.hasApiKey,
    required this.apiKeyMasked,
    required this.baseUrl,
  });

  factory WebSearchConfig.fromJson(Map<String, dynamic> json) =>
      WebSearchConfig(
        enabled: json['enabled'] as bool? ?? false,
        hasApiKey: json['has_api_key'] as bool? ?? false,
        apiKeyMasked: json['api_key_masked'] as String? ?? '',
        baseUrl: json['base_url'] as String? ?? 'https://api.tavily.com',
      );
}
