import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/home/immersive_home_screen.dart';
import 'features/replay/replay_screen.dart';
import 'features/settings/settings_screen.dart';
import 'theme/app_theme.dart';

class RoundTableApp extends ConsumerWidget {
  const RoundTableApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp(
      title: '圆桌思辨',
      theme: AppTheme.darkTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: ThemeMode.dark,
      home: const ImmersiveHomeScreen(),
      routes: {
        '/home': (context) => const ImmersiveHomeScreen(),
        '/settings': (context) => const SettingsScreen(),
        '/replay': (context) => const ReplayScreen(),
      },
      debugShowCheckedModeBanner: false,
    );
  }
}