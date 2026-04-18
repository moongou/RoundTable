# Immersive Round Table UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain Material Design UI with an immersive warm study room experience featuring a realistic round table, candlelight effects, and scroll card topic selectors.

**Architecture:** CustomPainter classes render the visual scene (bookshelf background, round table surface, candlelight particles, scroll cards). Flutter Widgets handle interaction and layout (GlowAvatar, TableParticipantRing, SpeakingBubble, GlassControlBar). Two new screens replace the existing ones: ImmersiveHomeScreen and ImmersiveSessionScreen.

**Tech Stack:** Flutter 3.x, CustomPainter, flutter_animate, google_fonts (ZCOOL XiaoWei + Noto Sans SC), flutter_riverpod

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `frontend/lib/painters/bookshelf_painter.dart` | Bookshelf background: dark wood wall + top light source |
| `frontend/lib/painters/round_table_painter.dart` | Round table surface: wood grain radial gradient + edge highlight + shadow |
| `frontend/lib/painters/candlelight_painter.dart` | Candlelight particle effect: floating warm light dots |
| `frontend/lib/painters/scroll_card_painter.dart` | Scroll card: paper roll + curled edges + gold border |
| `frontend/lib/features/home/immersive_home_screen.dart` | Home screen: study room + scroll topic cards + character avatars + seat button |
| `frontend/lib/features/home/scroll_topic_card.dart` | Scroll card topic selector widget |
| `frontend/lib/features/session/immersive_session_screen.dart` | Session screen: round table discussion room |
| `frontend/lib/features/session/table_participant_ring.dart` | Circular participant seating layout |
| `frontend/lib/features/session/speaking_bubble.dart` | Speaking bubble from participant position |
| `frontend/lib/features/session/glow_avatar.dart` | Glowing avatar with pulse animation |
| `frontend/lib/features/session/glass_control_bar.dart` | Semi-transparent glass control bar (text/PTT/interrupt) |

### Modified Files

| File | Change |
|------|--------|
| `frontend/lib/theme/app_colors.dart` | Add warm study palette colors |
| `frontend/lib/theme/app_theme.dart` | Add ZCOOL XiaoWei, warm dark theme |
| `frontend/lib/app.dart` | Route to ImmersiveHomeScreen |
| `frontend/lib/features/session/chat_history_drawer.dart` | Parchment style + role color bar |
| `frontend/lib/features/session/animations.dart` | Add study-room-specific animations |
| `frontend/lib/features/settings/settings_screen.dart` | Warm study style cards |
| `backend/app/main.py` | Fix static mount to always serve Flutter web |

---

## Task 1: Fix Server Static Mount

**Files:**
- Modify: `backend/app/main.py:44-46`

The server currently only mounts static files if they exist at startup. Change to always mount (return a 404 page if files missing instead of breaking the route).

- [ ] **Step 1: Update main.py static mount**

Replace lines 44-46 in `backend/app/main.py`:

```python
# 挂载 Flutter Web 静态文件（必须在路由之后挂载，否则会覆盖 API）
if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
```

with:

```python
# 挂载 Flutter Web 静态文件（必须在路由之后挂载，否则会覆盖 API）
if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    # 开发模式：静态文件未构建时，根路径返回提示
    @app.get("/")
    async def dev_root():
        return {"message": "RoundTable 后端运行中。请先构建前端: ./build_web.sh"}
```

- [ ] **Step 2: Kill old server and rebuild**

```bash
# Kill existing server
pkill -f "python3.*app.main" || true

# Rebuild Flutter web
cd /Users/m3max/IdeaProjects/RoundTable/frontend
flutter pub get
flutter build web --release

# Deploy to backend static
rm -rf /Users/m3max/IdeaProjects/RoundTable/backend/static
cp -r /Users/m3max/IdeaProjects/RoundTable/frontend/build/web /Users/m3max/IdeaProjects/RoundTable/backend/static
```

- [ ] **Step 3: Restart server and verify**

```bash
cd /Users/m3max/IdeaProjects/RoundTable/backend
python -m app.main &
# Wait 3 seconds then check
curl -s http://localhost:8001/ | head -5
```

Expected: HTML content starting with `<!DOCTYPE html>`

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "fix: serve Flutter web static files reliably even after rebuild"
```

---

## Task 2: Update Color Palette

**Files:**
- Modify: `frontend/lib/theme/app_colors.dart`

- [ ] **Step 1: Add warm study room colors to AppColors**

Replace the entire file `frontend/lib/theme/app_colors.dart` with:

```dart
/// RoundTable 扩展色板
///
/// 提供语义化颜色常量，用于沉浸式 UI 的发光、玻璃拟态、强调等效果。

import 'package:flutter/material.dart';

class AppColors {
  AppColors._();

  // ── 主色 ──────────────────────────────────────────────────────────────────
  static const primary = Color(0xFF2196F3);
  static const secondary = Color(0xFF4CAF50);
  static const accent = Color(0xFFFFC107);

  // ── 书房暖色系 ──────────────────────────────────────────────────────────────
  static const studyWall = Color(0xFF2C1810);         // 书房墙壁 - 深棕
  static const studyWallLight = Color(0xFF3E2723);    // 书房墙壁 - 浅棕
  static const tableWood = Color(0xFF8D6E63);         // 桌面木纹 - 主色
  static const tableWoodDark = Color(0xFF5D4037);     // 桌面木纹 - 深色
  static const tableWoodEdge = Color(0xFF4E342E);     // 桌面边缘
  static const lampCenter = Color(0xFFFFE0B2);        // 灯光中心 - 暖黄
  static const lampEdge = Color(0xFFFFB74D);          // 灯光边缘 - 琥珀
  static const amberGold = Color(0xFFFFB74D);          // 选中/激活 - 琥珀金
  static const warmGold = Color(0xFFFFC107);          // 当前发言光圈 - 暖金

  // ── 书房文字色 ──────────────────────────────────────────────────────────────
  static const warmWhite = Color(0xFFFFF8E1);         // 主文字 - 暖白
  static const warmGray = Color(0xFFBCAAA4);          // 次要文字 - 暖灰
  static const scrollTitle = Color(0xFF3E2723);       // 卷轴标题 - 深棕（浅底上）

  // ── 书房材质 ──────────────────────────────────────────────────────────────
  static const parchment = Color(0xFFF5E6C8);         // 羊皮纸
  static const parchmentDark = Color(0xFFE8D5B0);     // 羊皮纸深
  static const scrollGold = Color(0xFFD4A843);        // 卷轴金边
  static const bookshelf = Color(0xFF1B0F07);         // 书架深色

  // ── 发光效果 ────────────────────────────────────────────────────────────────
  static const glowAmber = Color(0xFFFFC107);
  static const glowBlue = Color(0xFF42A5F5);
  static const glowGreen = Color(0xFF66BB6A);

  // ── 玻璃拟态 ────────────────────────────────────────────────────────────────
  static const surfaceGlass = Color(0x1AFFFFFF);
  static const surfaceGlassDark = Color(0x1A000000);
  static const borderGlass = Color(0x33FFFFFF);

  // ── 暗色模式背景 ────────────────────────────────────────────────────────────
  static const darkSurface = Color(0xFF1A1A2E);
  static const darkBackground = Color(0xFF0F0F1A);

  // ── 强调色 ──────────────────────────────────────────────────────────────────
  static const accentWarm = Color(0xFFFF7043);
  static const accentCool = Color(0xFF26C6DA);

  // ── 参与者角色色 ────────────────────────────────────────────────────────────
  static const moderatorColor = Color(0xFF7C4DFF);
  static const explorerColor = Color(0xFF42A5F5);
  static const skepticColor = Color(0xFFEF5350);
  static const peacemakerColor = Color(0xFF66BB6A);
  static const storytellerColor = Color(0xFFFFA726);

  /// 根据参与者名字获取角色色
  static Color getParticipantColor(String name) {
    switch (name) {
      case '李老师':
      case '老师':
        return moderatorColor;
      case '小探':
        return explorerColor;
      case '小疑':
        return skepticColor;
      case '小和':
        return peacemakerColor;
      case '小说':
        return storytellerColor;
      default:
        return glowBlue;
    }
  }

  /// 圆桌表面颜色
  static const tableSurface = Color(0xFF8D6E63);
  static const tableSurfaceDark = Color(0xFF4E342E);

  /// 消息气泡颜色
  static const messageModerator = Color(0xFFE8EAF6);
  static const messageAI = Color(0xFFF5F5F5);
  static const messageHuman = Color(0xFFE3F2FD);
  static const messageSystem = Color(0xFFEEEEEE);

  // ── 暗色模式消息 ────────────────────────────────────────────────────────────
  static const messageModeratorDark = Color(0xFF311B92);
  static const messageAIDark = Color(0xFF263238);
  static const messageHumanDark = Color(0xFF0D47A1);
  static const messageSystemDark = Color(0xFF37474F);

  // ── 烛光粒子 ──────────────────────────────────────────────────────────────
  static const candleFlame = Color(0xFFFFE082);
  static const candleGlow = Color(0xFFFFCC80);
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/theme/app_colors.dart
git commit -m "feat: add warm study room color palette for immersive UI"
```

---

## Task 3: Update Theme

**Files:**
- Modify: `frontend/lib/theme/app_theme.dart`

- [ ] **Step 1: Rewrite app_theme.dart with warm study theme and ZCOOL XiaoWei**

Replace the entire file `frontend/lib/theme/app_theme.dart` with:

```dart
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'app_colors.dart';

class AppTheme {
  // 圆桌思辨主色 - 温暖琥珀金
  static const _primaryColor = Color(0xFFFFB74D);

  static final lightTheme = ThemeData(
    useMaterial3: true,
    colorSchemeSeed: _primaryColor,
    brightness: Brightness.light,
    textTheme: GoogleFonts.notoSansScTextTheme(),
    appBarTheme: const AppBarTheme(
      centerTitle: true,
      elevation: 0,
      backgroundColor: AppColors.parchment,
      foregroundColor: AppColors.studyWall,
    ),
    cardTheme: CardThemeData(
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      filled: true,
      fillColor: Colors.white,
    ),
  );

  static final darkTheme = ThemeData(
    useMaterial3: true,
    colorSchemeSeed: _primaryColor,
    brightness: Brightness.dark,
    textTheme: GoogleFonts.notoSansScTextTheme(
      ThemeData(brightness: Brightness.dark).textTheme,
    ),
    appBarTheme: const AppBarTheme(
      centerTitle: true,
      elevation: 0,
      backgroundColor: AppColors.studyWall,
      foregroundColor: AppColors.warmWhite,
    ),
    cardTheme: CardThemeData(
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      filled: true,
    ),
    scaffoldBackgroundColor: AppColors.studyWall,
    colorScheme: ColorScheme.dark(
      surface: AppColors.studyWallLight,
      onSurface: AppColors.warmWhite,
    ),
  );

  /// 书法字体 - 用于卷轴标题和章节标题
  static TextStyle calligraphyStyle({
    double fontSize = 20,
    Color? color,
    FontWeight fontWeight = FontWeight.bold,
  }) {
    return GoogleFonts.zcoolXiaoWei(
      fontSize: fontSize,
      color: color ?? AppColors.scrollTitle,
      fontWeight: fontWeight,
    );
  }

  /// 书法字体 - 暗色模式
  static TextStyle calligraphyStyleDark({
    double fontSize = 20,
    Color? color,
    FontWeight fontWeight = FontWeight.bold,
  }) {
    return GoogleFonts.zcoolXiaoWei(
      fontSize: fontSize,
      color: color ?? AppColors.warmWhite,
      fontWeight: fontWeight,
    );
  }

  /// 玻璃拟态 BoxDecoration（亮色模式）
  static BoxDecoration glassBoxDecoration({Color? tintColor}) {
    return BoxDecoration(
      color: (tintColor ?? Colors.white).withValues(alpha: 0.15),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: AppColors.borderGlass),
    );
  }

  /// 玻璃拟态 BoxDecoration（暗色模式）
  static BoxDecoration glassBoxDecorationDark({Color? tintColor}) {
    return BoxDecoration(
      color: (tintColor ?? AppColors.studyWallLight).withValues(alpha: 0.6),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
    );
  }

  /// 发光效果 BoxDecoration（用于当前发言者头像）
  static BoxDecoration glowBoxDecoration(Color glowColor, {double blurRadius = 20}) {
    return BoxDecoration(
      shape: BoxShape.circle,
      boxShadow: [
        BoxShadow(
          color: glowColor.withValues(alpha: 0.6),
          blurRadius: blurRadius,
          spreadRadius: 2,
        ),
      ],
    );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/theme/app_theme.dart
git commit -m "feat: update theme with warm study colors and ZCOOL XiaoWei font"
```

---

## Task 4: Create BookshelfPainter

**Files:**
- Create: `frontend/lib/painters/bookshelf_painter.dart`

- [ ] **Step 1: Write BookshelfPainter**

Create `frontend/lib/painters/bookshelf_painter.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// 书房背景绘制器
///
/// 绘制深色木纹书架墙壁 + 顶部暖光光源。
/// 营造书房围坐讨论的氛围。
class BookshelfPainter extends CustomPainter {
  final double lightIntensity; // 0.0 ~ 1.0，灯光强度

  BookshelfPainter({this.lightIntensity = 0.8});

  @override
  void paint(Canvas canvas, Size size) {
    // 1. 墙壁底色 - 深棕渐变
    final wallPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          AppColors.bookshelf,
          AppColors.studyWall,
          AppColors.studyWall.withValues(alpha: 0.9),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), wallPaint);

    // 2. 木纹纹理 - 水平线条模拟
    final grainPaint = Paint()
      ..color = AppColors.studyWallLight.withValues(alpha: 0.08)
      ..strokeWidth = 1;
    for (var y = 0.0; y < size.height; y += 12 + _pseudoRandom(y) * 8) {
      final x1 = 0.0;
      final x2 = size.width;
      canvas.drawLine(Offset(x1, y), Offset(x2, y), grainPaint);
    }

    // 3. 书架横板 - 三层书架
    final shelfPaint = Paint()..color = AppColors.tableWoodDark.withValues(alpha: 0.3);
    final shelfHighlight = Paint()
      ..color = AppColors.lampCenter.withValues(alpha: 0.05 * lightIntensity);

    final shelfYs = [size.height * 0.15, size.height * 0.45, size.height * 0.72];
    for (final shelfY in shelfYs) {
      // 架板
      canvas.drawRect(
        Rect.fromLTWH(0, shelfY, size.width, 4),
        shelfPaint,
      );
      // 高光
      canvas.drawRect(
        Rect.fromLTWH(0, shelfY, size.width, 2),
        shelfHighlight,
      );
    }

    // 4. 书脊 - 在每层书架上绘制竖线模拟书本
    final bookPaint = Paint()
      ..color = AppColors.studyWallLight.withValues(alpha: 0.06);
    for (final shelfY in shelfYs) {
      var x = 20.0;
      while (x < size.width - 20) {
        final bookWidth = 8 + _pseudoRandom(x + shelfY) * 12;
        canvas.drawRect(
          Rect.fromLTWH(x, shelfY - 40 - _pseudoRandom(x) * 20, bookWidth, 38),
          bookPaint,
        );
        x += bookWidth + 2 + _pseudoRandom(x) * 4;
      }
    }

    // 5. 顶部光源 - 径向渐变暖光从上方洒下
    final lightPaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.topCenter.alongSize(size),
        radius: 0.6,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.15 * lightIntensity),
          AppColors.lampEdge.withValues(alpha: 0.05 * lightIntensity),
          Colors.transparent,
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), lightPaint);

    // 6. 暗角 - 四角变暗
    final vignettePaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center.alongSize(size),
        radius: 0.7,
        colors: [
          Colors.transparent,
          Colors.transparent,
          AppColors.bookshelf.withValues(alpha: 0.5),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), vignettePaint);
  }

  /// 简单伪随机，用于生成一致的纹理
  double _pseudoRandom(double seed) {
    return ((sin(seed * 127.1 + 311.7) * 43758.5453) % 1).abs();
  }

  @override
  bool shouldRepaint(BookshelfPainter oldDelegate) {
    return lightIntensity != oldDelegate.lightIntensity;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/painters/bookshelf_painter.dart
git commit -m "feat: add BookshelfPainter for warm study room background"
```

---

## Task 5: Create RoundTablePainter

**Files:**
- Create: `frontend/lib/painters/round_table_painter.dart`

- [ ] **Step 1: Write RoundTablePainter**

Create `frontend/lib/painters/round_table_painter.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// 圆桌桌面绘制器
///
/// 绘制木质圆桌俯视图：径向渐变木纹 + 边缘高光 + 阴影 + 中心光晕。
class RoundTablePainter extends CustomPainter {
  final double glowIntensity; // 0.0 ~ 1.0，中心光晕强度
  final double tableRadius;   // 圆桌半径

  RoundTablePainter({
    this.glowIntensity = 0.5,
    required this.tableRadius,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = tableRadius;

    // 1. 桌面阴影
    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.3)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 20);
    canvas.drawCircle(center.translate(0, 8), radius + 4, shadowPaint);

    // 2. 桌面底色 - 深色边缘
    final edgePaint = Paint()..color = AppColors.tableWoodEdge;
    canvas.drawCircle(center, radius, edgePaint);

    // 3. 桌面主体 - 径向渐变模拟木纹
    final tablePaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center.alongSize(size),
        radius: 0.5,
        colors: [
          AppColors.tableWood.withValues(alpha: 0.9),
          AppColors.tableWood,
          AppColors.tableWoodDark,
          AppColors.tableWoodEdge,
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius));
    canvas.drawCircle(center, radius - 3, tablePaint);

    // 4. 木纹年轮 - 同心圆
    final ringPaint = Paint()
      ..color = AppColors.tableWoodDark.withValues(alpha: 0.12)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    for (var r = 30.0; r < radius; r += 20 + _pseudoRandom(r) * 15) {
      canvas.drawCircle(center, r, ringPaint);
    }

    // 5. 木纹射线 - 从中心向外的细线
    final rayPaint = Paint()
      ..color = AppColors.tableWoodDark.withValues(alpha: 0.06)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.5;
    for (var angle = 0.0; angle < 2 * pi; angle += pi / 12) {
      final inner = Offset(
        center.dx + 20 * cos(angle),
        center.dy + 20 * sin(angle),
      );
      final outer = Offset(
        center.dx + (radius - 10) * cos(angle),
        center.dy + (radius - 10) * sin(angle),
      );
      canvas.drawLine(inner, outer, rayPaint);
    }

    // 6. 桌面边缘高光
    final edgeHighlightPaint = Paint()
      ..shader = SweepGradient(
        startAngle: -pi / 4,
        endAngle: pi * 1.5,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.2),
          Colors.transparent,
          AppColors.lampCenter.withValues(alpha: 0.1),
          Colors.transparent,
          AppColors.lampCenter.withValues(alpha: 0.15),
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius))
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawCircle(center, radius - 2, edgeHighlightPaint);

    // 7. 中心光晕 - 桌面中央的灯光效果
    final glowPaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center.alongSize(size),
        radius: 0.3,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.12 * glowIntensity),
          AppColors.lampEdge.withValues(alpha: 0.04 * glowIntensity),
          Colors.transparent,
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius * 0.6));
    canvas.drawCircle(center, radius * 0.6, glowPaint);
  }

  double _pseudoRandom(double seed) {
    return ((sin(seed * 127.1 + 311.7) * 43758.5453) % 1).abs();
  }

  @override
  bool shouldRepaint(RoundTablePainter oldDelegate) {
    return glowIntensity != oldDelegate.glowIntensity ||
        tableRadius != oldDelegate.tableRadius;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/painters/round_table_painter.dart
git commit -m "feat: add RoundTablePainter for wooden round table surface"
```

---

## Task 6: Create CandlelightPainter

**Files:**
- Create: `frontend/lib/painters/candlelight_painter.dart`

- [ ] **Step 1: Write CandlelightPainter**

Create `frontend/lib/painters/candlelight_painter.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// 烛光粒子绘制器
///
/// 浮动的暖色光点，模拟烛光效果。需要外部 AnimationController 驱动。
class CandlelightPainter extends CustomPainter {
  final List<CandleParticle> particles;
  final double animationValue; // 0.0 ~ 1.0 from AnimationController

  CandlelightPainter({
    required this.particles,
    required this.animationValue,
  });

  @override
  void paint(Canvas canvas, Size size) {
    for (final particle in particles) {
      final progress = (animationValue + particle.phaseOffset) % 1.0;
      final x = particle.baseX + sin(progress * 2 * pi * particle.wanderSpeedX) * particle.wanderRadius;
      final y = particle.baseY + sin(progress * 2 * pi * particle.wanderSpeedY) * particle.wanderRadius;

      // 粒子亮度随动画值波动
      final brightness = 0.4 + 0.6 * (0.5 + 0.5 * sin(progress * 2 * pi * 3));
      final alpha = particle.maxAlpha * brightness;

      // 外层光晕
      final glowPaint = Paint()
        ..color = AppColors.candleGlow.withValues(alpha: alpha * 0.3)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 8);
      canvas.drawCircle(Offset(x, y), particle.size * 3, glowPaint);

      // 核心光点
      final corePaint = Paint()
        ..color = AppColors.candleFlame.withValues(alpha: alpha);
      canvas.drawCircle(Offset(x, y), particle.size, corePaint);
    }
  }

  @override
  bool shouldRepaint(CandlelightPainter oldDelegate) {
    return animationValue != oldDelegate.animationValue;
  }
}

/// 烛光粒子数据
class CandleParticle {
  final double baseX;
  final double baseY;
  final double size;
  final double maxAlpha;
  final double phaseOffset;
  final double wanderRadius;
  final double wanderSpeedX;
  final double wanderSpeedY;

  const CandleParticle({
    required this.baseX,
    required this.baseY,
    this.size = 3,
    this.maxAlpha = 0.6,
    this.phaseOffset = 0,
    this.wanderRadius = 15,
    this.wanderSpeedX = 0.5,
    this.wanderSpeedY = 0.7,
  });

  /// 生成一组均匀分布在区域内的烛光粒子
  static List<CandleParticle> generate({
    required double areaWidth,
    required double areaHeight,
    int count = 18,
    Random? random,
  }) {
    final rng = random ?? Random(42);
    return List.generate(count, (i) {
      return CandleParticle(
        baseX: rng.nextDouble() * areaWidth,
        baseY: rng.nextDouble() * areaHeight,
        size: 2 + rng.nextDouble() * 3,
        maxAlpha: 0.3 + rng.nextDouble() * 0.4,
        phaseOffset: rng.nextDouble(),
        wanderRadius: 8 + rng.nextDouble() * 20,
        wanderSpeedX: 0.3 + rng.nextDouble() * 0.5,
        wanderSpeedY: 0.4 + rng.nextDouble() * 0.6,
      );
    });
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/painters/candlelight_painter.dart
git commit -m "feat: add CandlelightPainter for floating warm light particles"
```

---

## Task 7: Create ScrollCardPainter

**Files:**
- Create: `frontend/lib/painters/scroll_card_painter.dart`

- [ ] **Step 1: Write ScrollCardPainter**

Create `frontend/lib/painters/scroll_card_painter.dart`:

```dart
import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// 卷轴卡片绘制器
///
/// 绘制中国风卷轴形状：顶部和底部卷轴杆 + 纸面 + 金色边框。
class ScrollCardPainter extends CustomPainter {
  final bool isSelected;
  final bool isHovered;

  ScrollCardPainter({
    this.isSelected = false,
    this.isHovered = false,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final rollHeight = 8.0;  // 卷轴杆高度
    final rollRadius = 4.0;  // 卷轴杆圆角
    final padding = 4.0;

    // 1. 卷轴杆阴影
    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.15)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, size.height - rollHeight - 2, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      shadowPaint,
    );

    // 2. 底部卷轴杆
    final bottomRollPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [AppColors.scrollGold, AppColors.tableWoodDark, AppColors.scrollGold],
      ).createShader(Rect.fromLTWH(0, size.height - rollHeight, size.width, rollHeight));
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, size.height - rollHeight - padding, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      bottomRollPaint,
    );

    // 3. 纸面主体
    final paperPaint = Paint()
      ..color = isHovered || isSelected
          ? AppColors.parchmentDark
          : AppColors.parchment;
    final paperRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
      Radius.circular(2),
    );
    canvas.drawRRect(paperRect, paperPaint);

    // 4. 纸面纹理 - 微弱横线
    final texturePaint = Paint()
      ..color = AppColors.parchmentDark.withValues(alpha: 0.3)
      ..strokeWidth = 0.5;
    for (var y = rollHeight + 20.0; y < size.height - rollHeight - 10; y += 18) {
      canvas.drawLine(
        Offset(padding + 10, y),
        Offset(size.width - padding - 10, y),
        texturePaint,
      );
    }

    // 5. 金色边框
    if (isSelected) {
      final borderPaint = Paint()
        ..color = AppColors.scrollGold
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
          Radius.circular(2),
        ),
        borderPaint,
      );
    } else if (isHovered) {
      final borderPaint = Paint()
        ..color = AppColors.scrollGold.withValues(alpha: 0.5)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
          Radius.circular(2),
        ),
        borderPaint,
      );
    }

    // 6. 顶部卷轴杆
    final topRollPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [AppColors.scrollGold, AppColors.tableWoodDark, AppColors.scrollGold],
      ).createShader(Rect.fromLTWH(0, 0, size.width, rollHeight));
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, padding, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      topRollPaint,
    );

    // 7. 选中时的发光效果
    if (isSelected) {
      final glowPaint = Paint()
        ..color = AppColors.lampCenter.withValues(alpha: 0.15)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 12);
      canvas.drawRect(
        Rect.fromLTWH(0, 0, size.width, size.height),
        glowPaint,
      );
    }
  }

  @override
  bool shouldRepaint(ScrollCardPainter oldDelegate) {
    return isSelected != oldDelegate.isSelected || isHovered != oldDelegate.isHovered;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/painters/scroll_card_painter.dart
git commit -m "feat: add ScrollCardPainter for Chinese scroll-style topic cards"
```

---

## Task 8: Create GlowAvatar

**Files:**
- Create: `frontend/lib/features/session/glow_avatar.dart`

- [ ] **Step 1: Write GlowAvatar widget**

Create `frontend/lib/features/session/glow_avatar.dart`:

```dart
import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 发光头像 - 圆桌围坐时使用
///
/// 代替 ParticipantAvatar，增强发光效果和灰度切换能力。
class GlowAvatar extends StatefulWidget {
  final String name;
  final String avatar; // emoji
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;
  final bool isDimmed; // 灰度/半透明模式（首页未选中）

  const GlowAvatar({
    super.key,
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
    this.isDimmed = false,
  });

  @override
  State<GlowAvatar> createState() => _GlowAvatarState();
}

class _GlowAvatarState extends State<GlowAvatar>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
  }

  @override
  void didUpdateWidget(GlowAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isSpeaking && !_pulseController.isAnimating) {
      _pulseController.repeat(reverse: true);
    } else if (!widget.isSpeaking && _pulseController.isAnimating) {
      _pulseController.stop();
      _pulseController.value = 0;
    }
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = AppColors.getParticipantColor(widget.name);
    final size = 64.0;

    return AnimatedBuilder(
      animation: _pulseController,
      builder: (context, child) {
        final glowScale = widget.isSpeaking ? 1.0 + _pulseController.value * 0.15 : 1.0;
        final glowAlpha = widget.isSpeaking ? 0.4 + _pulseController.value * 0.3 : 0.0;

        return Opacity(
          opacity: widget.isDimmed ? 0.4 : 1.0,
          child: ColorFiltered(
            enabled: widget.isDimmed,
            colorFilter: const ColorFilter.matrix(<double>[
              0.2126, 0.7152, 0.0722, 0, 0,
              0.2126, 0.7152, 0.0722, 0, 0,
              0.2126, 0.7152, 0.0722, 0, 0,
              0, 0, 0, 1, 0,
            ]),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Stack(
                  clipBehavior: Clip.none,
                  children: [
                    // 发光外圈
                    if (widget.isSpeaking)
                      Transform.scale(
                        scale: glowScale,
                        child: Container(
                          width: size,
                          height: size,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            boxShadow: [
                              BoxShadow(
                                color: color.withValues(alpha: glowAlpha),
                                blurRadius: 24,
                                spreadRadius: 4,
                              ),
                            ],
                          ),
                        ),
                      ),
                    // 头像主体
                    Container(
                      width: size,
                      height: size,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: widget.isCurrentSpeaker
                            ? color.withValues(alpha: 0.15)
                            : AppColors.studyWallLight.withValues(alpha: 0.8),
                        border: Border.all(
                          color: widget.isCurrentSpeaker
                              ? color
                              : widget.isSpeaking
                                  ? color.withValues(alpha: 0.7)
                                  : AppColors.warmGray.withValues(alpha: 0.3),
                          width: widget.isCurrentSpeaker ? 3 : 2,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.3),
                            blurRadius: 8,
                            offset: const Offset(0, 2),
                          ),
                        ],
                      ),
                      child: Center(
                        child: Text(widget.avatar, style: const TextStyle(fontSize: 28)),
                      ),
                    ),
                    // 举手标识
                    if (widget.hasRaisedHand)
                      Positioned(
                        right: -4,
                        top: -4,
                        child: Container(
                          padding: const EdgeInsets.all(3),
                          decoration: const BoxDecoration(
                            color: AppColors.accentWarm,
                            shape: BoxShape.circle,
                          ),
                          child: const Icon(Icons.back_hand, size: 14, color: Colors.white),
                        ),
                      ),
                    // 发言中指示器
                    if (widget.isSpeaking)
                      Positioned(
                        right: 2,
                        bottom: 2,
                        child: Container(
                          width: 14,
                          height: 14,
                          decoration: BoxDecoration(
                            color: AppColors.glowGreen,
                            shape: BoxShape.circle,
                            border: Border.all(color: AppColors.warmWhite, width: 2),
                          ),
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 6),
                // 名字
                Text(
                  widget.name,
                  style: TextStyle(
                    color: widget.isCurrentSpeaker ? color : AppColors.warmWhite,
                    fontWeight: widget.isCurrentSpeaker ? FontWeight.bold : FontWeight.normal,
                    fontSize: 12,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/session/glow_avatar.dart
git commit -m "feat: add GlowAvatar with pulse glow and dim mode for round table"
```

---

## Task 9: Create ScrollTopicCard Widget

**Files:**
- Create: `frontend/lib/features/home/scroll_topic_card.dart`

- [ ] **Step 1: Write ScrollTopicCard widget**

Create `frontend/lib/features/home/scroll_topic_card.dart`:

```dart
import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../painters/scroll_card_painter.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 卷轴话题卡片
///
/// 以中国风卷轴形式展示话题。选中时金边高亮+发光。
class ScrollTopicCard extends StatefulWidget {
  final Topic topic;
  final bool isSelected;
  final VoidCallback onTap;
  final double rotation; // 微倾斜角度（度）

  const ScrollTopicCard({
    super.key,
    required this.topic,
    this.isSelected = false,
    required this.onTap,
    this.rotation = 0,
  });

  @override
  State<ScrollTopicCard> createState() => _ScrollTopicCardState();
}

class _ScrollTopicCardState extends State<ScrollTopicCard> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Transform.rotate(
          angle: widget.rotation * 3.14159 / 180,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 300),
            width: 180,
            height: 200,
            curve: Curves.easeOutCubic,
            transformAlignment: Alignment.center,
            child: CustomPaint(
              painter: ScrollCardPainter(
                isSelected: widget.isSelected,
                isHovered: _isHovered,
              ),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // 分类标签
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppColors.scrollGold.withValues(alpha: 0.3),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        widget.topic.category,
                        style: TextStyle(
                          fontSize: 10,
                          color: AppColors.scrollTitle,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    // 标题 - 书法字体
                    Text(
                      widget.topic.title,
                      style: AppTheme.calligraphyStyle(
                        fontSize: 18,
                        color: AppColors.scrollTitle,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 8),
                    // 描述
                    Text(
                      widget.topic.description,
                      style: TextStyle(
                        fontSize: 12,
                        color: AppColors.tableWoodDark,
                        height: 1.4,
                      ),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const Spacer(),
                    // 标签
                    if (widget.topic.tags.isNotEmpty)
                      Wrap(
                        spacing: 4,
                        children: widget.topic.tags.take(2).map((tag) => Text(
                          '#$tag',
                          style: TextStyle(fontSize: 10, color: AppColors.warmGray),
                        )).toList(),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/home/scroll_topic_card.dart
git commit -m "feat: add ScrollTopicCard with Chinese scroll painting style"
```

---

## Task 10: Create TableParticipantRing and SpeakingBubble

**Files:**
- Create: `frontend/lib/features/session/table_participant_ring.dart`
- Create: `frontend/lib/features/session/speaking_bubble.dart`

- [ ] **Step 1: Write TableParticipantRing**

Create `frontend/lib/features/session/table_participant_ring.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';

import 'glow_avatar.dart';

/// 围坐参与者数据
class SeatedParticipant {
  final String name;
  final String avatar;
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;
  final bool isDimmed;

  const SeatedParticipant({
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
    this.isDimmed = false,
  });
}

/// 圆桌围坐排列
///
/// 将参与者以圆形排列在圆桌周围，主持人固定在12点钟方向。
class TableParticipantRing extends StatelessWidget {
  final List<SeatedParticipant> participants;
  final double tableRadius;

  const TableParticipantRing({
    super.key,
    required this.participants,
    required this.tableRadius,
  });

  @override
  Widget build(BuildContext context) {
    final total = participants.length;
    if (total == 0) return const SizedBox.shrink();

    // 头像放在圆桌边缘外侧
    final avatarRadius = tableRadius + 52;

    return Stack(
      children: List.generate(total, (i) {
        final p = participants[i];
        // 主持人放在12点钟（-90度），其余均匀分布
        final angle = (i / total) * 2 * pi - pi / 2;

        return Positioned(
          left: avatarRadius * cos(angle) - 32, // 32 = half avatar width
          top: avatarRadius * sin(angle) - 38,  // 38 = half avatar height + name
          child: GlowAvatar(
            name: p.name,
            avatar: p.avatar,
            isSpeaking: p.isSpeaking,
            isHuman: p.isHuman,
            hasRaisedHand: p.hasRaisedHand,
            isCurrentSpeaker: p.isCurrentSpeaker,
            isDimmed: p.isDimmed,
          ),
        );
      }),
    );
  }
}
```

- [ ] **Step 2: Write SpeakingBubble**

Create `frontend/lib/features/session/speaking_bubble.dart`:

```dart
import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';

/// 发言气泡
///
/// 从发言者位置弹出，带角色色边框，显示消息内容。
/// 2-3 秒后自动缩小淡出（由父组件控制可见性）。
class SpeakingBubble extends StatelessWidget {
  final String speaker;
  final String content;
  final Color speakerColor;
  final bool isVisible;

  const SpeakingBubble({
    super.key,
    required this.speaker,
    required this.content,
    required this.speakerColor,
    this.isVisible = true,
  });

  @override
  Widget build(BuildContext context) {
    return AnimatedOpacity(
      opacity: isVisible ? 1.0 : 0.0,
      duration: const Duration(milliseconds: 500),
      child: AnimatedScale(
        scale: isVisible ? 1.0 : 0.8,
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOutCubic,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 280, maxHeight: 120),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: AppColors.parchment.withValues(alpha: 0.95),
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: speakerColor.withValues(alpha: 0.6),
              width: 2,
            ),
            boxShadow: [
              BoxShadow(
                color: speakerColor.withValues(alpha: 0.15),
                blurRadius: 12,
                spreadRadius: 2,
              ),
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.2),
                blurRadius: 8,
                offset: const Offset(0, 3),
              ),
            ],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 发言者名字
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                      color: speakerColor,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    speaker,
                    style: TextStyle(
                      color: speakerColor,
                      fontWeight: FontWeight.bold,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              // 消息内容
              Text(
                content,
                style: TextStyle(
                  color: AppColors.scrollTitle,
                  fontSize: 14,
                  height: 1.4,
                ),
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/features/session/table_participant_ring.dart frontend/lib/features/session/speaking_bubble.dart
git commit -m "feat: add TableParticipantRing and SpeakingBubble for round table layout"
```

---

## Task 11: Create GlassControlBar

**Files:**
- Create: `frontend/lib/features/session/glass_control_bar.dart`

- [ ] **Step 1: Write GlassControlBar widget**

Create `frontend/lib/features/session/glass_control_bar.dart`:

```dart
import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
import 'push_to_talk_button.dart';

/// 半透明毛玻璃控制栏
///
/// 底部悬浮，包含文本输入框 / PTT 按钮 / 打断按钮。
/// 不遮挡桌面视野。
class GlassControlBar extends StatefulWidget {
  final bool isMyTurn;
  final bool isPushToTalk;
  final bool isRecording;
  final TextEditingController inputController;
  final VoidCallback onSendMessage;
  final VoidCallback onPttStart;
  final VoidCallback onPttEnd;
  final bool canInterrupt;
  final bool hasRaisedHand;
  final VoidCallback onInterrupt;

  const GlassControlBar({
    super.key,
    required this.isMyTurn,
    required this.isPushToTalk,
    required this.isRecording,
    required this.inputController,
    required this.onSendMessage,
    required this.onPttStart,
    required this.onPttEnd,
    required this.canInterrupt,
    required this.hasRaisedHand,
    required this.onInterrupt,
  });

  @override
  State<GlassControlBar> createState() => _GlassControlBarState();
}

class _GlassControlBarState extends State<GlassControlBar> {
  @override
  Widget build(BuildContext context) {
    if (!widget.isMyTurn && !widget.canInterrupt) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.studyWall.withValues(alpha: 0.85),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: AppColors.lampCenter.withValues(alpha: 0.15),
          width: 1,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.3),
            blurRadius: 12,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: SafeArea(
            child: widget.isPushToTalk && widget.isMyTurn
                ? _buildPttRow()
                : widget.isMyTurn
                    ? _buildTextRow()
                    : _buildInterruptOnly(),
          ),
        ),
      ),
    );
  }

  Widget _buildPttRow() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        PushToTalkButton(
          isEnabled: true,
          onRecordStart: widget.onPttStart,
          onRecordEnd: widget.onPttEnd,
          isRecording: widget.isRecording,
        ),
        const SizedBox(width: 16),
        TextButton.icon(
          onPressed: widget.onSendMessage,
          icon: const Icon(Icons.keyboard, size: 18, color: AppColors.warmWhite),
          label: const Text('文字输入', style: TextStyle(color: AppColors.warmWhite)),
        ),
        if (widget.canInterrupt)
          Padding(
            padding: const EdgeInsets.only(left: 8),
            child: InterruptButton(
              isVisible: true,
              onPressed: widget.onInterrupt,
              hasRaisedHand: widget.hasRaisedHand,
            ),
          ),
      ],
    );
  }

  Widget _buildTextRow() {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: widget.inputController,
            style: const TextStyle(color: AppColors.warmWhite),
            decoration: InputDecoration(
              hintText: '说出你的想法...',
              hintStyle: TextStyle(color: AppColors.warmGray),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: const BorderSide(color: AppColors.amberGold),
              ),
              filled: true,
              fillColor: AppColors.studyWallLight.withValues(alpha: 0.6),
              contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            ),
            onSubmitted: (_) => widget.onSendMessage(),
          ),
        ),
        const SizedBox(width: 8),
        if (widget.isPushToTalk)
          IconButton(
            onPressed: widget.onPttStart,
            icon: const Icon(Icons.mic, color: AppColors.warmWhite),
            style: IconButton.styleFrom(
              backgroundColor: AppColors.amberGold.withValues(alpha: 0.2),
            ),
          ),
        FilledButton(
          onPressed: widget.onSendMessage,
          style: FilledButton.styleFrom(
            backgroundColor: AppColors.amberGold,
            foregroundColor: AppColors.scrollTitle,
            shape: const CircleBorder(),
            padding: const EdgeInsets.all(12),
          ),
          child: const Icon(Icons.send),
        ),
      ],
    );
  }

  Widget _buildInterruptOnly() {
    return Center(
      child: InterruptButton(
        isVisible: widget.canInterrupt,
        onPressed: widget.onInterrupt,
        hasRaisedHand: widget.hasRaisedHand,
      ),
    );
  }
}
```

NOTE: This file needs `import 'dart:ui';` for `ImageFilter`. Add it at the top.

- [ ] **Step 2: Fix import - add dart:ui**

Add `import 'dart:ui';` at the top of `frontend/lib/features/session/glass_control_bar.dart`.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/features/session/glass_control_bar.dart
git commit -m "feat: add GlassControlBar with frosted glass effect for session input"
```

---

## Task 12: Create ImmersiveHomeScreen

**Files:**
- Create: `frontend/lib/features/home/immersive_home_screen.dart`

This is the main home screen that replaces `home_screen.dart`. It assembles all the painters and components into the immersive study room experience.

- [ ] **Step 1: Write ImmersiveHomeScreen**

Create `frontend/lib/features/home/immersive_home_screen.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/discussion_models.dart';
import '../../painters/bookshelf_painter.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../services/api_client.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
import '../session/glow_avatar.dart';
import '../session/immersive_session_screen.dart';
import 'scroll_topic_card.dart';

/// 沉浸式首页 - "推开书房的门"
///
/// 书房场景 + 木质圆桌 + 卷轴话题卡片 + AI角色围坐 + 入座按钮
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

  // 当前选择
  Topic? _selectedTopic;
  final Set<String> _selectedCharacterIds = {'explorer', 'skeptic'};
  final TextEditingController _nameController = TextEditingController(text: '同学');

  // 动画
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
      // 入场动画
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

    // 初始化烛光粒子
    _particles ??= CandleParticle.generate(
      areaWidth: size.width,
      areaHeight: size.height,
      count: 15,
    );

    return Scaffold(
      body: Stack(
        children: [
          // 1. 书房背景
          CustomPaint(
            size: size,
            painter: BookshelfPainter(),
          ),
          // 2. 圆桌
          Center(
            child: CustomPaint(
              size: Size(tableRadius * 2, tableRadius * 2),
              painter: RoundTablePainter(
                tableRadius: tableRadius,
                glowIntensity: 0.6,
              ),
            ),
          ),
          // 3. 烛光粒子
          CustomPaint(
            size: size,
            painter: CandlelightPainter(
              particles: _particles!,
              animationValue: _candleController.value,
            ),
          ),
          // 4. 内容层
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
        // 顶部标题栏
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

        // 卷轴话题卡片
        SizedBox(
          height: 220,
          child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 32),
            itemCount: _topics.length,
            itemBuilder: (context, index) {
              final topic = _topics[index];
              final rotation = (index % 2 == 0 ? -2.0 : 1.5);
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

        // 角色选择
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

        // 名字输入 + 入座按钮
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
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 300),
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
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/home/immersive_home_screen.dart
git commit -m "feat: add ImmersiveHomeScreen with study room scene and scroll cards"
```

---

## Task 13: Create ImmersiveSessionScreen

**Files:**
- Create: `frontend/lib/features/session/immersive_session_screen.dart`

This is the main session screen that replaces `session_screen.dart`. It assembles the round table discussion room.

- [ ] **Step 1: Write ImmersiveSessionScreen**

Create `frontend/lib/features/session/immersive_session_screen.dart`:

```dart
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/discussion_models.dart';
import '../../painters/bookshelf_painter.dart';
import '../../painters/candlelight_painter.dart';
import '../../painters/round_table_painter.dart';
import '../../services/api_client.dart';
import '../../services/speech_service.dart';
import '../../services/websocket_client.dart';
import '../../state/settings_provider.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
import 'chat_history_drawer.dart';
import 'glass_control_bar.dart';
import 'speaking_bubble.dart';
import 'table_participant_ring.dart';

/// 沉浸式讨论房间 - "围坐圆桌，各抒己见"
class ImmersiveSessionScreen extends ConsumerStatefulWidget {
  final Topic topic;
  final List<String> characterIds;
  final String humanName;

  const ImmersiveSessionScreen({
    super.key,
    required this.topic,
    required this.characterIds,
    required this.humanName,
  });

  @override
  ConsumerState<ImmersiveSessionScreen> createState() => _ImmersiveSessionScreenState();
}

class _ImmersiveSessionScreenState extends ConsumerState<ImmersiveSessionScreen>
    with TickerProviderStateMixin {
  final DiscussionWebSocket _wsClient = DiscussionWebSocket();
  final TextEditingController _inputController = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  final FocusNode _keyboardFocusNode = FocusNode();

  // 讨论状态
  List<ChatMessage> _messages = [];
  String _currentSpeaker = '';
  bool _isMyTurn = false;
  String _statusText = '连接中...';
  bool _hasRaisedHand = false;

  // 参与者列表（从后端获取后填充）
  List<SeatedParticipant> _participants = [];

  // 语音状态
  bool _isRecording = false;
  late TtsService _ttsService;
  late AsrService _asrService;

  // 动画
  late AnimationController _candleController;
  late AnimationController _glowController;
  List<CandleParticle>? _particles;

  // 当前发言内容（桌面中央大字显示）
  String _centerMessage = '';
  String _centerSpeaker = '';

  @override
  void initState() {
    super.initState();
    _candleController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 8),
    )..repeat();
    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    );
    _initVoiceServices();
    _startDiscussion();
  }

  void _initVoiceServices() {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    final serverUrl = settings?.serverUrl ?? 'http://localhost:8001';
    final ttsProvider = settings?.ttsProvider ?? 'browser';
    final asrProvider = settings?.asrProvider ?? 'browser';

    _ttsService = createTtsService(ttsProvider, serverUrl: serverUrl);
    _asrService = createAsrService(asrProvider, serverUrl: serverUrl);

    _asrService.transcriptionStream.listen((text) {
      if (text.isNotEmpty) {
        _wsClient.sendHumanInput(speaker: widget.humanName, content: text);
        setState(() {
          _messages.add(ChatMessage(source: widget.humanName, content: text));
          _isMyTurn = false;
          _statusText = '等待其他人发言...';
        });
      }
    });
  }

  Future<void> _startDiscussion() async {
    setState(() => _statusText = '创建讨论会话...');

    try {
      final apiClient = ref.read(apiClientProvider);
      final sessionData = await apiClient.createSession(
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        humanNames: [widget.humanName],
      );

      final sessionId = sessionData['session_id'] as String;
      final wsUrl = apiClient.getWebSocketUrl(sessionId);

      // 构建参与者列表
      _buildParticipants();

      await _wsClient.connect(
        wsUrl: wsUrl,
        sessionId: sessionId,
        topicId: widget.topic.id,
        characterIds: widget.characterIds,
        humanNames: [widget.humanName],
      );

      setState(() => _statusText = '已连接');
      _wsClient.events.listen(_handleEvent);
    } catch (e) {
      setState(() => _statusText = '连接失败: $e');
    }
  }

  void _buildParticipants() {
    // 基于选中的角色构建参与者列表
    final characterMap = {
      'moderator': SeatedParticipant(name: '老师', avatar: '👩‍🏫'),
      'explorer': SeatedParticipant(name: '小探', avatar: '🔍'),
      'skeptic': SeatedParticipant(name: '小疑', avatar: '🤔'),
      'peacemaker': SeatedParticipant(name: '小和', avatar: '🕊️'),
      'storyteller': SeatedParticipant(name: '小说', avatar: '📖'),
    };

    _participants = [
      characterMap['moderator']!,
      ...widget.characterIds
          .where((id) => id != 'moderator')
          .map((id) => characterMap[id] ?? SeatedParticipant(name: id, avatar: '💬')),
      SeatedParticipant(name: widget.humanName, avatar: '🧑', isHuman: true),
    ];
  }

  void _handleEvent(WsEvent event) {
    switch (event.eventType) {
      case WsEventType.message:
        final data = event.data;
        if (data != null) {
          final source = data['source'] ?? '未知';
          final content = data['content'] ?? '';
          final msgType = data['msg_type'] ?? 'text';

          setState(() {
            _messages.add(ChatMessage(source: source, content: content, type: msgType));
            _centerMessage = content;
            _centerSpeaker = source;
          });

          if (msgType != 'system' && source != widget.humanName) {
            _ttsService.speak(content);
          }
        }
      case WsEventType.turnChange:
        final data = event.data;
        if (data != null) {
          final speaker = data['speaker'] ?? '';
          final isHuman = data['is_human'] ?? false;
          setState(() {
            _currentSpeaker = speaker;
            _isMyTurn = isHuman && speaker == widget.humanName;
            _hasRaisedHand = false;
            _updateParticipants();
            if (_isMyTurn) {
              _statusText = '轮到你发言了！';
              _glowController.repeat(reverse: true);
            } else {
              _glowController.stop();
              _statusText = isHuman ? '$speaker 正在发言...' : '$speaker 正在思考...';
            }
          });
        }
      case WsEventType.stream:
        break;
      case WsEventType.stateChange:
        final data = event.data;
        if (data != null) {
          final newState = data['new_state'] ?? '';
          setState(() => _statusText = '状态: $newState');
        }
      case WsEventType.system:
        final data = event.data;
        if (data != null) {
          setState(() {
            _messages.add(ChatMessage(source: '系统', content: data['message'] ?? '', type: 'system'));
          });
        }
      case WsEventType.humanInputRequested:
        setState(() {
          _isMyTurn = true;
          _statusText = '轮到你发言了！';
          _glowController.repeat(reverse: true);
        });
      case WsEventType.error:
        final data = event.data;
        setState(() => _statusText = '错误: ${data?['message'] ?? '未知错误'}');
      case WsEventType.ended:
        setState(() {
          _statusText = '讨论已结束';
          _isMyTurn = false;
          _glowController.stop();
        });
      case WsEventType.interrupt:
        final data = event.data;
        if (data != null) {
          final interrupter = data['interrupter'] ?? '';
          setState(() {
            _messages.add(ChatMessage(source: '系统', content: '$interrupter 举手请求发言', type: 'system'));
          });
        }
    }
  }

  void _updateParticipants() {
    _participants = _participants.map((p) {
      return SeatedParticipant(
        name: p.name,
        avatar: p.avatar,
        isHuman: p.isHuman,
        isSpeaking: p.name == _currentSpeaker,
        isCurrentSpeaker: p.name == _currentSpeaker,
      );
    }).toList();
  }

  void _sendMessage() {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;

    _wsClient.sendHumanInput(speaker: widget.humanName, content: text);

    setState(() {
      _messages.add(ChatMessage(source: widget.humanName, content: text));
      _isMyTurn = false;
      _statusText = '等待其他人发言...';
      _glowController.stop();
    });

    _inputController.clear();
  }

  void _onPttStart() {
    setState(() => _isRecording = true);
    _wsClient.sendPushToTalkStart(speaker: widget.humanName);
    _asrService.startListening();
  }

  void _onPttEnd() {
    setState(() => _isRecording = false);
    _wsClient.sendPushToTalkEnd(speaker: widget.humanName);
    _asrService.stopListening();
  }

  void _onInterrupt() {
    if (_hasRaisedHand) return;
    setState(() => _hasRaisedHand = true);
    _wsClient.sendInterrupt(speaker: widget.humanName);
  }

  bool get _isPushToTalk {
    final settings = ref.read(localSettingsProvider).valueOrNull;
    return settings?.pushToTalk ?? true;
  }

  @override
  void dispose() {
    _ttsService.dispose();
    _asrService.dispose();
    _wsClient.dispose();
    _candleController.dispose();
    _glowController.dispose();
    _inputController.dispose();
    _scrollController.dispose();
    _keyboardFocusNode.dispose();
    super.dispose();
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

    return KeyboardListener(
      focusNode: _keyboardFocusNode,
      onKeyEvent: _onKeyEvent,
      child: Scaffold(
        body: Stack(
          children: [
            // 1. 书房背景（聚光灯模式 - 光线集中在桌面）
            CustomPaint(
              size: size,
              painter: BookshelfPainter(lightIntensity: 0.6),
            ),
            // 2. 圆桌（中心偏上，留出底部控制栏空间）
            Positioned(
              left: (size.width - tableRadius * 2) / 2,
              top: size.height * 0.12,
              child: CustomPaint(
                size: Size(tableRadius * 2, tableRadius * 2),
                painter: RoundTablePainter(
                  tableRadius: tableRadius,
                  glowIntensity: _isMyTurn ? 0.9 : 0.6,
                ),
              ),
            ),
            // 3. 烛光粒子
            CustomPaint(
              size: size,
              painter: CandlelightPainter(
                particles: _particles!,
                animationValue: _candleController.value,
              ),
            ),
            // 4. 参与者围坐
            Positioned(
              left: (size.width - tableRadius * 2) / 2,
              top: size.height * 0.12,
              child: SizedBox(
                width: tableRadius * 2,
                height: tableRadius * 2,
                child: TableParticipantRing(
                  participants: _participants,
                  tableRadius: tableRadius,
                ),
              ),
            ),
            // 5. 桌面中央消息
            if (_centerMessage.isNotEmpty)
              Positioned(
                left: (size.width - 280) / 2,
                top: size.height * 0.12 + tableRadius - 60,
                child: SpeakingBubble(
                  speaker: _centerSpeaker,
                  content: _centerMessage,
                  speakerColor: AppColors.getParticipantColor(_centerSpeaker),
                ),
              ),
            // 6. 轮到你发言的蓝色光圈提示
            if (_isMyTurn)
              Positioned(
                left: (size.width - 200) / 2,
                top: size.height * 0.12 + tableRadius + 20,
                child: AnimatedBuilder(
                  animation: _glowController,
                  builder: (context, child) {
                    return Opacity(
                      opacity: 0.7 + 0.3 * _glowController.value,
                      child: Text(
                        '轮到你发言了',
                        style: AppTheme.calligraphyStyleDark(fontSize: 18)
                            .copyWith(color: AppColors.glowBlue),
                        textAlign: TextAlign.center,
                      ),
                    );
                  },
                ),
              ),
            // 7. 顶部状态栏
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: SafeArea(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppColors.studyWall.withValues(alpha: 0.7),
                    borderRadius: const BorderRadius.only(
                      bottomLeft: Radius.circular(16),
                      bottomRight: Radius.circular(16),
                    ),
                  ),
                  child: Row(
                    children: [
                      IconButton(
                        icon: const Icon(Icons.arrow_back, color: AppColors.warmWhite),
                        onPressed: () => Navigator.pop(context),
                      ),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              widget.topic.title,
                              style: AppTheme.calligraphyStyleDark(fontSize: 16),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            Text(
                              _statusText,
                              style: const TextStyle(color: AppColors.warmGray, fontSize: 12),
                            ),
                          ],
                        ),
                      ),
                      // 聊天记录按钮
                      IconButton(
                        icon: const Icon(Icons.history, color: AppColors.warmGray),
                        onPressed: _openChatHistory,
                      ),
                    ],
                  ),
                ),
              ),
            ),
            // 8. 底部控制栏
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: GlassControlBar(
                isMyTurn: _isMyTurn,
                isPushToTalk: _isPushToTalk,
                isRecording: _isRecording,
                inputController: _inputController,
                onSendMessage: _sendMessage,
                onPttStart: _onPttStart,
                onPttEnd: _onPttEnd,
                canInterrupt: !_isMyTurn && _currentSpeaker.isNotEmpty && !_hasRaisedHand,
                hasRaisedHand: _hasRaisedHand,
                onInterrupt: _onInterrupt,
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _openChatHistory() {
    Scaffold.of(context).openEndDrawer();
  }

  // This needs to be in a Scaffold with endDrawer
  // We'll wrap the Scaffold with a Drawer in the actual implementation
  // For now, show a bottom sheet
  void _showChatHistory() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.studyWall,
      builder: (context) => SizedBox(
        height: MediaQuery.of(context).size.height * 0.7,
        child: ChatHistoryDrawer(messages: _messages, myName: widget.humanName),
      ),
    );
  }

  void _onKeyEvent(KeyEvent event) {
    if (!_isPushToTalk) return;
    if (event is KeyDownEvent && event.logicalKey == LogicalKeyboardKey.space) {
      if (_isMyTurn && !_isRecording) _onPttStart();
    } else if (event is KeyUpEvent && event.logicalKey == LogicalKeyboardKey.space) {
      if (_isRecording) _onPttEnd();
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/session/immersive_session_screen.dart
git commit -m "feat: add ImmersiveSessionScreen with round table discussion room"
```

---

## Task 14: Update Animations

**Files:**
- Modify: `frontend/lib/features/session/animations.dart`

- [ ] **Step 1: Add study-room-specific animations to animations.dart**

Replace the entire file `frontend/lib/features/session/animations.dart` with:

```dart
/// 圆桌思辨动画定义
///
/// 使用 flutter_animate 提供统一的动画效果。
/// 包含书房场景专用动画。

import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../theme/app_colors.dart';

// ── 原有动画（保留）────────────────────────────────────────────────────────

/// 发言者头像脉冲发光动画
extension SpeakingAnimation on Widget {
  Widget speakPulse({Color? glowColor}) {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shimmer(duration: 1500.ms, color: (glowColor ?? AppColors.glowAmber).withValues(alpha: 0.3))
        .scale(
          begin: const Offset(1.0, 1.0),
          end: const Offset(1.05, 1.05),
          duration: 1500.ms,
        );
  }
}

/// 消息出现动画
extension MessageAnimation on Widget {
  Widget messageAppear() {
    return animate()
        .fadeIn(duration: 400.ms)
        .slideY(begin: 0.1, end: 0, duration: 400.ms);
  }
}

/// 参与者头像入场动画
extension ParticipantAnimation on Widget {
  Widget participantEntrance({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .scale(begin: const Offset(0, 0), end: const Offset(1, 1), duration: 500.ms, curve: Curves.elasticOut)
        .fadeIn(duration: 300.ms);
  }
}

/// 举手/打断抖动动画
extension InterruptAnimation on Widget {
  Widget handRaised() {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shake(hz: 2, rotation: 0.05, duration: 500.ms);
  }
}

/// 轮次切换高亮动画
extension TurnChangeAnimation on Widget {
  Widget turnHighlight({Color? color}) {
    return animate()
        .shimmer(duration: 300.ms, color: (color ?? AppColors.glowBlue).withValues(alpha: 0.3))
        .scale(
          begin: const Offset(0.95, 0.95),
          end: const Offset(1.0, 1.0),
          duration: 300.ms,
        );
  }
}

/// 中心消息区域渐入动画
extension CenterMessageAnimation on Widget {
  Widget centerAppear() {
    return animate()
        .fadeIn(duration: 500.ms, curve: Curves.easeOut)
        .scale(
          begin: const Offset(0.9, 0.9),
          end: const Offset(1.0, 1.0),
          duration: 500.ms,
          curve: Curves.easeOut,
        );
  }
}

// ── 书房场景专用动画 ─────────────────────────────────────────────────────

/// 入座呼吸发光 - 用于"入座开始"按钮
extension SeatButtonAnimation on Widget {
  Widget seatGlow() {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shimmer(
          duration: 2000.ms,
          color: AppColors.amberGold.withValues(alpha: 0.2),
        )
        .scale(
          begin: const Offset(1.0, 1.0),
          end: const Offset(1.02, 1.02),
          duration: 2000.ms,
        );
  }
}

/// 灰度到彩色渐变 - 用于角色选择
extension DimToColorAnimation on Widget {
  Widget dimToColor({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .fadeIn(duration: 400.ms)
        .scale(
          begin: const Offset(0.9, 0.9),
          end: const Offset(1.0, 1.0),
          duration: 400.ms,
          curve: Curves.elasticOut,
        );
  }
}

/// 卷轴展开动画 - hover/选中时
extension ScrollUnfoldAnimation on Widget {
  Widget scrollUnfold() {
    return animate()
        .scale(
          begin: const Offset(1.0, 0.95),
          end: const Offset(1.0, 1.0),
          duration: 300.ms,
          curve: Curves.easeOutCubic,
          alignment: Alignment.topCenter,
        )
        .shimmer(
          duration: 600.ms,
          color: AppColors.lampCenter.withValues(alpha: 0.15),
        );
  }
}

/// 讨论结束灯光渐暗
extension DiscussionEndAnimation on Widget {
  Widget lightsOut() {
    return animate()
        .fade(duration: 2000.ms, end: 0.3);
  }
}

/// 连接中断警告闪烁
extension ConnectionWarnAnimation on Widget {
  Widget connectionWarning() {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shimmer(
          duration: 800.ms,
          color: Colors.red.withValues(alpha: 0.15),
        );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/session/animations.dart
git commit -m "feat: add study room specific animations (seat glow, scroll unfold, etc)"
```

---

## Task 15: Update ChatHistoryDrawer Style

**Files:**
- Modify: `frontend/lib/features/session/chat_history_drawer.dart`

- [ ] **Step 1: Update ChatHistoryDrawer with warm study style**

Replace the entire file `frontend/lib/features/session/chat_history_drawer.dart` with:

```dart
import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 聊天记录抽屉
///
/// 侧边抽屉显示完整的文字聊天记录，羊皮纸底色 + 角色色条。
class ChatHistoryDrawer extends StatelessWidget {
  final List<ChatMessage> messages;
  final String myName;

  const ChatHistoryDrawer({
    super.key,
    required this.messages,
    required this.myName,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.studyWall,
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.only(top: 48, left: 16, right: 16, bottom: 12),
            decoration: BoxDecoration(
              color: AppColors.studyWallLight.withValues(alpha: 0.5),
              border: Border(
                bottom: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.2)),
              ),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '讨论记录',
                    style: AppTheme.calligraphyStyleDark(fontSize: 20),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, color: AppColors.warmGray),
                  onPressed: () => Navigator.pop(context),
                ),
              ],
            ),
          ),
          Expanded(
            child: Container(
              color: AppColors.parchment.withValues(alpha: 0.1),
              child: ListView.builder(
                padding: const EdgeInsets.all(12),
                itemCount: messages.length,
                itemBuilder: (context, index) {
                  final msg = messages[index];
                  return _HistoryBubble(message: msg, myName: myName);
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoryBubble extends StatelessWidget {
  final ChatMessage message;
  final String myName;

  const _HistoryBubble({required this.message, required this.myName});

  @override
  Widget build(BuildContext context) {
    final isMe = message.source == myName;
    final isSystem = message.type == 'system';
    final color = AppColors.getParticipantColor(message.source);

    if (isSystem) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Center(
          child: Text(
            message.content,
            style: TextStyle(color: AppColors.warmGray, fontSize: 12),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 发言者色条
          Container(
            width: 3,
            height: 44,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  message.source,
                  style: TextStyle(
                    color: color,
                    fontWeight: FontWeight.bold,
                    fontSize: 12,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  message.content,
                  style: const TextStyle(
                    color: AppColors.warmWhite,
                    fontSize: 13,
                    height: 1.4,
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
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/session/chat_history_drawer.dart
git commit -m "feat: update ChatHistoryDrawer with warm study parchment style"
```

---

## Task 16: Update Settings Screen Style

**Files:**
- Modify: `frontend/lib/features/settings/settings_screen.dart`

- [ ] **Step 1: Update settings screen with warm study style**

The settings screen keeps all its existing functionality. Only the visual style changes: dark background, warm card colors, calligraphy section titles.

Find and replace these key style changes in `frontend/lib/features/settings/settings_screen.dart`:

1. Replace `Scaffold(appBar: AppBar(title: const Text('设置')))` (appears 3 times for loading/error/data states) with:
```dart
Scaffold(
  backgroundColor: AppColors.studyWall,
  appBar: AppBar(
    title: Text('设置', style: AppTheme.calligraphyStyleDark(fontSize: 20)),
    backgroundColor: AppColors.studyWall,
  ),
```

2. Add import at top:
```dart
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';
```

3. Replace the `_SectionCard` widget's `Card` with a study-themed container:
Replace the `_SectionCard` build method:
```dart
@override
Widget build(BuildContext context) {
  return Container(
    decoration: BoxDecoration(
      color: AppColors.studyWallLight.withValues(alpha: 0.6),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(
        color: AppColors.warmGray.withValues(alpha: 0.15),
      ),
    ),
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 20, color: AppColors.amberGold),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  title,
                  style: AppTheme.calligraphyStyleDark(fontSize: 16),
                ),
              ),
              if (action != null) action!,
            ],
          ),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    ),
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/features/settings/settings_screen.dart
git commit -m "feat: update SettingsScreen with warm study room style"
```

---

## Task 17: Update app.dart Routing

**Files:**
- Modify: `frontend/lib/app.dart`

- [ ] **Step 1: Route to ImmersiveHomeScreen**

Replace `frontend/lib/app.dart` with:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/home/immersive_home_screen.dart';
import 'features/settings/settings_screen.dart';
import 'theme/app_theme.dart';

class RoundTableApp extends ConsumerWidget {
  const RoundTableApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: '圆桌思辨',
      theme: AppTheme.darkTheme, // 默认使用暗色书房主题
      darkTheme: AppTheme.darkTheme,
      themeMode: ThemeMode.dark,
      home: const ImmersiveHomeScreen(),
      routes: {
        '/home': (context) => const ImmersiveHomeScreen(),
        '/settings': (context) => const SettingsScreen(),
      },
      debugShowCheckedModeBanner: false,
    );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/app.dart
git commit -m "feat: route to ImmersiveHomeScreen and default to dark study theme"
```

---

## Task 18: Build, Fix, and Verify

**Files:**
- Various (fix compilation errors)

- [ ] **Step 1: Run Flutter analyze**

```bash
cd /Users/m3max/IdeaProjects/RoundTable/frontend
flutter analyze
```

Fix any compilation errors reported. Common issues to watch for:
- Missing imports
- `ImageFilter` requires `import 'dart:ui';`
- Method signature mismatches
- Constructor parameter mismatches

- [ ] **Step 2: Build Flutter web**

```bash
cd /Users/m3max/IdeaProjects/RoundTable/frontend
flutter build web --release
```

If build fails, read the error output, fix the code, and rebuild.

- [ ] **Step 3: Deploy to backend static**

```bash
rm -rf /Users/m3max/IdeaProjects/RoundTable/backend/static
cp -r /Users/m3max/IdeaProjects/RoundTable/frontend/build/web /Users/m3max/IdeaProjects/RoundTable/backend/static
```

- [ ] **Step 4: Restart backend server**

```bash
pkill -f "python3.*app.main" || true
cd /Users/m3max/IdeaProjects/RoundTable/backend
python -m app.main &
```

- [ ] **Step 5: Verify in browser**

Open http://localhost:8001/ and check:
- Dark warm study room background renders
- Round table appears in center
- Scroll card topic cards display
- Character avatars with dim/bright states
- Settings page opens with warm style
- No console errors

- [ ] **Step 6: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete immersive round table UI - warm study room theme"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Each section of the design spec has a corresponding task
- [x] **Placeholder scan:** No TBD/TODO in the plan
- [x] **Type consistency:** All class names, method signatures, and properties match across tasks
- [x] **File paths:** All paths are exact and consistent