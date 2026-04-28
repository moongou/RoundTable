import 'dart:convert';

// 配置相关数据模型，与后端 config API 对齐

const String teacherVoiceSpeaker = '李老师';
const String thinkerVoiceSpeaker = '思想家';
const String voiceRoleTeacher = 'teacher';
const String voiceRoleThinker = 'thinker';
const String voiceRoleStudentMale = 'student_male';
const String voiceRoleStudentFemale = 'student_female';

const List<String> maleStudentVoiceSpeakers = <String>[
  '小探',
  '小和',
  '小明',
  '小思',
  '小理',
  '小行',
  '可乐',
];

const List<String> femaleStudentVoiceSpeakers = <String>[
  '小疑',
  '小说',
  '小爱',
  '小想',
];

const List<String> configurableVoiceSpeakers = <String>[
  teacherVoiceSpeaker,
  ...maleStudentVoiceSpeakers,
  ...femaleStudentVoiceSpeakers,
  thinkerVoiceSpeaker,
];

String voiceAssignmentStorageKey({
  required String providerId,
  required String speaker,
}) {
  return '${providerId.trim()}::${speaker.trim()}';
}

Map<String, String> decodeStoredVoiceAssignments(String? rawJson) {
  if (rawJson == null || rawJson.trim().isEmpty) {
    return const {};
  }

  try {
    final decoded = jsonDecode(rawJson);
    if (decoded is! Map) {
      return const {};
    }
    final normalized = <String, String>{};
    decoded.forEach((key, value) {
      final normalizedKey = key.toString().trim();
      final normalizedValue = value?.toString().trim() ?? '';
      if (normalizedKey.isEmpty || normalizedValue.isEmpty) {
        return;
      }
      normalized[normalizedKey] = normalizedValue;
    });
    return normalized;
  } catch (_) {
    return const {};
  }
}

String encodeStoredVoiceAssignments(Map<String, String> assignments) {
  final sanitized = <String, String>{};
  assignments.forEach((key, value) {
    final normalizedKey = key.trim();
    final normalizedValue = value.trim();
    if (normalizedKey.isEmpty || normalizedValue.isEmpty) {
      return;
    }
    sanitized[normalizedKey] = normalizedValue;
  });
  return jsonEncode(sanitized);
}

class VoicePreset {
  final String voice;
  final String label;
  final String role;
  final String note;

  const VoicePreset({
    required this.voice,
    required this.label,
    required this.role,
    this.note = '',
  });
}

class VoiceServicePalette {
  final String serviceId;
  final String title;
  final String summary;
  final int targetPresetCount;
  final List<VoicePreset> presets;
  final Map<String, String> defaultAssignments;
  final String note;

  const VoiceServicePalette({
    required this.serviceId,
    required this.title,
    required this.summary,
    required this.targetPresetCount,
    required this.presets,
    required this.defaultAssignments,
    this.note = '',
  });

  List<VoicePreset> presetsForRole(String role) {
    return presets.where((preset) => preset.role == role).toList();
  }

  VoicePreset? presetForVoice(String voice) {
    final normalized = voice.trim();
    for (final preset in presets) {
      if (preset.voice == normalized) {
        return preset;
      }
    }
    return null;
  }
}

final Map<String, VoiceServicePalette> kVoiceServicePalettes =
    <String, VoiceServicePalette>{
  'edge_tts': VoiceServicePalette(
    serviceId: 'edge_tts',
    title: 'Edge TTS 音色库',
    summary: '大陆中文语音为主，保留当前老师与学生的熟悉听感。',
    targetPresetCount: 25,
    note: 'Edge 预设不依赖当前测试接口返回的 models 列表，直接使用已知可用的 Azure Neural voice id。',
    presets: const <VoicePreset>[
      VoicePreset(
        voice: 'zh-CN-YunxiNeural',
        label: '童声男 01 · 云溪',
        role: voiceRoleStudentMale,
        note: '轻快、明亮，适合探索型发言。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunhaoNeural',
        label: '童声男 02 · 云浩',
        role: voiceRoleStudentMale,
        note: '稳一点，适合解释型角色。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunjieNeural',
        label: '童声男 03 · 云杰',
        role: voiceRoleStudentMale,
        note: '更像课堂里主动举手的男生。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunxiaNeural',
        label: '童声男 04 · 云夏',
        role: voiceRoleStudentMale,
        note: '偏柔和，适合慢一点的表达。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunyangNeural',
        label: '童声男 05 · 云扬',
        role: voiceRoleStudentMale,
        note: '当前默认活泼男声基线。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunfengNeural',
        label: '童声男 06 · 云峰',
        role: voiceRoleStudentMale,
        note: '更沉稳，适合理性型角色。',
      ),
      VoicePreset(
        voice: 'zh-TW-YunJheNeural',
        label: '童声男 07 · 云哲',
        role: voiceRoleStudentMale,
        note: '口型圆润，适合备用男童声。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoyiNeural',
        label: '童声女 01 · 小伊',
        role: voiceRoleStudentFemale,
        note: '当前默认女童声基线。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaohanNeural',
        label: '童声女 02 · 小涵',
        role: voiceRoleStudentFemale,
        note: '更清亮，适合讲故事。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaomengNeural',
        label: '童声女 03 · 小萌',
        role: voiceRoleStudentFemale,
        note: '轻柔、亲近。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaomoNeural',
        label: '童声女 04 · 小墨',
        role: voiceRoleStudentFemale,
        note: '吐字干净，适合理性表达。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoqiuNeural',
        label: '童声女 05 · 小秋',
        role: voiceRoleStudentFemale,
        note: '温柔，适合共情型角色。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoruiNeural',
        label: '童声女 06 · 小蕊',
        role: voiceRoleStudentFemale,
        note: '响度稳定，适合高频长对话。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoshuangNeural',
        label: '童声女 07 · 小霜',
        role: voiceRoleStudentFemale,
        note: '清脆、有辨识度。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoxuanNeural',
        label: '童声女 08 · 小萱',
        role: voiceRoleStudentFemale,
        note: '当前创新型角色默认基线。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoxiaoNeural',
        label: '老师 01 · 小晓',
        role: voiceRoleTeacher,
        note: '当前老师音色基线。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoyanNeural',
        label: '老师 02 · 小颜',
        role: voiceRoleTeacher,
        note: '更温暖，适合引导式口吻。',
      ),
      VoicePreset(
        voice: 'zh-CN-XiaoyouNeural',
        label: '老师 03 · 小悠',
        role: voiceRoleTeacher,
        note: '成熟度更高，适合课堂总结。',
      ),
      VoicePreset(
        voice: 'zh-HK-HiuGaaiNeural',
        label: '老师 04 · 晓佳',
        role: voiceRoleTeacher,
        note: '港区女声，音色更透亮。',
      ),
      VoicePreset(
        voice: 'zh-HK-HiuMaanNeural',
        label: '老师 05 · 晓雯',
        role: voiceRoleTeacher,
        note: '更柔和，适合耐心解释。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunzeNeural',
        label: '思想家 01 · 云泽',
        role: voiceRoleThinker,
        note: '当前思想家默认基线。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunjianNeural',
        label: '思想家 02 · 云谏',
        role: voiceRoleThinker,
        note: '更利落，适合短句判断。',
      ),
      VoicePreset(
        voice: 'zh-HK-WanLungNeural',
        label: '思想家 03 · 云朗',
        role: voiceRoleThinker,
        note: '偏低沉，适合金句朗读。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunyiNeural',
        label: '思想家 04 · 云逸',
        role: voiceRoleThinker,
        note: '更平稳，适合分析长句。',
      ),
      VoicePreset(
        voice: 'zh-CN-YunxiaNeural',
        label: '思想家 05 · 云遐',
        role: voiceRoleThinker,
        note: '保守备用，声音更安静。',
      ),
    ],
    defaultAssignments: const <String, String>{
      teacherVoiceSpeaker: 'zh-CN-XiaoxiaoNeural',
      '小探': 'zh-CN-YunxiNeural',
      '小疑': 'zh-CN-XiaoyiNeural',
      '小和': 'zh-CN-YunhaoNeural',
      '小说': 'zh-CN-XiaohanNeural',
      '小明': 'zh-CN-YunjieNeural',
      '小思': 'zh-CN-YunxiaNeural',
      '小理': 'zh-CN-YunyangNeural',
      '小爱': 'zh-CN-XiaoyouNeural',
      '小想': 'zh-CN-XiaoxuanNeural',
      '小行': 'zh-CN-YunfengNeural',
      '可乐': 'zh-CN-YunxiNeural',
      thinkerVoiceSpeaker: 'zh-CN-YunzeNeural',
    },
  ),
  'chattts': VoiceServicePalette(
    serviceId: 'chattts',
    title: 'ChatTTS 音色库',
    summary: '使用稳定 seed 命名，确保本地 WebUI 每次生成同一人格。',
    targetPresetCount: 25,
    presets: const <VoicePreset>[
      VoicePreset(
        voice: 'chattts-kite-boy',
        label: '童声男 01 · 风筝',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-lake-boy',
        label: '童声男 02 · 湖岸',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-pine-boy',
        label: '童声男 03 · 松针',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-river-boy',
        label: '童声男 04 · 河畔',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-ember-boy',
        label: '童声男 05 · 火苗',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-orbit-boy',
        label: '童声男 06 · 轨迹',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-stone-boy',
        label: '童声男 07 · 石阶',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'chattts-cloud-girl',
        label: '童声女 01 · 云朵',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-mint-girl',
        label: '童声女 02 · 薄荷',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-pearl-girl',
        label: '童声女 03 · 珍珠',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-spark-girl',
        label: '童声女 04 · 星火',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-berry-girl',
        label: '童声女 05 · 莓果',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-moon-girl',
        label: '童声女 06 · 月白',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-coral-girl',
        label: '童声女 07 · 珊瑚',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-lotus-girl',
        label: '童声女 08 · 青莲',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'chattts-teacher-amber',
        label: '老师 01 · 琥珀',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'chattts-teacher-cedar',
        label: '老师 02 · 雪松',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'chattts-teacher-iris',
        label: '老师 03 · 鸢尾',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'chattts-teacher-harbor',
        label: '老师 04 · 海港',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'chattts-teacher-violet',
        label: '老师 05 · 紫藤',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'chattts-thinker-ink',
        label: '思想家 01 · 墨色',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'chattts-thinker-slate',
        label: '思想家 02 · 岩板',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'chattts-thinker-bronze',
        label: '思想家 03 · 青铜',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'chattts-thinker-nocturne',
        label: '思想家 04 · 夜曲',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'chattts-thinker-ridge',
        label: '思想家 05 · 山脊',
        role: voiceRoleThinker,
      ),
    ],
    defaultAssignments: const <String, String>{
      teacherVoiceSpeaker: 'chattts-teacher-amber',
      '小探': 'chattts-kite-boy',
      '小疑': 'chattts-cloud-girl',
      '小和': 'chattts-lake-boy',
      '小说': 'chattts-mint-girl',
      '小明': 'chattts-pine-boy',
      '小思': 'chattts-river-boy',
      '小理': 'chattts-ember-boy',
      '小爱': 'chattts-pearl-girl',
      '小想': 'chattts-spark-girl',
      '小行': 'chattts-orbit-boy',
      '可乐': 'chattts-kite-boy',
      thinkerVoiceSpeaker: 'chattts-thinker-ink',
    },
  ),
  'vibevoice': VoiceServicePalette(
    serviceId: 'vibevoice',
    title: 'VibeVoice 音色库',
    summary: '直接映射本地 `.pt` 预设，当前机器上可用 25 组。',
    targetPresetCount: 25,
    presets: const <VoicePreset>[
      VoicePreset(
        voice: 'en-carter_man',
        label: '童声男 01 · Carter',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'en-davis_man',
        label: '童声男 02 · Davis',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'en-frank_man',
        label: '童声男 03 · Frank',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'de-spk0_man',
        label: '童声男 04 · 德语 Spk0',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'in-samuel_man',
        label: '童声男 05 · Samuel',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'jp-spk0_man',
        label: '童声男 06 · 日语 Spk0',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'it-spk1_man',
        label: '童声男 07 · 意语 Spk1',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'en-emma_woman',
        label: '童声女 01 · Emma',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'en-grace_woman',
        label: '童声女 02 · Grace',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'de-spk1_woman',
        label: '童声女 03 · 德语 Spk1',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'fr-spk1_woman',
        label: '童声女 04 · 法语 Spk1',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'kr-spk0_woman',
        label: '童声女 05 · 韩语 Spk0',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'nl-spk1_woman',
        label: '童声女 06 · 荷语 Spk1',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'pt-spk0_woman',
        label: '童声女 07 · 葡语 Spk0',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'jp-spk1_woman',
        label: '童声女 08 · 日语 Spk1',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'it-spk0_woman',
        label: '老师 01 · 意语 Spk0',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'pl-spk1_woman',
        label: '老师 02 · 波兰语 Spk1',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'sp-spk0_woman',
        label: '老师 03 · 西语 Spk0',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'pt-spk1_man',
        label: '老师 04 · 葡语 Spk1',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'sp-spk1_man',
        label: '老师 05 · 西语 Spk1',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'en-mike_man',
        label: '思想家 01 · Mike',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'fr-spk0_man',
        label: '思想家 02 · 法语 Spk0',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'kr-spk1_man',
        label: '思想家 03 · 韩语 Spk1',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'nl-spk0_man',
        label: '思想家 04 · 荷语 Spk0',
        role: voiceRoleThinker,
      ),
      VoicePreset(
        voice: 'pl-spk0_man',
        label: '思想家 05 · 波兰语 Spk0',
        role: voiceRoleThinker,
      ),
    ],
    defaultAssignments: const <String, String>{
      teacherVoiceSpeaker: 'it-spk0_woman',
      '小探': 'en-carter_man',
      '小疑': 'en-emma_woman',
      '小和': 'en-davis_man',
      '小说': 'en-grace_woman',
      '小明': 'en-frank_man',
      '小思': 'de-spk0_man',
      '小理': 'in-samuel_man',
      '小爱': 'de-spk1_woman',
      '小想': 'fr-spk1_woman',
      '小行': 'jp-spk0_man',
      '可乐': 'en-carter_man',
      thinkerVoiceSpeaker: 'en-mike_man',
    },
  ),
  'openvoice': VoiceServicePalette(
    serviceId: 'openvoice',
    title: 'OpenVoice 音色库',
    summary: '当前仓库内真实可用的克隆参考音频共 12 组，先按素材上限展示。',
    targetPresetCount: 25,
    note: 'OpenVoice 受参考音频素材数量限制，现阶段无法无损扩展到 25 个真实克隆音色。',
    presets: const <VoicePreset>[
      VoicePreset(
        voice: 'ov:student_xiaotan',
        label: '童声男 01 · 小探',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaohe',
        label: '童声男 02 · 小和',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoming',
        label: '童声男 03 · 小明',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaosi',
        label: '童声男 04 · 小思',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoli',
        label: '童声男 05 · 小理',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoxing',
        label: '童声男 06 · 小行',
        role: voiceRoleStudentMale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoyi',
        label: '童声女 01 · 小疑',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoshuo',
        label: '童声女 02 · 小说',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoai',
        label: '童声女 03 · 小爱',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'ov:student_xiaoxiang',
        label: '童声女 04 · 小想',
        role: voiceRoleStudentFemale,
      ),
      VoicePreset(
        voice: 'ov:teacher_li',
        label: '老师 01 · 李老师',
        role: voiceRoleTeacher,
      ),
      VoicePreset(
        voice: 'ov:thinker_elder',
        label: '思想家 01 · 长者',
        role: voiceRoleThinker,
      ),
    ],
    defaultAssignments: const <String, String>{
      teacherVoiceSpeaker: 'ov:teacher_li',
      '小探': 'ov:student_xiaotan',
      '小疑': 'ov:student_xiaoyi',
      '小和': 'ov:student_xiaohe',
      '小说': 'ov:student_xiaoshuo',
      '小明': 'ov:student_xiaoming',
      '小思': 'ov:student_xiaosi',
      '小理': 'ov:student_xiaoli',
      '小爱': 'ov:student_xiaoai',
      '小想': 'ov:student_xiaoxiang',
      '小行': 'ov:student_xiaoxing',
      '可乐': 'ov:student_xiaotan',
      thinkerVoiceSpeaker: 'ov:thinker_elder',
    },
  ),
};

VoiceServicePalette? voiceServicePalette(String providerId) {
  return kVoiceServicePalettes[providerId.trim()];
}

Map<String, String> defaultVoiceAssignmentsForProvider(String providerId) {
  return voiceServicePalette(providerId)?.defaultAssignments ?? const {};
}

/// LLM 提供商信息
class ProviderInfo {
  final String id;
  final String name;
  final String baseUrl;
  final String model;
  final bool hasApiKey;
  final bool isActive;
  final bool needsApiKey;

  const ProviderInfo({
    required this.id,
    required this.name,
    required this.baseUrl,
    required this.model,
    required this.hasApiKey,
    required this.isActive,
    required this.needsApiKey,
  });

  factory ProviderInfo.fromJson(Map<String, dynamic> json) => ProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        baseUrl: json['base_url'] as String? ?? '',
        model: json['model'] as String? ?? '',
        hasApiKey: json['has_api_key'] as bool? ?? false,
        isActive: json['is_active'] as bool? ?? false,
        needsApiKey: json['needs_api_key'] as bool? ?? true,
      );

  /// 提供商图标/emoji
  String get icon {
    switch (id) {
      case 'openai':
        return '🌐';
      case 'qwen':
        return '🔮';
      case 'deepseek':
        return '🔍';
      case 'siliconflow':
        return '🌊';
      case 'ollama':
        return '🦙';
      case 'ollama_cloud':
        return '☁️';
      case 'doubao':
        return '🫘';
      case 'volcengine':
        return '🌋';
      case 'bailian':
        return '🔥';
      case 'zhipu':
        return '🧠';
      case 'anthropic':
        return '🤖';
      case 'gemini':
        return '💎';
      default:
        return '🤖';
    }
  }

  /// 是否为本地部署（不需要云端 API Key）
  bool get isLocal => id == 'ollama';
}

/// 提供商连接测试结果
class ProviderTestResult {
  final bool success;
  final List<String> models;
  final String? error;

  const ProviderTestResult({
    required this.success,
    required this.models,
    this.error,
  });

  factory ProviderTestResult.fromJson(Map<String, dynamic> json) =>
      ProviderTestResult(
        success: json['success'] as bool? ?? false,
        models: List<String>.from(json['models'] as List? ?? []),
        error: json['error'] as String?,
      );
}

/// 语音识别（ASR）提供商
class SpeechProviderInfo {
  final String id;
  final String name;
  final bool isActive;
  final bool available;
  final String url;
  final String defaultUrl;
  final bool needsApiKey;
  final bool hasApiKey;
  final String model;
  final String defaultModel;
  final String voice;
  final String defaultVoice;
  final String mode;

  const SpeechProviderInfo({
    required this.id,
    required this.name,
    required this.isActive,
    this.available = false,
    this.url = '',
    this.defaultUrl = '',
    this.needsApiKey = false,
    this.hasApiKey = false,
    this.model = '',
    this.defaultModel = '',
    this.voice = '',
    this.defaultVoice = '',
    this.mode = 'local',
  });

  factory SpeechProviderInfo.fromJson(Map<String, dynamic> json) =>
      SpeechProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        isActive: json['is_active'] as bool? ?? false,
        available: json['available'] as bool? ?? false,
        url: json['url'] as String? ?? '',
        defaultUrl: json['default_url'] as String? ?? '',
        needsApiKey: json['needs_api_key'] as bool? ?? false,
        hasApiKey: json['has_api_key'] as bool? ?? false,
        model: json['model'] as String? ?? '',
        defaultModel: json['default_model'] as String? ?? '',
        voice: json['voice'] as String? ?? '',
        defaultVoice: json['default_voice'] as String? ?? '',
        mode: json['mode'] as String? ?? 'local',
      );

  bool get isCloud => mode == 'cloud';

  bool get isMainlandPreferred {
    switch (id) {
      case 'siliconflow_asr':
      case 'siliconflow_tts':
        return true;
      default:
        return !isCloud;
    }
  }

  String get icon {
    switch (id) {
      case 'browser':
        return '🌐';
      case 'funasr':
        return '🎙️';
      case 'capswriter':
        return '⌨️';
      case 'vosk':
        return '📡';
      case 'chattts':
        return '💬';
      case 'edge_tts':
        return '🗣️';
      case 'openvoice':
        return '🧬';
      case 'cosyvoice':
        return '🎵';
      case 'siliconflow_asr':
      case 'siliconflow_tts':
        return '🌊';
      case 'openai_whisper':
      case 'openai_tts':
        return '🌐';
      case 'groq_whisper':
        return '⚡';
      case 'disabled':
        return '⏸️';
      default:
        return isCloud ? '☁️' : '🎧';
    }
  }
}

/// 语音服务连接测试结果
class VoiceServiceTestResult {
  final bool success;
  final List<String> voices;
  final List<String> models;
  final String? error;
  final String url;
  final int? statusCode;
  final double? latencyMs;
  final String requestedModel;
  final bool modelValid;
  final String voiceUsed;

  const VoiceServiceTestResult({
    required this.success,
    required this.voices,
    this.models = const [],
    required this.url,
    this.statusCode,
    this.latencyMs,
    this.requestedModel = '',
    this.modelValid = true,
    this.voiceUsed = '',
    this.error,
  });

  factory VoiceServiceTestResult.fromJson(Map<String, dynamic> json) =>
      VoiceServiceTestResult(
        success: json['success'] as bool? ?? false,
        voices: List<String>.from(json['voices'] as List? ?? []),
        models: List<String>.from(json['models'] as List? ?? []),
        url: json['url'] as String? ?? '',
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
        requestedModel: json['requested_model'] as String? ?? '',
        modelValid: json['model_valid'] as bool? ?? true,
        voiceUsed: json['voice_used'] as String? ?? '',
        error: json['error'] as String?,
      );
}

/// 语音配置（包含 ASR/TTS 提供商列表和 push_to_talk）
class SpeechConfig {
  final List<SpeechProviderInfo> asrProviders;
  final List<SpeechProviderInfo> ttsProviders;
  final bool pushToTalk;
  final String ttsVoice;
  final String cosyvoiceVoice;

  const SpeechConfig({
    required this.asrProviders,
    required this.ttsProviders,
    required this.pushToTalk,
    this.ttsVoice = 'zh-CN-XiaoxiaoNeural',
    this.cosyvoiceVoice = 'default',
  });

  factory SpeechConfig.fromJson(Map<String, dynamic> json) => SpeechConfig(
        asrProviders: (json['asr'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        ttsProviders: (json['tts'] as List)
            .map((e) => SpeechProviderInfo.fromJson(e as Map<String, dynamic>))
            .toList(),
        pushToTalk: json['push_to_talk'] as bool? ?? true,
        ttsVoice: json['tts_voice'] as String? ?? 'zh-CN-XiaoxiaoNeural',
        cosyvoiceVoice: json['cosyvoice_voice'] as String? ?? 'default',
      );
}

String? resolveConfiguredVoiceForSpeaker({
  required LocalSettings? settings,
  required String providerId,
  required String speaker,
}) {
  final normalizedSpeaker = speaker.trim();
  if (normalizedSpeaker.isEmpty) {
    return null;
  }

  final configured = settings?.resolveVoiceAssignment(
    providerId: providerId,
    speaker: normalizedSpeaker,
  );
  if (configured != null && configured.isNotEmpty) {
    return configured;
  }

  final defaults = defaultVoiceAssignmentsForProvider(providerId);
  final fallback = defaults[normalizedSpeaker]?.trim();
  if (fallback != null && fallback.isNotEmpty) {
    return fallback;
  }
  return null;
}

/// 本地服务健康状态
class ServiceHealth {
  final String name;
  final String url;
  final bool reachable;
  final int? statusCode;
  final double? latencyMs;
  final String? detail;

  const ServiceHealth({
    required this.name,
    required this.url,
    required this.reachable,
    this.statusCode,
    this.latencyMs,
    this.detail,
  });

  factory ServiceHealth.fromJson(String name, Map<String, dynamic> json) =>
      ServiceHealth(
        name: name,
        url: json['url'] as String? ?? '',
        reachable: json['reachable'] as bool? ?? false,
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
        detail: json['detail'] as String?,
      );
}

class ValidationCheck {
  final String name;
  final bool ok;
  final String detail;
  final int? statusCode;
  final double? latencyMs;

  const ValidationCheck({
    required this.name,
    required this.ok,
    required this.detail,
    this.statusCode,
    this.latencyMs,
  });

  factory ValidationCheck.fromJson(Map<String, dynamic> json) =>
      ValidationCheck(
        name: json['name'] as String? ?? '未命名项目',
        ok: json['ok'] as bool? ?? false,
        detail: json['detail'] as String? ?? '',
        statusCode: json['status_code'] as int?,
        latencyMs: (json['latency_ms'] as num?)?.toDouble(),
      );
}

/// 当前生效配置
class CurrentConfig {
  final String llmProvider;
  final String llmProviderName;
  final String apiKeyMasked;
  final String model;
  final String asrProvider;
  final String ttsProvider;
  final bool pushToTalk;
  final bool webSearchEnabled;
  final bool tavilyConfigured;

  const CurrentConfig({
    required this.llmProvider,
    required this.llmProviderName,
    required this.apiKeyMasked,
    required this.model,
    required this.asrProvider,
    required this.ttsProvider,
    required this.pushToTalk,
    this.webSearchEnabled = false,
    this.tavilyConfigured = false,
  });

  factory CurrentConfig.fromJson(Map<String, dynamic> json) => CurrentConfig(
        llmProvider: json['llm_provider'] as String,
        llmProviderName: json['llm_provider_name'] as String,
        apiKeyMasked: json['api_key_masked'] as String? ?? '',
        model: json['model'] as String,
        asrProvider: json['asr_provider'] as String? ?? 'funasr',
        ttsProvider: json['tts_provider'] as String? ?? 'edge_tts',
        pushToTalk: json['push_to_talk'] as bool? ?? true,
        webSearchEnabled: json['web_search_enabled'] as bool? ?? false,
        tavilyConfigured: json['tavily_configured'] as bool? ?? false,
      );
}

/// 配置验证结果
class ConfigValidation {
  final bool valid;
  final String message;
  final List<ValidationCheck> checks;

  const ConfigValidation({
    required this.valid,
    required this.message,
    this.checks = const [],
  });

  factory ConfigValidation.fromJson(Map<String, dynamic> json) =>
      ConfigValidation(
        valid: json['valid'] as bool,
        message: json['message'] as String,
        checks: (json['checks'] as List<dynamic>? ?? const [])
            .whereType<Map>()
            .map((e) => ValidationCheck.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
      );
}

class SavedConfigProfile {
  final String profileId;
  final String name;
  final String description;
  final String createdAt;
  final String updatedAt;
  final String llmProvider;
  final String model;
  final String asrProvider;
  final String ttsProvider;
  final bool hasLocalSettings;

  const SavedConfigProfile({
    required this.profileId,
    required this.name,
    this.description = '',
    this.createdAt = '',
    this.updatedAt = '',
    this.llmProvider = '',
    this.model = '',
    this.asrProvider = '',
    this.ttsProvider = '',
    this.hasLocalSettings = false,
  });

  factory SavedConfigProfile.fromJson(Map<String, dynamic> json) =>
      SavedConfigProfile(
        profileId: json['profile_id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        description: json['description'] as String? ?? '',
        createdAt: json['created_at'] as String? ?? '',
        updatedAt: json['updated_at'] as String? ?? '',
        llmProvider: json['llm_provider'] as String? ?? '',
        model: json['model'] as String? ?? '',
        asrProvider: json['asr_provider'] as String? ?? '',
        ttsProvider: json['tts_provider'] as String? ?? '',
        hasLocalSettings: json['has_local_settings'] as bool? ?? false,
      );
}

/// 本地持久化设置（保存在 SharedPreferences）
class LocalSettings {
  final String serverUrl;
  final String llmProvider;
  final String asrProvider;
  final String ttsProvider;
  final Map<String, String> ttsVoiceAssignments;
  final bool pushToTalk;
  final bool streamUserSubtitles;
  final bool asrStreamingEnabled;
  final String micActivationMode;
  final String micControlMode;

  /// 麦克风热键标识：
  /// 'right_alt' / 'left_alt' / 'any_alt' / 'f12' / 'right_ctrl' / 'left_ctrl' / 'space'
  /// 默认 macOS = 'right_alt' (Right Option)，其他平台 = 'f12'。
  final String micHotkey;

  const LocalSettings({
    this.serverUrl = 'http://localhost:8001',
    this.llmProvider = 'openai',
    this.asrProvider = 'funasr',
    this.ttsProvider = 'edge_tts',
    this.ttsVoiceAssignments = const {},
    this.pushToTalk = true,
    this.streamUserSubtitles = true,
    this.asrStreamingEnabled = true,
    this.micActivationMode = 'manual',
    this.micControlMode = 'hold_ctrl',
    this.micHotkey = 'right_alt',
  });

  LocalSettings copyWith({
    String? serverUrl,
    String? llmProvider,
    String? asrProvider,
    String? ttsProvider,
    Map<String, String>? ttsVoiceAssignments,
    bool? pushToTalk,
    bool? streamUserSubtitles,
    bool? asrStreamingEnabled,
    String? micActivationMode,
    String? micControlMode,
    String? micHotkey,
  }) =>
      LocalSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        llmProvider: llmProvider ?? this.llmProvider,
        asrProvider: asrProvider ?? this.asrProvider,
        ttsProvider: ttsProvider ?? this.ttsProvider,
        ttsVoiceAssignments: ttsVoiceAssignments ?? this.ttsVoiceAssignments,
        pushToTalk: pushToTalk ?? this.pushToTalk,
        streamUserSubtitles: streamUserSubtitles ?? this.streamUserSubtitles,
        asrStreamingEnabled: asrStreamingEnabled ?? this.asrStreamingEnabled,
        micActivationMode: micActivationMode ?? this.micActivationMode,
        micControlMode: micControlMode ?? this.micControlMode,
        micHotkey: micHotkey ?? this.micHotkey,
      );

  String? resolveVoiceAssignment({
    required String providerId,
    required String speaker,
  }) {
    final key = voiceAssignmentStorageKey(
      providerId: providerId,
      speaker: speaker,
    );
    final raw = ttsVoiceAssignments[key]?.trim();
    if (raw == null || raw.isEmpty) {
      return null;
    }
    return raw;
  }

  Map<String, String> assignmentsForProvider(String providerId) {
    final prefix = '${providerId.trim()}::';
    final filtered = <String, String>{};
    ttsVoiceAssignments.forEach((key, value) {
      if (!key.startsWith(prefix)) {
        return;
      }
      filtered[key.substring(prefix.length)] = value;
    });
    return filtered;
  }

  LocalSettings withVoiceAssignment({
    required String providerId,
    required String speaker,
    required String voice,
  }) {
    final next = Map<String, String>.from(ttsVoiceAssignments);
    final key = voiceAssignmentStorageKey(
      providerId: providerId,
      speaker: speaker,
    );
    final normalizedVoice = voice.trim();
    if (normalizedVoice.isEmpty) {
      next.remove(key);
    } else {
      next[key] = normalizedVoice;
    }
    return copyWith(ttsVoiceAssignments: next);
  }

  LocalSettings resetVoiceAssignmentsForProvider(String providerId) {
    final prefix = '${providerId.trim()}::';
    final next = Map<String, String>.from(ttsVoiceAssignments)
      ..removeWhere((key, _) => key.startsWith(prefix));
    return copyWith(ttsVoiceAssignments: next);
  }

  Map<String, dynamic> toJson() => {
        'server_url': serverUrl,
        'llm_provider': llmProvider,
        'asr_provider': asrProvider,
        'tts_provider': ttsProvider,
        'tts_voice_assignments': ttsVoiceAssignments,
        'push_to_talk': pushToTalk,
        'stream_user_subtitles': streamUserSubtitles,
        'asr_streaming_enabled': asrStreamingEnabled,
        'mic_activation_mode': micActivationMode,
        'mic_control_mode': micControlMode,
        'mic_hotkey': micHotkey,
      };
}

/// 网络搜索配置
class WebSearchConfig {
  final bool enabled;
  final bool hasApiKey;
  final String apiKeyMasked;
  final String baseUrl;

  const WebSearchConfig({
    required this.enabled,
    required this.hasApiKey,
    required this.apiKeyMasked,
    required this.baseUrl,
  });

  factory WebSearchConfig.fromJson(Map<String, dynamic> json) =>
      WebSearchConfig(
        enabled: json['enabled'] as bool? ?? false,
        hasApiKey: json['has_api_key'] as bool? ?? false,
        apiKeyMasked: json['api_key_masked'] as String? ?? '',
        baseUrl: json['base_url'] as String? ?? 'https://api.tavily.com',
      );
}
