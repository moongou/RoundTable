import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/config_models.dart';
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

class _SettingsContentState extends ConsumerState<_SettingsContent> {
  late TextEditingController _serverUrlCtrl;

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

  @override
  void initState() {
    super.initState();
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
    _serverUrlCtrl.dispose();
    _tavilyKeyCtrl.dispose();
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
                _DialogRow('交互方式', s.pushToTalk ? '按住说话' : '自由对话'),
                _DialogRow('服务器', s.serverUrl),
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
    if (url.isNotEmpty && urlMap.containsKey(p.id))
      updates[urlMap[p.id]!] = url;
    if (key.isNotEmpty && keyMap.containsKey(p.id))
      updates[keyMap[p.id]!] = key;
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
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth > 900;
          if (wide) {
            return _buildWideLayout(
                s, providersAsync, speechAsync, healthAsync, currentAsync);
          }
          return _buildNarrowLayout(
              s, providersAsync, speechAsync, healthAsync, currentAsync);
        },
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Wide layout (>900 px): two columns
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildWideLayout(LocalSettings s, AsyncValue providersAsync,
      AsyncValue speechAsync, AsyncValue healthAsync, AsyncValue currentAsync) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Left column – server + LLM + web search
        Expanded(
          flex: 5,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 10, 32),
            children: [
              _buildServerSection(currentAsync),
              const SizedBox(height: 14),
              _buildLlmSection(providersAsync, s),
              const SizedBox(height: 14),
              _buildTavilySection(currentAsync),
            ],
          ),
        ),
        // Right column – ASR + TTS + interaction + summary + health
        Expanded(
          flex: 4,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(10, 16, 20, 32),
            children: [
              _buildAsrSection(speechAsync, s),
              const SizedBox(height: 14),
              _buildTtsSection(speechAsync, s),
              const SizedBox(height: 14),
              _buildInteractionSection(s),
              const SizedBox(height: 14),
              _buildSummarySection(s, currentAsync),
              const SizedBox(height: 14),
              _buildHealthSection(healthAsync),
            ],
          ),
        ),
      ],
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Narrow layout (≤900 px): single column
  // ─────────────────────────────────────────────────────────────────────────

  Widget _buildNarrowLayout(LocalSettings s, AsyncValue providersAsync,
      AsyncValue speechAsync, AsyncValue healthAsync, AsyncValue currentAsync) {
    return ListView(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      children: [
        _buildServerSection(currentAsync),
        const SizedBox(height: 14),
        _buildLlmSection(providersAsync, s),
        const SizedBox(height: 14),
        _buildAsrSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildTtsSection(speechAsync, s),
        const SizedBox(height: 14),
        _buildInteractionSection(s),
        const SizedBox(height: 14),
        _buildTavilySection(currentAsync),
        const SizedBox(height: 14),
        _buildSummarySection(s, currentAsync),
        const SizedBox(height: 14),
        _buildHealthSection(healthAsync),
        const SizedBox(height: 32),
      ],
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  //  Section builders (reused by both layouts)
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
            subtitle: const Text('空格键：按住录音，松开发送  ·  Esc：取消',
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
              _SummaryRow('交互方式', s.pushToTalk ? '按住说话' : '自由对话'),
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
            obscure: true),
        const SizedBox(height: 8),
      ],
      const _Label('请求地址（Base URL）'),
      _Field(ctrl: _bu(p), hint: p.baseUrl),
      const SizedBox(height: 8),
      Row(children: [
        _OutBtn(testing ? '测试中…' : '🔌 测试连接',
            onTap: testing ? null : () => _testProvider(p), loading: testing),
        if (result != null) ...[
          const SizedBox(width: 8),
          Icon(result.success ? Icons.check_circle : Icons.cancel,
              color: result.success ? Colors.green : Colors.red, size: 14),
          const SizedBox(width: 4),
          Flexible(
              child: Text(
            result.success
                ? '✓ ${result.models.length} 个模型可用'
                : result.error ?? '失败',
            style: TextStyle(
                fontSize: 10,
                color: result.success ? Colors.green : Colors.red),
            overflow: TextOverflow.ellipsis,
          )),
        ],
      ]),
      const SizedBox(height: 8),
      const _Label('模型'),
      if (result != null && result.success && result.models.isNotEmpty)
        _DropField(items: result.models, ctrl: _mc(p))
      else
        _Field(ctrl: _mc(p), hint: p.model),
      const SizedBox(height: 10),
      Row(children: [
        Expanded(
            child: _OutBtn('应用（本次有效）',
                onTap: saving ? null : () => _saveProvider(p))),
        const SizedBox(width: 8),
        Expanded(
            child: _GoldBtn('💾 写入 .env',
                onTap: saving ? null : () => _saveProvider(p, persist: true))),
      ]),
      if (!active) ...[
        const SizedBox(height: 8),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: () => _selectProvider(p),
            icon: const Icon(Icons.check_circle_outline, size: 14),
            label: const Text('使用此提供商'),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.amberGold,
              side: const BorderSide(color: AppColors.amberGold),
              padding: const EdgeInsets.symmetric(vertical: 8),
              textStyle: const TextStyle(fontSize: 12),
            ),
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

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Radio<String>(
          value: p.id,
          groupValue: activeId,
          activeColor: AppColors.amberGold,
          materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
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
        _Field(
            ctrl: _vk(p),
            hint: p.hasApiKey ? '已配置（输入新值覆盖）' : '输入 API Key',
            obscure: true),
        const SizedBox(height: 6),
      ],
      const _Label('服务地址'),
      _Field(
          ctrl: _vu(p),
          hint:
              p.defaultUrl.isNotEmpty ? p.defaultUrl : 'http://localhost:???'),
      const SizedBox(height: 8),
      Row(children: [
        _OutBtn(testing ? '测试中…' : '🔌 测试连接',
            onTap: testing ? null : () => _testVoice(p), loading: testing),
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
      if (result != null && result.success && voices.isNotEmpty) ...[
        const SizedBox(height: 8),
        const _Label('选择音色'),
        _VoiceDrop(
          voices: voices,
          value: _selectedVoice[p.id],
          onChanged: (v) => setState(() => _selectedVoice[p.id] = v),
        ),
      ],
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: _OutBtn('应用', onTap: () => _saveVoice(p))),
        const SizedBox(width: 8),
        Expanded(
            child:
                _GoldBtn('💾 .env', onTap: () => _saveVoice(p, persist: true))),
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

class _GoldBtn extends StatelessWidget {
  final String label;
  final VoidCallback? onTap;
  const _GoldBtn(this.label, {this.onTap});
  @override
  Widget build(BuildContext context) => FilledButton(
        onPressed: onTap,
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.amberGold,
          foregroundColor: AppColors.scrollTitle,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
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
  const _OutBtn(this.label, {this.onTap, this.loading = false});
  @override
  Widget build(BuildContext context) => OutlinedButton(
        onPressed: onTap,
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.warmWhite,
          side: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.5)),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
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
      subtitle: Text(health.url,
          style: const TextStyle(color: AppColors.warmGray, fontSize: 10)),
      trailing: Text(ok ? '在线' : '离线',
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
