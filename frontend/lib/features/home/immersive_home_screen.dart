import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/discussion_models.dart';
import '../../painters/bookshelf_painter.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
import '../session/glow_avatar.dart';
import '../session/immersive_session_screen.dart';
import 'scroll_topic_card.dart';

/// 沉浸式首页 - "推开书房的门"
class ImmersiveHomeScreen extends ConsumerStatefulWidget {
  const ImmersiveHomeScreen({super.key});

  @override
  ConsumerState<ImmersiveHomeScreen> createState() => _ImmersiveHomeScreenState();
}

class _ImmersiveHomeScreenState extends ConsumerState<ImmersiveHomeScreen>
    with TickerProviderStateMixin {
  List<Topic> _topics = [];
  List<CharacterTemplate> _characters = [];
  bool _loading = true;

  Topic? _selectedTopic;
  final Set<String> _selectedCharacterIds = {'explorer', 'skeptic'};
  final TextEditingController _nameController = TextEditingController(text: '同学');

  late AnimationController _candleController;
  late AnimationController _entranceController;
  List<CandleParticle>? _particles;

  @override
  void initState() {
    super.initState();
    _candleController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
    _entranceController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    );
    _loadData();
  }

  Future<void> _loadData() async {
    try {
      final apiClient = ref.read(apiClientProvider);
      final topicsData = await apiClient.getTopics();
      final charsData = await apiClient.getCharacters();
      setState(() {
        _topics = topicsData.map((t) => Topic.fromJson(t)).toList();
        _characters = charsData.map((c) => CharacterTemplate.fromJson(c)).toList();
        _loading = false;
      });
      _entranceController.forward();
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
  void dispose() {
    _candleController.dispose();
    _entranceController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  bool get _canStart => _selectedTopic != null;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final tableRadius = min(size.width, size.height) * 0.22;

    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 15,
    );

    return Scaffold(
      body: Stack(
        children: [
          CustomPaint(
            size: size,
            painter: BookshelfPainter(),
          ),
          Center(
            child: CustomPaint(
              size: Size(tableRadius * 2, tableRadius * 2),
              painter: RoundTablePainter(
                tableRadius: tableRadius,
                glowIntensity: 0.6,
              ),
            ),
          ),
          CustomPaint(
            size: size,
            painter: CandlelightPainter(
              particles: _particles!,
              animationValue: _candleController.value,
            ),
          ),
          AnimatedBuilder(
            animation: _entranceController,
            builder: (context, child) {
              final t = Curves.easeOutCubic.transform(_entranceController.value);
              return Opacity(
                opacity: t,
                child: Transform.scale(
                  scale: 0.95 + 0.05 * t,
                  child: child,
                ),
              );
            },
            child: _buildContent(size, tableRadius),
          ),
        ],
      ),
    );
  }

  Widget _buildContent(Size size, double tableRadius) {
    if (_loading) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(color: AppColors.amberGold),
            const SizedBox(height: 16),
            Text('推开书房的门...', style: AppTheme.calligraphyStyleDark(fontSize: 18)),
          ],
        ),
      );
    }

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.only(top: 48, left: 24, right: 24),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  '圆桌思辨',
                  style: AppTheme.calligraphyStyleDark(fontSize: 28),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.settings, color: AppColors.warmGray),
                onPressed: () => Navigator.pushNamed(context, '/settings'),
              ),
            ],
          ),
        ),
        Text(
          '选择话题，入座讨论',
          style: TextStyle(color: AppColors.warmGray, fontSize: 14),
        ),

        const Spacer(),

        SizedBox(
          height: 220,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 32),
            itemCount: _topics.length,
            itemBuilder: (context, index) {
              final topic = _topics[index];
              final rotation = (index % 2 == 0) ? -2.0 : 1.5;
              return Padding(
                padding: const EdgeInsets.only(right: 16),
                child: ScrollTopicCard(
                  topic: topic,
                  isSelected: _selectedTopic?.id == topic.id,
                  onTap: () => setState(() => _selectedTopic = topic),
                  rotation: rotation,
                ),
              );
            },
          ),
        ),

        const SizedBox(height: 24),

        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Text('选择讨论角色', style: AppTheme.calligraphyStyleDark(fontSize: 16)),
        ),
        const SizedBox(height: 12),
        SizedBox(
          height: 90,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 24),
            itemCount: _characters.where((c) => c.id != 'moderator').length,
            itemBuilder: (context, index) {
              final char = _characters.where((c) => c.id != 'moderator').toList()[index];
              final isSelected = _selectedCharacterIds.contains(char.id);
              return Padding(
                padding: const EdgeInsets.only(right: 16),
                child: GestureDetector(
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
                  child: GlowAvatar(
                    name: char.displayName,
                    avatar: char.avatar,
                    isDimmed: !isSelected,
                    isCurrentSpeaker: isSelected,
                  ),
                ),
              );
            },
          ),
        ),

        const SizedBox(height: 24),

        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Row(
            children: [
              SizedBox(
                width: 120,
                child: TextField(
                  controller: _nameController,
                  style: const TextStyle(color: AppColors.warmWhite, fontSize: 14),
                  decoration: InputDecoration(
                    hintText: '你的名字',
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
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: FilledButton.icon(
                  onPressed: _canStart ? _startDiscussion : null,
                  icon: const Icon(Icons.event_seat),
                  label: Text(
                    _canStart ? '入座开始' : '选择话题后入座',
                    style: AppTheme.calligraphyStyle(
                      fontSize: 16,
                      color: _canStart ? AppColors.scrollTitle : AppColors.warmGray,
                    ),
                  ),
                  style: FilledButton.styleFrom(
                    backgroundColor: _canStart
                        ? AppColors.amberGold
                        : AppColors.studyWallLight,
                    foregroundColor: AppColors.scrollTitle,
                    minimumSize: const Size(double.infinity, 48),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(16),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),

        const SizedBox(height: 32),
      ],
    );
  }

  void _startDiscussion() {
    if (_selectedTopic == null) return;
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => ImmersiveSessionScreen(
          topic: _selectedTopic!,
          characterIds: _selectedCharacterIds.toList(),
          humanName: _nameController.text.isEmpty ? '同学' : _nameController.text,
        ),
      ),
    );
  }
}