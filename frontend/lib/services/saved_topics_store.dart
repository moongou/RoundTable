import 'package:shared_preferences/shared_preferences.dart';

/// 用户保存的自由话题本地存储。
/// 需求17（保存常用）+ 需求22（设置页与首页共享同一套数据）。
class SavedTopicsStore {
  static const _kKey = 'saved_free_topics_v1';

  static Future<List<String>> load() async {
    final sp = await SharedPreferences.getInstance();
    return sp.getStringList(_kKey) ?? <String>[];
  }

  static Future<void> save(List<String> topics) async {
    final sp = await SharedPreferences.getInstance();
    // 去重 + 去空 + 截断（最多保留 30 条）
    final cleaned = <String>[];
    for (final t in topics) {
      final s = t.trim();
      if (s.isEmpty) continue;
      if (cleaned.contains(s)) continue;
      cleaned.add(s);
      if (cleaned.length >= 30) break;
    }
    await sp.setStringList(_kKey, cleaned);
  }

  static Future<void> add(String topic) async {
    final cur = await load();
    if (cur.contains(topic.trim()) || topic.trim().isEmpty) return;
    cur.insert(0, topic.trim());
    await save(cur);
  }

  static Future<void> remove(String topic) async {
    final cur = await load();
    cur.removeWhere((t) => t == topic);
    await save(cur);
  }
}
