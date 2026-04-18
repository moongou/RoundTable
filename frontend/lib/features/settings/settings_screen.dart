import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/config_models.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 设置页面 - 配置 LLM 提供商、语音服务、交互方式
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final localSettings = ref.watch(localSettingsProvider);

    return localSettings.when(
      loading: () => Scaffold(
        backgroundColor: AppColors.studyWall,
        appBar: AppBar(
          title: Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
          backgroundColor: AppColors.studyWall,
        ),
        body: const Center(child: CircularProgressIndicator(color: AppColors.amberGold)),
      ),
      error: (e, _) => Scaffold(
        backgroundColor: AppColors.studyWall,
        appBar: AppBar(
          title: Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
          backgroundColor: AppColors.studyWall,
        ),
        body: Center(child: Text('加载设置失败: $e', style: const TextStyle(color: AppColors.warmWhite))),
      ),
      data: (settings) => _SettingsContent(settings: settings),
    );
  }
}

class _SettingsContent extends ConsumerStatefulWidget {
  final LocalSettings settings;

  const _SettingsContent({required this.settings});

  @override
  ConsumerState<_SettingsContent> createState() => _SettingsContentState();
}

class _SettingsContentState extends ConsumerState<_SettingsContent> {
  late TextEditingController _serverUrlController;
  bool _healthChecking = false;

  @override
  void initState() {
    super.initState();
    _serverUrlController =
        TextEditingController(text: widget.settings.serverUrl);
  }

  @override
  void dispose() {
    _serverUrlController.dispose();
    super.dispose();
  }

  Future<void> _saveServerUrl() async {
    final url = _serverUrlController.text.trim();
    if (url.isNotEmpty) {
      await ref.read(localSettingsProvider.notifier).setServerUrl(url);
      // 刷新远端配置（因为 baseUrl 变了）
      ref.invalidate(providersProvider);
      ref.invalidate(speechConfigProvider);
      ref.invalidate(healthStatusProvider);
      ref.invalidate(currentConfigProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('服务器地址已更新'), duration: Duration(seconds: 2)),
        );
      }
    }
  }

  Future<void> _checkHealth() async {
    setState(() => _healthChecking = true);
    ref.invalidate(healthStatusProvider);
    // 等待 FutureProvider 完成
    await Future.delayed(const Duration(seconds: 2));
    setState(() => _healthChecking = false);
  }

  Future<void> _validateConfig() async {
    ref.invalidate(configValidationProvider);
    final result = await ref.read(configValidationProvider.future);
    if (mounted) {
      final success = result?.valid ?? false;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(success ? '配置有效' : (result?.message ?? '配置无效')),
          backgroundColor: success ? Colors.green : Colors.red,
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.settings;
    final providersAsync = ref.watch(providersProvider);
    final speechAsync = ref.watch(speechConfigProvider);
    final healthAsync = ref.watch(healthStatusProvider);
    final currentAsync = ref.watch(currentConfigProvider);

    return Scaffold(
      backgroundColor: AppColors.studyWall,
      appBar: AppBar(
        title: Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
        backgroundColor: AppColors.studyWall,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // ── 服务器地址 ────────────────────────────────────────────────────
          _SectionCard(
            title: '服务器地址',
            icon: Icons.dns_outlined,
            children: [
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _serverUrlController,
                      style: const TextStyle(color: AppColors.warmWhite),
                      decoration: InputDecoration(
                        hintText: 'http://localhost:8001',
                        hintStyle: const TextStyle(color: AppColors.warmGray),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                          borderSide: const BorderSide(color: AppColors.amberGold),
                        ),
                        filled: true,
                        fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                      ),
                      onSubmitted: (_) => _saveServerUrl(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _saveServerUrl,
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.amberGold,
                      foregroundColor: AppColors.scrollTitle,
                    ),
                    child: const Text('保存'),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              currentAsync.when(
                data: (config) => Text(
                  '当前: ${config.llmProviderName} / ${config.model}',
                  style: const TextStyle(color: AppColors.warmGray, fontSize: 12),
                ),
                loading: () => const SizedBox.shrink(),
                error: (_, __) => const SizedBox.shrink(),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── AI 模型提供商 ──────────────────────────────────────────────────
          _SectionCard(
            title: 'AI 模型提供商',
            icon: Icons.smart_toy_outlined,
            action: TextButton.icon(
              onPressed: _validateConfig,
              icon: Icon(Icons.check_circle_outline, size: 18, color: AppColors.amberGold),
              label: Text('验证配置', style: TextStyle(color: AppColors.amberGold)),
            ),
            children: [
              providersAsync.when(
                data: (providers) => Column(
                  children: providers
                      .map((p) => _ProviderTile(
                            provider: p,
                            isSelected: p.id == settings.llmProvider,
                            onTap: () => ref
                                .read(localSettingsProvider.notifier)
                                .setLlmProvider(p.id),
                          ))
                      .toList(),
                ),
                loading: () => const Center(
                  child: CircularProgressIndicator(color: AppColors.amberGold),
                ),
                error: (e, _) => Text('加载失败: $e',
                    style: const TextStyle(color: Colors.red)),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── 语音识别（ASR）─────────────────────────────────────────────────
          _SectionCard(
            title: '语音识别（ASR）',
            icon: Icons.mic_outlined,
            children: [
              speechAsync.when(
                data: (speech) => Column(
                  children: speech.asrProviders
                      .map((p) => RadioListTile<String>(
                            title: Text(p.name, style: const TextStyle(color: AppColors.warmWhite)),
                            value: p.id,
                            groupValue: settings.asrProvider,
                            onChanged: (val) => ref
                                .read(localSettingsProvider.notifier)
                                .setAsrProvider(val!),
                            contentPadding: EdgeInsets.zero,
                            dense: true,
                            activeColor: AppColors.amberGold,
                          ))
                      .toList(),
                ),
                loading: () => const Center(
                  child: CircularProgressIndicator(color: AppColors.amberGold),
                ),
                error: (e, _) => Text('加载失败: $e',
                    style: const TextStyle(color: Colors.red)),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── 语音合成（TTS）─────────────────────────────────────────────────
          _SectionCard(
            title: '语音合成（TTS）',
            icon: Icons.volume_up_outlined,
            children: [
              speechAsync.when(
                data: (speech) => Column(
                  children: speech.ttsProviders
                      .map((p) => RadioListTile<String>(
                            title: Text(p.name, style: const TextStyle(color: AppColors.warmWhite)),
                            value: p.id,
                            groupValue: settings.ttsProvider,
                            onChanged: (val) => ref
                                .read(localSettingsProvider.notifier)
                                .setTtsProvider(val!),
                            contentPadding: EdgeInsets.zero,
                            dense: true,
                            activeColor: AppColors.amberGold,
                          ))
                      .toList(),
                ),
                loading: () => const Center(
                  child: CircularProgressIndicator(color: AppColors.amberGold),
                ),
                error: (e, _) => Text('加载失败: $e',
                    style: const TextStyle(color: Colors.red)),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── 交互方式 ────────────────────────────────────────────────────────
          _SectionCard(
            title: '交互方式',
            icon: Icons.touch_app_outlined,
            children: [
              SwitchListTile(
                title: const Text('按住说话（Push-to-Talk）', style: TextStyle(color: AppColors.warmWhite)),
                subtitle: const Text('开启时空格键/按钮按住才发言，松开结束；关闭时点击开始/结束发言',
                    style: TextStyle(color: AppColors.warmGray, fontSize: 12)),
                value: settings.pushToTalk,
                onChanged: (val) => ref
                    .read(localSettingsProvider.notifier)
                    .setPushToTalk(val),
                contentPadding: EdgeInsets.zero,
                activeColor: AppColors.amberGold,
              ),
            ],
          ),
          const SizedBox(height: 16),

          // ── 本地服务状态 ────────────────────────────────────────────────────
          _SectionCard(
            title: '本地服务状态',
            icon: Icons.monitor_heart_outlined,
            action: TextButton.icon(
              onPressed: _healthChecking ? null : _checkHealth,
              icon: _healthChecking
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.amberGold),
                    )
                  : const Icon(Icons.refresh, size: 18, color: AppColors.amberGold),
              label: Text('刷新', style: TextStyle(color: AppColors.amberGold)),
            ),
            children: [
              healthAsync.when(
                data: (healthMap) => Column(
                  children: healthMap.entries.map((entry) {
                    final h = entry.value;
                    return _ServiceHealthTile(health: h);
                  }).toList(),
                ),
                loading: () => const Center(child: CircularProgressIndicator(color: AppColors.amberGold)),
                error: (e, _) => Text('检查失败: $e',
                    style: const TextStyle(color: Colors.red)),
              ),
            ],
          ),

          const SizedBox(height: 32),
        ],
      ),
    );
  }
}

// ── 子组件 ──────────────────────────────────────────────────────────────────

class _SectionCard extends StatelessWidget {
  final String title;
  final IconData icon;
  final Widget? action;
  final List<Widget> children;

  const _SectionCard({
    required this.title,
    required this.icon,
    this.action,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.studyWallLight.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: AppColors.warmGray.withValues(alpha: 0.15),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 20, color: AppColors.amberGold),
              const SizedBox(width: 8),
              Expanded(
                child: Text(title, style: AppTheme.calligraphyStyleDark(fontSize: 16)),
              ),
              if (action != null) action!,
            ],
          ),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }
}

class _ProviderTile extends StatelessWidget {
  final ProviderInfo provider;
  final bool isSelected;
  final VoidCallback onTap;

  const _ProviderTile({
    required this.provider,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            border: Border.all(
              color: isSelected
                  ? AppColors.amberGold
                  : AppColors.warmGray.withValues(alpha: 0.2),
              width: isSelected ? 2 : 1,
            ),
            borderRadius: BorderRadius.circular(12),
            color: isSelected
                ? AppColors.amberGold.withValues(alpha: 0.1)
                : null,
          ),
          child: Row(
            children: [
              // 图标
              Text(provider.icon, style: const TextStyle(fontSize: 24)),
              const SizedBox(width: 12),
              // 提供商信息
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          provider.name,
                          style: TextStyle(
                            color: AppColors.warmWhite,
                            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (isSelected)
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.amberGold,
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              '使用中',
                              style: TextStyle(
                                color: AppColors.scrollTitle,
                                fontSize: 10,
                              ),
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      provider.model,
                      style: const TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 12,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      provider.baseUrl,
                      style: const TextStyle(
                        color: AppColors.warmGray,
                        fontSize: 10,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              // API Key 状态标识
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  if (provider.needsApiKey)
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          provider.hasApiKey ? Icons.check_circle : Icons.warning_amber,
                          size: 16,
                          color: provider.hasApiKey ? Colors.green : Colors.orange,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          provider.hasApiKey ? '已配置' : '未配置',
                          style: TextStyle(
                            color: provider.hasApiKey ? Colors.green : Colors.orange,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    )
                  else
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.computer, size: 16, color: Colors.blue),
                        const SizedBox(width: 4),
                        Text(
                          '本地',
                          style: TextStyle(
                            color: Colors.blue,
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ServiceHealthTile extends StatelessWidget {
  final ServiceHealth health;

  const _ServiceHealthTile({required this.health});

  @override
  Widget build(BuildContext context) {
    final color = health.reachable ? Colors.green : Colors.red;
    final serviceName = _serviceDisplayName(health.name);

    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        health.reachable ? Icons.check_circle : Icons.cancel,
        color: color,
        size: 20,
      ),
      title: Text(serviceName, style: const TextStyle(color: AppColors.warmWhite)),
      subtitle: Text(
        health.url,
        style: const TextStyle(color: AppColors.warmGray, fontSize: 11),
      ),
      trailing: Text(
        health.reachable ? '在线' : '离线',
        style: TextStyle(color: color, fontWeight: FontWeight.bold),
      ),
    );
  }

  String _serviceDisplayName(String name) {
    const nameMap = {
      'edge_tts': 'OpenAI Edge TTS',
      'cosyvoice': 'CosyVoice',
      'funasr': 'FunASR',
      'ollama': 'Ollama',
    };
    return nameMap[name] ?? name;
  }
}