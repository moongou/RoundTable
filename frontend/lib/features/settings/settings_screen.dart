import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 设置页面 - 配置 LLM 提供商和服务器地址
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _serverController = TextEditingController(text: 'http://localhost:8000');
  String _selectedProvider = 'openai';

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _serverController.text = prefs.getString('server_url') ?? 'http://localhost:8000';
      _selectedProvider = prefs.getString('llm_provider') ?? 'openai';
    });
  }

  Future<void> _saveSettings() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', _serverController.text);
    await prefs.setString('llm_provider', _selectedProvider);

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('设置已保存')),
      );
    }
  }

  @override
  void dispose() {
    _serverController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 服务器地址
          Text('服务器地址', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _serverController,
            decoration: const InputDecoration(
              hintText: 'http://localhost:8000',
              border: OutlineInputBorder(),
              helperText: '后端 API 服务器地址',
            ),
          ),
          const SizedBox(height: 24),

          // LLM 提供商
          Text('AI 模型提供商', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          RadioListTile<String>(
            title: const Text('OpenAI'),
            subtitle: const Text('GPT-4o / GPT-4o-mini'),
            value: 'openai',
            groupValue: _selectedProvider,
            onChanged: (value) => setState(() => _selectedProvider = value!),
          ),
          RadioListTile<String>(
            title: const Text('通义千问'),
            subtitle: const Text('Qwen-Max / Qwen-Plus'),
            value: 'qwen',
            groupValue: _selectedProvider,
            onChanged: (value) => setState(() => _selectedProvider = value!),
          ),
          RadioListTile<String>(
            title: const Text('DeepSeek'),
            subtitle: const Text('DeepSeek-V3 / DeepSeek-Chat'),
            value: 'deepseek',
            groupValue: _selectedProvider,
            onChanged: (value) => setState(() => _selectedProvider = value!),
          ),
          const SizedBox(height: 24),

          // 保存按钮
          FilledButton.icon(
            onPressed: _saveSettings,
            icon: const Icon(Icons.save),
            label: const Text('保存设置'),
            style: FilledButton.styleFrom(
              minimumSize: const Size(double.infinity, 48),
            ),
          ),
        ],
      ),
    );
  }
}