import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

class SavedTopicItem {
  final String title;
  final String originalContent;
  final DateTime? savedAt;

  const SavedTopicItem({
    required this.title,
    this.originalContent = '',
    this.savedAt,
  });

  String get normalizedTitle => title.trim();

  String get normalizedOriginalContent => originalContent.trim();

  bool get hasOriginalContent {
    final normalized = normalizedOriginalContent;
    return normalized.isNotEmpty && normalized != normalizedTitle;
  }

  SavedTopicItem normalized() {
    return SavedTopicItem(
      title: normalizedTitle,
      originalContent: hasOriginalContent ? normalizedOriginalContent : '',
      savedAt: savedAt,
    );
  }

  Map<String, dynamic> toJson() => {
        'title': normalizedTitle,
        'original_content': hasOriginalContent ? normalizedOriginalContent : '',
        'saved_at': savedAt?.toIso8601String(),
      };

  factory SavedTopicItem.fromJson(Map<String, dynamic> json) {
    final savedAtRaw = (json['saved_at'] as String? ?? '').trim();
    return SavedTopicItem(
      title: (json['title'] as String? ?? '').trim(),
      originalContent: (json['original_content'] as String? ?? '').trim(),
      savedAt: savedAtRaw.isEmpty ? null : DateTime.tryParse(savedAtRaw),
    ).normalized();
  }

  factory SavedTopicItem.fromLegacyString(String raw) {
    return SavedTopicItem(title: raw.trim()).normalized();
  }
}

/// 用户保存的自由话题本地存储。
/// 需求17（保存常用）+ 需求22（设置页与首页共享同一套数据）。
class SavedTopicsStore {
  static const _kKey = 'saved_free_topics_v2';
  static const _kLegacyKey = 'saved_free_topics_v1';

  static Future<List<SavedTopicItem>> load() async {
    final sp = await SharedPreferences.getInstance();
    final raw = sp.getStringList(_kKey);
    if (raw != null) {
      return _decode(raw);
    }

    final legacy = sp.getStringList(_kLegacyKey) ?? <String>[];
    if (legacy.isEmpty) {
      return <SavedTopicItem>[];
    }

    final migrated = _sanitize(
      legacy.map(SavedTopicItem.fromLegacyString),
    );
    await save(migrated);
    return migrated;
  }

  static Future<void> save(List<SavedTopicItem> topics) async {
    final sp = await SharedPreferences.getInstance();
    final cleaned = _sanitize(topics);
    await sp.setStringList(
      _kKey,
      cleaned.map((item) => jsonEncode(item.toJson())).toList(),
    );
    await sp.remove(_kLegacyKey);
  }

  static Future<void> add(
    String topic, {
    String originalContent = '',
    DateTime? savedAt,
  }) async {
    final entry = SavedTopicItem(
      title: topic,
      originalContent: originalContent,
      savedAt: savedAt ?? DateTime.now(),
    ).normalized();
    if (entry.normalizedTitle.isEmpty) {
      return;
    }

    final cur = await load();
    cur.removeWhere(
      (item) =>
          item.normalizedTitle.toLowerCase() ==
          entry.normalizedTitle.toLowerCase(),
    );
    cur.insert(0, entry);
    await save(cur);
  }

  static Future<void> remove(SavedTopicItem topic) async {
    final cur = await load();
    cur.removeWhere((item) => _isSameTopic(item, topic));
    await save(cur);
  }

  static List<SavedTopicItem> _decode(List<String> raw) {
    return _sanitize(
      raw.map(_decodeItem).whereType<SavedTopicItem>(),
    );
  }

  static SavedTopicItem? _decodeItem(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) {
      return null;
    }

    if (trimmed.startsWith('{')) {
      try {
        final decoded = jsonDecode(trimmed);
        if (decoded is Map<String, dynamic>) {
          return SavedTopicItem.fromJson(decoded);
        }
        if (decoded is Map) {
          return SavedTopicItem.fromJson(Map<String, dynamic>.from(decoded));
        }
      } catch (_) {}
    }

    return SavedTopicItem.fromLegacyString(trimmed);
  }

  static List<SavedTopicItem> _sanitize(Iterable<SavedTopicItem> topics) {
    final cleaned = <SavedTopicItem>[];
    final seen = <String>{};
    for (final topic in topics) {
      final normalized = topic.normalized();
      final title = normalized.normalizedTitle;
      if (title.isEmpty) {
        continue;
      }
      final key = title.toLowerCase();
      if (seen.contains(key)) {
        continue;
      }
      seen.add(key);
      cleaned.add(normalized);
      if (cleaned.length >= 30) {
        break;
      }
    }
    return cleaned;
  }

  static bool _isSameTopic(SavedTopicItem left, SavedTopicItem right) {
    final leftSavedAt = left.savedAt?.toIso8601String();
    final rightSavedAt = right.savedAt?.toIso8601String();
    if (leftSavedAt != null && rightSavedAt != null) {
      return leftSavedAt == rightSavedAt;
    }
    return left.normalizedTitle == right.normalizedTitle &&
        left.normalizedOriginalContent == right.normalizedOriginalContent;
  }
}
