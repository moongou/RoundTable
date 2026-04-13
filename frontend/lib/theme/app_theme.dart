import 'package:flutter/material.dart';

class AppTheme {
  // 圆桌思辨主色 - 温暖的蓝绿色，代表思考和交流
  static const _primaryColor = Color(0xFF2196F3);
  static const _secondaryColor = Color(0xFF4CAF50);
  static const _accentColor = Color(0xFFFFC107);

  static final lightTheme = ThemeData(
    useMaterial3: true,
    colorSchemeSeed: _primaryColor,
    brightness: Brightness.light,
    fontFamily: 'NotoSansSC',
    appBarTheme: const AppBarTheme(
      centerTitle: true,
      elevation: 0,
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
  );

  static final darkTheme = ThemeData(
    useMaterial3: true,
    colorSchemeSeed: _primaryColor,
    brightness: Brightness.dark,
    fontFamily: 'NotoSansSC',
    appBarTheme: const AppBarTheme(
      centerTitle: true,
      elevation: 0,
    ),
    cardTheme: CardThemeData(
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
    ),
  );
}