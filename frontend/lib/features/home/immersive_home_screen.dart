import 'dart:math';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

import '../../models/discussion_models.dart';
import '../../painters/bookshelf_painter.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
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
  List<Map<String, dynamic>> _thinkers = [];
  List<Map<String, dynamic>> _categories = [];
  bool _loading = true;

  Topic? _selectedTopic;
  String? _selectedCategory;
  final Set<String> _selectedCharacterIds = {};
  final Set<String> _selectedThinkerIds = {};
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
      List<Map<String, dynamic>> thinkersData = [];
      List<Map<String, dynamic>> categoriesData = [];
      try {
        thinkersData = await apiClient.getThinkers();
        categoriesData = await apiClient.getTopicCategories();
      } catch (_) {}
      setState(() {
        _topics = topicsData.map((t) => Topic.fromJson(t)).toList();
        _characters = charsData.map((c) => CharacterTemplate.fromJson(c)).toList();
        _thinkers = thinkersData;
        _categories = categoriesData;
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

  void _openDevPanel(BuildContext context) {
    // Open devpanel in a new tab - runs on :8888 on the same host
    final host = Uri.base.host;
    final url = 'http://$host:8888';
    html.window.open(url, '_blank');
  }

  @override
  void dispose() {
    _candleController.dispose();
    _entranceController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  bool get _canStart => _selectedTopic != null && (_selectedCharacterIds.isNotEmpty || _selectedThinkerIds.isNotEmpty);

  List<Topic> get _filteredTopics {
    if (_selectedCategory == null) return _topics;
    return _topics.where((t) => t.category == _selectedCategory).toList();
  }

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

    // 将思想家分为左右两组
    final halfCount = (_thinkers.length / 2).ceil();
    final leftThinkers = _thinkers.sublist(0, halfCount);
    final rightThinkers = _thinkers.length > halfCount
        ? _thinkers.sublist(halfCount)
        : <Map<String, dynamic>>[];

    return Stack(
      children: [
        // ── 主内容列（中央纵向布局）──────────────────────────────────────
        Column(
          children: [
            // ─ 顶部标题栏 ──────────────────────────────────────────────
            Padding(
              padding: const EdgeInsets.only(top: 48, left: 24, right: 24),
              child: Row(
                children: [
                  Expanded(
                    child: Text('圆桌思辨', style: AppTheme.calligraphyStyleDark(fontSize: 28)),
                  ),
                  // 姓名输入
                  SizedBox(
                    width: 100,
                    height: 36,
                    child: TextField(
                      controller: _nameController,
                      style: const TextStyle(color: AppColors.warmWhite, fontSize: 13),
                      decoration: InputDecoration(
                        hintText: '你的名字',
                        hintStyle: const TextStyle(color: AppColors.warmGray, fontSize: 12),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10),
                          borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10),
                          borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(10),
                          borderSide: const BorderSide(color: AppColors.amberGold),
                        ),
                        filled: true,
                        fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                        contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 0),
                      ),
                    ),
                  ),
                  const SizedBox(width: 4),
                  IconButton(
                    icon: const Icon(Icons.monitor_heart_outlined, color: AppColors.warmGray, size: 20),
                    tooltip: '后台服务',
                    onPressed: () => _openDevPanel(context),
                  ),
                  IconButton(
                    icon: const Icon(Icons.settings, color: AppColors.warmGray, size: 20),
                    onPressed: () => Navigator.pushNamed(context, '/settings'),
                  ),
                ],
              ),
            ),
            Text('选择话题，入座讨论', style: TextStyle(color: AppColors.warmGray, fontSize: 14)),
            const SizedBox(height: 10),

            // ─ 话题分类筛选 ─────────────────────────────────────────────
            if (_categories.isNotEmpty) ...[
              SizedBox(
                height: 34,
                child: ScrollConfiguration(
                  behavior: ScrollConfiguration.of(context).copyWith(
                    dragDevices: {PointerDeviceKind.touch, PointerDeviceKind.mouse, PointerDeviceKind.trackpad},
                  ),
                  child: ListView.builder(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    itemCount: _categories.length + 1,
                    itemBuilder: (context, index) {
                      if (index == 0) {
                        final isAll = _selectedCategory == null;
                        return Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: ChoiceChip(
                            label: Text('全部', style: TextStyle(
                              color: isAll ? AppColors.scrollTitle : AppColors.warmGray,
                              fontSize: 12,
                            )),
                            selected: isAll,
                            selectedColor: AppColors.amberGold,
                            backgroundColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                            onSelected: (_) => setState(() => _selectedCategory = null),
                          ),
                        );
                      }
                      final cat = _categories[index - 1];
                      final catId = cat['id'] as String? ?? '';
                      final catName = cat['name'] as String? ?? catId;
                      final count = cat['count'] as int? ?? 0;
                      final isSelected = _selectedCategory == catId;
                      return Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          label: Text('$catName($count)', style: TextStyle(
                            color: isSelected ? AppColors.scrollTitle : AppColors.warmGray,
                            fontSize: 12,
                          )),
                          selected: isSelected,
                          selectedColor: AppColors.amberGold,
                          backgroundColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                          onSelected: (_) => setState(() => _selectedCategory = catId),
                        ),
                      );
                    },
                  ),
                ),
              ),
              const SizedBox(height: 10),
            ],

            // ─ 话题卡片（水平滚动，支持鼠标拖动） ────────────────────────
            SizedBox(
              height: 190,
              child: ScrollConfiguration(
                behavior: ScrollConfiguration.of(context).copyWith(
                  dragDevices: {
                    PointerDeviceKind.touch,
                    PointerDeviceKind.mouse,
                    PointerDeviceKind.trackpad,
                  },
                ),
                child: ListView.builder(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  itemCount: _filteredTopics.length,
                  itemBuilder: (context, index) {
                    final topic = _filteredTopics[index];
                    final rotation = (index % 2 == 0) ? -2.0 : 1.5;
                    return Padding(
                      padding: const EdgeInsets.only(right: 14),
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
            ),

            // ─ 圆桌中央空间（让圆桌背景透出来）────────────────────────
            const Spacer(),

            // ─ 角色选择区域（共同思辨的角色）──────────────────────────
            if (_characters.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Row(children: [
                  Text('共同思辨的角色', style: AppTheme.calligraphyStyleDark(fontSize: 14)),
                  const SizedBox(width: 8),
                  if (_selectedCharacterIds.isNotEmpty)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                      decoration: BoxDecoration(
                        color: AppColors.amberGold,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text('${_selectedCharacterIds.length}位',
                          style: const TextStyle(color: AppColors.scrollTitle, fontSize: 11, fontWeight: FontWeight.w500)),
                    ),
                ]),
              ),
              const SizedBox(height: 6),
              SizedBox(
                height: 44,
                child: ScrollConfiguration(
                  behavior: ScrollConfiguration.of(context).copyWith(
                    dragDevices: {PointerDeviceKind.touch, PointerDeviceKind.mouse, PointerDeviceKind.trackpad},
                  ),
                  child: ListView.builder(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    itemCount: _characters.length,
                    itemBuilder: (context, index) {
                      final char = _characters[index];
                      final id = char.id;
                      final name = char.name;
                      final avatar = char.avatar;
                      final isSelected = _selectedCharacterIds.contains(id);
                      return Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: FilterChip(
                          avatar: Text(avatar, style: const TextStyle(fontSize: 14)),
                          label: Text(name, style: TextStyle(
                            color: isSelected ? AppColors.scrollTitle : AppColors.warmWhite,
                            fontSize: 12,
                          )),
                          selected: isSelected,
                          selectedColor: const Color(0xFF5BA3D9).withValues(alpha: 0.8),
                          backgroundColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                          checkmarkColor: AppColors.scrollTitle,
                          onSelected: (selected) {
                            setState(() {
                              if (selected) _selectedCharacterIds.add(id);
                              else _selectedCharacterIds.remove(id);
                            });
                          },
                        ),
                      );
                    },
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],

            // ─ 底部：提示 + 入座按钮 ────────────────────────────────
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 220,
                    height: 48,
                    child: FilledButton.icon(
                      onPressed: _canStart ? _startDiscussion : null,
                      icon: const Icon(Icons.event_seat, size: 18),
                      label: Text(
                        _canStart ? '入座开始' : '选择话题与参与者',
                        style: AppTheme.calligraphyStyle(
                          fontSize: 16,
                          color: _canStart ? Colors.white : AppColors.warmGray,
                        ),
                      ),
                      style: FilledButton.styleFrom(
                        backgroundColor: _canStart
                            ? const Color(0xFF5BA3D9)
                            : AppColors.studyWallLight,
                        foregroundColor: Colors.white,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(24),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 28),
          ],
        ),

        // ── 左侧思想家面板（圆桌左侧中部）──────────────────────────────
        if (_thinkers.isNotEmpty)
          Positioned(
            left: 0,
            top: 0,
            bottom: 0,
            width: 130,
            child: Center(
              child: _ThinkerPanel(
                thinkers: leftThinkers,
                selectedIds: _selectedThinkerIds,
                onToggle: (id) {
                  setState(() {
                    if (_selectedThinkerIds.contains(id)) {
                      _selectedThinkerIds.remove(id);
                    } else {
                      _selectedThinkerIds.add(id);
                    }
                  });
                },
                title: '思想家',
              ),
            ),
          ),

        // ── 右侧思想家面板（圆桌右侧中部）──────────────────────────────
        if (_thinkers.isNotEmpty && rightThinkers.isNotEmpty)
          Positioned(
            right: 0,
            top: 0,
            bottom: 0,
            width: 130,
            child: Center(
              child: _ThinkerPanel(
                thinkers: rightThinkers,
                selectedIds: _selectedThinkerIds,
                onToggle: (id) {
                  setState(() {
                    if (_selectedThinkerIds.contains(id)) {
                      _selectedThinkerIds.remove(id);
                    } else {
                      _selectedThinkerIds.add(id);
                    }
                  });
                },
                title: null,
              ),
            ),
          ),
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
          thinkerIds: _selectedThinkerIds.toList(),
          humanName: _nameController.text.isEmpty ? '同学' : _nameController.text,
        ),
      ),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  思想家侧边面板
// ─────────────────────────────────────────────────────────────────────────────

class _ThinkerPanel extends StatelessWidget {
  final List<Map<String, dynamic>> thinkers;
  final Set<String> selectedIds;
  final void Function(String id) onToggle;
  final String? title;

  const _ThinkerPanel({
    required this.thinkers,
    required this.selectedIds,
    required this.onToggle,
    this.title,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 80, horizontal: 6),
      padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 6),
      decoration: BoxDecoration(
        color: AppColors.studyWallLight.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.warmGray.withValues(alpha: 0.2)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.3),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (title != null) ...[
            Text(title!, style: const TextStyle(
              color: AppColors.amberGold,
              fontSize: 11,
              fontWeight: FontWeight.bold,
              letterSpacing: 1,
            )),
            const SizedBox(height: 6),
            Container(height: 1, color: AppColors.warmGray.withValues(alpha: 0.2)),
            const SizedBox(height: 6),
          ],
          ...thinkers.map((thinker) {
            final id = thinker['id'] as String? ?? '';
            final name = thinker['name'] as String? ?? id;
            final avatar = thinker['avatar'] as String? ?? '🧠';
            final isSelected = selectedIds.contains(id);
            return Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: InkWell(
                onTap: () => onToggle(id),
                borderRadius: BorderRadius.circular(8),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 150),
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
                  decoration: BoxDecoration(
                    color: isSelected
                        ? AppColors.amberGold.withValues(alpha: 0.25)
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(
                      color: isSelected
                          ? AppColors.amberGold.withValues(alpha: 0.6)
                          : Colors.transparent,
                    ),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(avatar, style: const TextStyle(fontSize: 20)),
                      const SizedBox(height: 2),
                      Text(
                        name,
                        style: TextStyle(
                          color: isSelected ? AppColors.amberGold : AppColors.warmWhite,
                          fontSize: 10,
                          fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                        ),
                        textAlign: TextAlign.center,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
              ),
            );
          }),
        ],
      ),
    );
  }
}