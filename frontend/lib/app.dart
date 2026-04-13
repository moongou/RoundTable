import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/home/home_screen.dart';
import 'features/session/session_screen.dart';
import 'features/settings/settings_screen.dart';
import 'theme/app_theme.dart';

class RoundTableApp extends ConsumerWidget {
  const RoundTableApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: '圆桌思辨',
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: ThemeMode.system,
      home: const HomeScreen(),
      routes: {
        '/home': (context) => const HomeScreen(),
        '/session': (context) => const SessionScreen(),
        '/settings': (context) => const SettingsScreen(),
      },
      debugShowCheckedModeBanner: false,
    );
  }
}