import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../services/api_client.dart';
import '../session/session_screen.dart';

/// 首页 - 话题选择和角色配置
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final ApiClient _apiClient = ApiClient();
  List<Topic> _topics = [];
  List<CharacterTemplate> _characters = [];
  bool _loading = true;

  // 当前选择
  Topic? _selectedTopic;
  final Set<String> _selectedCharacterIds = {'explorer', 'skeptic'};
  final TextEditingController _nameController = TextEditingController(text: '同学');

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    try {
      final topicsData = await _apiClient.getTopics();
      final charsData = await _apiClient.getCharacters();
      setState(() {
        _topics = topicsData.map((t) => Topic.fromJson(t)).toList();
        _characters = charsData.map((c) => CharacterTemplate.fromJson(c)).toList();
        _loading = false;
      });
    } catch (e) {
      setState(() => _loading = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('加载失败: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('圆桌思辨'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () => Navigator.pushNamed(context, '/settings'),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _loadData,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  // 标题区域
                  Text(
                    '选择一个话题，开始思辨讨论',
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'AI主持人会引导讨论，虚拟角色各持己见，你来决定你的立场！',
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                          color: Colors.grey[600],
                        ),
                  ),
                  const SizedBox(height: 24),

                  // 话题列表
                  Text('选择话题', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  ..._topics.map((topic) => _TopicCard(
                        topic: topic,
                        isSelected: _selectedTopic?.id == topic.id,
                        onTap: () => setState(() => _selectedTopic = topic),
                      )),

                  const SizedBox(height: 24),

                  // 角色选择
                  Text('选择讨论角色', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: _characters
                        .where((c) => c.id != 'moderator')
                        .map((char) => _CharacterChip(
                              character: char,
                              isSelected: _selectedCharacterIds.contains(char.id),
                              onTap: () {
                                setState(() {
                                  if (_selectedCharacterIds.contains(char.id)) {
                                    if (_selectedCharacterIds.length > 1) {
                                      _selectedCharacterIds.remove(char.id);
                                    }
                                  } else {
                                    _selectedCharacterIds.add(char.id);
                                  }
                                });
                              },
                            ))
                        .toList(),
                  ),

                  const SizedBox(height: 24),

                  // 你的名字
                  Text('你的名字', style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  TextField(
                    controller: _nameController,
                    decoration: const InputDecoration(
                      hintText: '输入你的名字',
                      border: OutlineInputBorder(),
                    ),
                  ),

                  const SizedBox(height: 32),

                  // 开始按钮
                  FilledButton.icon(
                    onPressed: _selectedTopic != null ? _startDiscussion : null,
                    icon: const Icon(Icons.forum),
                    label: const Text('开始讨论'),
                    style: FilledButton.styleFrom(
                      minimumSize: const Size(double.infinity, 48),
                    ),
                  ),
                ],
              ),
            ),
    );
  }

  void _startDiscussion() {
    if (_selectedTopic == null) return;

    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SessionScreen(
          topic: _selectedTopic!,
          characterIds: _selectedCharacterIds.toList(),
          humanName: _nameController.text.isEmpty ? '同学' : _nameController.text,
        ),
      ),
    );
  }
}

class _TopicCard extends StatelessWidget {
  final Topic topic;
  final bool isSelected;
  final VoidCallback onTap;

  const _TopicCard({
    required this.topic,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: isSelected ? Theme.of(context).colorScheme.primaryContainer : null,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      topic.title,
                      style: Theme.of(context).textTheme.titleSmall?.copyWith(
                            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                          ),
                    ),
                  ),
                  if (isSelected)
                    Icon(Icons.check_circle, color: Theme.of(context).colorScheme.primary),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                topic.description,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Colors.grey[600],
                    ),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 4,
                children: [
                  Chip(
                    label: Text(topic.category),
                    visualDensity: VisualDensity.compact,
                  ),
                  ...topic.tags.take(2).map(
                        (tag) => Chip(
                          label: Text(tag),
                          visualDensity: VisualDensity.compact,
                        ),
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

class _CharacterChip extends StatelessWidget {
  final CharacterTemplate character;
  final bool isSelected;
  final VoidCallback onTap;

  const _CharacterChip({
    required this.character,
    required this.isSelected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return FilterChip(
      label: Text('${character.avatar} ${character.displayName}'),
      selected: isSelected,
      onSelected: (_) => onTap(),
      showCheckmark: false,
    );
  }
}