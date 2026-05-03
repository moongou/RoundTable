import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/config_models.dart';
import '../../services/speech_service.dart';
import '../../services/saved_topics_store.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 设置页面 - 完整配置面板
/// 支持 LLM 提供商选择与配置（连接测试 + 模型获取）、语音服务、交互方式
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const _SettingsContent();
  }
}

// ─────────────────────────────────────────────────────────────────────────────

class _SettingsContent extends ConsumerStatefulWidget {
  const _SettingsContent();
  @override
  ConsumerState<_SettingsContent> createState() => _SettingsContentState();
}

class _SettingsContentState extends ConsumerState<_SettingsContent>
    with SingleTickerProviderStateMixin {
  late TabController _tabController;

  String? _expandedProvider;
  String? _expandedVoiceService;

  final Map<String, TextEditingController> _apiKeyCtrl = {};
  final Map<String, TextEditingController> _baseUrlCtrl = {};
  final Map<String, TextEditingController> _modelCtrl = {};
  final Map<String, ProviderTestResult?> _providerTestResult = {};
  final Map<String, bool> _testingProvider = {};
  final Map<String, bool> _savingProvider = {};

  final Map<String, TextEditingController> _voiceUrlCtrl = {};
  final Map<String, TextEditingController> _voiceKeyCtrl = {};
  final Map<String, TextEditingController> _voiceModelCtrl = {};
  final Map<String, String?> _selectedVoice = {};
  final Map<String, VoiceServiceTestResult?> _voiceTestResult = {};
  final Map<String, bool> _testingVoice = {};
  final Map<String, bool> _savingVoice = {};

  // Web search (Tavily) state
  final TextEditingController _tavilyKeyCtrl = TextEditingController();
  bool _testingTavily = false;
  Map<String, dynamic>? _tavilyTestResult;

  bool _healthRefreshing = false;

  // Benchmark state
  bool _benchmarkingAsr = false;
  bool _benchmarkingTts = false;
  bool _benchmarkingLlm = false;
  Map<String, dynamic>? _asrBenchmark;
  Map<String, dynamic>? _ttsBenchmark;
  Map<String, dynamic>? _llmBenchmark;

  // ScrollController 保持页面位置不跳动
  final _aiScrollCtrl = ScrollController();
  final _asrScrollCtrl = ScrollController();
  final _ttsScrollCtrl = ScrollController();
  final _voiceStudioScrollCtrl = ScrollController();
  final _generalScrollCtrl = ScrollController();
  final _profilesScrollCtrl = ScrollController();
  String _voiceStudioProviderId = 'edge_tts';

  final TextEditingController _profileNameCtrl = TextEditingController();
  final TextEditingController _profileDescriptionCtrl = TextEditingController();
  bool _savingConfigProfile = false;
  String? _profileBusyId;

  // Voice service test state
  bool _testingVoiceService = false;
  Map<String, dynamic>? _voiceServiceTestResult;
  bool _runningDeepVoiceTest = false;
  Map<String, dynamic>? _deepVoiceTestResult;

  // Interactive ASR/TTS diagnostics
  AsrService? _interactiveAsrService;
  StreamSubscription<AsrResult>? _interactiveAsrSub;
  TtsService? _interactiveTtsService;
  bool _interactiveAsrRunning = false;
  bool _interactiveTtsRunning = false;
  String _interactiveAsrText = '';
  String? _interactiveAsrError;
  String? _interactiveTtsStatus;
  String? _interactiveTtsError;
  final TextEditingController _interactiveTtsTextCtrl =
      TextEditingController(text: '你好，这是一段设置页里的合成试听文本。');
  String? _pendingSpeechSelectionSync;

  String _interactionModeLabel(LocalSettings s) {
    if (!s.pushToTalk) return '自由对话';
    return '按住 ${micHotkeyLabel(s.micHotkey)} 说话';
  }

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 7, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    _tavilyKeyCtrl.dispose();
    _interactiveTtsTextCtrl.dispose();
    _aiScrollCtrl.dispose();
    _asrScrollCtrl.dispose();
    _ttsScrollCtrl.dispose();
    _voiceStudioScrollCtrl.dispose();
    _generalScrollCtrl.dispose();
    _profilesScrollCtrl.dispose();
    _profileNameCtrl.dispose();
    _profileDescriptionCtrl.dispose();
    _interactiveAsrSub?.cancel();
    _interactiveAsrService?.dispose();
    _interactiveTtsService?.dispose();
    for (final c in [
      ..._apiKeyCtrl.values,
      ..._baseUrlCtrl.values,
      ..._modelCtrl.values,
      ..._voiceUrlCtrl.values,
      ..._voiceKeyCtrl.values
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  TextEditingController _pk(ProviderInfo p) =>
      _apiKeyCtrl.putIfAbsent(p.id, () => TextEditingController());
  TextEditingController _bu(ProviderInfo p) => _baseUrlCtrl.putIfAbsent(
      p.id, () => TextEditingController(text: p.baseUrl));
  TextEditingController _mc(ProviderInfo p) =>
      _modelCtrl.putIfAbsent(p.id, () => TextEditingController(text: p.model));
  TextEditingController _vu(SpeechProviderInfo p) =>
      _voiceUrlCtrl.putIfAbsent(p.id, () => TextEditingController(text: p.url));
  TextEditingController _vk(SpeechProviderInfo p) =>
      _voiceKeyCtrl.putIfAbsent(p.id, () => TextEditingController());
  TextEditingController _vm(SpeechProviderInfo p) => _voiceModelCtrl
      .putIfAbsent(p.id, () => TextEditingController(text: p.model));

  String _resolvedServerUrl(LocalSettings? settings) {
    final resolved = settings?.serverUrl.trim() ?? '';
    return resolved.isNotEmpty ? resolved : 'http://localhost:8001';
  }

  // ── actions ──────────────────────────────────────────────────────────────

  Future<void> _testProvider(ProviderInfo p) async {
    setState(() => _testingProvider[p.id] = true);
    try {
      final result = await ref.read(apiClientProvider).testProvider(
            providerId: p.id,
            apiKey: _apiKeyCtrl[p.id]?.text.trim(),
            baseUrl: _baseUrlCtrl[p.id]?.text.trim(),
            model: _modelCtrl[p.id]?.text.trim(),
          );
      setState(() => _providerTestResult[p.id] = result);
      if (result.success && result.models.isNotEmpty) {
        final cur = _modelCtrl[p.id]?.text ?? p.model;
        if (!result.models.contains(cur)) {
          _mc(p).text = result.models.first;
        }
      }
    } catch (e) {
      setState(() => _providerTestResult[p.id] =
          ProviderTestResult(success: false, models: [], error: e.toString()));
    } finally {
      setState(() => _testingProvider[p.id] = false);
    }
  }

  Future<void> _saveProvider(ProviderInfo p, {bool persist = false}) async {
    setState(() => _savingProvider[p.id] = true);
    try {
      final updates = <String, dynamic>{};
      final key = _apiKeyCtrl[p.id]?.text.trim() ?? '';
      final url = _baseUrlCtrl[p.id]?.text.trim() ?? '';
      final mdl = _modelCtrl[p.id]?.text.trim() ?? '';
      final tested = _providerTestResult[p.id];
      final reusingSavedConfig = key.isEmpty &&
          url == p.baseUrl &&
          mdl == p.model &&
          url.isNotEmpty &&
          mdl.isNotEmpty &&
          (!p.needsApiKey || p.hasApiKey);

      if (!reusingSavedConfig) {
        if (tested == null || !tested.success || tested.models.isEmpty) {
          _snackErr('请先点击“测试连接”，并确保返回可用模型后再保存。');
          return;
        }
        if (mdl.isEmpty || !tested.models.contains(mdl)) {
          _snackErr('当前模型不可用，请从下拉中选择已验证模型。');
          return;
        }
      }

      if (key.isNotEmpty) updates['${p.id}_api_key'] = key;
      if (url.isNotEmpty) updates['${p.id}_base_url'] = url;
      if (mdl.isNotEmpty) updates['${p.id}_model'] = mdl;
      // 同时切换到此提供商（保存即激活）
      updates['llm_provider'] = p.id;
      final client = ref.read(apiClientProvider);
      if (persist) {
        await client.saveConfig(updates);
        _snack('✅ 已写入 .env 并切换到 ${p.name}');
      } else {
        await client.updateConfig(updates);
        _snack('✅ 已应用并切换到 ${p.name}（本次运行有效）');
      }
      await ref.read(localSettingsProvider.notifier).setLlmProvider(p.id);
      setState(() => _expandedProvider = p.id);
      ref.invalidate(providersProvider);
      ref.invalidate(currentConfigProvider);
    } catch (e) {
      _snackErr('保存失败: $e');
    } finally {
      setState(() => _savingProvider[p.id] = false);
    }
  }

  Future<void> _selectProvider(ProviderInfo p) async {
    try {
      await ref.read(apiClientProvider).updateConfig({'llm_provider': p.id});
      await ref.read(localSettingsProvider.notifier).setLlmProvider(p.id);
      setState(() => _expandedProvider = p.id);
      ref.invalidate(providersProvider);
      ref.invalidate(currentConfigProvider);
      _snack('已切换到 ${p.name}');
    } catch (e) {
      _snackErr('切换失败: $e');
    }
  }

  Future<void> _testVoice(SpeechProviderInfo p) async {
    setState(() {
      _testingVoice[p.id] = true;
      _voiceTestResult[p.id] = null;
    });
    final url = _voiceUrlCtrl[p.id]?.text.trim();
    final apiKey = _voiceKeyCtrl[p.id]?.text.trim();
    try {
      final result = await ref.read(apiClientProvider).testVoiceService(
            service: p.id,
            url: url,
            apiKey: apiKey,
            model: _voiceModelCtrl[p.id]?.text.trim(),
            voice: _selectedVoice[p.id],
          );
      setState(() {
        _voiceTestResult[p.id] = result;
        if (result.success &&
            result.models.isNotEmpty &&
            !result.models.contains(_voiceModelCtrl[p.id]?.text.trim())) {
          _vm(p).text = result.models.first;
        }
        if (result.success &&
            result.voices.isNotEmpty &&
            !result.voices.contains(_selectedVoice[p.id])) {
          _selectedVoice[p.id] = result.voices.first;
        }
      });
    } catch (e) {
      setState(() {
        _voiceTestResult[p.id] = VoiceServiceTestResult(
          success: false,
          voices: const [],
          models: const [],
          url: url ?? p.url,
          error: e.toString(),
        );
      });
    } finally {
      setState(() => _testingVoice[p.id] = false);
    }
  }

  Future<void> _saveVoice(SpeechProviderInfo p,
      {required bool isAsr, bool persist = false}) async {
    setState(() => _savingVoice[p.id] = true);
    final voice = _selectedVoice[p.id];

    try {
      final tested = _voiceTestResult[p.id];
      if (p.isCloud && (tested == null || !tested.success)) {
        _snackErr('请先测试连接成功，再应用云端语音配置。');
        return;
      }
      if (p.isCloud && tested != null && !tested.modelValid) {
        _snackErr('当前模型不可用，请先测试并选择可用模型。');
        return;
      }

      final updates = _buildVoiceRuntimeUpdates(
        providerId: p.id,
        isAsr: isAsr,
        voice: voice,
      );

      final client = ref.read(apiClientProvider);
      if (persist) {
        await client.saveConfig(updates);
        _snack('✅ 语音配置已写入 .env 并切换到 ${p.name}');
      } else {
        await client.updateConfig(updates);
        _snack('✅ 已应用并切换到 ${p.name}');
      }
      if (isAsr) {
        await ref.read(localSettingsProvider.notifier).setAsrProvider(p.id);
      } else {
        await ref.read(localSettingsProvider.notifier).setTtsProvider(p.id);
      }
      setState(() => _expandedVoiceService = p.id);
      ref.invalidate(currentConfigProvider);
    } catch (e) {
      _snackErr('保存失败: $e');
    } finally {
      setState(() => _savingVoice[p.id] = false);
    }
  }

  Future<void> _selectSpeechProvider(
    SpeechProviderInfo p, {
    required bool isAsr,
  }) async {
    try {
      await ref
          .read(apiClientProvider)
          .updateConfig({isAsr ? 'asr_provider' : 'tts_provider': p.id});
      if (isAsr) {
        await ref.read(localSettingsProvider.notifier).setAsrProvider(p.id);
      } else {
        await ref.read(localSettingsProvider.notifier).setTtsProvider(p.id);
      }
      setState(() => _expandedVoiceService = p.isCloud ? p.id : null);
      ref.invalidate(currentConfigProvider);
      final isLocalOffline = !p.isCloud && p.id != 'disabled' && !p.available;
      if (isLocalOffline) {
        _snack('已切换到 ${p.name}。本地服务当前离线，配置已保存，待服务启动后自动生效。');
      } else {
        _snack('已切换到 ${p.name}');
      }
    } catch (e) {
      _snackErr('切换失败: $e');
    }
  }

  Future<void> _testTavily() async {
    setState(() => _testingTavily = true);
    try {
      final result = await ref.read(apiClientProvider).testWebSearch(
            apiKey: _tavilyKeyCtrl.text.trim(),
          );
      setState(() => _tavilyTestResult = result);
    } catch (e) {
      setState(
          () => _tavilyTestResult = {'success': false, 'error': e.toString()});
    } finally {
      setState(() => _testingTavily = false);
    }
  }

  Future<void> _saveTavily({bool persist = false}) async {
    final key = _tavilyKeyCtrl.text.trim();
    if (key.isEmpty) {
      _snack('请输入 Tavily API Key');
      return;
    }
    try {
      final updates = <String, dynamic>{
        'tavily_api_key': key,
        'web_search_enabled': true,
      };
      final client = ref.read(apiClientProvider);
      if (persist) {
        await client.saveConfig(updates);
        _snack('✅ Tavily 配置已写入 .env');
      } else {
        await client.updateConfig(updates);
        _snack('✅ Tavily 配置已应用');
      }
      ref.invalidate(currentConfigProvider);
    } catch (e) {
      _snackErr('保存失败: $e');
    }
  }

  Future<void> _saveCurrentConfigProfile(LocalSettings s) async {
    final name = _profileNameCtrl.text.trim();
    if (name.isEmpty) {
      _snackErr('请先为这套配置输入一个名称。');
      return;
    }

    setState(() => _savingConfigProfile = true);
    try {
      final result = await ref.read(apiClientProvider).saveConfigProfile(
            name: name,
            description: _profileDescriptionCtrl.text.trim(),
            localSettings: s,
          );
      if (result['success'] != true) {
        _snackErr(result['message'] as String? ?? '保存配置失败');
        return;
      }
      _profileNameCtrl.clear();
      _profileDescriptionCtrl.clear();
      ref.invalidate(configProfilesProvider);
      _snack(result['message'] as String? ?? '配置已保存');
    } catch (e) {
      _snackErr('保存配置失败: $e');
    } finally {
      if (mounted) {
        setState(() => _savingConfigProfile = false);
      }
    }
  }

  Future<void> _loadSavedConfigProfile(SavedConfigProfile profile) async {
    setState(() => _profileBusyId = profile.profileId);
    try {
      final result = await ref.read(apiClientProvider).loadConfigProfile(
            profile.profileId,
          );
      if (result['success'] != true) {
        _snackErr(result['message'] as String? ?? '载入配置失败');
        return;
      }

      final localSettings = Map<String, dynamic>.from(
        result['local_settings'] as Map? ?? const {},
      );
      await ref
          .read(localSettingsProvider.notifier)
          .applySnapshot(localSettings);
      ref.invalidate(providersProvider);
      ref.invalidate(speechConfigProvider);
      ref.invalidate(currentConfigProvider);
      ref.invalidate(healthStatusProvider);
      ref.invalidate(configProfilesProvider);
      _snack(result['message'] as String? ?? '配置已载入');
    } catch (e) {
      _snackErr('载入配置失败: $e');
    } finally {
      if (mounted) {
        setState(() => _profileBusyId = null);
      }
    }
  }

  Future<void> _deleteSavedConfigProfile(SavedConfigProfile profile) async {
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            backgroundColor: AppColors.studyWallLight,
            title: const Text('删除配置'),
            content: Text('确认删除“${profile.name}”？'),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('取消'),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('删除'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) {
      return;
    }

    setState(() => _profileBusyId = profile.profileId);
    try {
      final result = await ref.read(apiClientProvider).deleteConfigProfile(
            profile.profileId,
          );
      if (result['success'] != true) {
        _snackErr(result['message'] as String? ?? '删除配置失败');
        return;
      }
      ref.invalidate(configProfilesProvider);
      _snack(result['message'] as String? ?? '配置已删除');
    } catch (e) {
      _snackErr('删除配置失败: $e');
    } finally {
      if (mounted) {
        setState(() => _profileBusyId = null);
      }
    }
  }

  void _snack(String m) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(m), duration: const Duration(seconds: 2)));
  }

  void _snackErr(String m) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(m),
        backgroundColor: Colors.red,
        duration: const Duration(seconds: 3)));
  }

  Future<void> _refreshLocalServiceStatus() async {
    if (_healthRefreshing) return;
    setState(() => _healthRefreshing = true);
    try {
      await Future.wait<Object?>([
        ref.refresh(speechConfigProvider.future),
        ref.refresh(healthStatusProvider.future),
        ref.refresh(currentConfigProvider.future),
      ]);
      _snack('本地服务状态已刷新');
    } catch (e) {
      _snackErr('本地服务状态刷新失败: $e');
    } finally {
      if (mounted) {
        setState(() => _healthRefreshing = false);
      }
    }
  }

  void _syncSpeechSelectionsWithCurrentConfig(
    LocalSettings settings,
    CurrentConfig? current,
  ) {
    if (current == null) {
      _pendingSpeechSelectionSync = null;
      return;
    }

    final nextAsrProvider = current.asrProvider.trim();
    final nextTtsProvider = current.ttsProvider.trim();
    if (nextAsrProvider.isEmpty || nextTtsProvider.isEmpty) {
      return;
    }
    if (settings.asrProvider == nextAsrProvider &&
        settings.ttsProvider == nextTtsProvider) {
      _pendingSpeechSelectionSync = null;
      return;
    }

    final syncKey = '$nextAsrProvider|$nextTtsProvider';
    if (_pendingSpeechSelectionSync == syncKey) {
      return;
    }
    _pendingSpeechSelectionSync = syncKey;

    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final notifier = ref.read(localSettingsProvider.notifier);
      if (settings.asrProvider != nextAsrProvider) {
        await notifier.setAsrProvider(nextAsrProvider);
      }
      if (settings.ttsProvider != nextTtsProvider) {
        await notifier.setTtsProvider(nextTtsProvider);
      }
      _pendingSpeechSelectionSync = null;
    });
  }

  // ── build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final localAsync = ref.watch(localSettingsProvider);
    final s = localAsync.valueOrNull ?? const LocalSettings();
    final providersAsync = ref.watch(providersProvider);
    final speechAsync = ref.watch(speechConfigProvider);
    final healthAsync = ref.watch(healthStatusProvider);
    final currentAsync = ref.watch(currentConfigProvider);
    final profilesAsync = ref.watch(configProfilesProvider);
    _syncSpeechSelectionsWithCurrentConfig(s, currentAsync.valueOrNull);

    if (localAsync.isLoading) {
      return Scaffold(
        backgroundColor: AppColors.studyWall,
        appBar: AppBar(
            title:
                Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
            backgroundColor: AppColors.studyWall),
        body: const Center(
            child: CircularProgressIndicator(color: AppColors.amberGold)),
      );
    }

    return Scaffold(
      backgroundColor: AppColors.studyWall,
      appBar: AppBar(
        title: Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
        backgroundColor: AppColors.studyWall,
        bottom: TabBar(
          controller: _tabController,
          isScrollable: true,
          labelColor: AppColors.amberGold,
          unselectedLabelColor: AppColors.warmGray,
          indicatorColor: AppColors.amberGold,
          labelStyle:
              const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
          tabs: const [
            Tab(icon: Icon(Icons.smart_toy_outlined, size: 18), text: 'AI 模型'),
            Tab(icon: Icon(Icons.mic_outlined, size: 18), text: '语音识别'),
            Tab(icon: Icon(Icons.volume_up_outlined, size: 18), text: '语音合成'),
            Tab(icon: Icon(Icons.graphic_eq_outlined, size: 18), text: '音色工坊'),
            Tab(icon: Icon(Icons.forum_outlined, size: 18), text: '话题'),
            Tab(icon: Icon(Icons.settings_outlined, size: 18), text: '通用'),
            Tab(
                icon: Icon(Icons.bookmark_add_outlined, size: 18),
                text: '保存配置'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildAiModelTab(s, providersAsync, currentAsync),
          _buildAsrTab(s, speechAsync),
          _buildTtsTab(s, speechAsync),
          _buildVoiceStudioTab(s, speechAsync),
          const _TopicsTab(),
          _buildGeneralTab(s, currentAsync, healthAsync),
          _buildConfigProfilesTab(s, currentAsync, profilesAsync),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Tab builders (Req8: 4 sub-pages)
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildAiModelTab(
      LocalSettings s,
      AsyncValue<List<ProviderInfo>> providersAsync,
      AsyncValue<CurrentConfig> currentAsync) {
    return ListView(
      controller: _aiScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildAiConfigSection(currentAsync),
        const SizedBox(height: 14),
        _buildLlmSection(providersAsync, s),
        const SizedBox(height: 14),
        _buildTavilySection(currentAsync),
        const SizedBox(height: 14),
        _buildLlmBenchmarkSection(),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildAsrTab(LocalSettings s, AsyncValue<SpeechConfig> speechAsync) {
    final speechConfig = speechAsync.valueOrNull;
    return ListView(
      controller: _asrScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildAsrSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildAsrBenchmarkSection(),
        const SizedBox(height: 14),
        _buildVoiceServiceTestSection(
          type: 'asr',
          settings: s,
          speechConfig: speechConfig,
        ),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildTtsTab(LocalSettings s, AsyncValue<SpeechConfig> speechAsync) {
    final speechConfig = speechAsync.valueOrNull;
    return ListView(
      controller: _ttsScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildTtsSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildTtsBenchmarkSection(),
        const SizedBox(height: 14),
        _buildVoiceServiceTestSection(
          type: 'tts',
          settings: s,
          speechConfig: speechConfig,
        ),
        const SizedBox(height: 32),
      ],
    );
  }

  List<SpeechProviderInfo> _voiceStudioProviders(SpeechConfig? speechConfig) {
    const order = <String>[
      'edge_tts',
      'openvoice',
      'vibevoice',
      'chattts',
    ];
    final existing = <String, SpeechProviderInfo>{
      for (final provider
          in speechConfig?.ttsProviders ?? const <SpeechProviderInfo>[])
        provider.id: provider,
    };
    return [
      for (final providerId in order)
        existing[providerId] ??
            SpeechProviderInfo(
              id: providerId,
              name: voiceServicePalette(providerId)?.title ?? providerId,
              isActive: false,
              available: false,
            ),
    ];
  }

  String _voiceStudioRoleForSpeaker(String speaker) {
    if (speaker == teacherVoiceSpeaker) {
      return voiceRoleTeacher;
    }
    if (speaker == thinkerVoiceSpeaker) {
      return voiceRoleThinker;
    }
    if (maleStudentVoiceSpeakers.contains(speaker)) {
      return voiceRoleStudentMale;
    }
    return voiceRoleStudentFemale;
  }

  String _voiceStudioCurrentVoice(
    LocalSettings settings,
    String providerId,
    String speaker,
  ) {
    return resolveConfiguredVoiceForSpeaker(
          settings: settings,
          providerId: providerId,
          speaker: speaker,
        ) ??
        '';
  }

  Future<void> _applyVoiceStudioVoice({
    required String providerId,
    required String speaker,
    required String voice,
  }) async {
    await ref.read(localSettingsProvider.notifier).setTtsVoiceAssignment(
          providerId: providerId,
          speaker: speaker,
          voice: voice,
        );
    final paletteTitle = voiceServicePalette(providerId)?.title ?? providerId;
    _snack('已更新 $paletteTitle 的 $speaker 音色');
  }

  Future<void> _resetVoiceStudioSpeaker({
    required String providerId,
    required String speaker,
  }) async {
    await ref.read(localSettingsProvider.notifier).setTtsVoiceAssignment(
          providerId: providerId,
          speaker: speaker,
          voice: '',
        );
    _snack('已恢复 $speaker 的默认音色');
  }

  Future<void> _resetVoiceStudioProvider(String providerId) async {
    await ref
        .read(localSettingsProvider.notifier)
        .resetTtsVoiceAssignmentsForProvider(providerId);
    final paletteTitle = voiceServicePalette(providerId)?.title ?? providerId;
    _snack('已恢复 $paletteTitle 的默认分配');
  }

  Future<void> _previewVoiceStudioVoice({
    required LocalSettings settings,
    required SpeechConfig? speechConfig,
    required String providerId,
    required String voice,
    required String label,
  }) async {
    if (_interactiveTtsRunning) {
      try {
        await _interactiveTtsService?.stop();
      } catch (_) {}
      _interactiveTtsService?.dispose();
    }

    final sampleText = _interactiveTtsTextCtrl.text.trim();
    if (sampleText.isEmpty) {
      _snackErr('请先输入一段要试听的文本。');
      return;
    }

    final serverUrl = _resolvedServerUrl(settings);
    final providerUrl =
        _resolveSpeechProviderUrl(speechConfig, providerId, isAsr: false);
    final tts = createTtsService(
      providerId,
      serverUrl: serverUrl,
      providerUrl: providerUrl,
    );

    setState(() {
      _interactiveTtsService = tts;
      _interactiveTtsRunning = true;
      _interactiveTtsError = null;
      _interactiveTtsStatus = '正在试听 ${label.split('·').last.trim()}…';
    });

    try {
      await tts.speak(sampleText, voice: voice);
      if (!mounted) return;
      setState(() {
        _interactiveTtsStatus = '试听完成：$label';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _interactiveTtsStatus = null;
        _interactiveTtsError = e.toString();
      });
    } finally {
      tts.dispose();
      if (mounted) {
        setState(() {
          _interactiveTtsService = null;
          _interactiveTtsRunning = false;
        });
      }
    }
  }

  Widget _buildVoiceStudioTab(
    LocalSettings settings,
    AsyncValue<SpeechConfig> speechAsync,
  ) {
    final speechConfig = speechAsync.valueOrNull;
    final providers = _voiceStudioProviders(speechConfig);
    final activeProviderId =
        providers.any((p) => p.id == _voiceStudioProviderId)
            ? _voiceStudioProviderId
            : providers.first.id;
    final activeProvider =
        providers.firstWhere((p) => p.id == activeProviderId);
    final palette = voiceServicePalette(activeProviderId);
    final actualPresetCount = palette?.presets.length ?? 0;
    final targetPresetCount = palette?.targetPresetCount ?? 0;

    Widget buildSpeakerCard(String speaker) {
      final role = _voiceStudioRoleForSpeaker(speaker);
      final presets = palette?.presetsForRole(role) ?? const <VoicePreset>[];
      final selectedVoice =
          _voiceStudioCurrentVoice(settings, activeProviderId, speaker);
      final hasOverride = settings.resolveVoiceAssignment(
            providerId: activeProviderId,
            speaker: speaker,
          ) !=
          null;
      VoicePreset? selectedPreset;
      for (final preset in presets) {
        if (preset.voice == selectedVoice) {
          selectedPreset = preset;
          break;
        }
      }
      selectedPreset ??= presets.isNotEmpty ? presets.first : null;

      final roleLabel = switch (role) {
        voiceRoleTeacher => '老师',
        voiceRoleThinker => '思想家',
        voiceRoleStudentMale => '男童声',
        _ => '女童声',
      };
      final previewPreset = selectedPreset;

      return Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(16),
          color: AppColors.studyWall.withValues(alpha: 0.55),
          border: Border.all(
            color: hasOverride
                ? AppColors.amberGold.withValues(alpha: 0.46)
                : AppColors.warmGray.withValues(alpha: 0.18),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        speaker,
                        style: const TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 14,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Row(
                        children: [
                          _Chip(roleLabel, AppColors.amberGold),
                          const SizedBox(width: 6),
                          _Chip(
                            hasOverride ? '已自定义' : '默认',
                            hasOverride
                                ? const Color(0xFF8BC34A)
                                : AppColors.warmGray,
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                if (selectedPreset != null)
                  SizedBox(
                    width: 132,
                    child: Text(
                      selectedPreset.label,
                      textAlign: TextAlign.right,
                      style: const TextStyle(
                        color: AppColors.warmWhite,
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            if (presets.isEmpty)
              const Text(
                '当前服务暂无这一角色的预设音色。',
                style: TextStyle(color: Colors.orange, fontSize: 11),
              )
            else
              DropdownButtonFormField<String>(
                initialValue: presets.any((p) => p.voice == selectedVoice)
                    ? selectedVoice
                    : presets.first.voice,
                dropdownColor: AppColors.studyWallLight,
                style: const TextStyle(
                  color: AppColors.warmWhite,
                  fontSize: 13,
                ),
                decoration: InputDecoration(
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(9),
                    borderSide: BorderSide(
                      color: AppColors.warmGray.withValues(alpha: 0.3),
                    ),
                  ),
                  filled: true,
                  fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  isDense: true,
                ),
                items: presets
                    .map(
                      (preset) => DropdownMenuItem<String>(
                        value: preset.voice,
                        child: Text(
                          preset.label,
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                    )
                    .toList(),
                onChanged: (value) {
                  if (value == null || value.isEmpty) {
                    return;
                  }
                  unawaited(
                    _applyVoiceStudioVoice(
                      providerId: activeProviderId,
                      speaker: speaker,
                      voice: value,
                    ),
                  );
                },
              ),
            if (selectedPreset != null && selectedPreset.note.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                selectedPreset.note,
                style: TextStyle(
                  color: AppColors.warmGray.withValues(alpha: 0.96),
                  fontSize: 11,
                ),
              ),
            ],
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                _OutBtn(
                  _interactiveTtsRunning ? '试听中…' : '试听当前音色',
                  onTap: previewPreset == null || _interactiveTtsRunning
                      ? null
                      : () => unawaited(
                            _previewVoiceStudioVoice(
                              settings: settings,
                              speechConfig: speechConfig,
                              providerId: activeProviderId,
                              voice: previewPreset.voice,
                              label: previewPreset.label,
                            ),
                          ),
                ),
                _OutBtn(
                  '恢复默认',
                  onTap: hasOverride
                      ? () => unawaited(
                            _resetVoiceStudioSpeaker(
                              providerId: activeProviderId,
                              speaker: speaker,
                            ),
                          )
                      : null,
                ),
              ],
            ),
          ],
        ),
      );
    }

    return ListView(
      controller: _voiceStudioScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _Section(
          title: '音色工坊',
          icon: Icons.graphic_eq_outlined,
          children: [
            Text(
              '分别为 Edge TTS、OpenVoice、VibeVoice、ChatTTS 维护一套独立音色库。这里改的是“角色到音色”的映射，不会强制切换你当前正在使用的 TTS 服务。',
              style: TextStyle(
                color: AppColors.warmGray.withValues(alpha: 0.96),
                fontSize: 12.5,
              ),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                for (final provider in providers)
                  GestureDetector(
                    onTap: () =>
                        setState(() => _voiceStudioProviderId = provider.id),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 180),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 10,
                      ),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(14),
                        color: provider.id == activeProviderId
                            ? AppColors.amberGold.withValues(alpha: 0.14)
                            : AppColors.studyWall.withValues(alpha: 0.48),
                        border: Border.all(
                          color: provider.id == activeProviderId
                              ? AppColors.amberGold
                              : AppColors.warmGray.withValues(alpha: 0.22),
                        ),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(provider.icon,
                              style: const TextStyle(fontSize: 16)),
                          const SizedBox(width: 8),
                          Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                provider.name,
                                style: const TextStyle(
                                  color: AppColors.warmWhite,
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                provider.available ? '服务在线' : '按当前状态显示',
                                style: TextStyle(
                                  color: provider.available
                                      ? Colors.greenAccent
                                      : AppColors.warmGray,
                                  fontSize: 10,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 14),
            if (palette != null)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(16),
                  gradient: const LinearGradient(
                    colors: [Color(0xFF3F2E21), Color(0xFF1E1A17)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  border: Border.all(
                    color: AppColors.amberGold.withValues(alpha: 0.24),
                  ),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      palette.title,
                      style: AppTheme.calligraphyStyleDark(fontSize: 18),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      palette.summary,
                      style: TextStyle(
                        color: AppColors.warmGray.withValues(alpha: 0.95),
                        fontSize: 12,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        _Chip('预设 $actualPresetCount/$targetPresetCount',
                            AppColors.amberGold),
                        _Chip(
                          settings.ttsProvider == activeProviderId
                              ? '当前 TTS'
                              : '独立配置',
                          settings.ttsProvider == activeProviderId
                              ? const Color(0xFF8BC34A)
                              : AppColors.warmGray,
                        ),
                        _Chip(
                          activeProvider.available ? '在线' : '离线可配',
                          activeProvider.available
                              ? const Color(0xFF4CAF50)
                              : const Color(0xFFB0BEC5),
                        ),
                      ],
                    ),
                    if (palette.note.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        palette.note,
                        style: const TextStyle(
                          color: Color(0xFFFFCC80),
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            const SizedBox(height: 14),
            const _Label('试听文本（与下方 TTS 实机试听共用）'),
            TextField(
              controller: _interactiveTtsTextCtrl,
              style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
              maxLines: 2,
              decoration: InputDecoration(
                hintText: '输入一段要试听的文本…',
                hintStyle:
                    const TextStyle(color: AppColors.warmGray, fontSize: 12),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(9),
                  borderSide: BorderSide(
                    color: AppColors.warmGray.withValues(alpha: 0.3),
                  ),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(9),
                  borderSide: BorderSide(
                    color: AppColors.warmGray.withValues(alpha: 0.3),
                  ),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(9),
                  borderSide: const BorderSide(color: AppColors.amberGold),
                ),
                filled: true,
                fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
              ),
            ),
            if (_interactiveTtsStatus != null) ...[
              const SizedBox(height: 10),
              Text(
                _interactiveTtsStatus!,
                style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
              ),
            ],
            if (_interactiveTtsError != null) ...[
              const SizedBox(height: 8),
              Text(
                '试听错误：$_interactiveTtsError',
                style: const TextStyle(color: Colors.redAccent, fontSize: 11),
              ),
            ],
          ],
        ),
        const SizedBox(height: 14),
        _Section(
          title: '角色分配',
          icon: Icons.groups_2_outlined,
          action: _OutBtn(
            '整组恢复默认',
            onTap: () => unawaited(_resetVoiceStudioProvider(activeProviderId)),
          ),
          children: [
            Text(
              '老师、每位学生和思想家都可以在当前服务下拥有单独音色；默认分配保留你现在熟悉的基线，新分配只会覆盖这一项。',
              style: TextStyle(
                color: AppColors.warmGray.withValues(alpha: 0.95),
                fontSize: 12,
              ),
            ),
            const SizedBox(height: 12),
            for (final speaker in configurableVoiceSpeakers) ...[
              buildSpeakerCard(speaker),
              if (speaker != configurableVoiceSpeakers.last)
                const SizedBox(height: 10),
            ],
          ],
        ),
      ],
    );
  }

  Widget _buildGeneralTab(
      LocalSettings s,
      AsyncValue<CurrentConfig> currentAsync,
      AsyncValue<Map<String, ServiceHealth>> healthAsync) {
    return ListView(
      controller: _generalScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildInteractionSection(s),
        const SizedBox(height: 14),
        _buildSummarySection(s, currentAsync),
        const SizedBox(height: 14),
        _buildHealthSection(healthAsync),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildConfigProfilesTab(
    LocalSettings s,
    AsyncValue<CurrentConfig> currentAsync,
    AsyncValue<List<SavedConfigProfile>> profilesAsync,
  ) {
    final current = currentAsync.valueOrNull;
    final profiles = profilesAsync.valueOrNull ?? const <SavedConfigProfile>[];

    return ListView(
      controller: _profilesScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _Section(
          title: '保存当前整套配置',
          icon: Icons.bookmark_add_outlined,
          children: [
            const Text(
              '把当前 AI、语音和本地交互偏好保存成一个配置集，之后可以在首页直接加载。',
              style: TextStyle(color: AppColors.warmGray, fontSize: 12.5),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                _ConfigBadge(
                    label: 'LLM',
                    value: current?.llmProviderName ?? s.llmProvider),
                _ConfigBadge(label: '模型', value: current?.model ?? '-'),
                _ConfigBadge(
                    label: 'ASR', value: current?.asrProvider ?? s.asrProvider),
                _ConfigBadge(
                    label: 'TTS', value: current?.ttsProvider ?? s.ttsProvider),
                _ConfigBadge(label: '服务器', value: s.serverUrl),
              ],
            ),
            const SizedBox(height: 12),
            const _Label('配置名称'),
            _Field(
                ctrl: _profileNameCtrl,
                hint: '例如：Gemini + CapsWriter + OpenVoice'),
            const SizedBox(height: 10),
            const _Label('备注（可选）'),
            _Field(ctrl: _profileDescriptionCtrl, hint: '可写当前用途、适用场景或账号说明'),
            const SizedBox(height: 12),
            Row(
              children: [
                _GoldBtn(
                  _savingConfigProfile ? '保存中…' : '保存当前配置',
                  onTap: _savingConfigProfile
                      ? null
                      : () => _saveCurrentConfigProfile(s),
                ),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    '保存后不会覆盖 .env，而是单独生成一套可随时载入的配置快照。',
                    style: TextStyle(color: AppColors.warmGray, fontSize: 11.5),
                  ),
                ),
              ],
            ),
          ],
        ),
        const SizedBox(height: 14),
        _Section(
          title: '已保存配置',
          icon: Icons.library_books_outlined,
          children: [
            if (profilesAsync.isLoading)
              const Padding(
                padding: EdgeInsets.only(bottom: 12),
                child: LinearProgressIndicator(color: AppColors.amberGold),
              ),
            if (profilesAsync.hasError)
              Text(
                '配置列表加载失败: ${profilesAsync.error}',
                style: const TextStyle(color: Colors.redAccent, fontSize: 12),
              )
            else if (profiles.isEmpty)
              const Text(
                '还没有保存过配置。先在上方输入名称并保存一套。',
                style: TextStyle(color: AppColors.warmGray, fontSize: 12.5),
              )
            else
              ...profiles.map((profile) {
                final busy = _profileBusyId == profile.profileId;
                return Container(
                  margin: const EdgeInsets.only(bottom: 12),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(14),
                    color: AppColors.studyWallLight.withValues(alpha: 0.36),
                    border: Border.all(
                      color: AppColors.amberGold.withValues(alpha: 0.12),
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              profile.name,
                              style: const TextStyle(
                                color: AppColors.warmWhite,
                                fontSize: 14,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                          Text(
                            profile.updatedAt.isEmpty
                                ? ''
                                : profile.updatedAt
                                    .replaceFirst('T', ' ')
                                    .split('.')
                                    .first,
                            style: const TextStyle(
                              color: AppColors.warmGray,
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                      if (profile.description.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text(
                          profile.description,
                          style: const TextStyle(
                            color: AppColors.warmGray,
                            fontSize: 12,
                          ),
                        ),
                      ],
                      const SizedBox(height: 10),
                      Wrap(
                        spacing: 10,
                        runSpacing: 10,
                        children: [
                          _ConfigBadge(
                              label: 'LLM',
                              value: profile.llmProvider.isEmpty
                                  ? '-'
                                  : profile.llmProvider),
                          _ConfigBadge(
                              label: '模型',
                              value:
                                  profile.model.isEmpty ? '-' : profile.model),
                          _ConfigBadge(
                              label: 'ASR',
                              value: profile.asrProvider.isEmpty
                                  ? '-'
                                  : profile.asrProvider),
                          _ConfigBadge(
                              label: 'TTS',
                              value: profile.ttsProvider.isEmpty
                                  ? '-'
                                  : profile.ttsProvider),
                        ],
                      ),
                      const SizedBox(height: 10),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          _OutBtn(
                            busy ? '处理中…' : '立即载入',
                            onTap: busy
                                ? null
                                : () => _loadSavedConfigProfile(profile),
                          ),
                          _OutBtn(
                            '删除',
                            onTap: busy
                                ? null
                                : () => _deleteSavedConfigProfile(profile),
                          ),
                        ],
                      ),
                    ],
                  ),
                );
              }),
          ],
        ),
        const SizedBox(height: 32),
      ],
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Benchmark sections (Req5, Req6, Req7)
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildLlmBenchmarkSection() => _Section(
        title: 'AI 模型响应测速',
        icon: Icons.speed_outlined,
        children: [
          Row(children: [
            _GoldBtn(
              _benchmarkingLlm ? '测试中...' : '开始测试',
              onTap: _benchmarkingLlm ? null : _runLlmBenchmark,
            ),
            if (_llmBenchmark != null) ...[
              const SizedBox(width: 12),
              Text(
                '推荐: ${_llmBenchmark!['recommended'] ?? _llmBenchmark!['results']?[0]?['provider'] ?? '-'}',
                style:
                    const TextStyle(color: AppColors.amberGold, fontSize: 12),
              ),
            ],
          ]),
          if (_benchmarkingLlm)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(color: AppColors.amberGold),
            ),
          if (_llmBenchmark != null) _buildBenchmarkResults(_llmBenchmark!),
        ],
      );

  Widget _buildAsrBenchmarkSection() => _Section(
        title: 'ASR 服务性能对比',
        icon: Icons.timer_outlined,
        children: [
          Row(children: [
            _GoldBtn(
              _benchmarkingAsr ? '测试中...' : '全部测试（3轮）',
              onTap: _benchmarkingAsr ? null : _runAsrBenchmark,
            ),
            if (_asrBenchmark?['recommended'] != null) ...[
              const SizedBox(width: 12),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.green.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  '推荐: ${_asrBenchmark!['recommended']}',
                  style: const TextStyle(color: Colors.green, fontSize: 12),
                ),
              ),
            ],
          ]),
          if (_benchmarkingAsr)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(color: AppColors.amberGold),
            ),
          if (_asrBenchmark != null) _buildBenchmarkResults(_asrBenchmark!),
        ],
      );

  Widget _buildTtsBenchmarkSection() => _Section(
        title: 'TTS 服务性能对比',
        icon: Icons.timer_outlined,
        children: [
          Row(children: [
            _GoldBtn(
              _benchmarkingTts ? '测试中...' : '全部测试（3轮）',
              onTap: _benchmarkingTts ? null : _runTtsBenchmark,
            ),
            if (_ttsBenchmark?['recommended'] != null) ...[
              const SizedBox(width: 12),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: Colors.green.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  '推荐: ${_ttsBenchmark!['recommended']}',
                  style: const TextStyle(color: Colors.green, fontSize: 12),
                ),
              ),
            ],
          ]),
          if (_benchmarkingTts)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(color: AppColors.amberGold),
            ),
          if (_ttsBenchmark != null) _buildBenchmarkResults(_ttsBenchmark!),
        ],
      );

  Widget _buildVoiceServiceTestSection({
    required String type,
    required LocalSettings settings,
    required SpeechConfig? speechConfig,
  }) =>
      _Section(
        title: '独立服务测试',
        icon: Icons.science_outlined,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: _serviceTestProviderIds(
              type: type,
              settings: settings,
              speechConfig: speechConfig,
            )
                .map((svc) => _OutBtn(
                      svc,
                      onTap: _testingVoiceService
                          ? null
                          : () => _runVoiceServiceTest(svc, type),
                    ))
                .toList(),
          ),
          if (_testingVoiceService)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(color: AppColors.amberGold),
            ),
          if (_voiceServiceTestResult != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${_voiceServiceTestResult!['service']} - ${_voiceServiceTestResult!['status']}',
                      style: TextStyle(
                        color: _voiceServiceTestResult!['status'] == 'ok'
                            ? Colors.green
                            : Colors.orange,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (_voiceServiceTestResult!['health_ms'] != null)
                      Text(
                        '健康检查: ${_voiceServiceTestResult!['health_ms']}ms',
                        style: const TextStyle(
                            color: AppColors.warmGray, fontSize: 11),
                      ),
                    if (_voiceServiceTestResult!['synth_ms'] != null)
                      Text(
                        '合成延迟: ${_voiceServiceTestResult!['synth_ms']}ms  音频: ${_voiceServiceTestResult!['audio_size']}B',
                        style: const TextStyle(
                            color: AppColors.warmGray, fontSize: 11),
                      ),
                    if (_voiceServiceTestResult!['error'] != null)
                      Text(
                        '错误: ${_voiceServiceTestResult!['error']}',
                        style: const TextStyle(
                            color: Colors.redAccent, fontSize: 11),
                      ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              _OutBtn(
                _runningDeepVoiceTest
                    ? '深度测试中…'
                    : (type == 'asr' ? '🧪 ASR 示例识别' : '🧪 TTS 示例合成'),
                onTap:
                    (_runningDeepVoiceTest || _voiceServiceTestResult == null)
                        ? null
                        : () => _runDeepVoiceTest(
                              (_voiceServiceTestResult!['service'] ?? '')
                                  .toString(),
                              type,
                            ),
                loading: _runningDeepVoiceTest,
              ),
              const SizedBox(width: 8),
              Text(
                type == 'asr' ? '生成示例识别文本供你确认' : '返回合成延迟与音频大小',
                style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
              ),
            ],
          ),
          if (_deepVoiceTestResult != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                decoration: BoxDecoration(
                  color: Colors.black.withValues(alpha: 0.25),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(
                      color: AppColors.warmGray.withValues(alpha: 0.2)),
                ),
                child: _deepVoiceTestResult!['status'] == 'ok'
                    ? (type == 'asr'
                        ? Text(
                            '原文：${_deepVoiceTestResult!['expected_text']} ｜ 识别：${_deepVoiceTestResult!['recognized_text']} ｜ 匹配度：${_deepVoiceTestResult!['match_percent']}%',
                            style: const TextStyle(
                                color: AppColors.warmWhite, fontSize: 11),
                          )
                        : Text(
                            '文本：${_deepVoiceTestResult!['preview_text']} ｜ 延迟：${_deepVoiceTestResult!['synth_ms']}ms ｜ 音频：${_deepVoiceTestResult!['audio_size']}B',
                            style: const TextStyle(
                                color: AppColors.warmWhite, fontSize: 11),
                          ))
                    : Text(
                        '深度测试失败：${_deepVoiceTestResult!['error'] ?? '未知错误'}',
                        style: const TextStyle(
                            color: Colors.redAccent, fontSize: 11),
                      ),
              ),
            ),
          const SizedBox(height: 12),
          _buildInteractiveVoiceDiagnosticCard(
            type: type,
            settings: settings,
            speechConfig: speechConfig,
          ),
        ],
      );

  Widget _buildInteractiveVoiceDiagnosticCard({
    required String type,
    required LocalSettings settings,
    required SpeechConfig? speechConfig,
  }) {
    final providerId =
        type == 'asr' ? settings.asrProvider : settings.ttsProvider;
    final providerUrl = _resolveSpeechProviderUrl(
      speechConfig,
      providerId,
      isAsr: type == 'asr',
    );

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.22),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: AppColors.warmGray.withValues(alpha: 0.2),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            type == 'asr' ? '实机录音测试' : '实机试听测试',
            style: const TextStyle(
              color: AppColors.warmWhite,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            type == 'asr'
                ? '直接调用当前启用的 ${providerId.toUpperCase()}，你可以当场确认是不是“真能听见你说话”。'
                : '直接调用当前启用的 ${providerId.toUpperCase()}，你可以当场确认是不是“真能播出声音”。',
            style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
          ),
          const SizedBox(height: 4),
          Text(
            providerUrl.isEmpty ? '当前地址：跟随后端默认配置' : '当前地址：$providerUrl',
            style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
          ),
          const SizedBox(height: 10),
          if (type == 'asr') ...[
            Row(
              children: [
                _OutBtn(
                  _interactiveAsrRunning ? '结束并整理文本' : '开始录一段',
                  onTap: () =>
                      _toggleInteractiveAsrTest(settings, speechConfig),
                  loading: false,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _interactiveAsrRunning
                        ? '正在监听，请说一句完整的话后再点一次结束。'
                        : '建议先说一句完整短句，便于判断是权限问题还是服务问题。',
                    style: const TextStyle(
                        color: AppColors.warmGray, fontSize: 11),
                  ),
                ),
              ],
            ),
            if (_interactiveAsrText.trim().isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: SelectableText(
                  _interactiveAsrText,
                  style: const TextStyle(
                    color: AppColors.warmWhite,
                    fontSize: 12,
                  ),
                ),
              ),
            if (_interactiveAsrError != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '识别错误：$_interactiveAsrError',
                  style: const TextStyle(
                    color: Colors.redAccent,
                    fontSize: 11,
                  ),
                ),
              ),
          ] else ...[
            TextField(
              controller: _interactiveTtsTextCtrl,
              minLines: 2,
              maxLines: 3,
              style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
              decoration: InputDecoration(
                hintText: '输入一段要试听的文本…',
                hintStyle:
                    const TextStyle(color: AppColors.warmGray, fontSize: 12),
                filled: true,
                fillColor: AppColors.studyWall,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: AppColors.warmGray),
                ),
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                _OutBtn(
                  _interactiveTtsRunning ? '停止试听' : '立即试听',
                  onTap: () =>
                      _toggleInteractiveTtsTest(settings, speechConfig),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    _interactiveTtsRunning
                        ? '正在播放，请直接确认当前扬声器有没有声音。'
                        : '这一步是真正的播放，不只是测速接口。',
                    style: const TextStyle(
                        color: AppColors.warmGray, fontSize: 11),
                  ),
                ),
              ],
            ),
            if (_interactiveTtsStatus != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  _interactiveTtsStatus!,
                  style: const TextStyle(
                    color: AppColors.warmWhite,
                    fontSize: 11,
                  ),
                ),
              ),
            if (_interactiveTtsError != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  '试听错误：$_interactiveTtsError',
                  style: const TextStyle(
                    color: Colors.redAccent,
                    fontSize: 11,
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }

  Widget _buildBenchmarkResults(Map<String, dynamic> data) {
    final results = (data['results'] as List<dynamic>?) ?? [];
    if (results.isEmpty) {
      return const Padding(
        padding: EdgeInsets.only(top: 8),
        child: Text('无结果',
            style: TextStyle(color: AppColors.warmGray, fontSize: 12)),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        children: results.map((r) {
          final m = r as Map<String, dynamic>;
          final name =
              m['service'] ?? m['provider_name'] ?? m['provider'] ?? '-';
          final status = (m['status'] ?? 'unknown').toString();
          final isOk = status == 'ok';
          final isSkipped = status == 'skipped';
          String detail = '';
          if (m.containsKey('short_text')) {
            detail =
                '短: ${m['short_text']?['avg_ms'] ?? '-'}ms  长: ${m['long_text']?['avg_ms'] ?? '-'}ms';
          } else if (m.containsKey('latency')) {
            detail = '平均: ${m['latency']?['avg_ms'] ?? '-'}ms';
          } else if (m.containsKey('avg_ms')) {
            detail =
                '平均: ${m['avg_ms']}ms  最小: ${m['min_ms']}ms  最大: ${m['max_ms']}ms';
          } else if (m.containsKey('error')) {
            detail = m['error'] as String;
          }
          if (detail.isEmpty && isSkipped) {
            final reason = (m['reason'] ?? '').toString().trim();
            detail = reason.isEmpty ? '该项被跳过' : '已跳过: $reason';
          }
          final note = (m['note'] ?? '').toString().trim();
          if (note.isNotEmpty) {
            detail = detail.isEmpty ? note : '$detail  ｜  $note';
          }

          final iconData = isOk
              ? Icons.check_circle
              : (isSkipped ? Icons.remove_circle_outline : Icons.cancel);
          final iconColor = isOk
              ? Colors.green
              : (isSkipped ? AppColors.warmGray : Colors.red);
          final detailColor = isOk
              ? AppColors.warmGray
              : (isSkipped ? Colors.orangeAccent : Colors.redAccent);

          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: [
                Icon(
                  iconData,
                  size: 14,
                  color: iconColor,
                ),
                const SizedBox(width: 6),
                Text(name,
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.w600)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(detail,
                      style: TextStyle(color: detailColor, fontSize: 11)),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }

  // ── Benchmark actions ────────────────────────────────────────────────────

  Future<void> _runAsrBenchmark() async {
    setState(() {
      _benchmarkingAsr = true;
      _asrBenchmark = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final resp = await client.dio
          .post('/api/v1/benchmark/asr', queryParameters: {'rounds': 3});
      final merged =
          Map<String, dynamic>.from(resp.data as Map<String, dynamic>);
      final results = ((merged['results'] as List<dynamic>?) ?? [])
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();

      final browserResult = await _runBrowserAsrBenchmarkLocal(rounds: 3);
      results.removeWhere((r) => (r['service'] ?? '').toString() == 'browser');
      results.add(browserResult);

      final ok = results
          .where((r) => r['status'] == 'ok' && r['latency'] is Map)
          .toList()
        ..sort((a, b) {
          final av =
              ((a['latency'] as Map)['avg_ms'] as num?)?.toDouble() ?? 999999;
          final bv =
              ((b['latency'] as Map)['avg_ms'] as num?)?.toDouble() ?? 999999;
          return av.compareTo(bv);
        });

      merged['results'] = results;
      merged['recommended'] =
          ok.isNotEmpty ? ok.first['service'] : merged['recommended'];
      setState(() => _asrBenchmark = merged);
    } catch (e) {
      _snackErr('ASR 测试失败: $e');
    } finally {
      setState(() => _benchmarkingAsr = false);
    }
  }

  Future<Map<String, dynamic>> _runBrowserAsrBenchmarkLocal(
      {int rounds = 3}) async {
    final serverUrl =
        _resolvedServerUrl(ref.read(localSettingsProvider).valueOrNull);
    final asr = createAsrService('browser', serverUrl: serverUrl);
    try {
      if (!asr.isAvailable) {
        return {
          'service': 'browser',
          'status': 'skipped',
          'reason': 'browser asr unavailable',
          'error': '浏览器原生 ASR 当前不可用',
          'note': '受浏览器实现与权限策略影响',
        };
      }

      final latencies = <double>[];
      for (var i = 0; i < rounds; i++) {
        final sw = Stopwatch()..start();
        await asr.startListening();
        sw.stop();
        latencies.add(sw.elapsedMilliseconds.toDouble());
        await Future.delayed(const Duration(milliseconds: 180));
        await asr.stopListening();
        await Future.delayed(const Duration(milliseconds: 120));
      }

      final avg = latencies.reduce((a, b) => a + b) / latencies.length;
      final minV = latencies.reduce((a, b) => a < b ? a : b);
      final maxV = latencies.reduce((a, b) => a > b ? a : b);

      return {
        'service': 'browser',
        'status': 'ok',
        'latency': {
          'avg_ms': double.parse(avg.toStringAsFixed(1)),
          'min_ms': double.parse(minV.toStringAsFixed(1)),
          'max_ms': double.parse(maxV.toStringAsFixed(1)),
          'rounds': latencies.length,
          'errors': 0,
        },
        'note': '本地浏览器 ASR 启动延迟（不含口述识别耗时）',
      };
    } catch (e) {
      return {
        'service': 'browser',
        'status': 'error',
        'error': '$e',
        'note': '请允许麦克风权限后重试',
      };
    } finally {
      asr.dispose();
    }
  }

  Future<void> _runTtsBenchmark() async {
    setState(() {
      _benchmarkingTts = true;
      _ttsBenchmark = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final resp = await client.dio
          .post('/api/v1/benchmark/tts', queryParameters: {'rounds': 3});
      setState(() => _ttsBenchmark = resp.data as Map<String, dynamic>);
    } catch (e) {
      _snackErr('TTS 测试失败: $e');
    } finally {
      setState(() => _benchmarkingTts = false);
    }
  }

  Future<void> _runLlmBenchmark() async {
    setState(() {
      _benchmarkingLlm = true;
      _llmBenchmark = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final resp = await client.dio
          .post('/api/v1/benchmark/llm', queryParameters: {'rounds': 2});
      setState(() => _llmBenchmark = resp.data as Map<String, dynamic>);
    } catch (e) {
      _snackErr('LLM 测试失败: $e');
    } finally {
      setState(() => _benchmarkingLlm = false);
    }
  }

  Future<void> _runVoiceServiceTest(String serviceId, String type) async {
    setState(() {
      _testingVoiceService = true;
      _voiceServiceTestResult = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final resp = await client.dio
          .post('/api/v1/benchmark/voice/test', queryParameters: {
        'service_id': serviceId,
        'service_type': type,
      });
      setState(
          () => _voiceServiceTestResult = resp.data as Map<String, dynamic>);
    } catch (e) {
      _snackErr('服务测试失败: $e');
    } finally {
      setState(() => _testingVoiceService = false);
    }
  }

  Future<void> _runDeepVoiceTest(String serviceId, String type) async {
    setState(() {
      _runningDeepVoiceTest = true;
      _deepVoiceTestResult = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final resp = await client.dio
          .post('/api/v1/benchmark/voice/deep-test', queryParameters: {
        'service_id': serviceId,
        'service_type': type,
      });
      setState(() => _deepVoiceTestResult = resp.data as Map<String, dynamic>);
    } catch (e) {
      _snackErr('深度测试失败: $e');
    } finally {
      setState(() => _runningDeepVoiceTest = false);
    }
  }

  String _resolveSpeechProviderUrl(
    SpeechConfig? speechConfig,
    String providerId, {
    required bool isAsr,
  }) {
    final manualUrl = _voiceUrlCtrl[providerId]?.text.trim() ?? '';
    if (manualUrl.isNotEmpty) {
      return _normalizeSpeechProviderUrl(
        providerId,
        manualUrl,
        isAsr: isAsr,
      );
    }
    final providers = isAsr
        ? (speechConfig?.asrProviders ?? const <SpeechProviderInfo>[])
        : (speechConfig?.ttsProviders ?? const <SpeechProviderInfo>[]);
    for (final provider in providers) {
      if (provider.id == providerId) {
        if (provider.url.trim().isNotEmpty) {
          return _normalizeSpeechProviderUrl(
            providerId,
            provider.url.trim(),
            isAsr: isAsr,
          );
        }
        if (provider.defaultUrl.trim().isNotEmpty) {
          return _normalizeSpeechProviderUrl(
            providerId,
            provider.defaultUrl.trim(),
            isAsr: isAsr,
          );
        }
      }
    }
    return _fallbackSpeechProviderUrl(providerId, isAsr: isAsr);
  }

  String _normalizeSpeechProviderUrl(
    String providerId,
    String url, {
    required bool isAsr,
  }) {
    final trimmed = url.trim();
    if (trimmed.isEmpty) {
      return _fallbackSpeechProviderUrl(providerId, isAsr: isAsr);
    }

    // 兼容旧配置：若仍指向 6666 聚合网关，自动切到独立端口。
    if (trimmed.contains(':6666')) {
      final fallback = _fallbackSpeechProviderUrl(providerId, isAsr: isAsr);
      if (fallback.isNotEmpty) {
        return fallback;
      }
    }

    return trimmed;
  }

  String _fallbackSpeechProviderUrl(String providerId, {required bool isAsr}) {
    if (isAsr) {
      switch (providerId) {
        case 'capswriter':
          return 'ws://localhost:6016';
        case 'vosk':
          return 'http://localhost:6702';
        case 'funasr':
          return 'ws://localhost:10095';
      }
      return '';
    }

    switch (providerId) {
      case 'chattts':
        return 'http://localhost:9998';
      case 'edge_tts':
        return 'http://localhost:5051';
      case 'cosyvoice':
        return 'http://localhost:50000';
      case 'vibevoice':
        return 'http://localhost:6704';
      case 'fireredtts':
        return 'http://localhost:6706';
      case 'openvoice':
        return 'http://localhost:6707';
    }
    return '';
  }

  bool _isCloudAsrProvider(String providerId) => const <String>{
        'openai_whisper',
        'siliconflow_asr',
        'groq_whisper',
      }.contains(providerId);

  List<String> _serviceTestProviderIds({
    required String type,
    required LocalSettings settings,
    required SpeechConfig? speechConfig,
  }) {
    final providers = type == 'asr'
        ? (speechConfig?.asrProviders ?? const <SpeechProviderInfo>[])
        : (speechConfig?.ttsProviders ?? const <SpeechProviderInfo>[]);
    final activeId =
        type == 'asr' ? settings.asrProvider : settings.ttsProvider;
    final ordered = <String>[];

    void addProvider(String providerId) {
      if (providerId == 'disabled' || providerId == 'browser') return;
      if (!ordered.contains(providerId)) {
        ordered.add(providerId);
      }
    }

    addProvider(activeId);
    for (final provider in providers) {
      addProvider(provider.id);
    }

    if (ordered.isEmpty) {
      return type == 'asr'
          ? <String>['capswriter', 'vosk', 'funasr', 'siliconflow_asr']
          : <String>[
              'edge_tts',
              'vibevoice',
              'fireredtts',
              'openvoice',
              'cosyvoice'
            ];
    }

    return ordered;
  }

  List<SpeechProviderInfo> _interactiveAsrCandidates(
    LocalSettings settings,
    SpeechConfig? speechConfig,
  ) {
    final providers =
        speechConfig?.asrProviders ?? const <SpeechProviderInfo>[];
    if (providers.isEmpty) {
      const fallbackNames = <String, String>{
        'browser': '浏览器原生语音识别',
        'capswriter': 'CapsWriter 本地服务（推荐，低延迟）',
        'vosk': 'Vosk 本地服务（支持流式）',
        'funasr': 'FunASR 本地服务',
        'openai_whisper': 'OpenAI Whisper API',
        'siliconflow_asr': '硅基流动 ASR',
        'groq_whisper': 'Groq Whisper API',
      };
      const fallbackOrder = <String>[
        'capswriter',
        'vosk',
        'funasr',
        'browser',
        'openai_whisper',
        'siliconflow_asr',
        'groq_whisper',
      ];

      final orderedIds = <String>[];
      void addFallback(String providerId) {
        if (providerId.isEmpty || providerId == 'disabled') return;
        if (!orderedIds.contains(providerId)) {
          orderedIds.add(providerId);
        }
      }

      addFallback(settings.asrProvider);
      for (final providerId in fallbackOrder) {
        addFallback(providerId);
      }

      return orderedIds
          .map(
            (providerId) => SpeechProviderInfo(
              id: providerId,
              name: fallbackNames[providerId] ?? providerId,
              isActive: providerId == settings.asrProvider,
              available: providerId != 'disabled',
              mode: switch (providerId) {
                'openai_whisper' ||
                'siliconflow_asr' ||
                'groq_whisper' =>
                  'cloud',
                _ => 'local',
              },
            ),
          )
          .toList(growable: false);
    }

    final ordered = <SpeechProviderInfo>[];

    void addProvider(SpeechProviderInfo provider) {
      if (provider.id == 'disabled') return;
      if (ordered.any((item) => item.id == provider.id)) return;
      ordered.add(provider);
    }

    for (final provider in providers) {
      if (provider.id == settings.asrProvider) {
        addProvider(provider);
      }
    }
    for (final provider in providers) {
      if (provider.available) {
        addProvider(provider);
      }
    }
    for (final provider in providers) {
      addProvider(provider);
    }

    return ordered;
  }

  bool _supportsStreamingAsr(String providerId) => const <String>{
        'capswriter',
        'vosk',
        'funasr',
      }.contains(providerId);

  String? _resolveInteractiveVoice(
      String providerId, SpeechConfig? speechConfig) {
    final selected = _selectedVoice[providerId]?.trim();
    if (selected != null && selected.isNotEmpty) {
      return selected;
    }
    if (providerId == 'cosyvoice') {
      return speechConfig?.cosyvoiceVoice;
    }
    return speechConfig?.ttsVoice;
  }

  Map<String, dynamic> _buildVoiceRuntimeUpdates({
    required String providerId,
    required bool isAsr,
    String? voice,
  }) {
    final updates = <String, dynamic>{};
    final url = _voiceUrlCtrl[providerId]?.text.trim() ?? '';
    final key = _voiceKeyCtrl[providerId]?.text.trim() ?? '';
    final model = _voiceModelCtrl[providerId]?.text.trim() ?? '';

    const urlMap = {
      'chattts': 'chattts_url',
      'capswriter': 'capswriter_url',
      'vosk': 'vosk_url',
      'funasr': 'funasr_url',
      'edge_tts': 'edge_tts_url',
      'cosyvoice': 'cosyvoice_url',
      'vibevoice': 'vibevoice_url',
      'fireredtts': 'fireredtts_url',
      'openvoice': 'openvoice_url',
      'openai_whisper': 'openai_whisper_base_url',
      'siliconflow_asr': 'siliconflow_asr_base_url',
      'groq_whisper': 'groq_whisper_base_url',
      'openai_tts': 'openai_tts_base_url',
      'siliconflow_tts': 'siliconflow_tts_base_url',
    };
    const keyMap = {
      'openai_whisper': 'openai_whisper_api_key',
      'siliconflow_asr': 'siliconflow_asr_api_key',
      'groq_whisper': 'groq_whisper_api_key',
      'openai_tts': 'openai_tts_api_key',
      'siliconflow_tts': 'siliconflow_tts_api_key',
    };
    const modelMap = {
      'openai_whisper': 'openai_whisper_model',
      'siliconflow_asr': 'siliconflow_asr_model',
      'groq_whisper': 'groq_whisper_model',
      'openai_tts': 'openai_tts_model',
      'siliconflow_tts': 'siliconflow_tts_model',
    };
    const voiceMap = {
      'edge_tts': 'tts_voice',
      'openvoice': 'tts_voice',
      'cosyvoice': 'cosyvoice_voice',
      'openai_tts': 'openai_tts_voice',
    };

    if (url.isNotEmpty && urlMap.containsKey(providerId)) {
      updates[urlMap[providerId]!] = url;
    }
    if (key.isNotEmpty && keyMap.containsKey(providerId)) {
      updates[keyMap[providerId]!] = key;
    }
    if (model.isNotEmpty && modelMap.containsKey(providerId)) {
      updates[modelMap[providerId]!] = model;
    }
    if (voice != null && voice.isNotEmpty && voiceMap.containsKey(providerId)) {
      updates[voiceMap[providerId]!] = voice;
    }
    updates[isAsr ? 'asr_provider' : 'tts_provider'] = providerId;
    return updates;
  }

  Future<void> _stopInteractiveAsrTest() async {
    final service = _interactiveAsrService;
    if (service == null) return;

    try {
      await service.stopListening();
    } catch (_) {}

    // 给最后一帧最终文本一个极短的落地时间，避免页面比流式回调更早收尾。
    await Future.delayed(const Duration(milliseconds: 150));

    var refinedText = _interactiveAsrText.trim();
    if (refinedText.isNotEmpty) {
      try {
        refinedText = await service.refineTranscript(refinedText);
      } catch (_) {}
    }

    await _interactiveAsrSub?.cancel();
    _interactiveAsrSub = null;
    service.dispose();

    if (!mounted) return;
    setState(() {
      _interactiveAsrService = null;
      _interactiveAsrRunning = false;
      _interactiveAsrText = refinedText;
    });

    if (refinedText.isEmpty && (_interactiveAsrError?.trim().isEmpty ?? true)) {
      _snackErr('没有收到识别结果，请检查麦克风权限或 ASR 服务配置。');
    }
  }

  Future<void> _toggleInteractiveAsrTest(
    LocalSettings settings,
    SpeechConfig? speechConfig,
  ) async {
    if (_interactiveAsrRunning) {
      await _stopInteractiveAsrTest();
      return;
    }

    final serverUrl = _resolvedServerUrl(settings);
    final providerId = settings.asrProvider.trim();
    if (providerId.isEmpty || providerId == 'disabled') {
      _snackErr('请先选择一个可用的 ASR 服务。');
      return;
    }

    final candidate = _interactiveAsrCandidates(settings, speechConfig)
        .cast<SpeechProviderInfo?>()
        .firstWhere(
          (item) => item?.id == providerId,
          orElse: () => null,
        );
    if (candidate != null &&
        !candidate.available &&
        providerId != 'browser' &&
        _isCloudAsrProvider(providerId)) {
      _snackErr('$providerId 当前不可用，请先检查服务状态。');
      return;
    }

    final providerUrl =
        _resolveSpeechProviderUrl(speechConfig, providerId, isAsr: true);
    // 设置页“实机录音测试”对本地 ASR 强制走直连流式链路，避免回退到
    // 后端上传转写（依赖 ffmpeg）导致“录音测试启动即失败”。
    final preferServerProxy = _isCloudAsrProvider(providerId);
    final asr = createAsrService(
      providerId,
      serverUrl: serverUrl,
      providerUrl: providerUrl,
      preferServerProxy: preferServerProxy,
    );

    await _interactiveAsrSub?.cancel();
    _interactiveAsrSub = asr.transcriptionStream.listen(
      (result) {
        final nextText = result.text.trim();
        if (nextText.isEmpty || !mounted) return;
        setState(() {
          _interactiveAsrText = nextText;
          if (result.isFinal) {
            _interactiveAsrError = null;
          }
        });
      },
      onError: (error) {
        if (!mounted) return;
        setState(() {
          _interactiveAsrError = error.toString();
        });
      },
    );

    setState(() {
      _interactiveAsrService = asr;
      _interactiveAsrRunning = true;
      _interactiveAsrText = '';
      _interactiveAsrError = null;
    });

    try {
      await asr.warmup();
      if (!asr.isAvailable) {
        throw StateError('$providerId 当前不可用');
      }
      await asr.startListening();
      await Future.delayed(const Duration(milliseconds: 250));
      if (!asr.isListening) {
        throw StateError('语音识别启动失败');
      }
    } catch (e) {
      await _interactiveAsrSub?.cancel();
      _interactiveAsrSub = null;
      asr.dispose();
      if (!mounted) return;
      setState(() {
        _interactiveAsrService = null;
        _interactiveAsrRunning = false;
        _interactiveAsrError = e.toString();
      });
      _snackErr('语音识别启动失败: $e');
    }
  }

  Future<void> _toggleInteractiveTtsTest(
    LocalSettings settings,
    SpeechConfig? speechConfig,
  ) async {
    if (_interactiveTtsRunning) {
      try {
        await _interactiveTtsService?.stop();
      } catch (_) {}
      _interactiveTtsService?.dispose();
      if (!mounted) return;
      setState(() {
        _interactiveTtsService = null;
        _interactiveTtsRunning = false;
        _interactiveTtsStatus = '已停止试听';
      });
      return;
    }

    final sampleText = _interactiveTtsTextCtrl.text.trim();
    if (sampleText.isEmpty) {
      _snackErr('请先输入一段要试听的文本。');
      return;
    }

    final serverUrl = _resolvedServerUrl(settings);
    final providerId = settings.ttsProvider;
    final voice = _resolveInteractiveVoice(providerId, speechConfig);
    final providerUrl =
        _resolveSpeechProviderUrl(speechConfig, providerId, isAsr: false);

    if (providerId != 'browser' && providerId != 'disabled') {
      try {
        await ref.read(apiClientProvider).updateConfig(
              _buildVoiceRuntimeUpdates(
                providerId: providerId,
                isAsr: false,
                voice: voice,
              ),
            );
        ref.invalidate(currentConfigProvider);
      } catch (e) {
        _snackErr('同步 TTS 配置失败，改为仅本地试听: $e');
      }
    }

    final tts = createTtsService(
      providerId,
      serverUrl: serverUrl,
      providerUrl: providerUrl,
    );

    setState(() {
      _interactiveTtsService = tts;
      _interactiveTtsRunning = true;
      _interactiveTtsError = null;
      _interactiveTtsStatus = '正在播放 ${providerId.toUpperCase()} 试听…';
    });

    try {
      await tts.speak(
        sampleText,
        voice: voice,
      );
      if (!mounted) return;
      setState(() {
        _interactiveTtsStatus = '试听完成，可直接判断当前 TTS 是否真正有声。';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _interactiveTtsError = e.toString();
        _interactiveTtsStatus = null;
      });
    } finally {
      tts.dispose();
      if (mounted) {
        setState(() {
          _interactiveTtsService = null;
          _interactiveTtsRunning = false;
        });
      }
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Section builders (reused by tabs)
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildAiConfigSection(AsyncValue<CurrentConfig> currentAsync) =>
      _Section(
        title: '当前配置',
        icon: Icons.tune_outlined,
        children: [
          const SizedBox(height: 8),
          currentAsync.when(
            data: (c) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '这里只展示当前生效的 AI 配置。服务地址跟随当前运行环境，不再在设置页单独编辑。',
                  style: TextStyle(color: AppColors.warmGray, fontSize: 12),
                ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    _ConfigBadge(label: '提供商', value: c.llmProviderName),
                    _ConfigBadge(label: '模型', value: c.model),
                    _ConfigBadge(
                      label: 'API Key',
                      value: c.apiKeyMasked.isNotEmpty ? '已保存' : '未配置',
                    ),
                  ],
                ),
              ],
            ),
            loading: () => const SizedBox.shrink(),
            error: (_, __) => const Text(
              '⚠ 无法读取当前 AI 配置，请检查后端是否正常运行。',
              style: TextStyle(color: Colors.orange, fontSize: 11),
            ),
          ),
        ],
      );

  Widget _buildLlmSection(
          AsyncValue<List<ProviderInfo>> providersAsync, LocalSettings s) =>
      _Section(
        title: 'AI 模型提供商',
        icon: Icons.smart_toy_outlined,
        children: [
          providersAsync.when(
            skipLoadingOnRefresh: true,
            data: (list) {
              final visibleProviders =
                  list.where((p) => p.id != 'gemini').toList(growable: false);
              final selectedProvider =
                  _selectedProviderForPanel(visibleProviders, s);
              if (selectedProvider == null) {
                return const Text(
                  '当前没有可用的模型提供商。',
                  style: TextStyle(color: AppColors.warmGray, fontSize: 12),
                );
              }

              final result = _providerTestResult[selectedProvider.id];
              final testing = _testingProvider[selectedProvider.id] ?? false;
              final saving = _savingProvider[selectedProvider.id] ?? false;

              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '先选择提供商，再在下方集中配置 API Key、请求地址和模型。',
                    style: TextStyle(color: AppColors.warmGray, fontSize: 12),
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: visibleProviders
                        .map(
                          (p) => _providerSelectorButton(
                            p,
                            selected: p.id == selectedProvider.id,
                            active: p.id == s.llmProvider,
                            onTap: () =>
                                setState(() => _expandedProvider = p.id),
                          ),
                        )
                        .toList(),
                  ),
                  const SizedBox(height: 14),
                  _providerForm(selectedProvider, result, testing, saving),
                ],
              );
            },
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('模型提供商加载失败: $e'),
          ),
        ],
      );

  ProviderInfo? _selectedProviderForPanel(
    List<ProviderInfo> providers,
    LocalSettings settings,
  ) {
    if (providers.isEmpty) {
      return null;
    }

    final preferredIds = <String>[
      if ((_expandedProvider ?? '').trim().isNotEmpty)
        _expandedProvider!.trim(),
      settings.llmProvider,
    ];

    for (final providerId in preferredIds) {
      for (final provider in providers) {
        if (provider.id == providerId) {
          return provider;
        }
      }
    }

    return providers.first;
  }

  Widget _buildAsrSection(
          AsyncValue<SpeechConfig> speechAsync, LocalSettings s) =>
      _Section(
        title: '语音识别（ASR）',
        icon: Icons.mic_outlined,
        children: [
          speechAsync.when(
            skipLoadingOnRefresh: true,
            data: (sp) => _buildSpeechSection(
              providers: sp.asrProviders,
              activeId: s.asrProvider,
              settings: s,
              isAsr: true,
            ),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('语音识别配置加载失败: $e'),
          ),
        ],
      );

  Widget _buildTtsSection(
          AsyncValue<SpeechConfig> speechAsync, LocalSettings s) =>
      _Section(
        title: '语音合成（TTS）',
        icon: Icons.volume_up_outlined,
        children: [
          speechAsync.when(
            skipLoadingOnRefresh: true,
            data: (sp) => _buildSpeechSection(
              providers: sp.ttsProviders,
              activeId: s.ttsProvider,
              settings: s,
              isAsr: false,
            ),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('语音合成配置加载失败: $e'),
          ),
        ],
      );

  Widget _buildSpeechSection({
    required List<SpeechProviderInfo> providers,
    required String activeId,
    required LocalSettings settings,
    required bool isAsr,
  }) {
    bool isOperational(SpeechProviderInfo provider) {
      final hasRequiredApiKey = !provider.needsApiKey || provider.hasApiKey;
      return provider.id != 'disabled' &&
          provider.available &&
          hasRequiredApiKey;
    }

    final cloudProviders =
        providers.where((p) => p.isCloud).toList(growable: false);
    final localProviders =
        providers.where((p) => !p.isCloud).toList(growable: false);

    final preferredCloudProviders = cloudProviders
        .where((p) => p.isMainlandPreferred && isOperational(p))
        .toList(growable: false);
    final otherCloudProviders = cloudProviders
        .where((p) => !p.isMainlandPreferred || !isOperational(p))
        .toList(growable: false);

    final preferredLocalProviders =
        localProviders.where(isOperational).toList(growable: false);
    final otherLocalProviders =
        localProviders.where((p) => !isOperational(p)).toList(growable: false);

    final selectedCloud = _selectedSpeechProviderForPanel(
      providers: preferredCloudProviders,
      activeId: activeId,
    );
    final selectedOtherCloud = _selectedSpeechProviderForPanel(
      providers: otherCloudProviders,
      activeId: activeId,
    );
    final activeLocal = _findSpeechProvider(providers, activeId);
    final hasOtherServices =
        otherCloudProviders.isNotEmpty || otherLocalProviders.isNotEmpty;
    final otherServicesCount =
        otherCloudProviders.length + otherLocalProviders.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (preferredCloudProviders.isNotEmpty) ...[
          Text(
            isAsr ? '云端 ASR（中国大陆优先）' : '云端 TTS（中国大陆优先）',
            style: const TextStyle(
              color: AppColors.warmWhite,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            '当前优先展示中国大陆可直接配置的云平台与默认模型。首选使用硅基流动；其余本地服务仍完整保留。',
            style: TextStyle(
              color: AppColors.warmGray.withValues(alpha: 0.95),
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: preferredCloudProviders
                .map(
                  (p) => _speechProviderSelectorButton(
                    p,
                    selected: selectedCloud?.id == p.id,
                    active: activeId == p.id,
                    onTap: () => setState(() => _expandedVoiceService = p.id),
                  ),
                )
                .toList(),
          ),
          if (selectedCloud != null) ...[
            const SizedBox(height: 12),
            _cloudVoiceProviderForm(
              selectedCloud,
              _voiceTestResult[selectedCloud.id],
              _testingVoice[selectedCloud.id] ?? false,
              _savingVoice[selectedCloud.id] ?? false,
              isAsr: isAsr,
              activeId: activeId,
            ),
          ],
        ],
        if (preferredLocalProviders.isNotEmpty) ...[
          if (preferredCloudProviders.isNotEmpty) const SizedBox(height: 16),
          Text(
            isAsr ? '可用 ASR 服务（绿标）' : '可用 TTS 服务（绿标）',
            style: const TextStyle(
              color: AppColors.warmWhite,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            '可用性来自“通用 > 本地服务状态”的刷新结果。这里单选后立即生效，无需再测试连接、应用或写入 .env。',
            style: TextStyle(
              color: AppColors.warmGray.withValues(alpha: 0.95),
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 10),
          ...preferredLocalProviders.map(
            (p) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: _speechTile(p, activeId, isAsr: isAsr),
            ),
          ),
          if (isAsr) ...[
            if (activeLocal != null &&
                !activeLocal.isCloud &&
                _supportsStreamingAsr(activeLocal.id)) ...[
              const SizedBox(height: 4),
              _buildAsrStreamingPreference(activeLocal, settings),
            ],
          ],
        ],
        if (hasOtherServices) ...[
          if (preferredCloudProviders.isNotEmpty ||
              preferredLocalProviders.isNotEmpty)
            const SizedBox(height: 10),
          Theme(
            data: Theme.of(context).copyWith(
              dividerColor: Colors.transparent,
            ),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              iconColor: AppColors.warmGray,
              collapsedIconColor: AppColors.warmGray,
              title: Text(
                '其他服务（$otherServicesCount）',
                style: const TextStyle(
                  color: AppColors.warmGray,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
              subtitle: const Text(
                '不可用、缺少配置或非大陆优先服务已折叠到这里。',
                style: TextStyle(
                  color: AppColors.warmGray,
                  fontSize: 10.5,
                ),
              ),
              children: [
                if (otherCloudProviders.isNotEmpty) ...[
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Text(
                      isAsr ? '其他云端 ASR' : '其他云端 TTS',
                      style: const TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: otherCloudProviders
                        .map(
                          (p) => _speechProviderSelectorButton(
                            p,
                            selected: selectedOtherCloud?.id == p.id,
                            active: activeId == p.id,
                            onTap: () =>
                                setState(() => _expandedVoiceService = p.id),
                          ),
                        )
                        .toList(),
                  ),
                  if (selectedOtherCloud != null) ...[
                    const SizedBox(height: 12),
                    _cloudVoiceProviderForm(
                      selectedOtherCloud,
                      _voiceTestResult[selectedOtherCloud.id],
                      _testingVoice[selectedOtherCloud.id] ?? false,
                      _savingVoice[selectedOtherCloud.id] ?? false,
                      isAsr: isAsr,
                      activeId: activeId,
                    ),
                  ],
                ],
                if (otherLocalProviders.isNotEmpty) ...[
                  if (otherCloudProviders.isNotEmpty)
                    const SizedBox(height: 12),
                  ...otherLocalProviders.map(
                    (p) => Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _speechTile(p, activeId, isAsr: isAsr),
                    ),
                  ),
                ],
                const SizedBox(height: 2),
              ],
            ),
          ),
        ],
      ],
    );
  }

  SpeechProviderInfo? _selectedSpeechProviderForPanel({
    required List<SpeechProviderInfo> providers,
    required String activeId,
  }) {
    if (providers.isEmpty) {
      return null;
    }

    final preferredIds = <String>[
      if ((_expandedVoiceService ?? '').trim().isNotEmpty)
        _expandedVoiceService!.trim(),
      activeId,
    ];

    for (final normalized in preferredIds) {
      if (normalized.isEmpty) {
        continue;
      }
      for (final provider in providers) {
        if (provider.id == normalized) {
          return provider;
        }
      }
    }

    return providers.first;
  }

  SpeechProviderInfo? _findSpeechProvider(
    List<SpeechProviderInfo> providers,
    String providerId,
  ) {
    for (final provider in providers) {
      if (provider.id == providerId) {
        return provider;
      }
    }
    return null;
  }

  Widget _buildAsrStreamingPreference(
    SpeechProviderInfo provider,
    LocalSettings settings,
  ) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        color: AppColors.studyWall.withValues(alpha: 0.68),
        border: Border.all(
          color: AppColors.warmGray.withValues(alpha: 0.18),
        ),
      ),
      child: SwitchListTile(
        title: const Text('启用流式语音识别',
            style: TextStyle(color: AppColors.warmWhite, fontSize: 13)),
        subtitle: Text(
          settings.asrStreamingEnabled
              ? '当前 ${provider.name} 会优先走实时草稿识别。'
              : '当前 ${provider.name} 会在录音结束后再上传识别。',
          style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
        ),
        value: settings.asrStreamingEnabled,
        onChanged: (enabled) => ref
            .read(localSettingsProvider.notifier)
            .setAsrStreamingEnabled(enabled),
        contentPadding: EdgeInsets.zero,
        activeThumbColor: AppColors.amberGold,
        dense: true,
      ),
    );
  }

  Widget _buildInteractionSection(LocalSettings s) => _Section(
        title: '互动与语音行为',
        icon: Icons.touch_app_outlined,
        children: [
          const Text('麦克风启动方式',
              style: TextStyle(
                  color: AppColors.warmWhite,
                  fontSize: 12,
                  fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              ChoiceChip(
                label: const Text('自动开启麦克风'),
                selected: s.micActivationMode == 'auto',
                onSelected: (_) => ref
                    .read(localSettingsProvider.notifier)
                    .setMicActivationMode('auto'),
                selectedColor: AppColors.amberGold,
                backgroundColor: AppColors.studyWall,
                labelStyle: TextStyle(
                  color: s.micActivationMode == 'auto'
                      ? Colors.black
                      : AppColors.warmWhite,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                side: BorderSide(
                  color: s.micActivationMode == 'auto'
                      ? AppColors.amberGold
                      : AppColors.warmGray.withValues(alpha: 0.45),
                ),
              ),
              ChoiceChip(
                label: const Text('用户手动开启'),
                selected: s.micActivationMode == 'manual',
                onSelected: (_) => ref
                    .read(localSettingsProvider.notifier)
                    .setMicActivationMode('manual'),
                selectedColor: AppColors.amberGold,
                backgroundColor: AppColors.studyWall,
                labelStyle: TextStyle(
                  color: s.micActivationMode == 'manual'
                      ? Colors.black
                      : AppColors.warmWhite,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                side: BorderSide(
                  color: s.micActivationMode == 'manual'
                      ? AppColors.amberGold
                      : AppColors.warmGray.withValues(alpha: 0.45),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            s.micActivationMode == 'auto'
                ? '轮到你时会自动开启麦克风；也可以用下方热键手动结束或重试。'
                : '轮到你时需要用下方热键或“讲话”按钮手动开始发言。',
            style: TextStyle(
              color: AppColors.warmGray.withValues(alpha: 0.92),
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 14),
          const Text('麦克风热键',
              style: TextStyle(
                color: AppColors.warmWhite,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: 4),
          Text(
            '在会话页用此热键开始/结束发言。macOS 默认 Right Option (右 Option)；浏览器有时分不清左右 Option，可选「任意 Option」获得更高兼容性。',
            style: TextStyle(
              color: AppColors.warmGray.withValues(alpha: 0.92),
              fontSize: 11,
            ),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            initialValue: s.micHotkey,
            dropdownColor: const Color(0xFF1B2D40),
            decoration: const InputDecoration(
              isDense: true,
              border: OutlineInputBorder(),
            ),
            items: const [
              DropdownMenuItem(
                  value: 'right_alt',
                  child: Text('Right Option / Right Alt（推荐 mac）',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'left_alt',
                  child: Text('Left Option / Left Alt',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'any_alt',
                  child: Text('任意 Option / Alt（兼容模式）',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'f12',
                  child: Text('F12',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'right_ctrl',
                  child: Text('Right Control',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'left_ctrl',
                  child: Text('Left Control',
                      style: TextStyle(color: AppColors.warmWhite))),
              DropdownMenuItem(
                  value: 'space',
                  child: Text('Space',
                      style: TextStyle(color: AppColors.warmWhite))),
            ],
            onChanged: (v) {
              if (v == null) return;
              ref.read(localSettingsProvider.notifier).setMicHotkey(v);
            },
          ),
          const SizedBox(height: 6),
          SwitchListTile(
            title: const Text('流式显示用户字幕',
                style: TextStyle(color: AppColors.warmWhite)),
            subtitle: const Text(
              '开启后，用户发言会实时滚动显示识别字幕；自由话题页的麦克风草稿也遵循这个设置。',
              style: TextStyle(color: AppColors.warmGray, fontSize: 11),
            ),
            value: s.streamUserSubtitles,
            onChanged: (v) {
              ref
                  .read(localSettingsProvider.notifier)
                  .setStreamUserSubtitles(v);
            },
            contentPadding: EdgeInsets.zero,
            activeThumbColor: AppColors.amberGold,
            dense: true,
          ),
        ],
      );

  Widget _buildTavilySection(AsyncValue<CurrentConfig> currentAsync) =>
      _Section(
        title: '网络搜索（Tavily）',
        icon: Icons.travel_explore_outlined,
        children: [
          currentAsync.when(
            data: (c) => Row(children: [
              Icon(c.webSearchEnabled ? Icons.check_circle : Icons.cancel,
                  size: 14,
                  color: c.webSearchEnabled ? Colors.green : Colors.red),
              const SizedBox(width: 6),
              Text(c.webSearchEnabled ? '✅ Tavily 已启用' : '❌ 未启用',
                  style: TextStyle(
                      color: c.webSearchEnabled ? Colors.green : Colors.orange,
                      fontSize: 12)),
            ]),
            loading: () => const SizedBox.shrink(),
            error: (_, __) => const SizedBox.shrink(),
          ),
          const SizedBox(height: 8),
          const Text('为圆桌讨论启用实时网络搜索能力',
              style: TextStyle(color: AppColors.warmGray, fontSize: 11)),
          const SizedBox(height: 8),
          const _Label('Tavily API Key'),
          _Field(
              ctrl: _tavilyKeyCtrl,
              hint: '输入 Tavily API Key（tvly-...）',
              obscure: true),
          const SizedBox(height: 8),
          Row(children: [
            _OutBtn(_testingTavily ? '测试中…' : '🔌 测试连接',
                onTap: _testingTavily ? null : _testTavily,
                loading: _testingTavily),
            if (_tavilyTestResult != null) ...[
              const SizedBox(width: 8),
              Icon(
                  _tavilyTestResult!['success'] == true
                      ? Icons.check_circle
                      : Icons.cancel,
                  color: _tavilyTestResult!['success'] == true
                      ? Colors.green
                      : Colors.red,
                  size: 14),
              const SizedBox(width: 4),
              Flexible(
                  child: Text(
                _tavilyTestResult!['success'] == true
                    ? '✓ 搜索可用'
                    : _tavilyTestResult!['error']?.toString() ?? '失败',
                style: TextStyle(
                    fontSize: 10,
                    color: _tavilyTestResult!['success'] == true
                        ? Colors.green
                        : Colors.red),
                overflow: TextOverflow.ellipsis,
              )),
            ],
          ]),
          const SizedBox(height: 10),
          Row(children: [
            Expanded(child: _OutBtn('应用（本次有效）', onTap: () => _saveTavily())),
            const SizedBox(width: 8),
            Expanded(
                child: _GoldBtn('💾 写入 .env',
                    onTap: () => _saveTavily(persist: true))),
          ]),
        ],
      );

  Widget _buildSummarySection(
          LocalSettings s, AsyncValue<CurrentConfig> currentAsync) =>
      _Section(
        title: '配置总览',
        icon: Icons.dashboard_outlined,
        children: [
          currentAsync.when(
            data: (c) =>
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              _SummaryRow('AI 模型', '${c.llmProviderName}  ›  ${c.model}'),
              _SummaryRow('API Key',
                  c.apiKeyMasked.isNotEmpty ? c.apiKeyMasked : '未配置'),
              _SummaryRow('语音识别', s.asrProvider.toUpperCase()),
              _SummaryRow('语音合成', s.ttsProvider.toUpperCase()),
              _SummaryRow(
                  '网络搜索', c.webSearchEnabled ? '✅ Tavily 已启用' : '❌ 未启用'),
              _SummaryRow('交互方式', _interactionModeLabel(s)),
            ]),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('无法加载配置: $e'),
          ),
        ],
      );

  Widget _buildHealthSection(
          AsyncValue<Map<String, ServiceHealth>> healthAsync) =>
      _Section(
        title: '本地服务状态',
        icon: Icons.monitor_heart_outlined,
        action: _SpeechRefreshButton(
          refreshing: _healthRefreshing,
          onPressed: _refreshLocalServiceStatus,
        ),
        children: [
          const Text(
            '这里是系统状态的统一展示入口。是否可用请以本地服务状态为准；刷新后会同步更新 ASR/TTS 的可用列表，不再单独显示“验证”结果。',
            style: TextStyle(color: AppColors.warmGray, fontSize: 12),
          ),
          const SizedBox(height: 10),
          healthAsync.when(
            data: (map) => Column(
                children: map.entries
                    .map((e) => _HealthTile(health: e.value))
                    .toList()),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('本地服务状态检查失败: $e'),
          ),
        ],
      );

  // ─────────────────────────────────────────────────────────────────────────
  //  Provider selector
  // ─────────────────────────────────────────────────────────────────────────

  Widget _providerSelectorButton(
    ProviderInfo p, {
    required bool selected,
    required bool active,
    required VoidCallback onTap,
  }) {
    return ConstrainedBox(
      constraints: const BoxConstraints(minWidth: 120, maxWidth: 188),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          key: ValueKey('llm-provider-selector-${p.id}'),
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Ink(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: selected
                    ? AppColors.amberGold
                    : active
                        ? AppColors.amberGold.withValues(alpha: 0.55)
                        : AppColors.warmGray.withValues(alpha: 0.2),
                width: selected ? 1.6 : 1,
              ),
              color: selected
                  ? AppColors.amberGold.withValues(alpha: 0.1)
                  : AppColors.studyWallLight.withValues(alpha: 0.38),
              boxShadow: selected
                  ? [
                      BoxShadow(
                        color: AppColors.amberGold.withValues(alpha: 0.12),
                        blurRadius: 14,
                        offset: const Offset(0, 6),
                      ),
                    ]
                  : null,
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(_icon(p.id), style: const TextStyle(fontSize: 18)),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        p.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 12,
                          fontWeight:
                              selected ? FontWeight.w700 : FontWeight.w600,
                        ),
                      ),
                    ),
                    if (active) _Chip('当前', AppColors.amberGold),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _providerForm(
      ProviderInfo p, ProviderTestResult? result, bool testing, bool saving) {
    final active =
        p.id == ref.read(localSettingsProvider).valueOrNull?.llmProvider;
    final currentModel = _modelCtrl[p.id]?.text.isNotEmpty == true
        ? _modelCtrl[p.id]!.text
        : p.model;

    return AnimatedContainer(
      key: ValueKey('llm-provider-panel-${p.id}'),
      duration: const Duration(milliseconds: 180),
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: active
              ? AppColors.amberGold
              : AppColors.warmGray.withValues(alpha: 0.22),
          width: active ? 1.6 : 1,
        ),
        color: active
            ? AppColors.amberGold.withValues(alpha: 0.07)
            : AppColors.studyWallLight.withValues(alpha: 0.4),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 38,
              height: 38,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(12),
                color: AppColors.studyWall.withValues(alpha: 0.82),
                border: Border.all(
                  color: AppColors.warmGray.withValues(alpha: 0.18),
                ),
              ),
              child: Text(_icon(p.id), style: const TextStyle(fontSize: 18)),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '当前编辑：${p.name}',
                    style: const TextStyle(
                      color: AppColors.warmWhite,
                      fontSize: 14,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    currentModel,
                    style: const TextStyle(
                      color: AppColors.warmGray,
                      fontSize: 11,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    active
                        ? '当前生效配置。会自动沿用上次保存的地址、模型和 API Key。'
                        : '这是备用供应商。保存时会自动切换到该提供商，并沿用上次保存的配置。',
                    style: const TextStyle(
                      color: AppColors.warmGray,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
            if (active) _Chip('使用中', AppColors.amberGold),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            _KeyBadge(p),
            if (!active) ...[
              const SizedBox(width: 8),
              _OutBtn(
                '设为当前',
                onTap: () => _selectProvider(p),
                height: 34,
                padding: 12,
              ),
            ],
          ],
        ),
        const SizedBox(height: 14),
        if (p.needsApiKey) ...[
          const _Label('API Key'),
          _Field(
            ctrl: _pk(p),
            hint: p.hasApiKey ? '已保存，留空则沿用当前 Key' : '输入 API Key',
            obscure: true,
          ),
          const SizedBox(height: 8),
        ],
        Text(
          '系统会自动沿用上次保存的地址、模型和 API Key；只有重新输入时才会覆盖。',
          style: const TextStyle(
            color: AppColors.warmGray,
            fontSize: 11,
          ),
        ),
        const SizedBox(height: 8),
        const _Label('请求地址（Base URL）'),
        _Field(ctrl: _bu(p), hint: p.baseUrl),
        const SizedBox(height: 8),
        const _Label('模型'),
        result != null && result.success && result.models.isNotEmpty
            ? _DropField(items: result.models, ctrl: _mc(p))
            : _Field(ctrl: _mc(p), hint: p.model),
        const SizedBox(height: 10),
        _ProviderActionBar(
          testing: testing,
          saving: saving,
          onApply: () => _saveProvider(p),
          onPersist: () => _saveProvider(p, persist: true),
          onTest: () => _testProvider(p),
        ),
        if (result != null) ...[
          const SizedBox(height: 8),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(8),
              color: Colors.black.withValues(alpha: 0.22),
              border: Border.all(
                color: (result.success ? Colors.green : Colors.red)
                    .withValues(alpha: 0.4),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  result.success ? Icons.check_circle : Icons.cancel,
                  color: result.success ? Colors.green : Colors.red,
                  size: 14,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    result.success
                        ? '连接成功，可用模型 ${result.models.length} 个'
                        : (result.error ?? '连接失败'),
                    style: TextStyle(
                      fontSize: 10,
                      color: result.success ? Colors.green : Colors.red,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ],
      ]),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Speech provider tile
  // ─────────────────────────────────────────────────────────────────────────

  Widget _speechTile(SpeechProviderInfo p, String activeId,
      {required bool isAsr}) {
    final active = p.id == activeId;
    final hasRequiredApiKey = !p.needsApiKey || p.hasApiKey;
    final isLocalProvider = !p.isCloud && p.id != 'disabled';
    final isOperational =
        p.id != 'disabled' && p.available && hasRequiredApiKey;
    final selectable =
        p.id == 'disabled' || isOperational || active || isLocalProvider;
    final availColor =
        isOperational ? const Color(0xFF4CAF50) : AppColors.warmWhite;
    final availTip = p.id == 'disabled'
        ? '当前未启用该功能'
        : !hasRequiredApiKey
            ? '缺少 API Key，当前不可用'
            : isOperational
                ? '功能正常'
                : isLocalProvider
                    ? '服务当前离线，允许先选中并保存，服务启动后即可生效'
                    : '服务不可用或未启动';
    String subtitleText() {
      if (p.id == 'disabled') {
        return '纯文本模式，不再调用语音能力。';
      }
      if (!selectable && !active) {
        return '当前不可用，请先到“通用 > 本地服务状态”刷新。';
      }
      if (p.id == 'browser') {
        return '浏览器内置能力，选择后立即生效。';
      }
      if (isAsr && _supportsStreamingAsr(p.id)) {
        return '本地服务，选择后立即生效，并支持流式识别。';
      }
      return '本地服务，选择后立即生效。';
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: active
              ? AppColors.amberGold.withValues(alpha: 0.9)
              : AppColors.warmGray.withValues(alpha: 0.18),
          width: active ? 1.3 : 1,
        ),
        color: active
            ? AppColors.amberGold.withValues(alpha: 0.08)
            : AppColors.studyWall.withValues(alpha: 0.55),
      ),
      child: Row(
        children: [
          // ignore: deprecated_member_use
          Radio<String>(
            value: p.id,
            // ignore: deprecated_member_use
            groupValue: activeId,
            activeColor: AppColors.amberGold,
            materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
            // ignore: deprecated_member_use
            onChanged: selectable
                ? (v) async {
                    if (v == null) return;
                    await _selectSpeechProvider(p, isAsr: isAsr);
                  }
                : null,
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(p.icon, style: const TextStyle(fontSize: 16)),
                    const SizedBox(width: 6),
                    Tooltip(
                      message: availTip,
                      child: Container(
                        width: 7,
                        height: 7,
                        margin: const EdgeInsets.only(right: 6),
                        decoration: BoxDecoration(
                          color: availColor,
                          shape: BoxShape.circle,
                        ),
                      ),
                    ),
                    Expanded(
                      child: Text(
                        p.name,
                        style: const TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 13,
                        ),
                      ),
                    ),
                    if (isAsr && _supportsStreamingAsr(p.id)) ...[
                      const SizedBox(width: 6),
                      _Chip('流式', AppColors.amberGold),
                    ],
                    if (active) ...[
                      const SizedBox(width: 6),
                      _Chip('使用中', AppColors.amberGold),
                    ],
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  subtitleText(),
                  style: const TextStyle(
                    color: AppColors.warmGray,
                    fontSize: 10.5,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _speechProviderSelectorButton(
    SpeechProviderInfo p, {
    required bool selected,
    required bool active,
    required VoidCallback onTap,
  }) {
    final model = _voiceModelCtrl[p.id]?.text.isNotEmpty == true
        ? _voiceModelCtrl[p.id]!.text
        : (p.model.isNotEmpty ? p.model : p.defaultModel);
    final statusColor = !p.needsApiKey
        ? Colors.lightBlue
        : p.hasApiKey
            ? Colors.green
            : Colors.orange;
    final statusText = !p.needsApiKey
        ? '免 Key'
        : p.hasApiKey
            ? 'Key 已配置'
            : '待配置';

    return ConstrainedBox(
      constraints: const BoxConstraints(minWidth: 160, maxWidth: 228),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          key: ValueKey('speech-provider-selector-${p.id}'),
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Ink(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: selected
                    ? AppColors.amberGold
                    : active
                        ? AppColors.amberGold.withValues(alpha: 0.55)
                        : AppColors.warmGray.withValues(alpha: 0.2),
                width: selected ? 1.6 : 1,
              ),
              color: selected
                  ? AppColors.amberGold.withValues(alpha: 0.1)
                  : AppColors.studyWallLight.withValues(alpha: 0.38),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(p.icon, style: const TextStyle(fontSize: 18)),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        p.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 12,
                          fontWeight:
                              selected ? FontWeight.w700 : FontWeight.w600,
                        ),
                      ),
                    ),
                    if (active) _Chip('当前', AppColors.amberGold),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  model,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AppColors.warmGray,
                    fontSize: 10.5,
                  ),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Container(
                      width: 7,
                      height: 7,
                      decoration: BoxDecoration(
                        color: statusColor,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        statusText,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: statusColor,
                          fontSize: 10.5,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    Icon(
                      selected
                          ? Icons.keyboard_arrow_down_rounded
                          : Icons.chevron_right_rounded,
                      color:
                          selected ? AppColors.amberGold : AppColors.warmGray,
                      size: 18,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _cloudVoiceProviderForm(
    SpeechProviderInfo p,
    VoiceServiceTestResult? result,
    bool testing,
    bool saving, {
    required bool isAsr,
    required String activeId,
  }) {
    final active = p.id == activeId;
    final currentModel = _voiceModelCtrl[p.id]?.text.isNotEmpty == true
        ? _voiceModelCtrl[p.id]!.text
        : (p.model.isNotEmpty ? p.model : p.defaultModel);
    final currentVoice =
        _selectedVoice[p.id] ?? (p.voice.isNotEmpty ? p.voice : p.defaultVoice);
    if (_selectedVoice[p.id] == null && currentVoice.isNotEmpty) {
      _selectedVoice[p.id] = currentVoice;
    }

    return AnimatedContainer(
      key: ValueKey('${isAsr ? 'asr' : 'tts'}-provider-panel-${p.id}'),
      duration: const Duration(milliseconds: 180),
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: active
              ? AppColors.amberGold
              : AppColors.warmGray.withValues(alpha: 0.22),
          width: active ? 1.6 : 1,
        ),
        color: active
            ? AppColors.amberGold.withValues(alpha: 0.07)
            : AppColors.studyWallLight.withValues(alpha: 0.4),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 38,
                height: 38,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  color: AppColors.studyWall.withValues(alpha: 0.82),
                  border: Border.all(
                    color: AppColors.warmGray.withValues(alpha: 0.18),
                  ),
                ),
                child: Text(p.icon, style: const TextStyle(fontSize: 18)),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '当前编辑：${p.name}',
                      style: const TextStyle(
                        color: AppColors.warmWhite,
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      currentModel,
                      style: const TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 11,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      active
                          ? '当前生效配置。测试通过后可以直接应用或写入 .env。'
                          : '这是大陆优先的云端备用方案。保存时会自动切换到该 provider。',
                      style: const TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
              if (active) _Chip('使用中', AppColors.amberGold),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _SpeechKeyBadge(p),
              if (!active) ...[
                const SizedBox(width: 8),
                _OutBtn(
                  '设为当前',
                  onTap: () => _selectSpeechProvider(p, isAsr: isAsr),
                  height: 34,
                  padding: 12,
                ),
              ],
            ],
          ),
          const SizedBox(height: 14),
          if (p.needsApiKey) ...[
            const _Label('API Key'),
            _Field(
              ctrl: _vk(p),
              hint: p.hasApiKey ? '已配置（输入新值覆盖）' : '输入 API Key',
              obscure: true,
            ),
            const SizedBox(height: 8),
          ],
          const _Label('请求地址（Base URL）'),
          _Field(ctrl: _vu(p), hint: p.url.isNotEmpty ? p.url : p.defaultUrl),
          const SizedBox(height: 8),
          const _Label('模型'),
          result != null && result.success && result.models.isNotEmpty
              ? _DropField(items: result.models, ctrl: _vm(p))
              : _Field(ctrl: _vm(p), hint: currentModel),
          if (!isAsr) ...[
            const SizedBox(height: 8),
            const _Label('音色'),
            if (result != null && result.success && result.voices.isNotEmpty)
              _VoiceDrop(
                voices: result.voices,
                value: _selectedVoice[p.id],
                onChanged: (v) => setState(() => _selectedVoice[p.id] = v),
              )
            else
              _Field(
                ctrl: TextEditingController(text: currentVoice),
                hint: p.defaultVoice,
                readOnly: true,
              ),
          ],
          const SizedBox(height: 10),
          _ProviderActionBar(
            testing: testing,
            saving: saving,
            onApply: () => _saveVoice(p, isAsr: isAsr),
            onPersist: () => _saveVoice(p, isAsr: isAsr, persist: true),
            onTest: () => _testVoice(p),
          ),
          if (result != null) ...[
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(8),
                color: Colors.black.withValues(alpha: 0.22),
                border: Border.all(
                  color: (result.success ? Colors.green : Colors.red)
                      .withValues(alpha: 0.4),
                ),
              ),
              child: Row(
                children: [
                  Icon(
                    result.success ? Icons.check_circle : Icons.cancel,
                    color: result.success ? Colors.green : Colors.red,
                    size: 14,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      result.success
                          ? (result.models.isNotEmpty
                              ? '连接成功，可用模型 ${result.models.length} 个'
                              : '连接成功')
                          : (result.error ?? '连接失败'),
                      style: TextStyle(
                        fontSize: 10,
                        color: result.success ? Colors.green : Colors.red,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  String _icon(String id) {
    const m = {
      'openai': '🌐',
      'qwen': '🔮',
      'deepseek': '🔍',
      'siliconflow': '🌊',
      'ollama': '🦙',
      'ollama_cloud': '☁️',
      'doubao': '🫘',
      'volcengine': '🌋',
      'bailian': '🔥',
      'zhipu': '🧠',
      'anthropic': '🤖',
      'gemini': '💎',
    };
    return m[id] ?? '🤖';
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  Shared leaf widgets
// ─────────────────────────────────────────────────────────────────────────────

class _Section extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget? action;
  final List<Widget> children;

  const _Section({
    required this.title,
    required this.icon,
    this.action,
    required this.children,
  });

  @override
  Widget build(BuildContext context) => Container(
        decoration: BoxDecoration(
          color: AppColors.studyWallLight,
          borderRadius: BorderRadius.circular(18),
          border: Border.all(
            color: AppColors.amberGold.withValues(alpha: 0.16),
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.12),
              blurRadius: 12,
              offset: const Offset(0, 6),
            ),
          ],
        ),
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, size: 18, color: AppColors.amberGold),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title,
                    style: AppTheme.calligraphyStyleDark(fontSize: 15),
                  ),
                ),
                if (action != null) action!,
              ],
            ),
            const SizedBox(height: 10),
            ...children,
          ],
        ),
      );
}

class _SpeechRefreshButton extends StatelessWidget {
  final bool refreshing;
  final VoidCallback onPressed;

  const _SpeechRefreshButton({
    required this.refreshing,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) => IconButton(
        tooltip: '手动刷新服务状态',
        onPressed: refreshing ? null : onPressed,
        icon: refreshing
            ? const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.amberGold,
                ),
              )
            : const Icon(
                Icons.refresh,
                size: 16,
                color: AppColors.amberGold,
              ),
      );
}

class _Field extends StatelessWidget {
  final TextEditingController ctrl;
  final String hint;
  final bool obscure;
  final bool readOnly;
  const _Field(
      {required this.ctrl,
      required this.hint,
      this.obscure = false,
      this.readOnly = false});

  @override
  Widget build(BuildContext context) => TextField(
        controller: ctrl,
        style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
        obscureText: obscure,
        readOnly: readOnly,
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: const TextStyle(color: AppColors.warmGray, fontSize: 12),
          border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide:
                  BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3))),
          enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide:
                  BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3))),
          focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide: const BorderSide(color: AppColors.amberGold)),
          filled: true,
          fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          isDense: true,
        ),
      );
}

class _Label extends StatelessWidget {
  final String text;
  const _Label(this.text);
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Text(text,
            style: const TextStyle(color: AppColors.warmGray, fontSize: 11)),
      );
}

class _ProviderActionBar extends StatelessWidget {
  final bool testing;
  final bool saving;
  final VoidCallback onApply;
  final VoidCallback onPersist;
  final VoidCallback onTest;

  const _ProviderActionBar({
    required this.testing,
    required this.saving,
    required this.onApply,
    required this.onPersist,
    required this.onTest,
  });

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          _OutBtn(
            testing ? '测试中…' : '测试连接',
            onTap: testing ? null : onTest,
            loading: testing,
            height: 36,
            padding: 14,
          ),
          _OutBtn(
            saving ? '应用中…' : '应用',
            onTap: saving ? null : onApply,
            height: 36,
            padding: 14,
          ),
          _GoldBtn(
            saving ? '写入中…' : '写入 .env',
            onTap: saving ? null : onPersist,
            height: 36,
            padding: 14,
          ),
        ],
      ),
    );
  }
}

class _GoldBtn extends StatelessWidget {
  final String label;
  final VoidCallback? onTap;
  final double? height;
  final double? padding;
  const _GoldBtn(this.label, {this.onTap, this.height, this.padding});
  @override
  Widget build(BuildContext context) => FilledButton(
        onPressed: onTap,
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.amberGold,
          foregroundColor: AppColors.scrollTitle,
          padding: EdgeInsets.symmetric(
              horizontal: padding ?? 12, vertical: (height ?? 28) / 4),
          textStyle: const TextStyle(fontSize: 12),
          minimumSize: Size.zero,
        ),
        child: Text(label),
      );
}

class _OutBtn extends StatelessWidget {
  final String label;
  final VoidCallback? onTap;
  final bool loading;
  final double? height;
  final double? padding;
  const _OutBtn(this.label,
      {this.onTap, this.loading = false, this.height, this.padding});
  @override
  Widget build(BuildContext context) => OutlinedButton(
        onPressed: onTap,
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.warmWhite,
          side: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.5)),
          padding: EdgeInsets.symmetric(
              horizontal: padding ?? 10, vertical: (height ?? 24) / 4),
          textStyle: const TextStyle(fontSize: 11),
          minimumSize: Size.zero,
        ),
        child: loading
            ? const SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: AppColors.warmGray))
            : Text(label),
      );
}

class _Chip extends StatelessWidget {
  final String label;
  final Color color;
  const _Chip(this.label, this.color);
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration:
            BoxDecoration(color: color, borderRadius: BorderRadius.circular(6)),
        child: Text(label,
            style: const TextStyle(
                color: AppColors.scrollTitle,
                fontSize: 10,
                fontWeight: FontWeight.w500)),
      );
}

class _ConfigBadge extends StatelessWidget {
  final String label;
  final String value;

  const _ConfigBadge({required this.label, required this.value});

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(10),
          color: AppColors.studyWallLight.withValues(alpha: 0.45),
          border: Border.all(
            color: AppColors.warmGray.withValues(alpha: 0.18),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label,
              style: const TextStyle(
                color: AppColors.warmGray,
                fontSize: 10,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              value,
              style: const TextStyle(
                color: AppColors.warmWhite,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      );
}

class _KeyBadge extends StatelessWidget {
  final ProviderInfo p;
  const _KeyBadge(this.p);
  @override
  Widget build(BuildContext context) {
    if (!p.needsApiKey) {
      return const Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.computer, size: 14, color: Colors.lightBlue),
        SizedBox(width: 3),
        Text('本地', style: TextStyle(color: Colors.lightBlue, fontSize: 11)),
        SizedBox(width: 4),
      ]);
    }
    final ok = p.hasApiKey;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(ok ? Icons.check_circle : Icons.warning_amber,
          size: 14, color: ok ? Colors.green : Colors.orange),
      const SizedBox(width: 3),
      Text(ok ? 'Key ✓' : '未配置',
          style: TextStyle(
              color: ok ? Colors.green : Colors.orange,
              fontSize: 11,
              fontWeight: FontWeight.w500)),
      const SizedBox(width: 4),
    ]);
  }
}

class _SpeechKeyBadge extends StatelessWidget {
  final SpeechProviderInfo p;
  const _SpeechKeyBadge(this.p);

  @override
  Widget build(BuildContext context) {
    if (!p.needsApiKey) {
      return const Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.computer, size: 14, color: Colors.lightBlue),
        SizedBox(width: 3),
        Text('本地', style: TextStyle(color: Colors.lightBlue, fontSize: 11)),
        SizedBox(width: 4),
      ]);
    }
    final ok = p.hasApiKey;
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(ok ? Icons.check_circle : Icons.warning_amber,
          size: 14, color: ok ? Colors.green : Colors.orange),
      const SizedBox(width: 3),
      Text(ok ? 'Key ✓' : '未配置',
          style: TextStyle(
              color: ok ? Colors.green : Colors.orange,
              fontSize: 11,
              fontWeight: FontWeight.w500)),
      const SizedBox(width: 4),
    ]);
  }
}

class _DropField extends StatefulWidget {
  final List<String> items;
  final TextEditingController ctrl;
  const _DropField({required this.items, required this.ctrl});
  @override
  State<_DropField> createState() => _DropFieldState();
}

class _DropFieldState extends State<_DropField> {
  late String? _val;
  @override
  void initState() {
    super.initState();
    _val = widget.items.contains(widget.ctrl.text)
        ? widget.ctrl.text
        : widget.items.first;
    widget.ctrl.text = _val!;
  }

  @override
  Widget build(BuildContext context) => DropdownButtonFormField<String>(
        initialValue: _val,
        dropdownColor: AppColors.studyWallLight,
        style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
        icon: const Icon(Icons.arrow_drop_down,
            color: AppColors.amberGold, size: 18),
        decoration: InputDecoration(
          border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide:
                  BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3))),
          enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide:
                  BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3))),
          focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(9),
              borderSide: const BorderSide(color: AppColors.amberGold)),
          filled: true,
          fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          isDense: true,
        ),
        items: widget.items
            .map((m) => DropdownMenuItem(
                value: m, child: Text(m, style: const TextStyle(fontSize: 12))))
            .toList(),
        onChanged: (v) {
          setState(() => _val = v);
          if (v != null) widget.ctrl.text = v;
        },
      );
}

class _VoiceDrop extends StatelessWidget {
  final List<String> voices;
  final String? value;
  final void Function(String?) onChanged;
  const _VoiceDrop(
      {required this.voices, required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final cur = voices.contains(value) ? value : voices.first;
    return DropdownButtonFormField<String>(
      initialValue: cur,
      dropdownColor: AppColors.studyWallLight,
      style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
      icon: const Icon(Icons.arrow_drop_down,
          color: AppColors.amberGold, size: 18),
      decoration: InputDecoration(
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(9),
            borderSide:
                BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3))),
        filled: true,
        fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
        contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        isDense: true,
      ),
      items: voices
          .map((v) => DropdownMenuItem(
              value: v, child: Text(v, style: const TextStyle(fontSize: 12))))
          .toList(),
      onChanged: onChanged,
    );
  }
}

class _HealthTile extends StatelessWidget {
  final ServiceHealth health;
  const _HealthTile({required this.health});
  @override
  Widget build(BuildContext context) {
    final ok = health.reachable;
    const nameMap = {
      'edge_tts': 'Edge TTS (本地)',
      'cosyvoice': 'CosyVoice (本地)',
      'funasr': 'FunASR (本地)',
      'ollama': 'Ollama',
    };
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(ok ? Icons.check_circle : Icons.cancel,
          color: ok ? Colors.green : Colors.red, size: 16),
      title: Text(nameMap[health.name] ?? health.name,
          style: const TextStyle(color: AppColors.warmWhite, fontSize: 12)),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(health.url,
              style: const TextStyle(color: AppColors.warmGray, fontSize: 10)),
          Text(
            health.detail ??
                'HTTP ${health.statusCode?.toString() ?? '-'} · ${health.latencyMs?.toStringAsFixed(1) ?? '-'}ms',
            style: TextStyle(
                color: ok ? AppColors.warmGray : Colors.redAccent,
                fontSize: 10),
          ),
        ],
      ),
      trailing: Text(ok ? '可用' : '不可用',
          style: TextStyle(
              color: ok ? Colors.green : Colors.red,
              fontWeight: FontWeight.bold,
              fontSize: 11)),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  final String label;
  final String value;
  const _SummaryRow(this.label, this.value);
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(children: [
          SizedBox(
            width: 72,
            child: Text(label,
                style:
                    const TextStyle(color: AppColors.warmGray, fontSize: 11)),
          ),
          Expanded(
            child: Text(value,
                style:
                    const TextStyle(color: AppColors.warmWhite, fontSize: 12)),
          ),
        ]),
      );
}

class _Spin extends StatelessWidget {
  const _Spin();
  @override
  Widget build(BuildContext context) => const Center(
        child: Padding(
            padding: EdgeInsets.all(14),
            child: CircularProgressIndicator(color: AppColors.amberGold)),
      );
}

class _ErrorBox extends StatelessWidget {
  final String msg;
  const _ErrorBox(this.msg);
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Colors.red.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: Colors.red.withValues(alpha: 0.3)),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Icon(Icons.error_outline, color: Colors.red, size: 14),
          const SizedBox(width: 6),
          Expanded(
              child: Text(msg,
                  style: const TextStyle(color: Colors.red, fontSize: 11))),
        ]),
      );
}

// ─────────────────────────────────────────────────────────────────────────────
// 需求22：话题管理（与首页自由话题共用同一存储）
// ─────────────────────────────────────────────────────────────────────────────

class _TopicsTab extends StatefulWidget {
  const _TopicsTab();
  @override
  State<_TopicsTab> createState() => _TopicsTabState();
}

class _TopicsTabState extends State<_TopicsTab> {
  List<SavedTopicItem> _items = const [];
  bool _loading = true;
  final TextEditingController _inputCtrl = TextEditingController();
  final TextEditingController _originalCtrl = TextEditingController();

  void _showSnack(String message, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: error ? Colors.red : null,
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _inputCtrl.dispose();
    _originalCtrl.dispose();
    super.dispose();
  }

  String _formatSavedAt(DateTime? value) {
    if (value == null) {
      return '时间未记录';
    }
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    final hour = value.hour.toString().padLeft(2, '0');
    final minute = value.minute.toString().padLeft(2, '0');
    return '${value.year}-$month-$day $hour:$minute';
  }

  Future<void> _load() async {
    final list = await SavedTopicsStore.load();
    if (!mounted) return;
    setState(() {
      _items = list;
      _loading = false;
    });
  }

  Future<void> _add() async {
    final topic = _inputCtrl.text.trim();
    final originalContent = _originalCtrl.text.trim();
    if (topic.isEmpty) {
      _showSnack('请输入要保存的话题', error: true);
      return;
    }
    await SavedTopicsStore.add(
      topic,
      originalContent: originalContent,
    );
    _inputCtrl.clear();
    _originalCtrl.clear();
    await _load();
    _showSnack('已添加到常用话题');
  }

  Future<void> _remove(SavedTopicItem topic) async {
    await SavedTopicsStore.remove(topic);
    await _load();
    _showSnack('已删除话题');
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(
          child: CircularProgressIndicator(color: AppColors.amberGold));
    }
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        Text('常用话题管理', style: AppTheme.calligraphyStyleDark(fontSize: 16)),
        const SizedBox(height: 4),
        Text(
          '此处保存的话题会出现在首页「自由话题」输入框上方；如果保存时已经经过 AI 整理，这里优先显示整理后的话题，并在下方保留原始描述。',
          style: TextStyle(
            color: AppColors.warmGray.withValues(alpha: 0.92),
            fontSize: 12,
            height: 1.45,
          ),
        ),
        const SizedBox(height: 16),
        Text(
          '已保存 ${_items.length} 个话题',
          style: const TextStyle(color: AppColors.amberGold, fontSize: 12),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _inputCtrl,
          style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
          decoration: InputDecoration(
            labelText: '话题内容',
            hintText: '添加一个话题…',
            labelStyle: const TextStyle(color: AppColors.warmGray),
            hintStyle: const TextStyle(color: AppColors.warmGray, fontSize: 12),
            filled: true,
            fillColor: AppColors.studyWall,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.warmGray),
            ),
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          ),
          onSubmitted: (_) => _add(),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: _originalCtrl,
          minLines: 2,
          maxLines: 3,
          style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
          decoration: InputDecoration(
            labelText: '原始话题内容（可选）',
            hintText: '如果你想保留自由话题最初的长描述，可以填在这里。',
            labelStyle: const TextStyle(color: AppColors.warmGray),
            hintStyle: const TextStyle(color: AppColors.warmGray, fontSize: 12),
            filled: true,
            fillColor: AppColors.studyWall,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: AppColors.warmGray),
            ),
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            ElevatedButton.icon(
              onPressed: _add,
              icon: const Icon(Icons.add, size: 16),
              label: const Text('添加到列表'),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.amberGold,
                foregroundColor: Colors.black,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          '保存后会同步出现在首页自由话题候选区；首页只显示整理后的主标题，设置页会额外保留原始内容与保存时间。',
          style: TextStyle(
            color: AppColors.warmGray.withValues(alpha: 0.92),
            fontSize: 11,
            height: 1.45,
          ),
        ),
        const SizedBox(height: 18),
        if (_items.isEmpty)
          const Padding(
            padding: EdgeInsets.all(24),
            child: Center(
              child: Text('暂无保存的话题',
                  style: TextStyle(color: AppColors.warmGray, fontSize: 13)),
            ),
          )
        else
          ..._items.asMap().entries.map(
            (entry) {
              final index = entry.key;
              final t = entry.value;
              return Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                decoration: BoxDecoration(
                  color: AppColors.studyWall,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                      color: AppColors.warmGray.withValues(alpha: 0.4)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 4,
                          ),
                          decoration: BoxDecoration(
                            color: AppColors.amberGold.withValues(alpha: 0.14),
                            borderRadius: BorderRadius.circular(999),
                          ),
                          child: Text(
                            '话题 ${(index + 1).toString().padLeft(2, '0')}',
                            style: const TextStyle(
                              color: AppColors.amberGold,
                              fontSize: 11,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            _formatSavedAt(t.savedAt),
                            style: const TextStyle(
                              color: AppColors.warmGray,
                              fontSize: 11,
                            ),
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.delete_outline,
                              color: AppColors.warmGray, size: 18),
                          tooltip: '删除',
                          onPressed: () => _remove(t),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      '话题内容',
                      style: TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      t.title,
                      style: const TextStyle(
                        color: AppColors.warmWhite,
                        fontSize: 13,
                        height: 1.45,
                      ),
                    ),
                    if (t.hasOriginalContent) ...[
                      const SizedBox(height: 10),
                      const Text(
                        '原始话题内容',
                        style: TextStyle(
                          color: AppColors.warmGray,
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        t.originalContent,
                        style: const TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 12,
                          height: 1.5,
                        ),
                      ),
                    ],
                  ],
                ),
              );
            },
          ),
        const SizedBox(height: 32),
      ],
    );
  }
}
