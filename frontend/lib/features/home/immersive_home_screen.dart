import 'dart:math';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;

import '../../models/discussion_models.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../state/settings_provider.dart';
import '../session/immersive_session_screen.dart';

// ─── Design tokens ────────────────────────────────────────────────────────────
const _kBg = Color(0xFF0A0A12);
const _kSurface = Color(0xFF12121E);
const _kCard = Color(0xFF181828);
const _kBorder = Color(0xFF2A2A3E);
const _kNeonCyan = Color(0xFF00E5FF);
const _kNeonGold = Color(0xFFFFCC44);
const _kNeonViolet = Color(0xFFAA88FF);
const _kTextPrimary = Color(0xFFEEEEFF);
const _kTextSecondary = Color(0xFF8888AA);

// ─── Main Screen ──────────────────────────────────────────────────────────────
class ImmersiveHomeScreen extends ConsumerStatefulWidget {
  const ImmersiveHomeScreen({super.key});

  @override
  ConsumerState<ImmersiveHomeScreen> createState() =>
      _ImmersiveHomeScreenState();
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
  final TextEditingController _nameController =
      TextEditingController(text: '同学');

  late AnimationController _pulseCtrl;
  late AnimationController _entranceCtrl;
  late AnimationController _orbCtrl;
  late Animation<double> _pulseAnim;
  List<CandleParticle>? _particles;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    )..repeat(reverse: true);
    _pulseAnim = Tween<double>(begin: 0.85, end: 1.0).animate(
        CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut));
    _orbCtrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 12),
    )..repeat();
    _entranceCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
    _loadData();
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    _entranceCtrl.dispose();
    _orbCtrl.dispose();
    _nameController.dispose();
    super.dispose();
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
      _entranceCtrl.forward();
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
    final host = Uri.base.host;
    html.window.open('http://$host:8888', '_blank');
  }

  bool get _canStart =>
      _selectedTopic != null &&
      (_selectedCharacterIds.isNotEmpty || _selectedThinkerIds.isNotEmpty);

  List<Topic> get _filteredTopics {
    if (_selectedCategory == null) return _topics;
    return _topics.where((t) => t.category == _selectedCategory).toList();
  }

  void _startDiscussion() {
    if (_selectedTopic == null) return;
    Navigator.push(
      context,
      PageRouteBuilder(
        pageBuilder: (_, a1, a2) => ImmersiveSessionScreen(
          topic: _selectedTopic!,
          characterIds: _selectedCharacterIds.toList(),
          thinkerIds: _selectedThinkerIds.toList(),
          humanName:
              _nameController.text.isEmpty ? '同学' : _nameController.text,
        ),
        transitionsBuilder: (_, a1, a2, child) => FadeTransition(
          opacity: CurvedAnimation(parent: a1, curve: Curves.easeIn),
          child: child,
        ),
        transitionDuration: const Duration(milliseconds: 500),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 20,
    );

    return Scaffold(
      backgroundColor: _kBg,
      body: Stack(
        children: [
          // Ambient orbs
          AnimatedBuilder(
            animation: _orbCtrl,
            builder: (_, __) => CustomPaint(
              size: size,
              painter: _AmbientOrbPainter(_orbCtrl.value),
            ),
          ),
          // Particles
          AnimatedBuilder(
            animation: _pulseCtrl,
            builder: (_, __) => CustomPaint(
              size: size,
              painter: CandlelightPainter(
                particles: _particles!,
                animationValue: _pulseCtrl.value,
              ),
            ),
          ),
          // Main content
          _loading
              ? const Center(child: _LoadingView())
              : AnimatedBuilder(
                  animation: _entranceCtrl,
                  builder: (_, child) {
                    final t =
                        Curves.easeOutQuart.transform(_entranceCtrl.value);
                    return Opacity(opacity: t.clamp(0.0, 1.0), child: child);
                  },
                  child: _buildLayout(size),
                ),
        ],
      ),
    );
  }

  Widget _buildLayout(Size size) {
    final isWide = size.width > 900;
    return Column(
      children: [
        _TopBar(
          nameController: _nameController,
          onSettings: () => Navigator.pushNamed(context, '/settings'),
          onDevPanel: () => _openDevPanel(context),
        ),
        Expanded(
          child: isWide
              ? _WideLayout(
                  topics: _filteredTopics,
                  categories: _categories,
                  selectedCategory: _selectedCategory,
                  selectedTopicId: _selectedTopic?.id,
                  characters: _characters,
                  selectedCharacterIds: _selectedCharacterIds,
                  thinkers: _thinkers,
                  selectedThinkerIds: _selectedThinkerIds,
                  canStart: _canStart,
                  pulseAnim: _pulseAnim,
                  onCategoryChanged: (v) =>
                      setState(() => _selectedCategory = v),
                  onTopicSelected: (t) => setState(() => _selectedTopic = t),
                  onCharacterToggled: (id) => setState(() {
                    if (_selectedCharacterIds.contains(id))
                      _selectedCharacterIds.remove(id);
                    else
                      _selectedCharacterIds.add(id);
                  }),
                  onThinkerToggled: (id) => setState(() {
                    if (_selectedThinkerIds.contains(id))
                      _selectedThinkerIds.remove(id);
                    else
                      _selectedThinkerIds.add(id);
                  }),
                  onStart: _startDiscussion,
                )
              : _NarrowLayout(
                  topics: _filteredTopics,
                  categories: _categories,
                  selectedCategory: _selectedCategory,
                  selectedTopicId: _selectedTopic?.id,
                  characters: _characters,
                  selectedCharacterIds: _selectedCharacterIds,
                  thinkers: _thinkers,
                  selectedThinkerIds: _selectedThinkerIds,
                  canStart: _canStart,
                  pulseAnim: _pulseAnim,
                  onCategoryChanged: (v) =>
                      setState(() => _selectedCategory = v),
                  onTopicSelected: (t) => setState(() => _selectedTopic = t),
                  onCharacterToggled: (id) => setState(() {
                    if (_selectedCharacterIds.contains(id))
                      _selectedCharacterIds.remove(id);
                    else
                      _selectedCharacterIds.add(id);
                  }),
                  onThinkerToggled: (id) => setState(() {
                    if (_selectedThinkerIds.contains(id))
                      _selectedThinkerIds.remove(id);
                    else
                      _selectedThinkerIds.add(id);
                  }),
                  onStart: _startDiscussion,
                ),
        ),
      ],
    );
  }
}

// ─── Top Bar ──────────────────────────────────────────────────────────────────
class _TopBar extends StatelessWidget {
  final TextEditingController nameController;
  final VoidCallback onSettings;
  final VoidCallback onDevPanel;

  const _TopBar({
    required this.nameController,
    required this.onSettings,
    required this.onDevPanel,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 58,
      padding: const EdgeInsets.symmetric(horizontal: 24),
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(bottom: BorderSide(color: _kBorder, width: 1)),
      ),
      child: Row(
        children: [
          RichText(
            text: const TextSpan(
              children: [
                TextSpan(
                  text: '圆桌',
                  style: TextStyle(
                    color: _kNeonGold,
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 3,
                  ),
                ),
                TextSpan(
                  text: ' 思辨',
                  style: TextStyle(
                    color: _kTextPrimary,
                    fontSize: 20,
                    fontWeight: FontWeight.w300,
                    letterSpacing: 2,
                  ),
                ),
              ],
            ),
          ),
          const Spacer(),
          _GlassField(controller: nameController, hint: '你的名号'),
          const SizedBox(width: 8),
          _IconBtn(
              icon: Icons.monitor_heart_outlined,
              tooltip: '后台服务',
              onTap: onDevPanel),
          _IconBtn(
              icon: Icons.settings_outlined,
              tooltip: '设置',
              onTap: onSettings),
        ],
      ),
    );
  }
}

class _GlassField extends StatelessWidget {
  final TextEditingController controller;
  final String hint;

  const _GlassField({required this.controller, required this.hint});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 112,
      height: 34,
      child: TextField(
        controller: controller,
        style: const TextStyle(
            color: _kTextPrimary, fontSize: 13, fontWeight: FontWeight.w500),
        decoration: InputDecoration(
          hintText: hint,
          hintStyle: const TextStyle(color: _kTextSecondary, fontSize: 12),
          prefixIcon:
              const Icon(Icons.person_outline, color: _kNeonCyan, size: 15),
          border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: _kBorder)),
          enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: _kBorder)),
          focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(8),
              borderSide: const BorderSide(color: _kNeonCyan, width: 1.5)),
          filled: true,
          fillColor: _kCard,
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 6, vertical: 0),
        ),
      ),
    );
  }
}

class _IconBtn extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  const _IconBtn(
      {required this.icon, required this.tooltip, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: SizedBox(
          width: 36,
          height: 36,
          child: Icon(icon, color: _kTextSecondary, size: 20),
        ),
      ),
    );
  }
}

// ─── Wide Layout ──────────────────────────────────────────────────────────────
class _WideLayout extends StatelessWidget {
  final List<Topic> topics;
  final List<Map<String, dynamic>> categories;
  final String? selectedCategory;
  final String? selectedTopicId;
  final List<CharacterTemplate> characters;
  final Set<String> selectedCharacterIds;
  final List<Map<String, dynamic>> thinkers;
  final Set<String> selectedThinkerIds;
  final bool canStart;
  final Animation<double> pulseAnim;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;
  final ValueChanged<String> onCharacterToggled;
  final ValueChanged<String> onThinkerToggled;
  final VoidCallback onStart;

  const _WideLayout({
    required this.topics,
    required this.categories,
    required this.selectedCategory,
    required this.selectedTopicId,
    required this.characters,
    required this.selectedCharacterIds,
    required this.thinkers,
    required this.selectedThinkerIds,
    required this.canStart,
    required this.pulseAnim,
    required this.onCategoryChanged,
    required this.onTopicSelected,
    required this.onCharacterToggled,
    required this.onThinkerToggled,
    required this.onStart,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Expanded(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 300,
                child: _LeftPanel(
                  topics: topics,
                  categories: categories,
                  selectedCategory: selectedCategory,
                  selectedTopicId: selectedTopicId,
                  onCategoryChanged: onCategoryChanged,
                  onTopicSelected: onTopicSelected,
                ),
              ),
              Expanded(
                child: Center(
                  child: _CenterTable(
                    canStart: canStart,
                    pulseAnim: pulseAnim,
                    onStart: onStart,
                    selectedCount:
                        selectedCharacterIds.length + selectedThinkerIds.length,
                  ),
                ),
              ),
              SizedBox(
                width: 250,
                child: _RightPanel(
                  thinkers: thinkers,
                  selectedThinkerIds: selectedThinkerIds,
                  onThinkerToggled: onThinkerToggled,
                ),
              ),
            ],
          ),
        ),
        _CharacterBar(
          characters: characters,
          selectedCharacterIds: selectedCharacterIds,
          onCharacterToggled: onCharacterToggled,
        ),
      ],
    );
  }
}

// ─── Narrow Layout ────────────────────────────────────────────────────────────
class _NarrowLayout extends StatelessWidget {
  final List<Topic> topics;
  final List<Map<String, dynamic>> categories;
  final String? selectedCategory;
  final String? selectedTopicId;
  final List<CharacterTemplate> characters;
  final Set<String> selectedCharacterIds;
  final List<Map<String, dynamic>> thinkers;
  final Set<String> selectedThinkerIds;
  final bool canStart;
  final Animation<double> pulseAnim;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;
  final ValueChanged<String> onCharacterToggled;
  final ValueChanged<String> onThinkerToggled;
  final VoidCallback onStart;

  const _NarrowLayout({
    required this.topics,
    required this.categories,
    required this.selectedCategory,
    required this.selectedTopicId,
    required this.characters,
    required this.selectedCharacterIds,
    required this.thinkers,
    required this.selectedThinkerIds,
    required this.canStart,
    required this.pulseAnim,
    required this.onCategoryChanged,
    required this.onTopicSelected,
    required this.onCharacterToggled,
    required this.onThinkerToggled,
    required this.onStart,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        children: [
          _CenterTable(
            canStart: canStart,
            pulseAnim: pulseAnim,
            onStart: onStart,
            selectedCount:
                selectedCharacterIds.length + selectedThinkerIds.length,
          ),
          const SizedBox(height: 20),
          _SectionPanel(
            title: '话题',
            neonColor: _kNeonCyan,
            child: _TopicsContent(
              topics: topics,
              categories: categories,
              selectedCategory: selectedCategory,
              selectedTopicId: selectedTopicId,
              onCategoryChanged: onCategoryChanged,
              onTopicSelected: onTopicSelected,
            ),
          ),
          const SizedBox(height: 14),
          _SectionPanel(
            title: '角色',
            neonColor: _kNeonViolet,
            child: _CharacterGrid(
              characters: characters,
              selectedIds: selectedCharacterIds,
              onToggled: onCharacterToggled,
            ),
          ),
          const SizedBox(height: 14),
          _SectionPanel(
            title: '思想家',
            neonColor: _kNeonGold,
            child: _ThinkerGrid(
              thinkers: thinkers,
              selectedIds: selectedThinkerIds,
              onToggled: onThinkerToggled,
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Left Panel ───────────────────────────────────────────────────────────────
class _LeftPanel extends StatelessWidget {
  final List<Topic> topics;
  final List<Map<String, dynamic>> categories;
  final String? selectedCategory;
  final String? selectedTopicId;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;

  const _LeftPanel({
    required this.topics,
    required this.categories,
    required this.selectedCategory,
    required this.selectedTopicId,
    required this.onCategoryChanged,
    required this.onTopicSelected,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: double.infinity,
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(right: BorderSide(color: _kBorder)),
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: _SectionPanel(
          title: '话题',
          neonColor: _kNeonCyan,
          child: _TopicsContent(
            topics: topics,
            categories: categories,
            selectedCategory: selectedCategory,
            selectedTopicId: selectedTopicId,
            onCategoryChanged: onCategoryChanged,
            onTopicSelected: onTopicSelected,
          ),
        ),
      ),
    );
  }
}

// ─── Right Panel ──────────────────────────────────────────────────────────────
class _RightPanel extends StatelessWidget {
  final List<Map<String, dynamic>> thinkers;
  final Set<String> selectedThinkerIds;
  final ValueChanged<String> onThinkerToggled;

  const _RightPanel({
    required this.thinkers,
    required this.selectedThinkerIds,
    required this.onThinkerToggled,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: double.infinity,
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(left: BorderSide(color: _kBorder)),
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: _SectionPanel(
          title: '思想家',
          neonColor: _kNeonGold,
          child: _ThinkerGrid(
            thinkers: thinkers,
            selectedIds: selectedThinkerIds,
            onToggled: onThinkerToggled,
          ),
        ),
      ),
    );
  }
}

// ─── Section Panel ────────────────────────────────────────────────────────────
class _SectionPanel extends StatelessWidget {
  final String title;
  final Color neonColor;
  final Widget child;

  const _SectionPanel(
      {required this.title, required this.neonColor, required this.child});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 3,
              height: 16,
              decoration: BoxDecoration(
                color: neonColor,
                borderRadius: BorderRadius.circular(2),
                boxShadow: [
                  BoxShadow(
                      color: neonColor.withValues(alpha: 0.6), blurRadius: 6)
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              title,
              style: TextStyle(
                color: neonColor,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 2,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        child,
      ],
    );
  }
}

// ─── Topics Content ───────────────────────────────────────────────────────────
class _TopicsContent extends StatelessWidget {
  final List<Topic> topics;
  final List<Map<String, dynamic>> categories;
  final String? selectedCategory;
  final String? selectedTopicId;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;

  const _TopicsContent({
    required this.topics,
    required this.categories,
    required this.selectedCategory,
    required this.selectedTopicId,
    required this.onCategoryChanged,
    required this.onTopicSelected,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (categories.isNotEmpty)
          SizedBox(
            height: 28,
            child: ScrollConfiguration(
              behavior: ScrollConfiguration.of(context).copyWith(
                dragDevices: {
                  PointerDeviceKind.touch,
                  PointerDeviceKind.mouse,
                  PointerDeviceKind.trackpad,
                },
              ),
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: [
                  _CatChip(
                      label: '全部',
                      selected: selectedCategory == null,
                      onTap: () => onCategoryChanged(null)),
                  ...categories.map((cat) {
                    final id = cat['id'] as String? ?? '';
                    final name = cat['name'] as String? ?? id;
                    return _CatChip(
                      label: name,
                      selected: selectedCategory == id,
                      onTap: () => onCategoryChanged(id),
                    );
                  }),
                ],
              ),
            ),
          ),
        const SizedBox(height: 10),
        ...topics.take(14).map((topic) => _TopicRow(
              topic: topic,
              isSelected: selectedTopicId == topic.id,
              onTap: () => onTopicSelected(topic),
            )),
        if (topics.length > 14)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('+${topics.length - 14} 个话题',
                style: const TextStyle(
                    color: _kTextSecondary, fontSize: 11)),
          ),
      ],
    );
  }
}

class _CatChip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _CatChip(
      {required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: selected ? _kNeonCyan.withValues(alpha: 0.15) : _kCard,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: selected
                  ? _kNeonCyan.withValues(alpha: 0.8)
                  : _kBorder,
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: selected ? _kNeonCyan : _kTextSecondary,
              fontSize: 11,
              fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
            ),
          ),
        ),
      ),
    );
  }
}

class _TopicRow extends StatefulWidget {
  final Topic topic;
  final bool isSelected;
  final VoidCallback onTap;

  const _TopicRow(
      {required this.topic, required this.isSelected, required this.onTap});

  @override
  State<_TopicRow> createState() => _TopicRowState();
}

class _TopicRowState extends State<_TopicRow> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          margin: const EdgeInsets.only(bottom: 4),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          decoration: BoxDecoration(
            color: widget.isSelected
                ? _kNeonCyan.withValues(alpha: 0.12)
                : _hovered
                    ? _kCard
                    : Colors.transparent,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: widget.isSelected
                  ? _kNeonCyan.withValues(alpha: 0.5)
                  : Colors.transparent,
            ),
          ),
          child: Row(
            children: [
              Text('💬', style: const TextStyle(fontSize: 14)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  widget.topic.title,
                  style: TextStyle(
                    color:
                        widget.isSelected ? _kNeonCyan : _kTextPrimary,
                    fontSize: 13,
                    fontWeight: widget.isSelected
                        ? FontWeight.w600
                        : FontWeight.normal,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (widget.isSelected)
                const Icon(Icons.check_circle,
                    color: _kNeonCyan, size: 14),
            ],
          ),
        ),
      ),
    );
  }
}

// ─── Character Grid ───────────────────────────────────────────────────────────
class _CharacterGrid extends StatelessWidget {
  final List<CharacterTemplate> characters;
  final Set<String> selectedIds;
  final ValueChanged<String> onToggled;

  const _CharacterGrid({
    required this.characters,
    required this.selectedIds,
    required this.onToggled,
  });

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: characters
          .map((char) => _CharChip(
                avatar: char.avatar,
                name: char.name,
                isSelected: selectedIds.contains(char.id),
                onTap: () => onToggled(char.id),
              ))
          .toList(),
    );
  }
}

class _CharChip extends StatefulWidget {
  final String avatar;
  final String name;
  final bool isSelected;
  final VoidCallback onTap;

  const _CharChip({
    required this.avatar,
    required this.name,
    required this.isSelected,
    required this.onTap,
  });

  @override
  State<_CharChip> createState() => _CharChipState();
}

class _CharChipState extends State<_CharChip> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: widget.isSelected
                ? _kNeonViolet.withValues(alpha: 0.18)
                : _hovered
                    ? _kCard
                    : _kCard.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: widget.isSelected
                  ? _kNeonViolet.withValues(alpha: 0.9)
                  : _kBorder,
              width: widget.isSelected ? 1.5 : 1,
            ),
            boxShadow: widget.isSelected
                ? [
                    BoxShadow(
                        color: _kNeonViolet.withValues(alpha: 0.3),
                        blurRadius: 8)
                  ]
                : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(widget.avatar, style: const TextStyle(fontSize: 14)),
              const SizedBox(width: 5),
              Text(
                widget.name,
                style: TextStyle(
                  color: widget.isSelected ? _kNeonViolet : _kTextPrimary,
                  fontSize: 12,
                  fontWeight: widget.isSelected
                      ? FontWeight.w600
                      : FontWeight.normal,
                ),
              ),
              if (widget.isSelected) ...[
                const SizedBox(width: 4),
                const Icon(Icons.close, color: _kNeonViolet, size: 12),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

// ─── Thinker Grid ─────────────────────────────────────────────────────────────
class _ThinkerGrid extends StatelessWidget {
  final List<Map<String, dynamic>> thinkers;
  final Set<String> selectedIds;
  final ValueChanged<String> onToggled;

  const _ThinkerGrid({
    required this.thinkers,
    required this.selectedIds,
    required this.onToggled,
  });

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: thinkers
          .map((t) => _ThinkerChip(
                avatar: t['avatar'] as String? ?? '🧠',
                name: t['name'] as String? ?? (t['id'] as String? ?? ''),
                isSelected: selectedIds.contains(t['id'] as String? ?? ''),
                onTap: () => onToggled(t['id'] as String? ?? ''),
              ))
          .toList(),
    );
  }
}

class _ThinkerChip extends StatefulWidget {
  final String avatar;
  final String name;
  final bool isSelected;
  final VoidCallback onTap;

  const _ThinkerChip({
    required this.avatar,
    required this.name,
    required this.isSelected,
    required this.onTap,
  });

  @override
  State<_ThinkerChip> createState() => _ThinkerChipState();
}

class _ThinkerChipState extends State<_ThinkerChip> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
          decoration: BoxDecoration(
            color: widget.isSelected
                ? _kNeonGold.withValues(alpha: 0.16)
                : _hovered
                    ? _kCard
                    : _kCard.withValues(alpha: 0.5),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: widget.isSelected
                  ? _kNeonGold.withValues(alpha: 0.9)
                  : _kBorder,
              width: widget.isSelected ? 1.5 : 1,
            ),
            boxShadow: widget.isSelected
                ? [
                    BoxShadow(
                        color: _kNeonGold.withValues(alpha: 0.25),
                        blurRadius: 6)
                  ]
                : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(widget.avatar, style: const TextStyle(fontSize: 13)),
              const SizedBox(width: 4),
              Text(
                widget.name,
                style: TextStyle(
                  color: widget.isSelected ? _kNeonGold : _kTextPrimary,
                  fontSize: 11,
                  fontWeight: widget.isSelected
                      ? FontWeight.w600
                      : FontWeight.normal,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ─── Center Table ─────────────────────────────────────────────────────────────
class _CenterTable extends StatelessWidget {
  final bool canStart;
  final Animation<double> pulseAnim;
  final VoidCallback onStart;
  final int selectedCount;

  const _CenterTable({
    required this.canStart,
    required this.pulseAnim,
    required this.onStart,
    required this.selectedCount,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final size = min(constraints.maxWidth, constraints.maxHeight);
      final tableR = (size * 0.38).clamp(100.0, 200.0);

      return SizedBox(
        width: tableR * 2.4,
        height: tableR * 2.4,
        child: Stack(
          alignment: Alignment.center,
          children: [
            // Outer pulse ring
            AnimatedBuilder(
              animation: pulseAnim,
              builder: (_, __) => Container(
                width: tableR * 2.2 * pulseAnim.value,
                height: tableR * 2.2 * pulseAnim.value,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: (canStart ? _kNeonCyan : _kBorder)
                        .withValues(alpha: 0.18 * pulseAnim.value),
                    width: 1,
                  ),
                ),
              ),
            ),
            // Mid ring
            AnimatedContainer(
              duration: const Duration(milliseconds: 400),
              width: tableR * 1.65,
              height: tableR * 1.65,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: (canStart ? _kNeonCyan : _kBorder)
                      .withValues(alpha: 0.1),
                  width: 1,
                ),
              ),
            ),
            // Table
            CustomPaint(
              size: Size(tableR * 2, tableR * 2),
              painter: RoundTablePainter(
                tableRadius: tableR,
                glowIntensity: canStart ? 0.8 : 0.35,
              ),
            ),
            // Start button
            _StartButton(
              canStart: canStart,
              pulseAnim: pulseAnim,
              onStart: onStart,
              selectedCount: selectedCount,
            ),
          ],
        ),
      );
    });
  }
}

class _StartButton extends StatefulWidget {
  final bool canStart;
  final Animation<double> pulseAnim;
  final VoidCallback onStart;
  final int selectedCount;

  const _StartButton({
    required this.canStart,
    required this.pulseAnim,
    required this.onStart,
    required this.selectedCount,
  });

  @override
  State<_StartButton> createState() => _StartButtonState();
}

class _StartButtonState extends State<_StartButton> {
  bool _hovered = false;
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final ready = widget.canStart;
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = _pressed = false),
      child: GestureDetector(
        onTapDown: ready ? (_) => setState(() => _pressed = true) : null,
        onTapUp: ready
            ? (_) {
                setState(() => _pressed = false);
                widget.onStart();
              }
            : null,
        onTapCancel: ready ? () => setState(() => _pressed = false) : null,
        child: AnimatedBuilder(
          animation: widget.pulseAnim,
          builder: (_, __) {
            final scale = _pressed
                ? 0.92
                : _hovered && ready
                    ? 1.07
                    : ready
                        ? 0.97 + 0.03 * widget.pulseAnim.value
                        : 1.0;
            return Transform.scale(
              scale: scale,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                width: 112,
                height: 112,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: ready
                      ? _kNeonCyan.withValues(
                          alpha: _hovered ? 0.22 : 0.10)
                      : _kCard.withValues(alpha: 0.5),
                  border: Border.all(
                    color: ready
                        ? _kNeonCyan.withValues(
                            alpha: _hovered ? 1.0 : 0.65)
                        : _kBorder,
                    width: ready ? 2 : 1,
                  ),
                  boxShadow: ready
                      ? [
                          BoxShadow(
                            color: _kNeonCyan.withValues(
                                alpha: _hovered ? 0.5 : 0.28),
                            blurRadius: _hovered ? 32 : 16,
                          ),
                        ]
                      : null,
                ),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      ready
                          ? Icons.event_seat_rounded
                          : Icons.chair_outlined,
                      color: ready ? _kNeonCyan : _kTextSecondary,
                      size: 26,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      ready ? '入座开始' : '选好话题\n与参与者',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: ready ? _kNeonCyan : _kTextSecondary,
                        fontSize: ready ? 14 : 10,
                        fontWeight: FontWeight.w700,
                        letterSpacing: ready ? 2 : 0.5,
                        height: 1.2,
                      ),
                    ),
                    if (ready && widget.selectedCount > 0) ...[
                      const SizedBox(height: 3),
                      Text(
                        '${widget.selectedCount}位参与',
                        style: const TextStyle(
                          color: _kNeonCyan,
                          fontSize: 9,
                          letterSpacing: 0.5,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

// ─── Character Bar ────────────────────────────────────────────────────────────
class _CharacterBar extends StatelessWidget {
  final List<CharacterTemplate> characters;
  final Set<String> selectedCharacterIds;
  final ValueChanged<String> onCharacterToggled;

  const _CharacterBar({
    required this.characters,
    required this.selectedCharacterIds,
    required this.onCharacterToggled,
  });

  @override
  Widget build(BuildContext context) {
    final moderator =
        characters.where((c) => c.id == 'moderator').firstOrNull;
    final selectableChars =
        characters.where((c) => c.id != 'moderator').toList();

    return Container(
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(top: BorderSide(color: _kBorder)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 10),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (moderator != null) ..._buildModeratorChip(moderator),
            if (moderator != null)
              Container(
                width: 1,
                height: 34,
                color: _kBorder,
                margin: const EdgeInsets.symmetric(horizontal: 16),
              ),
            ...selectableChars.map(
              (char) => Padding(
                padding: const EdgeInsets.only(right: 8),
                child: _CharChip(
                  avatar: char.avatar,
                  name: char.name,
                  isSelected: selectedCharacterIds.contains(char.id),
                  onTap: () => onCharacterToggled(char.id),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildModeratorChip(CharacterTemplate mod) => [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
          decoration: BoxDecoration(
            color: _kNeonGold.withValues(alpha: 0.12),
            border: Border.all(
                color: _kNeonGold.withValues(alpha: 0.65), width: 1.5),
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.school_rounded, color: _kNeonGold, size: 14),
              const SizedBox(width: 6),
              Text(mod.avatar, style: const TextStyle(fontSize: 15)),
              const SizedBox(width: 4),
              Text(
                mod.name,
                style: const TextStyle(
                    color: _kNeonGold,
                    fontSize: 13,
                    fontWeight: FontWeight.w600),
              ),
              const SizedBox(width: 5),
              Text(
                '主持',
                style: TextStyle(
                    color: _kNeonGold.withValues(alpha: 0.55), fontSize: 11),
              ),
            ],
          ),
        ),
      ];
}

// ─── Loading View ─────────────────────────────────────────────────────────────
class _LoadingView extends StatelessWidget {
  const _LoadingView();

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const SizedBox(
          width: 36,
          height: 36,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            valueColor: AlwaysStoppedAnimation<Color>(_kNeonCyan),
          ),
        ),
        const SizedBox(height: 16),
        const Text(
          '加载中...',
          style: TextStyle(
              color: _kTextSecondary, fontSize: 14, letterSpacing: 2),
        ),
      ],
    );
  }
}

// ─── Ambient Orb Painter ──────────────────────────────────────────────────────
class _AmbientOrbPainter extends CustomPainter {
  final double t;
  _AmbientOrbPainter(this.t);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..style = PaintingStyle.fill;

    void orb(double cx, double cy, double r, Color color, double alpha) {
      paint.shader = RadialGradient(
        colors: [color.withValues(alpha: alpha), Colors.transparent],
      ).createShader(Rect.fromCircle(
          center: Offset(cx * size.width, cy * size.height),
          radius: r * size.width));
      canvas.drawCircle(Offset(cx * size.width, cy * size.height),
          r * size.width, paint);
    }

    orb(
      0.1 + 0.04 * sin(t * 2 * pi),
      0.2 + 0.03 * cos(t * 2 * pi),
      0.28,
      const Color(0xFF00E5FF),
      0.10,
    );
    orb(
      0.88 + 0.03 * cos(t * 2 * pi + 1),
      0.75 + 0.04 * sin(t * 2 * pi + 1),
      0.24,
      const Color(0xFFFFCC44),
      0.09,
    );
    orb(
      0.5,
      0.5 + 0.02 * sin(t * 2 * pi + 2),
      0.20,
      const Color(0xFFAA88FF),
      0.06,
    );
  }

  @override
  bool shouldRepaint(_AmbientOrbPainter old) => old.t != t;
}