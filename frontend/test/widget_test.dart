import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:roundtable/app.dart';

void main() {
  testWidgets('App renders without error', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: RoundTableApp()));
    // Just verify the app renders - no crash
    await tester.pumpAndSettle();
  });
}