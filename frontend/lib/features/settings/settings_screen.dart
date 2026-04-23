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
  late TextEditingController _serverUrlCtrl;
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
  final Map<String, String?> _selectedVoice = {};
  final Map<String, VoiceServiceTestResult?> _voiceTestResult = {};
  final Map<String, bool> _testingVoice = {};

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
  final _generalScrollCtrl = ScrollController();

  // Voice service test state
  bool _testingVoiceService = false;
  Map<String, dynamic>? _voiceServiceTestResult;
  bool _runningDeepVoiceTest = false;
  Map<String, dynamic>? _deepVoiceTestResult;

  String _interactionModeLabel(LocalSettings s) {
    if (!s.pushToTalk) return '自由对话';
    if (s.micControlMode == 'hold_ctrl') return '按住 Ctrl 说话';
    return '双击 Ctrl 开关录音';
  }

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 5, vsync: this);
    _serverUrlCtrl = TextEditingController(text: 'http://localhost:8001');
    // Defer loading the actual server URL from provider
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = ref.read(localSettingsProvider).valueOrNull;
      if (s != null && _serverUrlCtrl.text != s.serverUrl) {
        _serverUrlCtrl.text = s.serverUrl;
      }
    });
  }

  @override
  void dispose() {
    _tabController.dispose();
    _serverUrlCtrl.dispose();
    _tavilyKeyCtrl.dispose();
    _aiScrollCtrl.dispose();
    _asrScrollCtrl.dispose();
    _ttsScrollCtrl.dispose();
    _generalScrollCtrl.dispose();
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

  // ── actions ──────────────────────────────────────────────────────────────

  Future<void> _saveServerUrl() async {
    final url = _serverUrlCtrl.text.trim();
    if (url.isEmpty) return;
    await ref.read(localSettingsProvider.notifier).setServerUrl(url);
    ref.invalidate(providersProvider);
    ref.invalidate(speechConfigProvider);
    ref.invalidate(healthStatusProvider);
    ref.invalidate(currentConfigProvider);
    _snack('服务器地址已更新');
  }

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

      if (tested == null || !tested.success || tested.models.isEmpty) {
        _snackErr('请先点击“测试连接”，并确保返回可用模型后再保存。');
        return;
      }
      if (mdl.isEmpty || !tested.models.contains(mdl)) {
        _snackErr('当前模型不可用，请从下拉中选择已验证模型。');
        return;
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
      ref.invalidate(providersProvider);
      ref.invalidate(currentConfigProvider);
      _snack('已切换到 ${p.name}');
    } catch (e) {
      _snackErr('切换失败: $e');
    }
  }

  Future<void> _validateConfig() async {
    ref.invalidate(configValidationProvider);
    try {
      final r = await ref.read(configValidationProvider.future);
      if (!mounted) return;
      final currentConfig = ref.read(currentConfigProvider).valueOrNull;
      final s =
          ref.read(localSettingsProvider).valueOrNull ?? const LocalSettings();
      showDialog(
        context: context,
        builder: (ctx) => AlertDialog(
          backgroundColor: AppColors.studyWallLight,
          title: Row(children: [
            Icon(r?.valid == true ? Icons.check_circle : Icons.error,
                color: r?.valid == true ? Colors.green : Colors.red, size: 22),
            const SizedBox(width: 8),
            Text(r?.valid == true ? '配置有效' : '配置无效',
                style: AppTheme.calligraphyStyleDark(fontSize: 18)),
          ]),
          content: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (r?.valid != true) ...[
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: Colors.red.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(r?.message ?? '未知错误',
                        style:
                            const TextStyle(color: Colors.red, fontSize: 12)),
                  ),
                  const SizedBox(height: 12),
                ],
                const Text('当前配置',
                    style: TextStyle(
                        color: AppColors.amberGold,
                        fontSize: 13,
                        fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                if (currentConfig != null) ...[
                  _DialogRow('AI 模型', currentConfig.llmProviderName),
                  _DialogRow('当前模型', currentConfig.model),
                  _DialogRow(
                      'API Key',
                      currentConfig.apiKeyMasked.isNotEmpty
                          ? currentConfig.apiKeyMasked
                          : '未配置'),
                ],
                _DialogRow('语音识别', s.asrProvider.toUpperCase()),
                _DialogRow('语音合成', s.ttsProvider.toUpperCase()),
                if (currentConfig != null)
                  _DialogRow('网络搜索',
                      currentConfig.webSearchEnabled ? 'Tavily 已启用' : '未启用'),
                _DialogRow('交互方式', _interactionModeLabel(s)),
                _DialogRow('服务器', s.serverUrl),
                const SizedBox(height: 12),
                const Text('可用性检测',
                    style: TextStyle(
                        color: AppColors.amberGold,
                        fontSize: 13,
                        fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                if ((r?.checks ?? const []).isEmpty)
                  const Text('暂无详细检测项',
                      style: TextStyle(color: AppColors.warmGray, fontSize: 11))
                else
                  ...r!.checks.map(
                    (c) => Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            c.ok ? Icons.check_circle : Icons.cancel,
                            color: c.ok ? Colors.green : Colors.red,
                            size: 14,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  c.name,
                                  style: const TextStyle(
                                      color: AppColors.warmWhite,
                                      fontSize: 12,
                                      fontWeight: FontWeight.w600),
                                ),
                                Text(
                                  c.detail,
                                  style: const TextStyle(
                                      color: AppColors.warmGray, fontSize: 11),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('关闭',
                  style: TextStyle(color: AppColors.amberGold)),
            ),
          ],
        ),
      );
    } catch (e) {
      _snackErr('验证失败: $e');
    }
  }

  Future<void> _testVoice(SpeechProviderInfo p) async {
    setState(() => _testingVoice[p.id] = true);
    try {
      final result = await ref.read(apiClientProvider).testVoiceService(
            service: p.id,
            url: _voiceUrlCtrl[p.id]?.text.trim(),
            apiKey: _voiceKeyCtrl[p.id]?.text.trim(),
          );
      setState(() => _voiceTestResult[p.id] = result);
    } catch (e) {
      setState(() => _voiceTestResult[p.id] = VoiceServiceTestResult(
          success: false, voices: [], url: '', error: e.toString()));
    } finally {
      setState(() => _testingVoice[p.id] = false);
    }
  }

  Future<void> _saveVoice(SpeechProviderInfo p, {bool persist = false}) async {
    final updates = <String, dynamic>{};
    final url = _voiceUrlCtrl[p.id]?.text.trim() ?? '';
    final key = _voiceKeyCtrl[p.id]?.text.trim() ?? '';
    final voice = _selectedVoice[p.id];
    const urlMap = {
      'funasr': 'funasr_url',
      'edge_tts': 'edge_tts_url',
      'cosyvoice': 'cosyvoice_url',
      'openai_whisper': 'openai_whisper_base_url',
      'openai_tts': 'openai_base_url',
    };
    const keyMap = {
      'openai_whisper': 'openai_whisper_api_key',
      'openai_tts': 'openai_api_key',
    };
    if (url.isNotEmpty && urlMap.containsKey(p.id)) {
      updates[urlMap[p.id]!] = url;
    }
    if (key.isNotEmpty && keyMap.containsKey(p.id)) {
      updates[keyMap[p.id]!] = key;
    }
    if (voice != null && voice.isNotEmpty) {
      updates[p.id == 'cosyvoice' ? 'cosyvoice_voice' : 'tts_voice'] = voice;
    }
    if (updates.isEmpty) {
      _snack('无内容更改');
      return;
    }
    try {
      final client = ref.read(apiClientProvider);
      if (persist) {
        await client.saveConfig(updates);
        _snack('✅ 语音配置已写入 .env');
      } else {
        await client.updateConfig(updates);
        _snack('✅ 语音配置已应用');
      }
      ref.invalidate(speechConfigProvider);
    } catch (e) {
      _snackErr('保存失败: $e');
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

  // ── build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final localAsync = ref.watch(localSettingsProvider);
    final s = localAsync.valueOrNull ?? const LocalSettings();
    final providersAsync = ref.watch(providersProvider);
    final speechAsync = ref.watch(speechConfigProvider);
    final healthAsync = ref.watch(healthStatusProvider);
    final currentAsync = ref.watch(currentConfigProvider);

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
        actions: [
          TextButton.icon(
            onPressed: _validateConfig,
            icon: const Icon(Icons.check_circle_outline,
                size: 16, color: AppColors.amberGold),
            label: const Text('验证',
                style: TextStyle(color: AppColors.amberGold, fontSize: 13)),
          ),
        ],
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
            Tab(icon: Icon(Icons.forum_outlined, size: 18), text: '话题'),
            Tab(icon: Icon(Icons.settings_outlined, size: 18), text: '通用'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _buildAiModelTab(s, providersAsync, currentAsync),
          _buildAsrTab(s, speechAsync, healthAsync),
          _buildTtsTab(s, speechAsync, healthAsync),
          const _TopicsTab(),
          _buildGeneralTab(s, currentAsync, healthAsync),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Tab builders (Req8: 4 sub-pages)
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildAiModelTab(
      LocalSettings s, AsyncValue providersAsync, AsyncValue currentAsync) {
    return ListView(
      controller: _aiScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildServerSection(currentAsync),
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

  Widget _buildAsrTab(
      LocalSettings s, AsyncValue speechAsync, AsyncValue healthAsync) {
    return ListView(
      controller: _asrScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildAsrSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildAsrBenchmarkSection(),
        const SizedBox(height: 14),
        _buildVoiceServiceTestSection(type: 'asr'),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildTtsTab(
      LocalSettings s, AsyncValue speechAsync, AsyncValue healthAsync) {
    return ListView(
      controller: _ttsScrollCtrl,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildTtsSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildTtsBenchmarkSection(),
        const SizedBox(height: 14),
        _buildVoiceServiceTestSection(type: 'tts'),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildGeneralTab(
      LocalSettings s, AsyncValue currentAsync, AsyncValue healthAsync) {
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
                '推荐: ${_llmBenchmark!['results']?[0]?['provider'] ?? '-'}',
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

  Widget _buildVoiceServiceTestSection({required String type}) => _Section(
        title: '独立服务测试',
        icon: Icons.science_outlined,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: (type == 'asr'
                    ? ['capswriter', 'vosk', 'funasr']
                    : [
                        'edge_tts',
                        'vibevoice',
                        'fireredtts',
                        'openvoice',
                        'cosyvoice'
                      ])
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
        ],
      );

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
          final name = m['service'] ?? m['provider'] ?? '-';
          final status = m['status'] ?? 'unknown';
          final isOk = status == 'ok';
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
          final note = (m['note'] ?? '').toString().trim();
          if (note.isNotEmpty) {
            detail = detail.isEmpty ? note : '$detail  ｜  $note';
          }
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: [
                Icon(
                  isOk ? Icons.check_circle : Icons.cancel,
                  size: 14,
                  color: isOk ? Colors.green : Colors.red,
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
                      style: TextStyle(
                          color: isOk ? AppColors.warmGray : Colors.redAccent,
                          fontSize: 11)),
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
    final serverUrl = _serverUrlCtrl.text.trim().isNotEmpty
        ? _serverUrlCtrl.text.trim()
        : 'http://localhost:8001';
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

  // ─────────────────────────────────────────────────────────────────────────
  //  Section builders (reused by tabs)
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildServerSection(AsyncValue currentAsync) => _Section(
        title: '服务器地址',
        icon: Icons.dns_outlined,
        children: [
          Row(children: [
            Expanded(
                child: _Field(
                    ctrl: _serverUrlCtrl,
                    hint: 'http://localhost:8001',
                    onDone: (_) => _saveServerUrl())),
            const SizedBox(width: 8),
            _GoldBtn('保存', onTap: _saveServerUrl),
          ]),
          const SizedBox(height: 8),
          currentAsync.when(
            data: (c) => Text('当前：${c.llmProviderName} › ${c.model}',
                style:
                    const TextStyle(color: AppColors.warmGray, fontSize: 11)),
            loading: () => const SizedBox.shrink(),
            error: (_, __) => const Text('⚠ 无法连接服务器，请检查地址',
                style: TextStyle(color: Colors.orange, fontSize: 11)),
          ),
        ],
      );

  Widget _buildLlmSection(AsyncValue providersAsync, LocalSettings s) =>
      _Section(
        title: 'AI 模型提供商',
        icon: Icons.smart_toy_outlined,
        children: [
          providersAsync.when(
            skipLoadingOnRefresh: true,
            data: (list) =>
                Column(children: list.map((p) => _providerTile(p, s)).toList()),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('模型提供商加载失败: $e'),
          ),
        ],
      );

  Widget _buildAsrSection(AsyncValue speechAsync, LocalSettings s) => _Section(
        title: '语音识别（ASR）',
        icon: Icons.mic_outlined,
        children: [
          speechAsync.when(
            skipLoadingOnRefresh: true,
            data: (sp) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: sp.asrProviders
                  .map((p) => _speechTile(p, s.asrProvider, isAsr: true))
                  .toList(),
            ),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('语音识别配置加载失败: $e'),
          ),
        ],
      );

  Widget _buildTtsSection(AsyncValue speechAsync, LocalSettings s) => _Section(
        title: '语音合成（TTS）',
        icon: Icons.volume_up_outlined,
        children: [
          speechAsync.when(
            skipLoadingOnRefresh: true,
            data: (sp) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: sp.ttsProviders
                  .map((p) => _speechTile(p, s.ttsProvider, isAsr: false))
                  .toList(),
            ),
            loading: () => const _Spin(),
            error: (e, _) => _ErrorBox('语音合成配置加载失败: $e'),
          ),
        ],
      );

  Widget _buildInteractionSection(LocalSettings s) => _Section(
        title: '交互方式',
        icon: Icons.touch_app_outlined,
        children: [
          SwitchListTile(
            title: const Text('按住说话（Push-to-Talk）',
                style: TextStyle(color: AppColors.warmWhite)),
            subtitle: const Text('双击 Ctrl：开始/结束录音  ·  可切换按住 Ctrl 模式',
                style: TextStyle(color: AppColors.warmGray, fontSize: 11)),
            value: s.pushToTalk,
            onChanged: (v) async {
              ref.read(localSettingsProvider.notifier).setPushToTalk(v);
              await ref
                  .read(apiClientProvider)
                  .updateConfig({'push_to_talk': v});
            },
            contentPadding: EdgeInsets.zero,
            activeThumbColor: AppColors.amberGold,
            dense: true,
          ),
          const SizedBox(height: 8),
          Opacity(
            opacity: s.pushToTalk ? 1.0 : 0.45,
            child: IgnorePointer(
              ignoring: !s.pushToTalk,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Ctrl 控制模式',
                      style: TextStyle(
                          color: AppColors.warmWhite,
                          fontSize: 12,
                          fontWeight: FontWeight.w600)),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 8,
                    children: [
                      ChoiceChip(
                        label: const Text('双击 Ctrl 开关录音'),
                        selected: s.micControlMode == 'double_ctrl',
                        onSelected: (_) async {
                          await ref
                              .read(localSettingsProvider.notifier)
                              .setMicControlMode('double_ctrl');
                        },
                      ),
                      ChoiceChip(
                        label: const Text('按住 Ctrl 说话'),
                        selected: s.micControlMode == 'hold_ctrl',
                        onSelected: (_) async {
                          await ref
                              .read(localSettingsProvider.notifier)
                              .setMicControlMode('hold_ctrl');
                        },
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      );

  Widget _buildTavilySection(AsyncValue currentAsync) => _Section(
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

  Widget _buildSummarySection(LocalSettings s, AsyncValue currentAsync) =>
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

  Widget _buildHealthSection(AsyncValue healthAsync) => _Section(
        title: '本地服务状态',
        icon: Icons.monitor_heart_outlined,
        action: IconButton(
          icon: _healthRefreshing
              ? const SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: AppColors.amberGold))
              : const Icon(Icons.refresh, size: 16, color: AppColors.amberGold),
          onPressed: _healthRefreshing
              ? null
              : () async {
                  setState(() => _healthRefreshing = true);
                  ref.invalidate(healthStatusProvider);
                  await Future.delayed(const Duration(seconds: 2));
                  setState(() => _healthRefreshing = false);
                },
          tooltip: '刷新',
        ),
        children: [
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
  //  Provider tile
  // ─────────────────────────────────────────────────────────────────────────

  Widget _providerTile(ProviderInfo p, LocalSettings s) {
    final active = p.id == s.llmProvider;
    final expanded = _expandedProvider == p.id;
    final testing = _testingProvider[p.id] ?? false;
    final saving = _savingProvider[p.id] ?? false;
    final result = _providerTestResult[p.id];

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: active
                ? AppColors.amberGold
                : AppColors.warmGray.withValues(alpha: 0.2),
            width: active ? 2 : 1,
          ),
          color: active ? AppColors.amberGold.withValues(alpha: 0.07) : null,
        ),
        child: Column(children: [
          ListTile(
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
            dense: true,
            leading: Text(_icon(p.id), style: const TextStyle(fontSize: 22)),
            title: Row(children: [
              Text(p.name,
                  style: TextStyle(
                      color: AppColors.warmWhite,
                      fontWeight: active ? FontWeight.bold : FontWeight.normal,
                      fontSize: 13)),
              if (active) ...[
                const SizedBox(width: 6),
                _Chip('使用中', AppColors.amberGold)
              ],
            ]),
            subtitle: Text(
              _modelCtrl[p.id]?.text.isNotEmpty == true
                  ? _modelCtrl[p.id]!.text
                  : p.model,
              style: const TextStyle(color: AppColors.warmGray, fontSize: 10),
            ),
            trailing: Row(mainAxisSize: MainAxisSize.min, children: [
              _KeyBadge(p),
              Icon(expanded ? Icons.expand_less : Icons.expand_more,
                  color: AppColors.warmGray, size: 18),
            ]),
            onTap: () =>
                setState(() => _expandedProvider = expanded ? null : p.id),
          ),
          if (expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              child: _providerForm(p, result, testing, saving),
            ),
        ]),
      ),
    );
  }

  Widget _providerForm(
      ProviderInfo p, ProviderTestResult? result, bool testing, bool saving) {
    final active =
        p.id == ref.read(localSettingsProvider).valueOrNull?.llmProvider;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Divider(color: AppColors.warmGray, height: 18),
      if (p.needsApiKey) ...[
        const _Label('API Key'),
        _Field(
          ctrl: _pk(p),
          hint: p.hasApiKey ? '已配置（输入新值覆盖）' : '输入 API Key',
          obscure: true,
        ),
        const SizedBox(height: 8),
      ],
      const _Label('请求地址（Base URL）'),
      _Field(ctrl: _bu(p), hint: p.baseUrl),
      const SizedBox(height: 8),
      Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const _Label('模型'),
              result != null && result.success && result.models.isNotEmpty
                  ? _DropField(items: result.models, ctrl: _mc(p))
                  : _Field(ctrl: _mc(p), hint: p.model),
            ],
          ),
        ),
        if (!active) ...[
          const SizedBox(width: 10),
          OutlinedButton.icon(
            onPressed: () => _selectProvider(p),
            icon: const Icon(Icons.check_circle_outline, size: 14),
            label: const Text('设为当前'),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.amberGold,
              side: const BorderSide(color: AppColors.amberGold),
              padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 12),
              textStyle: const TextStyle(fontSize: 11),
            ),
          ),
        ],
      ]),
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
    ]);
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Speech provider tile
  // ─────────────────────────────────────────────────────────────────────────

  Widget _speechTile(SpeechProviderInfo p, String activeId,
      {required bool isAsr}) {
    final active = p.id == activeId;
    final expanded = _expandedVoiceService == p.id;
    final needsCfg = p.id != 'browser' && p.id != 'disabled';
    final testing = _testingVoice[p.id] ?? false;
    final vResult = _voiceTestResult[p.id];

    // 可用性指示点（浏览器/禁用始终绿色；本地/云服务按探测结果）
    final availColor =
        p.available ? const Color(0xFF4CAF50) : const Color(0xFFBDBDBD);
    final availTip = p.available ? '服务可用' : '服务不可用或未启动';

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        // ignore: deprecated_member_use
        Radio<String>(
          value: p.id,
          // ignore: deprecated_member_use
          groupValue: activeId,
          activeColor: AppColors.amberGold,
          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
          // ignore: deprecated_member_use
          onChanged: (v) async {
            if (v == null) return;
            if (isAsr) {
              ref.read(localSettingsProvider.notifier).setAsrProvider(v);
              await ref
                  .read(apiClientProvider)
                  .updateConfig({'asr_provider': v});
            } else {
              ref.read(localSettingsProvider.notifier).setTtsProvider(v);
              await ref
                  .read(apiClientProvider)
                  .updateConfig({'tts_provider': v});
            }
          },
        ),
        Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            // 可用性指示点
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
            Text(p.name,
                style:
                    const TextStyle(color: AppColors.warmWhite, fontSize: 13)),
            if (active) ...[
              const SizedBox(width: 6),
              _Chip('使用中', AppColors.amberGold)
            ],
          ]),
          if (needsCfg && p.url.isNotEmpty)
            Text(p.url,
                style:
                    const TextStyle(color: AppColors.warmGray, fontSize: 10)),
        ])),
        if (needsCfg)
          IconButton(
            icon: Icon(expanded ? Icons.expand_less : Icons.settings,
                color: AppColors.warmGray, size: 15),
            onPressed: () =>
                setState(() => _expandedVoiceService = expanded ? null : p.id),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
            tooltip: '配置',
          ),
        if (p.id == 'disabled')
          const Padding(
            padding: EdgeInsets.only(right: 8),
            child: Text('纯文本',
                style: TextStyle(color: AppColors.warmGray, fontSize: 10)),
          ),
      ]),
      if (expanded && needsCfg)
        Padding(
          padding: const EdgeInsets.only(left: 36, bottom: 4),
          child: _voiceForm(p, vResult, testing),
        ),
    ]);
  }

  Widget _voiceForm(
      SpeechProviderInfo p, VoiceServiceTestResult? result, bool testing) {
    final voices = result?.voices ?? [];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const SizedBox(height: 4),
      if (p.needsApiKey) ...[
        const _Label('API Key'),
        FractionallySizedBox(
          alignment: Alignment.centerLeft,
          widthFactor: 0.5,
          child: _Field(
              ctrl: _vk(p),
              hint: p.hasApiKey ? '已配置（输入新值覆盖）' : '输入 API Key',
              obscure: true),
        ),
        const SizedBox(height: 6),
      ],
      const _Label('服务地址'),
      FractionallySizedBox(
        alignment: Alignment.centerLeft,
        widthFactor: 0.5,
        child: _Field(
            ctrl: _vu(p),
            hint: p.defaultUrl.isNotEmpty
                ? p.defaultUrl
                : 'http://localhost:???'),
      ),
      const SizedBox(height: 8),
      Row(children: [
        _OutBtn(testing ? '测试中…' : '🔌 测试连接',
            onTap: testing ? null : () => _testVoice(p),
            loading: testing,
            height: 36,
            padding: 12),
        if (result != null) ...[
          const SizedBox(width: 8),
          Icon(result.success ? Icons.check_circle : Icons.cancel,
              color: result.success ? Colors.green : Colors.red, size: 13),
          const SizedBox(width: 3),
          Flexible(
              child: Text(
            result.success
                ? (voices.isNotEmpty ? '✓ ${voices.length} 个音色' : '✓ 连接成功')
                : result.error ?? '失败',
            style: TextStyle(
                fontSize: 10,
                color: result.success ? Colors.green : Colors.red),
            overflow: TextOverflow.ellipsis,
          )),
        ],
      ]),
      if (result != null)
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(
            result.success
                ? 'HTTP ${result.statusCode ?? '-'} · ${result.latencyMs?.toStringAsFixed(1) ?? '-'}ms'
                : 'HTTP ${result.statusCode ?? '-'} · ${result.error ?? '连接失败'}',
            style: TextStyle(
              fontSize: 10,
              color: result.success ? AppColors.warmGray : Colors.redAccent,
            ),
          ),
        ),
      if (result != null && result.success && voices.isNotEmpty) ...[
        const SizedBox(height: 8),
        const _Label('选择音色'),
        FractionallySizedBox(
          alignment: Alignment.centerLeft,
          widthFactor: 0.5,
          child: _VoiceDrop(
            voices: voices,
            value: _selectedVoice[p.id],
            onChanged: (v) => setState(() => _selectedVoice[p.id] = v),
          ),
        ),
      ],
      const SizedBox(height: 8),
      Row(mainAxisAlignment: MainAxisAlignment.end, children: [
        _OutBtn('应用', onTap: () => _saveVoice(p), height: 36, padding: 12),
        const SizedBox(width: 8),
        _GoldBtn('💾 .env',
            onTap: () => _saveVoice(p, persist: true), height: 36, padding: 12),
      ]),
      const SizedBox(height: 4),
    ]);
  }

  String _icon(String id) {
    const m = {
      'openai': '🌐',
      'qwen': '🔮',
      'deepseek': '🔍',
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
  const _Section(
      {required this.title,
      required this.icon,
      this.action,
      required this.children});

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppColors.studyWallLight.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: AppColors.warmGray.withValues(alpha: 0.15)),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(icon, size: 18, color: AppColors.amberGold),
            const SizedBox(width: 7),
            Expanded(
                child: Text(title,
                    style: AppTheme.calligraphyStyleDark(fontSize: 15))),
            if (action != null) action!,
          ]),
          const SizedBox(height: 10),
          ...children,
        ]),
      );
}

class _Field extends StatelessWidget {
  final TextEditingController ctrl;
  final String hint;
  final bool obscure;
  final void Function(String)? onDone;
  const _Field(
      {required this.ctrl,
      required this.hint,
      this.obscure = false,
      this.onDone});

  @override
  Widget build(BuildContext context) => TextField(
        controller: ctrl,
        style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
        obscureText: obscure,
        onSubmitted: onDone,
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
    return LayoutBuilder(
      builder: (context, constraints) {
        final isCompact = constraints.maxWidth < 520;
        final buttons = [
          Expanded(
            child: _OutBtn(
              '应用（本次）',
              onTap: saving ? null : onApply,
              height: 40,
            ),
          ),
          Expanded(
            child: _GoldBtn(
              '写入 .env',
              onTap: saving ? null : onPersist,
              height: 40,
            ),
          ),
          Expanded(
            child: _OutBtn(
              testing ? '测试中…' : '测试连接',
              onTap: testing ? null : onTest,
              loading: testing,
              height: 40,
            ),
          ),
        ];

        if (isCompact) {
          return Column(
            children: [
              Row(children: [buttons[0], const SizedBox(width: 8), buttons[1]]),
              const SizedBox(height: 8),
              Row(children: [buttons[2]]),
            ],
          );
        }

        return Row(
          children: [
            buttons[0],
            const SizedBox(width: 8),
            buttons[1],
            const SizedBox(width: 8),
            buttons[2],
          ],
        );
      },
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

class _DialogRow extends StatelessWidget {
  final String label;
  final String value;
  const _DialogRow(this.label, this.value);
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(
            width: 80,
            child: Text(label,
                style:
                    const TextStyle(color: AppColors.warmGray, fontSize: 12)),
          ),
          Expanded(
            child: Text(value,
                style:
                    const TextStyle(color: AppColors.warmWhite, fontSize: 12)),
          ),
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
  List<String> _items = const [];
  bool _loading = true;
  final TextEditingController _inputCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _inputCtrl.dispose();
    super.dispose();
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
    final t = _inputCtrl.text.trim();
    if (t.isEmpty) return;
    await SavedTopicsStore.add(t);
    _inputCtrl.clear();
    await _load();
  }

  Future<void> _remove(String t) async {
    await SavedTopicsStore.remove(t);
    await _load();
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
        const Text(
          '此处保存的话题会出现在首页「自由话题」输入框上方，点击即可直接使用。',
          style: TextStyle(color: AppColors.warmGray, fontSize: 12),
        ),
        const SizedBox(height: 16),
        Row(children: [
          Expanded(
            child: TextField(
              controller: _inputCtrl,
              style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
              decoration: InputDecoration(
                hintText: '添加一个话题…',
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
              onSubmitted: (_) => _add(),
            ),
          ),
          const SizedBox(width: 10),
          ElevatedButton.icon(
            onPressed: _add,
            icon: const Icon(Icons.add, size: 16),
            label: const Text('添加'),
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.amberGold,
              foregroundColor: Colors.black,
            ),
          ),
        ]),
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
          ..._items.map(
            (t) => Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: BoxDecoration(
                color: AppColors.studyWall,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                    color: AppColors.warmGray.withValues(alpha: 0.4)),
              ),
              child: Row(children: [
                const Icon(Icons.bookmark,
                    color: AppColors.amberGold, size: 16),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(t,
                      style: const TextStyle(
                          color: AppColors.warmWhite, fontSize: 13)),
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline,
                      color: AppColors.warmGray, size: 18),
                  tooltip: '删除',
                  onPressed: () => _remove(t),
                ),
              ]),
            ),
          ),
        const SizedBox(height: 32),
      ],
    );
  }
}
