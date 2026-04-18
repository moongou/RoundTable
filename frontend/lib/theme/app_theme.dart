import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'app_colors.dart';

class AppTheme {
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

  static BoxDecoration glassBoxDecoration({Color? tintColor}) {
    return BoxDecoration(
      color: (tintColor ?? Colors.white).withValues(alpha: 0.15),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: AppColors.borderGlass),
    );
  }

  static BoxDecoration glassBoxDecorationDark({Color? tintColor}) {
    return BoxDecoration(
      color: (tintColor ?? AppColors.studyWallLight).withValues(alpha: 0.6),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
    );
  }

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