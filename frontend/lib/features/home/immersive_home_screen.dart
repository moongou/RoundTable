import 'dart:math';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/discussion_models.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../services/speech_service.dart';
import '../../state/settings_provider.dart';
import '../../utils/open_external_url_stub.dart'
    if (dart.library.html) '../../utils/open_external_url_web.dart';
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
      TextEditingController(text: '豆苗');
  final TextEditingController _freeTopicController = TextEditingController();
  bool _isFreeTopicMode = false;

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
    _pulseAnim = Tween<double>(begin: 0.85, end: 1.0)
        .animate(CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut));
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
    _freeTopicController.dispose();
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
        _characters =
            charsData.map((c) => CharacterTemplate.fromJson(c)).toList();
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
    openExternalUrl('http://$host:8888');
  }

  bool get _canStart =>
      (_selectedTopic != null ||
          (_isFreeTopicMode && _freeTopicController.text.trim().isNotEmpty)) &&
      (_selectedCharacterIds.isNotEmpty || _selectedThinkerIds.isNotEmpty);

  List<Topic> get _filteredTopics {
    if (_selectedCategory == null) return _topics;
    return _topics.where((t) => t.category == _selectedCategory).toList();
  }

  Future<void> _startDiscussion() async {
    // 确定话题：预设 or 自由话题
    final Topic effectiveTopic;
    if (_isFreeTopicMode) {
      final freeText = _freeTopicController.text.trim();
      if (freeText.isEmpty) return;
      effectiveTopic = Topic(
        id: 'free_topic',
        title: freeText,
        description: '由用户发起的自由讨论话题：$freeText',
        category: 'free',
      );
    } else {
      if (_selectedTopic == null) return;
      effectiveTopic = _selectedTopic!;
    }
    final apiClient = ref.read(apiClientProvider);
    try {
      final validation = await apiClient.validateConfig();
      if (!mounted) return;
      if (!validation.valid) {
        _showPreflightFailedDialog(
          title: '配置未通过会前检测',
          message: validation.message,
          details: validation.checks
              .where((c) => !c.ok)
              .map((c) => '${c.name}: ${c.detail}')
              .toList(),
        );
        return;
      }
    } catch (e) {
      if (!mounted) return;
      _showPreflightFailedDialog(
        title: '会前检测失败',
        message: '无法验证后端配置，请检查服务状态后重试。',
        details: ['错误: $e'],
      );
      return;
    }

    final local = ref.read(localSettingsProvider).valueOrNull;
    final asrProvider = local?.asrProvider ?? 'browser';
    final serverUrl = local?.serverUrl ?? 'http://localhost:8001';
    if (asrProvider == 'browser') {
      final asr = createAsrService('browser', serverUrl: serverUrl);
      try {
        if (!asr.isAvailable) {
          if (!mounted) return;
          _showPreflightFailedDialog(
            title: '浏览器语音不可用',
            message: '当前浏览器不支持语音识别（Web Speech API）。',
            details: const ['请切换 ASR 到 funasr/openai_whisper，或更换支持语音识别的浏览器。'],
          );
          return;
        }
      } finally {
        asr.dispose();
      }
    }

    Navigator.push(
      context,
      PageRouteBuilder(
        pageBuilder: (_, a1, a2) => ImmersiveSessionScreen(
          topic: effectiveTopic,
          characterIds: _selectedCharacterIds.toList(),
          thinkerIds: _selectedThinkerIds.toList(),
          humanName: _nameController.text.isEmpty ? '豆苗' : _nameController.text,
        ),
        transitionsBuilder: (_, a1, a2, child) => FadeTransition(
          opacity: CurvedAnimation(parent: a1, curve: Curves.easeIn),
          child: child,
        ),
        transitionDuration: const Duration(milliseconds: 500),
      ),
    );
  }

  void _showPreflightFailedDialog({
    required String title,
    required String message,
    required List<String> details,
  }) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: _kSurface,
        title: Text(title, style: const TextStyle(color: _kTextPrimary)),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(message, style: const TextStyle(color: _kTextSecondary)),
              const SizedBox(height: 10),
              ...details.map(
                (item) => Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text('• $item',
                      style: const TextStyle(color: _kTextPrimary, fontSize: 12)),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('知道了', style: TextStyle(color: _kNeonGold)),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(ctx).pop();
              Navigator.pushNamed(context, '/settings');
            },
            child: const Text('去设置', style: TextStyle(color: _kNeonCyan)),
          ),
        ],
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
                  isFreeTopicMode: _isFreeTopicMode,
                  freeTopicController: _freeTopicController,
                  onCategoryChanged: (v) =>
                      setState(() => _selectedCategory = v),
                  onTopicSelected: (t) => setState(() {
                    _selectedTopic = t;
                    _isFreeTopicMode = false;
                  }),
                  onFreeTopicModeToggled: () => setState(() {
                    _isFreeTopicMode = !_isFreeTopicMode;
                    if (_isFreeTopicMode) _selectedTopic = null;
                  }),
                  onFreeTopicChanged: () => setState(() {}),
                  onCharacterToggled: (id) => setState(() {
                    if (_selectedCharacterIds.contains(id)) {
                      _selectedCharacterIds.remove(id);
                    } else {
                      _selectedCharacterIds.add(id);
                    }
                  }),
                  onThinkerToggled: (id) => setState(() {
                    if (_selectedThinkerIds.contains(id)) {
                      _selectedThinkerIds.remove(id);
                    } else {
                      _selectedThinkerIds.add(id);
                    }
                  }),
                  onStart: () {
                    _startDiscussion();
                  },
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
                  isFreeTopicMode: _isFreeTopicMode,
                  freeTopicController: _freeTopicController,
                  onCategoryChanged: (v) =>
                      setState(() => _selectedCategory = v),
                  onTopicSelected: (t) => setState(() {
                    _selectedTopic = t;
                    _isFreeTopicMode = false;
                  }),
                  onFreeTopicModeToggled: () => setState(() {
                    _isFreeTopicMode = !_isFreeTopicMode;
                    if (_isFreeTopicMode) _selectedTopic = null;
                  }),
                  onFreeTopicChanged: () => setState(() {}),
                  onCharacterToggled: (id) => setState(() {
                    if (_selectedCharacterIds.contains(id)) {
                      _selectedCharacterIds.remove(id);
                    } else {
                      _selectedCharacterIds.add(id);
                    }
                  }),
                  onThinkerToggled: (id) => setState(() {
                    if (_selectedThinkerIds.contains(id)) {
                      _selectedThinkerIds.remove(id);
                    } else {
                      _selectedThinkerIds.add(id);
                    }
                  }),
                  onStart: () {
                    _startDiscussion();
                  },
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
      padding: const EdgeInsets.symmetric(horizontal: 20),
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(bottom: BorderSide(color: _kBorder, width: 1)),
      ),
      child: Row(
        children: [
          // ── Logo ──
          const _RoundTableLogo(size: 36),
          const SizedBox(width: 10),
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
              icon: Icons.settings_outlined, tooltip: '设置', onTap: onSettings),
        ],
      ),
    );
  }
}

/// 圆桌思辨 LOGO — 圆桌 + 发光圆环
class _RoundTableLogo extends StatelessWidget {
  final double size;
  const _RoundTableLogo({required this.size});

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      size: Size(size, size),
      painter: _LogoPainter(),
    );
  }
}

class _LogoPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final cx = size.width / 2;
    final cy = size.height / 2;
    final r = size.width * 0.42;

    // Outer glow ring
    final glowPaint = Paint()
      ..color = const Color(0xFF00E5FF).withValues(alpha: 0.25)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4);
    canvas.drawCircle(Offset(cx, cy), r + 2, glowPaint);

    // Table circle fill
    final tableFill = Paint()
      ..color = const Color(0xFF1A1830)
      ..style = PaintingStyle.fill;
    canvas.drawCircle(Offset(cx, cy), r, tableFill);

    // Table border
    final tableBorder = Paint()
      ..color = const Color(0xFFFFCC44)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    canvas.drawCircle(Offset(cx, cy), r, tableBorder);

    // Inner ring
    final innerRing = Paint()
      ..color = const Color(0xFFFFCC44).withValues(alpha: 0.3)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.8;
    canvas.drawCircle(Offset(cx, cy), r * 0.7, innerRing);

    // 5 seats around the table
    const seatCount = 5;
    for (int i = 0; i < seatCount; i++) {
      final angle = (i / seatCount) * 2 * 3.14159 - 3.14159 / 2;
      final sx = cx + (r + 4) * cos(angle);
      final sy = cy + (r + 4) * sin(angle);
      final seatPaint = Paint()
        ..color = (i == 0)
            ? const Color(0xFFFFCC44)
            : const Color(0xFF00E5FF).withValues(alpha: 0.8)
        ..style = PaintingStyle.fill;
      canvas.drawCircle(Offset(sx, sy), 2.8, seatPaint);
    }

    // Center dot
    canvas.drawCircle(
      Offset(cx, cy),
      3,
      Paint()..color = const Color(0xFFFFCC44).withValues(alpha: 0.6),
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
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
class _WideLayout extends StatefulWidget {
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
  final bool isFreeTopicMode;
  final TextEditingController freeTopicController;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;
  final VoidCallback onFreeTopicModeToggled;
  final VoidCallback onFreeTopicChanged;
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
    required this.isFreeTopicMode,
    required this.freeTopicController,
    required this.onCategoryChanged,
    required this.onTopicSelected,
    required this.onFreeTopicModeToggled,
    required this.onFreeTopicChanged,
    required this.onCharacterToggled,
    required this.onThinkerToggled,
    required this.onStart,
  });

  @override
  State<_WideLayout> createState() => _WideLayoutState();
}

class _WideLayoutState extends State<_WideLayout> {
  // Resizable panel widths
  double _leftPanelWidth = 340; // category+topic combined (c: +38px ≈1cm)
  double _rightPanelWidth = 390; // thinkers (e: 300*1.3=390)

  // Thinker domain filter (e)
  String? _selectedThinkerDomain;

  static const double _minLeft = 180;
  static const double _maxLeft = 520;
  static const double _minRight = 200;
  static const double _maxRight = 500;

  @override
  Widget build(BuildContext context) {
    final moderator =
        widget.characters.where((c) => c.id == 'moderator').firstOrNull;
    final selectableChars =
        widget.characters.where((c) => c.id != 'moderator').toList();
    final totalWidth = MediaQuery.of(context).size.width;

    // Ensure right panel doesn't exceed available space
    final maxRight =
        (totalWidth - _leftPanelWidth - 320).clamp(_minRight, _maxRight);
    final effectiveRight = _rightPanelWidth.clamp(_minRight, maxRight);

    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Left panel (resizable) ─────────────────────────────────────────
        SizedBox(
          width: _leftPanelWidth,
          child: Column(
            children: [
              // 自由话题切换按钮
              _FreeTopicToggle(
                isActive: widget.isFreeTopicMode,
                onToggle: widget.onFreeTopicModeToggled,
                controller: widget.freeTopicController,
                onChanged: widget.onFreeTopicChanged,
              ),
              // 预设话题列表（非自由话题模式时显示）
              if (!widget.isFreeTopicMode)
                Expanded(
                  child: _LeftPanel(
                    topics: widget.topics,
                    categories: widget.categories,
                    selectedCategory: widget.selectedCategory,
                    selectedTopicId: widget.selectedTopicId,
                    onCategoryChanged: widget.onCategoryChanged,
                    onTopicSelected: widget.onTopicSelected,
                  ),
                ),
              if (widget.isFreeTopicMode) const Expanded(child: SizedBox()),
            ],
          ),
        ),

        // ── Left drag handle ───────────────────────────────────────────────
        _ResizeHandle(
          axis: Axis.vertical,
          onDrag: (dx) {
            setState(() {
              _leftPanelWidth =
                  (_leftPanelWidth + dx).clamp(_minLeft, _maxLeft);
            });
          },
        ),

        // ── Center column ──────────────────────────────────────────────────
        Expanded(
          child: Column(
            children: [
              Expanded(
                child: Center(
                  child: moderator != null
                      ? _ModeratorBadge(mod: moderator)
                      : const SizedBox.shrink(),
                ),
              ),
              _CenterTable(
                canStart: widget.canStart,
                pulseAnim: widget.pulseAnim,
                onStart: widget.onStart,
                selectedCount: widget.selectedCharacterIds.length +
                    widget.selectedThinkerIds.length,
              ),
              Expanded(
                child: Center(
                  child: _InlineCharacterRow(
                    characters: selectableChars,
                    selectedCharacterIds: widget.selectedCharacterIds,
                    onCharacterToggled: widget.onCharacterToggled,
                  ),
                ),
              ),
            ],
          ),
        ),

        // ── Right drag handle ──────────────────────────────────────────────
        _ResizeHandle(
          axis: Axis.vertical,
          onDrag: (dx) {
            setState(() {
              _rightPanelWidth =
                  (_rightPanelWidth - dx).clamp(_minRight, maxRight);
            });
          },
        ),

        // ── Right panel (resizable) ────────────────────────────────────────
        SizedBox(
          width: effectiveRight,
          child: _RightPanel(
            thinkers: widget.thinkers,
            selectedThinkerIds: widget.selectedThinkerIds,
            onThinkerToggled: widget.onThinkerToggled,
            selectedDomain: _selectedThinkerDomain,
            onDomainChanged: (d) => setState(() => _selectedThinkerDomain = d),
          ),
        ),
      ],
    );
  }
}

/// Drag handle widget for resizing panels
class _ResizeHandle extends StatefulWidget {
  final Axis axis;
  final ValueChanged<double> onDrag;

  const _ResizeHandle({required this.axis, required this.onDrag});

  @override
  State<_ResizeHandle> createState() => _ResizeHandleState();
}

class _ResizeHandleState extends State<_ResizeHandle> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.resizeColumn,
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onHorizontalDragUpdate: (d) => widget.onDrag(d.delta.dx),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          width: 6,
          color: _hovered
              ? _kNeonCyan.withValues(alpha: 0.35)
              : _kBorder.withValues(alpha: 0.6),
          child: Center(
            child: Container(
              width: 2,
              height: 32,
              decoration: BoxDecoration(
                color: _hovered
                    ? _kNeonCyan
                    : _kTextSecondary.withValues(alpha: 0.4),
                borderRadius: BorderRadius.circular(1),
              ),
            ),
          ),
        ),
      ),
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
  final bool isFreeTopicMode;
  final TextEditingController freeTopicController;
  final ValueChanged<String?> onCategoryChanged;
  final ValueChanged<Topic> onTopicSelected;
  final VoidCallback onFreeTopicModeToggled;
  final VoidCallback onFreeTopicChanged;
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
    required this.isFreeTopicMode,
    required this.freeTopicController,
    required this.onCategoryChanged,
    required this.onTopicSelected,
    required this.onFreeTopicModeToggled,
    required this.onFreeTopicChanged,
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
          // 自由话题切换
          _FreeTopicToggle(
            isActive: isFreeTopicMode,
            onToggle: onFreeTopicModeToggled,
            controller: freeTopicController,
            onChanged: onFreeTopicChanged,
          ),
          const SizedBox(height: 14),
          if (!isFreeTopicMode)
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
          if (!isFreeTopicMode) const SizedBox(height: 14),
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

// ─── Left Panel (2 sub-columns: categories | topics) ─────────────────────────
class _LeftPanel extends StatefulWidget {
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
  State<_LeftPanel> createState() => _LeftPanelState();
}

class _LeftPanelState extends State<_LeftPanel> {
  double _catColWidth = 90;
  static const double _minCat = 72;
  static const double _maxCat = 180;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: double.infinity,
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(right: BorderSide(color: _kBorder)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Category column ───────────────────────────────────────────────
          SizedBox(
            width: _catColWidth,
            child: Container(
              decoration: const BoxDecoration(
                border: Border(right: BorderSide(color: _kBorder, width: 0.5)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Category header — larger font to signal first-level hierarchy
                  Padding(
                    padding: const EdgeInsets.fromLTRB(24, 16, 10, 10),
                    child: Row(
                      children: [
                        Container(
                          width: 2,
                          height: 14,
                          decoration: BoxDecoration(
                            color: _kNeonCyan,
                            borderRadius: BorderRadius.circular(1),
                          ),
                        ),
                        const SizedBox(width: 7),
                        const Text('分类',
                            style: TextStyle(
                              color: _kNeonCyan,
                              fontSize: 13, // larger for first level
                              fontWeight: FontWeight.w800,
                              letterSpacing: 2,
                            )),
                      ],
                    ),
                  ),
                  Expanded(
                    child: Center(
                      child: SingleChildScrollView(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            _CategoryItem(
                              label: '全部',
                              selected: widget.selectedCategory == null,
                              onTap: () => widget.onCategoryChanged(null),
                            ),
                            ...widget.categories.map((cat) {
                              final id = cat['id'] as String? ?? '';
                              final name = cat['name'] as String? ?? id;
                              return _CategoryItem(
                                label: name,
                                selected: widget.selectedCategory == id,
                                onTap: () => widget.onCategoryChanged(id),
                              );
                            }),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),

          // ── Inner drag handle ─────────────────────────────────────────────
          _ResizeHandle(
            axis: Axis.vertical,
            onDrag: (dx) {
              setState(() {
                _catColWidth = (_catColWidth + dx).clamp(_minCat, _maxCat);
              });
            },
          ),

          // ── Topics column ─────────────────────────────────────────────────
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Topic header — slightly smaller, italic to differentiate second level
                Padding(
                  padding: const EdgeInsets.fromLTRB(14, 16, 14, 10),
                  child: Row(
                    children: [
                      Container(
                        width: 2,
                        height: 12,
                        decoration: BoxDecoration(
                          color: _kNeonCyan.withValues(alpha: 0.6),
                          borderRadius: BorderRadius.circular(1),
                        ),
                      ),
                      const SizedBox(width: 7),
                      const Text('话题',
                          style: TextStyle(
                            color: _kTextSecondary,
                            fontSize: 11, // smaller for second level
                            fontWeight: FontWeight.w600,
                            letterSpacing: 1.8,
                            fontStyle: FontStyle.italic,
                          )),
                    ],
                  ),
                ),
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(10, 0, 10, 16),
                    child: Column(
                      children: widget.topics
                          .map((topic) => _TopicRow(
                                topic: topic,
                                isSelected: widget.selectedTopicId == topic.id,
                                onTap: () => widget.onTopicSelected(topic),
                              ))
                          .toList(),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 自由话题切换按钮与输入框
class _FreeTopicToggle extends StatelessWidget {
  final bool isActive;
  final VoidCallback onToggle;
  final TextEditingController controller;
  final VoidCallback onChanged;

  const _FreeTopicToggle({
    required this.isActive,
    required this.onToggle,
    required this.controller,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(bottom: BorderSide(color: _kBorder, width: 0.5)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          InkWell(
            onTap: onToggle,
            borderRadius: BorderRadius.circular(8),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: isActive
                    ? _kNeonGold.withValues(alpha: 0.15)
                    : _kCard,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: isActive
                      ? _kNeonGold.withValues(alpha: 0.6)
                      : _kBorder,
                ),
              ),
              child: Row(
                children: [
                  Icon(
                    isActive ? Icons.edit_note : Icons.lightbulb_outline,
                    color: isActive ? _kNeonGold : _kTextSecondary,
                    size: 18,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '自由话题',
                    style: TextStyle(
                      color: isActive ? _kNeonGold : _kTextSecondary,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const Spacer(),
                  Icon(
                    isActive ? Icons.toggle_on : Icons.toggle_off_outlined,
                    color: isActive ? _kNeonGold : _kTextSecondary,
                    size: 22,
                  ),
                ],
              ),
            ),
          ),
          if (isActive) ...[
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              onChanged: (_) => onChanged(),
              style: const TextStyle(color: _kTextPrimary, fontSize: 14),
              maxLines: 3,
              minLines: 1,
              decoration: InputDecoration(
                hintText: '输入你想讨论的话题...',
                hintStyle: TextStyle(
                    color: _kTextSecondary.withValues(alpha: 0.6),
                    fontSize: 13),
                filled: true,
                fillColor: _kCard,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: _kBorder),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: _kBorder),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: BorderSide(color: _kNeonGold),
                ),
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Vertical category item (for the left sub-column)
class _CategoryItem extends StatefulWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _CategoryItem(
      {required this.label, required this.selected, required this.onTap});

  @override
  State<_CategoryItem> createState() => _CategoryItemState();
}

/// Maps category IDs / names to emoji icons
String _categoryIcon(String label) {
  final map = {
    '全部': '🌐',
    '教育': '📚',
    'education': '📚',
    '科技': '🔬',
    '技术': '🔬',
    'technology': '🔬',
    'tech': '🔬',
    '伦理': '⚖️',
    'ethics': '⚖️',
    '健康': '🏥',
    'health': '🏥',
    '社会': '🏘️',
    'society': '🏘️',
    'social': '🏘️',
    '文学': '📖',
    'literature': '📖',
    '科学': '🔭',
    'science': '🔭',
    '生活': '🌱',
    'life': '🌱',
    '哲学': '💭',
    'philosophy': '💭',
    '经济': '💹',
    'economics': '💹',
    '历史': '🏛️',
    'history': '🏛️',
    '政治': '🗳️',
    'politics': '🗳️',
    '艺术': '🎨',
    'art': '🎨',
    '心理': '🧠',
    'psychology': '🧠',
    '宗教': '🙏',
    'religion': '🙏',
    '战略': '♟️',
    'strategy': '♟️',
    '商业': '💼',
    'business': '💼',
    '金融': '💰',
    'finance': '💰',
    '社会学': '👥',
    'sociology': '👥',
  };
  return map[label] ?? '●';
}

class _CategoryItemState extends State<_CategoryItem> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final icon = _categoryIcon(widget.label);
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 140),
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(24, 10, 10, 10),
          decoration: BoxDecoration(
            color: widget.selected
                ? _kNeonCyan.withValues(alpha: 0.15)
                : _hovered
                    ? _kCard
                    : Colors.transparent,
            border: Border(
              left: BorderSide(
                color: widget.selected ? _kNeonCyan : Colors.transparent,
                width: 2,
              ),
            ),
          ),
          child: Row(
            children: [
              Text(icon, style: const TextStyle(fontSize: 13)),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  widget.label,
                  style: TextStyle(
                    color: widget.selected ? _kNeonCyan : _kTextSecondary,
                    fontSize: 12,
                    fontWeight:
                        widget.selected ? FontWeight.w600 : FontWeight.normal,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
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
  final String? selectedDomain;
  final ValueChanged<String?> onDomainChanged;

  const _RightPanel({
    required this.thinkers,
    required this.selectedThinkerIds,
    required this.onThinkerToggled,
    required this.selectedDomain,
    required this.onDomainChanged,
  });

  /// Collect unique domains from thinker data
  List<Map<String, dynamic>> _getDomains() {
    final seen = <String>{};
    final domains = <Map<String, dynamic>>[];
    for (final t in thinkers) {
      final d = t['domain'] as String?;
      final dcn = t['domain_cn'] as String?;
      if (d != null && d.isNotEmpty && !seen.contains(d)) {
        seen.add(d);
        domains.add({'id': d, 'name': dcn ?? d});
      }
    }
    return domains;
  }

  List<Map<String, dynamic>> get _filteredThinkers {
    if (selectedDomain == null) return thinkers;
    return thinkers.where((t) => t['domain'] == selectedDomain).toList();
  }

  @override
  Widget build(BuildContext context) {
    final domains = _getDomains();
    return Container(
      height: double.infinity,
      decoration: const BoxDecoration(
        color: _kSurface,
        border: Border(left: BorderSide(color: _kBorder)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
            child: Row(
              children: [
                Container(
                  width: 3,
                  height: 16,
                  decoration: BoxDecoration(
                    color: _kNeonGold,
                    borderRadius: BorderRadius.circular(2),
                    boxShadow: [
                      BoxShadow(
                          color: _kNeonGold.withValues(alpha: 0.5),
                          blurRadius: 6)
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                const Text('思想家',
                    style: TextStyle(
                      color: _kNeonGold,
                      fontSize: 13,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 2,
                    )),
                const Spacer(),
                if (selectedThinkerIds.isNotEmpty)
                  Text('已选 ${selectedThinkerIds.length}',
                      style: TextStyle(
                          color: _kNeonGold.withValues(alpha: 0.7),
                          fontSize: 11)),
              ],
            ),
          ),
          const SizedBox(height: 10),
          // Domain filter tags
          if (domains.isNotEmpty) ...[
            SizedBox(
              height: 32,
              child: ScrollConfiguration(
                behavior: ScrollConfiguration.of(context).copyWith(
                  dragDevices: {
                    PointerDeviceKind.touch,
                    PointerDeviceKind.mouse,
                    PointerDeviceKind.trackpad
                  },
                ),
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    _DomainTag(
                        label: '全部',
                        selected: selectedDomain == null,
                        onTap: () => onDomainChanged(null)),
                    ...domains.map((d) => _DomainTag(
                          label: d['name'] as String,
                          selected: selectedDomain == d['id'],
                          onTap: () => onDomainChanged(selectedDomain == d['id']
                              ? null
                              : d['id'] as String),
                        )),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            const Divider(color: _kBorder, height: 1),
            const SizedBox(height: 8),
          ],
          // Thinker list
          Expanded(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 16),
              child: _ThinkerGrid(
                thinkers: _filteredThinkers,
                selectedIds: selectedThinkerIds,
                onToggled: onThinkerToggled,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _DomainTag extends StatefulWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;
  const _DomainTag(
      {required this.label, required this.selected, required this.onTap});
  @override
  State<_DomainTag> createState() => _DomainTagState();
}

class _DomainTagState extends State<_DomainTag> {
  bool _hovered = false;
  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: MouseRegion(
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTap: widget.onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 160),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: widget.selected
                  ? _kNeonGold.withValues(alpha: 0.18)
                  : _hovered
                      ? _kCard
                      : _kCard.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: widget.selected
                    ? _kNeonGold.withValues(alpha: 0.9)
                    : _kBorder,
                width: widget.selected ? 1.5 : 1,
              ),
              boxShadow: widget.selected
                  ? [
                      BoxShadow(
                          color: _kNeonGold.withValues(alpha: 0.2),
                          blurRadius: 6)
                    ]
                  : null,
            ),
            child: Text(widget.label,
                style: TextStyle(
                  color: widget.selected ? _kNeonGold : _kTextSecondary,
                  fontSize: 11,
                  fontWeight:
                      widget.selected ? FontWeight.w600 : FontWeight.normal,
                )),
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
        GridView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 2,
            mainAxisSpacing: 4,
            crossAxisSpacing: 4,
            mainAxisExtent: 44,
          ),
          itemCount: topics.length.clamp(0, 28),
          itemBuilder: (_, i) => _TopicRow(
            topic: topics[i],
            isSelected: selectedTopicId == topics[i].id,
            onTap: () => onTopicSelected(topics[i]),
          ),
        ),
        if (topics.length > 28)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text('+${topics.length - 28} 个话题',
                style: const TextStyle(color: _kTextSecondary, fontSize: 11)),
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
              color: selected ? _kNeonCyan.withValues(alpha: 0.8) : _kBorder,
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
                    color: widget.isSelected ? _kNeonCyan : _kTextPrimary,
                    fontSize: 13,
                    fontWeight:
                        widget.isSelected ? FontWeight.w600 : FontWeight.normal,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (widget.isSelected)
                const Icon(Icons.check_circle, color: _kNeonCyan, size: 14),
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
                  fontWeight:
                      widget.isSelected ? FontWeight.w600 : FontWeight.normal,
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
    return LayoutBuilder(builder: (context, constraints) {
      final chipW = (constraints.maxWidth - 8) / 2;
      return Wrap(
        spacing: 8,
        runSpacing: 8,
        children: thinkers
            .map((t) => SizedBox(
                  width: chipW,
                  child: _ThinkerChip(
                    avatar: t['avatar'] as String? ?? '🧠',
                    name: t['name'] as String? ?? (t['id'] as String? ?? ''),
                    isSelected: selectedIds.contains(t['id'] as String? ?? ''),
                    onTap: () => onToggled(t['id'] as String? ?? ''),
                  ),
                ))
            .toList(),
      );
    });
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
          width: double.infinity,
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
                  fontWeight:
                      widget.isSelected ? FontWeight.w600 : FontWeight.normal,
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
                  color:
                      (canStart ? _kNeonCyan : _kBorder).withValues(alpha: 0.1),
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
                      ? _kNeonCyan.withValues(alpha: _hovered ? 0.22 : 0.10)
                      : _kCard.withValues(alpha: 0.5),
                  border: Border.all(
                    color: ready
                        ? _kNeonCyan.withValues(alpha: _hovered ? 1.0 : 0.65)
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
                      ready ? Icons.event_seat_rounded : Icons.chair_outlined,
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

// ─── Moderator Badge (above table) ───────────────────────────────────────────
class _ModeratorBadge extends StatelessWidget {
  final CharacterTemplate mod;
  const _ModeratorBadge({required this.mod});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 9),
      decoration: BoxDecoration(
        color: _kNeonGold.withValues(alpha: 0.12),
        border:
            Border.all(color: _kNeonGold.withValues(alpha: 0.65), width: 1.5),
        borderRadius: BorderRadius.circular(24),
        boxShadow: [
          BoxShadow(color: _kNeonGold.withValues(alpha: 0.20), blurRadius: 18),
        ],
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.school_rounded, color: _kNeonGold, size: 15),
          const SizedBox(width: 7),
          Text(mod.avatar, style: const TextStyle(fontSize: 17)),
          const SizedBox(width: 5),
          Text(
            mod.name,
            style: const TextStyle(
                color: _kNeonGold, fontSize: 14, fontWeight: FontWeight.w600),
          ),
          const SizedBox(width: 7),
          Text(
            '主持',
            style: TextStyle(
                color: _kNeonGold.withValues(alpha: 0.55), fontSize: 11),
          ),
        ],
      ),
    );
  }
}

// ─── Inline Character Row (below table) ──────────────────────────────────────
class _InlineCharacterRow extends StatelessWidget {
  final List<CharacterTemplate> characters;
  final Set<String> selectedCharacterIds;
  final ValueChanged<String> onCharacterToggled;

  const _InlineCharacterRow({
    required this.characters,
    required this.selectedCharacterIds,
    required this.onCharacterToggled,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: characters
            .map((char) => Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: _CharChip(
                    avatar: char.avatar,
                    name: char.name,
                    isSelected: selectedCharacterIds.contains(char.id),
                    onTap: () => onCharacterToggled(char.id),
                  ),
                ))
            .toList(),
      ),
    );
  }
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
          style:
              TextStyle(color: _kTextSecondary, fontSize: 14, letterSpacing: 2),
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
      canvas.drawCircle(
          Offset(cx * size.width, cy * size.height), r * size.width, paint);
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
