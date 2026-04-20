import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/theme/app_theme.dart';

void main() {
  testWidgets('App shell renders with themes', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.lightTheme,
        darkTheme: AppTheme.darkTheme,
        home: const Scaffold(body: SizedBox.shrink()),
      ),
    );

    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
