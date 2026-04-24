import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/services/saved_topics_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('load migrates legacy string topics into structured records', () async {
    SharedPreferences.setMockInitialValues({
      'saved_free_topics_v1': ['  该不该限制短视频  ', '学校是否该晚一点上学'],
    });

    final topics = await SavedTopicsStore.load();

    expect(topics.length, 2);
    expect(topics[0].title, '该不该限制短视频');
    expect(topics[0].savedAt, isNull);
    expect(topics[0].hasOriginalContent, isFalse);
  });

  test('add stores title original content and time together', () async {
    SharedPreferences.setMockInitialValues({});
    final savedAt = DateTime(2026, 4, 24, 9, 30);

    await SavedTopicsStore.add(
      '小学生该不该限制刷短视频？',
      originalContent: '我想讨论的是现在很多小学生放学以后会刷很久短视频，影响作业和睡觉。',
      savedAt: savedAt,
    );

    final topics = await SavedTopicsStore.load();

    expect(topics.length, 1);
    expect(topics.single.title, '小学生该不该限制刷短视频？');
    expect(topics.single.originalContent, '我想讨论的是现在很多小学生放学以后会刷很久短视频，影响作业和睡觉。');
    expect(topics.single.savedAt, savedAt);
  });
}
