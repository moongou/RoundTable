/// 配置相关数据模型，与后端 config API 对齐

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
      default:
        return '🤖';
    }
  }

  /// 是否为本地部署（不需要云端 API Key）
  bool get isLocal => id == 'ollama';
}

/// 语音识别（ASR）提供商
class SpeechProviderInfo {
  final String id;
  final String name;
  final bool isActive;

  const SpeechProviderInfo({
    required this.id,
    required this.name,
    required this.isActive,
  });

  factory SpeechProviderInfo.fromJson(Map<String, dynamic> json) =>
      SpeechProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        isActive: json['is_active'] as bool? ?? false,
      );
}

/// 语音配置（包含 ASR/TTS 提供商列表和 push_to_talk）
class SpeechConfig {
  final List<SpeechProviderInfo> asrProviders;
  final List<SpeechProviderInfo> ttsProviders;
  final bool pushToTalk;

  const SpeechConfig({
    required this.asrProviders,
    required this.ttsProviders,
    required this.pushToTalk,
  });

  factory SpeechConfig.fromJson(Map<String, dynamic> json) => SpeechConfig(
        asrProviders: (json['asr'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        ttsProviders: (json['tts'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        pushToTalk: json['push_to_talk'] as bool? ?? true,
      );
}

/// 本地服务健康状态
class ServiceHealth {
  final String name;
  final String url;
  final bool reachable;
  final int? statusCode;

  const ServiceHealth({
    required this.name,
    required this.url,
    required this.reachable,
    this.statusCode,
  });

  factory ServiceHealth.fromJson(String name, Map<String, dynamic> json) =>
      ServiceHealth(
        name: name,
        url: json['url'] as String? ?? '',
        reachable: json['reachable'] as bool? ?? false,
        statusCode: json['status_code'] as int?,
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

  const CurrentConfig({
    required this.llmProvider,
    required this.llmProviderName,
    required this.apiKeyMasked,
    required this.model,
    required this.asrProvider,
    required this.ttsProvider,
    required this.pushToTalk,
  });

  factory CurrentConfig.fromJson(Map<String, dynamic> json) => CurrentConfig(
        llmProvider: json['llm_provider'] as String,
        llmProviderName: json['llm_provider_name'] as String,
        apiKeyMasked: json['api_key_masked'] as String? ?? '',
        model: json['model'] as String,
        asrProvider: json['asr_provider'] as String? ?? 'browser',
        ttsProvider: json['tts_provider'] as String? ?? 'browser',
        pushToTalk: json['push_to_talk'] as bool? ?? true,
      );
}

/// 配置验证结果
class ConfigValidation {
  final bool valid;
  final String message;

  const ConfigValidation({required this.valid, required this.message});

  factory ConfigValidation.fromJson(Map<String, dynamic> json) =>
      ConfigValidation(
        valid: json['valid'] as bool,
        message: json['message'] as String,
      );
}

/// 本地持久化设置（保存在 SharedPreferences）
class LocalSettings {
  final String serverUrl;
  final String llmProvider;
  final String asrProvider;
  final String ttsProvider;
  final bool pushToTalk;

  const LocalSettings({
    this.serverUrl = 'http://localhost:8001',
    this.llmProvider = 'openai',
    this.asrProvider = 'browser',
    this.ttsProvider = 'browser',
    this.pushToTalk = true,
  });

  LocalSettings copyWith({
    String? serverUrl,
    String? llmProvider,
    String? asrProvider,
    String? ttsProvider,
    bool? pushToTalk,
  }) =>
      LocalSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        llmProvider: llmProvider ?? this.llmProvider,
        asrProvider: asrProvider ?? this.asrProvider,
        ttsProvider: ttsProvider ?? this.ttsProvider,
        pushToTalk: pushToTalk ?? this.pushToTalk,
      );
}