/// RoundTable 扩展色板
///
/// 提供语义化颜色常量，用于沉浸式 UI 的发光、玻璃拟态、强调等效果。
library;

import 'package:flutter/material.dart';

class AppColors {
  AppColors._();

  // ── 主色 ──────────────────────────────────────────────────────────────────
  static const primary = Color(0xFF2196F3);
  static const secondary = Color(0xFF4CAF50);
  static const accent = Color(0xFFFFC107);

  // ── 书房暖色系 ──────────────────────────────────────────────────────────────
  static const studyWall = Color(0xFF2C1810);
  static const studyWallLight = Color(0xFF3E2723);
  static const tableWood = Color(0xFF8D6E63);
  static const tableWoodDark = Color(0xFF5D4037);
  static const tableWoodEdge = Color(0xFF4E342E);
  static const lampCenter = Color(0xFFFFE0B2);
  static const lampEdge = Color(0xFFFFB74D);
  static const amberGold = Color(0xFFFFB74D);
  static const warmGold = Color(0xFFFFC107);

  // ── 书房文字色 ──────────────────────────────────────────────────────────────
  static const warmWhite = Color(0xFFFFF8E1);
  static const warmGray = Color(0xFFBCAAA4);
  static const scrollTitle = Color(0xFF3E2723);

  // ── 书房材质 ──────────────────────────────────────────────────────────────
  static const parchment = Color(0xFFF5E6C8);
  static const parchmentDark = Color(0xFFE8D5B0);
  static const scrollGold = Color(0xFFD4A843);
  static const bookshelf = Color(0xFF1B0F07);

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
      case '可乐':
        return const Color(0xFFFFB74D); // 萌橘黄
      default:
        return glowBlue;
    }
  }

  static const tableSurface = Color(0xFF8D6E63);
  static const tableSurfaceDark = Color(0xFF4E342E);

  static const messageModerator = Color(0xFFE8EAF6);
  static const messageAI = Color(0xFFF5F5F5);
  static const messageHuman = Color(0xFFE3F2FD);
  static const messageSystem = Color(0xFFEEEEEE);

  static const messageModeratorDark = Color(0xFF311B92);
  static const messageAIDark = Color(0xFF263238);
  static const messageHumanDark = Color(0xFF0D47A1);
  static const messageSystemDark = Color(0xFF37474F);

  static const candleFlame = Color(0xFFFFE082);
  static const candleGlow = Color(0xFFFFCC80);
}
