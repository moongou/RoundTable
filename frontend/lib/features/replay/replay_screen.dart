// 思辨复盘 — 加载本地 ZIP 复盘包并按原顺序、原音色完整回放。
//
// 设计原则：
// - 不调用大模型，文稿原汁原味；
// - 优先使用 ZIP 内嵌的人声/角色录音；缺失录音的发言走 TTS 重读；
// - 老师点评、金句等通过文稿事件渲染，与会议过程一致。
//
// ZIP 结构（来自后端 build_meeting_script_package）：
//   script/<name>-script.json        必须，含 transcript[] 与 session 元信息
//   script/<name>-script.md          可选 markdown
//   recordings/recordings-manifest.json  可选清单
//   recordings/<relative_path>.<ext>     可选音频文件
//
// 实现：纯 Web，使用 dart:html AudioElement 进行播放，与 server_speech 对齐。

import 'dart:async';
import 'dart:convert';
import 'dart:html' as html;
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class ReplayScreen extends StatefulWidget {
  const ReplayScreen({super.key});

  @override
  State<ReplayScreen> createState() => _ReplayScreenState();
}

class _ReplayLine {
  final int index;
  final String speaker;
  final String content;
  final String kind; // speech / note / review / golden_quote
  final String? recordingId;
  final String? recordingRelPath;
  final String? characterId;
  _ReplayLine({
    required this.index,
    required this.speaker,
    required this.content,
    required this.kind,
    this.recordingId,
    this.recordingRelPath,
    this.characterId,
  });
}

class _ReplayPackage {
  final String topicTitle;
  final String sessionId;
  final List<_ReplayLine> lines;
  final Map<String, Uint8List> audioByRelPath; // relative path -> bytes
  final Map<String, dynamic> sessionMeta;
  final Map<String, String> speakerCharacterIds;
  _ReplayPackage({
    required this.topicTitle,
    required this.sessionId,
    required this.lines,
    required this.audioByRelPath,
    required this.sessionMeta,
    required this.speakerCharacterIds,
  });
}

class _PickedReplayZip {
  final String name;
  final Uint8List bytes;

  _PickedReplayZip({required this.name, required this.bytes});
}

class _ReplayScreenState extends State<ReplayScreen> {
  static const Set<String> _teacherSpeakerAliases = <String>{
    '李老师',
    '老师',
  };
  static const String _edgeTeacherVoice = 'zh-CN-XiaoxiaoNeural';
  static const String _edgeThinkerVoice = 'zh-CN-YunzeNeural';
  static const String _openVoiceTeacherProfile = 'ov:teacher_li';
  static const String _openVoiceThinkerProfile = 'ov:thinker_elder';
  static const Map<String, String> _edgeStudentVoices = <String, String>{
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
  };
  static const Map<String, String> _openVoiceStudentProfiles = <String, String>{
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
  };

  _ReplayPackage? _pkg;
  int _cursor = 0;
  bool _playing = false;
  bool _loading = false;
  double _playbackSpeed = 1.0;
  String _ttsProviderId = 'edge_tts';
  String? _error;
  String? _statusLine;

  html.AudioElement? _audioEl;
  String? _activeObjectUrl;
  Completer<void>? _playCompleter;
  bool _disposed = false;

  final Dio _dio = Dio(BaseOptions(
    baseUrl: kIsWeb ? Uri.base.origin : 'http://localhost:8001',
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 60),
  ));

  @override
  void dispose() {
    _disposed = true;
    _stopAudio();
    super.dispose();
  }

  // ── ZIP 加载 ──────────────────────────────────────────────────────────
  Future<void> _pickAndLoadZip() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final picked = await _pickZipFile();
      if (picked == null) {
        setState(() => _loading = false);
        return;
      }
      final pkg = _parseZip(picked.bytes);
      setState(() {
        _pkg = pkg;
        _cursor = 0;
        _loading = false;
        _statusLine =
            '已载入 ${picked.name}：${pkg.lines.length} 条文稿，含 ${pkg.audioByRelPath.length} 段音频。';
      });
      unawaited(_refreshRuntimeTtsProvider());
    } catch (e) {
      setState(() {
        _loading = false;
        _error = _formatLoadError(e);
      });
    }
  }

  Future<_PickedReplayZip?> _pickZipFile() async {
    final input = html.FileUploadInputElement()
      ..accept = '.zip,application/zip,application/x-zip-compressed'
      ..multiple = false;
    input.style.display = 'none';
    html.document.body?.append(input);
    input.click();
    try {
      await input.onChange.first.timeout(
        const Duration(minutes: 3),
        onTimeout: () => html.Event('timeout'),
      );
      final files = input.files;
      if (files == null || files.isEmpty) {
        return null;
      }
      final file = files.first;
      final bytes = await _readFileAsBytes(file);
      return _PickedReplayZip(name: file.name, bytes: bytes);
    } finally {
      input.remove();
    }
  }

  Future<Uint8List> _readFileAsBytes(html.File file) async {
    final reader = html.FileReader();
    final loadFuture = reader.onLoad.first;
    final errorFuture = reader.onError.first.then<void>((_) {
      throw Exception('无法读取 ZIP 内容');
    });
    final abortFuture = reader.onAbort.first.then<void>((_) {
      throw Exception('已取消读取 ZIP 内容');
    });

    reader.readAsArrayBuffer(file);
    await Future.any([loadFuture, errorFuture, abortFuture]);

    final result = reader.result;
    if (result is ByteBuffer) {
      return Uint8List.view(result);
    }
    if (result is Uint8List) {
      return result;
    }
    if (result is List<int>) {
      return Uint8List.fromList(result);
    }
    throw Exception('无法解析 ZIP 内容');
  }

  String _formatLoadError(Object error) {
    final raw = error.toString().replaceFirst(RegExp(r'^Exception: '), '');
    if (raw.contains('LateInitializationError')) {
      return '加载失败：浏览器文件选择器初始化失败，请刷新页面后重试。';
    }
    if (raw.contains('ZIP 内未找到 script/*.json 文稿')) {
      return '加载失败：这个 ZIP 里没有复盘文稿，请从历史会议重新下载包含 JSON 文稿的复盘包。';
    }
    return '加载失败：$raw';
  }

  _ReplayPackage _parseZip(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes);
    Map<String, dynamic>? scriptJson;
    final audioByRelPath = <String, Uint8List>{};
    final speakerCharacterIds = <String, String>{};

    for (final entry in archive) {
      if (!entry.isFile) continue;
      final name = entry.name;
      final content = entry.content as List<int>;
      if (name.startsWith('script/') && name.endsWith('.json')) {
        try {
          scriptJson =
              json.decode(utf8.decode(content)) as Map<String, dynamic>;
        } catch (_) {}
      } else if (name.startsWith('recordings/') &&
          !name.endsWith('.json') &&
          !name.endsWith('/')) {
        // 存为完整相对路径与短名两种 key，提升匹配稳健性
        final rel = name; // recordings/...
        audioByRelPath[rel] = Uint8List.fromList(content);
        // 也存一份去掉 'recordings/' 前缀的 key
        final stripped = name.substring('recordings/'.length);
        audioByRelPath[stripped] = audioByRelPath[rel]!;
      }
    }

    if (scriptJson == null) {
      throw Exception('ZIP 内未找到 script/*.json 文稿');
    }

    final session = (scriptJson['session'] as Map?) ?? {};
    final topic = (session['topic'] as Map?) ?? {};
    final topicTitle = (topic['title']?.toString().trim().isNotEmpty ?? false)
        ? topic['title'].toString()
        : (session['session_id']?.toString() ?? '思辨复盘');
    final transcript = (scriptJson['transcript'] as List?) ?? const [];
    final lines = <_ReplayLine>[];
    for (var i = 0; i < transcript.length; i++) {
      final raw = transcript[i];
      if (raw is! Map) continue;
      final speaker = (raw['speaker'] ?? '').toString();
      final content = (raw['content'] ?? '').toString();
      final kind = (raw['kind'] ?? 'speech').toString();
      String? recordingId;
      String? recordingRelPath;
      String? characterId;
      final recording = raw['recording'];
      if (recording is Map) {
        recordingId = recording['recording_id']?.toString();
        recordingRelPath = recording['relative_path']?.toString();
        characterId = recording['character_id']?.toString();
      }
      // 兜底：transcript 里有时直接带 character_id
      characterId ??= raw['character_id']?.toString();
      final normalizedSpeaker = _canonicalSpeakerName(speaker);
      final normalizedCharacter = (characterId ?? '').trim();
      if (normalizedSpeaker.isNotEmpty && normalizedCharacter.isNotEmpty) {
        speakerCharacterIds[normalizedSpeaker] = normalizedCharacter;
      }
      if (content.trim().isEmpty &&
          (recordingRelPath == null || recordingRelPath.isEmpty)) {
        continue;
      }
      lines.add(_ReplayLine(
        index: i,
        speaker: speaker.isEmpty ? '未知' : speaker,
        content: content,
        kind: kind,
        recordingId: recordingId,
        recordingRelPath: recordingRelPath,
        characterId: characterId,
      ));
    }

    return _ReplayPackage(
      topicTitle: topicTitle,
      sessionId: (session['session_id'] ?? '').toString(),
      lines: lines,
      audioByRelPath: audioByRelPath,
      sessionMeta: session.cast<String, dynamic>(),
      speakerCharacterIds: speakerCharacterIds,
    );
  }

  // ── 播放控制 ──────────────────────────────────────────────────────────
  Future<void> _start() async {
    if (_pkg == null || _playing) return;
    setState(() => _playing = true);
    while (_playing && !_disposed && _cursor < _pkg!.lines.length) {
      final currentIndex = _cursor;
      final line = _pkg!.lines[currentIndex];
      setState(() {
        _statusLine =
            '现场复盘中：${line.speaker} · ${_cursor + 1}/${_pkg!.lines.length}';
      });
      try {
        await _playLine(line);
      } catch (e) {
        // 单句失败不阻塞整体回放
        debugPrint('replay line ${line.index} failed: $e');
      }
      if (!_playing || _disposed) break;

      final nextIndex = currentIndex + 1;
      setState(() => _cursor = nextIndex);
      final hasNext = nextIndex < _pkg!.lines.length;
      if (!hasNext) {
        continue;
      }
      final nextLine = _pkg!.lines[nextIndex];
      final spacing = _lineJoinDelay(current: line, next: nextLine);
      if (spacing > Duration.zero) {
        await Future.delayed(spacing);
      }
    }
    if (mounted) {
      setState(() {
        _playing = false;
        if (_cursor >= (_pkg?.lines.length ?? 0)) {
          _statusLine = '复盘已结束。';
        }
      });
    }
  }

  Duration _lineJoinDelay({
    required _ReplayLine current,
    required _ReplayLine next,
  }) {
    var baseMs = 78;
    final sameSpeaker = _canonicalSpeakerName(current.speaker) ==
        _canonicalSpeakerName(next.speaker);
    if (sameSpeaker) {
      baseMs = 46;
    }
    if (current.kind == 'note' || next.kind == 'note') {
      baseMs += 16;
    }
    if (current.kind == 'golden_quote' || next.kind == 'golden_quote') {
      baseMs += 10;
    }
    final speedAdjusted =
        (baseMs / _playbackSpeed).round().clamp(22, 160).toInt();
    return Duration(milliseconds: speedAdjusted);
  }

  void _pause() {
    setState(() => _playing = false);
    _stopAudio();
  }

  void _restart() {
    _pause();
    setState(() {
      _cursor = 0;
      _statusLine = '已回到开头。';
    });
  }

  Future<void> _playLine(_ReplayLine line) async {
    // 备注/note 类无内容时直接跳过
    if (line.content.trim().isEmpty &&
        (line.recordingRelPath == null || line.recordingRelPath!.isEmpty)) {
      return;
    }
    // 优先使用嵌入音频
    if (line.recordingRelPath != null && line.recordingRelPath!.isNotEmpty) {
      final bytes = _pkg?.audioByRelPath[line.recordingRelPath!] ??
          _pkg?.audioByRelPath['recordings/${line.recordingRelPath!}'];
      if (bytes != null && bytes.isNotEmpty) {
        await _playBytes(bytes, _guessMime(line.recordingRelPath!));
        return;
      }
    }
    // 回退到 TTS 重读
    if (line.content.trim().isNotEmpty) {
      final bytes = await _synthesizeTts(line);
      if (bytes != null && bytes.isNotEmpty) {
        await _playBytes(bytes, 'audio/mpeg');
      }
    }
  }

  String _guessMime(String relPath) {
    final lower = relPath.toLowerCase();
    if (lower.endsWith('.mp3')) return 'audio/mpeg';
    if (lower.endsWith('.wav')) return 'audio/wav';
    if (lower.endsWith('.webm')) return 'audio/webm';
    if (lower.endsWith('.ogg')) return 'audio/ogg';
    if (lower.endsWith('.m4a')) return 'audio/mp4';
    return 'audio/mpeg';
  }

  String _canonicalSpeakerName(String speaker) {
    final normalized = speaker.trim();
    if (_teacherSpeakerAliases.contains(normalized)) {
      return '李老师';
    }
    return normalized;
  }

  bool _isTeacherSpeaker(String speaker) {
    return _teacherSpeakerAliases.contains(_canonicalSpeakerName(speaker));
  }

  String _stripStageDirectionsForSpeech(String text) {
    var v = text.trim();
    if (v.isEmpty) return '';
    v = v.replaceAll(RegExp(r'[（(][^）)]{1,40}[）)]'), ' ');
    v = v.replaceAll(RegExp(r'\s+'), ' ').trim();
    return v;
  }

  String _resolveCharacterIdForLine(_ReplayLine line) {
    final direct = (line.characterId ?? '').trim();
    if (direct.isNotEmpty) return direct;
    final pkg = _pkg;
    if (pkg == null) return '';
    return pkg.speakerCharacterIds[_canonicalSpeakerName(line.speaker)] ?? '';
  }

  String _resolveVoiceForSpeaker(String speaker) {
    final normalized = _canonicalSpeakerName(speaker);
    final useOpenVoice = _ttsProviderId == 'openvoice';
    if (_isTeacherSpeaker(normalized)) {
      return useOpenVoice ? _openVoiceTeacherProfile : _edgeTeacherVoice;
    }
    final studentVoice = useOpenVoice
        ? _openVoiceStudentProfiles[normalized]
        : _edgeStudentVoices[normalized];
    if (studentVoice != null && studentVoice.isNotEmpty) {
      return studentVoice;
    }
    return useOpenVoice ? _openVoiceThinkerProfile : _edgeThinkerVoice;
  }

  Future<void> _refreshRuntimeTtsProvider() async {
    try {
      final resp = await _dio.get('/api/v1/config/current');
      final data = resp.data;
      if (data is! Map) return;
      final provider =
          (data['tts_provider'] ?? data['tts_provider_id'] ?? '').toString();
      final normalized = provider.trim();
      if (normalized.isEmpty || !mounted) return;
      setState(() {
        _ttsProviderId = normalized;
      });
    } catch (_) {
      // 保持默认 provider，不阻塞复盘流程。
    }
  }

  Future<Uint8List?> _synthesizeTts(_ReplayLine line) async {
    try {
      final speechText = _stripStageDirectionsForSpeech(line.content);
      if (speechText.isEmpty) {
        return null;
      }
      final body = <String, dynamic>{
        'text': speechText,
        'provider': _ttsProviderId,
      };
      final characterId = _resolveCharacterIdForLine(line);
      if (characterId.isNotEmpty) {
        body['character_id'] = characterId;
      } else {
        body['voice'] = _resolveVoiceForSpeaker(line.speaker);
      }
      final resp = await _dio.post(
        '/api/v1/voice/tts',
        data: body,
        options: Options(responseType: ResponseType.bytes),
      );
      final data = resp.data;
      if (data is List<int>) {
        return Uint8List.fromList(data);
      }
      return null;
    } catch (e) {
      debugPrint('TTS synth failed: $e');
      return null;
    }
  }

  Future<void> _playBytes(Uint8List bytes, String mime) async {
    _stopAudio();
    final blob = html.Blob([bytes], mime);
    final url = html.Url.createObjectUrlFromBlob(blob);
    _activeObjectUrl = url;
    final el = html.AudioElement()
      ..src = url
      ..playbackRate = _playbackSpeed
      ..autoplay = true;
    _audioEl = el;
    final completer = Completer<void>();
    _playCompleter = completer;

    el.onEnded.listen((_) {
      if (!completer.isCompleted) completer.complete();
    });
    el.onError.listen((_) {
      if (!completer.isCompleted) completer.complete();
    });

    try {
      await el.play();
    } catch (_) {
      // play() 抛异常时仍等待 ended/error
    }
    await completer.future;
    if (_activeObjectUrl == url) {
      html.Url.revokeObjectUrl(url);
      _activeObjectUrl = null;
    }
    _audioEl = null;
    _playCompleter = null;
  }

  void _stopAudio() {
    try {
      _audioEl?.pause();
    } catch (_) {}
    if (_activeObjectUrl != null) {
      html.Url.revokeObjectUrl(_activeObjectUrl!);
      _activeObjectUrl = null;
    }
    _audioEl = null;
    final c = _playCompleter;
    if (c != null && !c.isCompleted) c.complete();
    _playCompleter = null;
  }

  void _jumpTo(int index) {
    final pkg = _pkg;
    if (pkg == null || pkg.lines.isEmpty) return;
    final safe = index.clamp(0, pkg.lines.length - 1).toInt();
    _pause();
    setState(() {
      _cursor = safe;
      _statusLine = '已定位到片段 ${safe + 1}/${pkg.lines.length}';
    });
  }

  bool _hasEmbeddedAudio(_ReplayLine line) {
    final rel = line.recordingRelPath;
    if (rel == null || rel.isEmpty) return false;
    final pkg = _pkg;
    if (pkg == null) return false;
    return pkg.audioByRelPath.containsKey(rel) ||
        pkg.audioByRelPath.containsKey('recordings/$rel');
  }

  Map<String, int> _speakerCounts(List<_ReplayLine> lines) {
    final map = <String, int>{};
    for (final line in lines) {
      final speaker = line.speaker.trim().isEmpty ? '未知' : line.speaker.trim();
      map[speaker] = (map[speaker] ?? 0) + 1;
    }
    return map;
  }

  Map<String, int> _kindCounts(List<_ReplayLine> lines) {
    final map = <String, int>{};
    for (final line in lines) {
      final kind = line.kind.trim().isEmpty ? 'speech' : line.kind.trim();
      map[kind] = (map[kind] ?? 0) + 1;
    }
    return map;
  }

  List<String> _participants(_ReplayPackage pkg) {
    final ordered = <String>[];
    final participants = pkg.sessionMeta['participants'];
    if (participants is List) {
      for (final item in participants) {
        final name = item.toString().trim();
        if (name.isEmpty || ordered.contains(name)) continue;
        ordered.add(name);
      }
    }
    for (final line in pkg.lines) {
      final name = line.speaker.trim();
      if (name.isEmpty || ordered.contains(name)) continue;
      ordered.add(name);
      if (ordered.length >= 10) break;
    }
    return ordered;
  }

  Color _kindColor(String kind) {
    switch (kind) {
      case 'note':
        return const Color(0xFF6E8CFF);
      case 'review':
        return const Color(0xFFFF8A65);
      case 'golden_quote':
        return const Color(0xFFF8C656);
      case 'speech':
      default:
        return const Color(0xFF68D8A6);
    }
  }

  IconData _kindIcon(String kind) {
    switch (kind) {
      case 'note':
        return Icons.sticky_note_2_outlined;
      case 'review':
        return Icons.rate_review_outlined;
      case 'golden_quote':
        return Icons.auto_awesome;
      case 'speech':
      default:
        return Icons.mic_none_rounded;
    }
  }

  String _kindLabel(String kind) {
    switch (kind) {
      case 'note':
        return '注记';
      case 'review':
        return '点评';
      case 'golden_quote':
        return '金句';
      case 'speech':
      default:
        return '发言';
    }
  }

  String _speakerAbbr(String speaker) {
    final s = speaker.trim();
    if (s.isEmpty) return '?';
    if (s.length <= 2) return s;
    return s.runes.take(2).map((r) => String.fromCharCode(r)).join();
  }

  // ── UI ───────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF090D14),
      body: _pkg == null ? _buildPicker() : _buildReplay(),
    );
  }

  Widget _buildPicker() {
    return Stack(
      children: [
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  const Color(0xFF0B1020),
                  const Color(0xFF14192C),
                  const Color(0xFF1A2038),
                ],
              ),
            ),
          ),
        ),
        Positioned(
          left: -120,
          top: -90,
          child: Container(
            width: 320,
            height: 320,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [Color(0x33F8C656), Color(0x0014192C)],
              ),
            ),
          ),
        ),
        Positioned(
          right: -140,
          bottom: -120,
          child: Container(
            width: 360,
            height: 360,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [Color(0x226E8CFF), Color(0x0014192C)],
              ),
            ),
          ),
        ),
        Center(
          child: Container(
            width: 760,
            constraints: const BoxConstraints(maxWidth: 760),
            margin: const EdgeInsets.all(24),
            padding: const EdgeInsets.symmetric(horizontal: 30, vertical: 28),
            decoration: BoxDecoration(
              color: const Color(0xAA0F1527),
              borderRadius: BorderRadius.circular(28),
              border: Border.all(color: const Color(0x334A5A8A)),
              boxShadow: const [
                BoxShadow(
                  color: Color(0x66000000),
                  blurRadius: 30,
                  offset: Offset(0, 18),
                ),
              ],
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '现场复盘剧场',
                  style: GoogleFonts.zcoolXiaoWei(
                    fontSize: 40,
                    color: const Color(0xFFF4D48D),
                    height: 1.12,
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  '不是语音播放器，而是讨论现场再演：舞台席位、发言轨道、导演台指标、节奏控制全部保留。',
                  style: GoogleFonts.notoSansSc(
                    fontSize: 14,
                    color: const Color(0xFFD2D9EE),
                    height: 1.55,
                  ),
                ),
                const SizedBox(height: 18),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: const [
                    _ReplayTag(label: '舞台席位映射', color: Color(0xFF6E8CFF)),
                    _ReplayTag(label: '事件轨道回放', color: Color(0xFF68D8A6)),
                    _ReplayTag(label: '导演台速率控制', color: Color(0xFFF8C656)),
                    _ReplayTag(label: '原声/TTS分轨', color: Color(0xFFFF8A65)),
                  ],
                ),
                const SizedBox(height: 24),
                Row(
                  children: [
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF27335E),
                        foregroundColor: const Color(0xFFF2F5FF),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 22, vertical: 14),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(14),
                        ),
                      ),
                      onPressed: _loading ? null : _pickAndLoadZip,
                      icon: const Icon(Icons.upload_file_rounded),
                      label: Text(_loading ? '载入中…' : '选择 ZIP 复盘包'),
                    ),
                    const SizedBox(width: 12),
                    Text(
                      '载入后自动进入现场模式',
                      style: GoogleFonts.notoSansSc(
                        fontSize: 12,
                        color: const Color(0x99D2D9EE),
                      ),
                    ),
                  ],
                ),
                if (_error != null) ...[
                  const SizedBox(height: 14),
                  Text(
                    _error!,
                    style: GoogleFonts.notoSansSc(
                      color: const Color(0xFFFF9AA2),
                      fontSize: 13,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildReplay() {
    final pkg = _pkg!;
    final total = pkg.lines.length;
    final safeCursor = total == 0 ? 0 : _cursor.clamp(0, total - 1);
    final progress = total == 0 ? 0.0 : (safeCursor / total).clamp(0.0, 1.0);
    final currentLine = total == 0 ? null : pkg.lines[safeCursor];

    return Stack(
      children: [
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  const Color(0xFF0A0F1C),
                  const Color(0xFF0F1629),
                  const Color(0xFF101827),
                ],
              ),
            ),
          ),
        ),
        Positioned(
          left: -100,
          top: 40,
          child: Container(
            width: 300,
            height: 300,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [Color(0x1F6E8CFF), Color(0x00000000)],
              ),
            ),
          ),
        ),
        Positioned(
          right: -120,
          top: 180,
          child: Container(
            width: 340,
            height: 340,
            decoration: const BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [Color(0x1FF8C656), Color(0x00000000)],
              ),
            ),
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
            child: LayoutBuilder(
              builder: (context, constraints) {
                final isWide = constraints.maxWidth >= 1100;
                return Column(
                  children: [
                    _buildReplayHeader(
                      pkg: pkg,
                      progress: progress,
                      total: total,
                      safeCursor: safeCursor,
                    ),
                    const SizedBox(height: 10),
                    if (_statusLine != null)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: Padding(
                          padding: const EdgeInsets.only(left: 2, bottom: 8),
                          child: Text(
                            _statusLine!,
                            style: GoogleFonts.notoSansSc(
                              color: const Color(0x99D3DBF0),
                              fontSize: 12,
                            ),
                          ),
                        ),
                      ),
                    Expanded(
                      child: isWide
                          ? Row(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                SizedBox(
                                  width: 304,
                                  child: _buildTimelineRail(
                                      pkg, safeCursor, currentLine),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    children: [
                                      Expanded(
                                        flex: 7,
                                        child: _buildStageArena(
                                            pkg, currentLine, safeCursor),
                                      ),
                                      const SizedBox(height: 12),
                                      Expanded(
                                        flex: 5,
                                        child: _buildNowSpeakingCard(
                                            currentLine, safeCursor, total),
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 12),
                                SizedBox(
                                  width: 320,
                                  child: _buildDirectorPanel(pkg, safeCursor),
                                ),
                              ],
                            )
                          : Column(
                              children: [
                                SizedBox(
                                  height: 290,
                                  child: _buildStageArena(
                                      pkg, currentLine, safeCursor),
                                ),
                                const SizedBox(height: 10),
                                SizedBox(
                                  height: 220,
                                  child: _buildDirectorPanel(pkg, safeCursor),
                                ),
                                const SizedBox(height: 10),
                                Expanded(
                                  child: _buildTimelineRail(
                                      pkg, safeCursor, currentLine),
                                ),
                              ],
                            ),
                    ),
                    const SizedBox(height: 10),
                    _buildControlDock(pkg, total),
                  ],
                );
              },
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildReplayHeader({
    required _ReplayPackage pkg,
    required double progress,
    required int total,
    required int safeCursor,
  }) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: const Color(0xCC111A2F),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0x3344588A)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x44000000),
            blurRadius: 20,
            offset: Offset(0, 8),
          ),
        ],
      ),
      child: Column(
        children: [
          Row(
            children: [
              Container(
                width: 42,
                height: 42,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  gradient: const LinearGradient(
                    colors: [Color(0xFF6E8CFF), Color(0xFF293D7A)],
                  ),
                ),
                child: const Icon(Icons.theater_comedy_outlined,
                    color: Color(0xFFF4F7FF)),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      pkg.topicTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: GoogleFonts.zcoolXiaoWei(
                        fontSize: 28,
                        color: const Color(0xFFF4D48D),
                        height: 1.1,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'Session: ${pkg.sessionId.isEmpty ? '未命名' : pkg.sessionId}',
                      style: GoogleFonts.notoSansSc(
                        fontSize: 11,
                        color: const Color(0x9FCBD4ED),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              FilledButton.tonalIcon(
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFF1D2A4B),
                  foregroundColor: const Color(0xFFE6EBFA),
                ),
                onPressed: _loading ? null : _pickAndLoadZip,
                icon: const Icon(Icons.folder_zip_outlined),
                label: const Text('更换包'),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: Container(
                  height: 6,
                  decoration: BoxDecoration(
                    color: const Color(0xFF1F2B48),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: FractionallySizedBox(
                      widthFactor: progress,
                      child: Container(
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(999),
                          gradient: const LinearGradient(
                            colors: [Color(0xFF68D8A6), Color(0xFFF8C656)],
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                decoration: BoxDecoration(
                  color: const Color(0xFF16213D),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: const Color(0x335E75AF)),
                ),
                child: Text(
                  '${total == 0 ? 0 : safeCursor + 1} / $total',
                  style: GoogleFonts.notoSansSc(
                    color: const Color(0xFFE8EEFF),
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildTimelineRail(
      _ReplayPackage pkg, int safeCursor, _ReplayLine? currentLine) {
    final lines = pkg.lines;
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xCC10192E),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0x3344588A)),
      ),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
            child: Row(
              children: [
                const Icon(Icons.route_rounded,
                    size: 16, color: Color(0xFFF8C656)),
                const SizedBox(width: 6),
                Text(
                  '事件轨道',
                  style: GoogleFonts.notoSansSc(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: const Color(0xFFE7EEFF),
                  ),
                ),
                const Spacer(),
                Text(
                  '${lines.length} 条',
                  style: GoogleFonts.notoSansSc(
                    fontSize: 11,
                    color: const Color(0x99D3DBF0),
                  ),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: Color(0x332F416D)),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(10),
              itemCount: lines.length,
              itemBuilder: (context, i) {
                final line = lines[i];
                final active = i == safeCursor;
                final color = _kindColor(line.kind);
                final hasAudio = _hasEmbeddedAudio(line);
                return InkWell(
                  onTap: () => _jumpTo(i),
                  borderRadius: BorderRadius.circular(12),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 220),
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.fromLTRB(10, 9, 10, 9),
                    decoration: BoxDecoration(
                      color: active
                          ? const Color(0xFF192849)
                          : const Color(0xA6152038),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(
                        color: active ? color : const Color(0x303E5280),
                        width: active ? 1.2 : 1,
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Text(
                              '#${i + 1}',
                              style: GoogleFonts.notoSansSc(
                                fontSize: 10,
                                color: const Color(0xFF9AB0DD),
                              ),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                line.speaker,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: GoogleFonts.notoSansSc(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w700,
                                  color: const Color(0xFFEAF1FF),
                                ),
                              ),
                            ),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: const Color(0x1AFFFFFF),
                                borderRadius: BorderRadius.circular(99),
                              ),
                              child: Text(
                                hasAudio ? '原声' : 'TTS',
                                style: GoogleFonts.notoSansSc(
                                  fontSize: 9,
                                  color: hasAudio
                                      ? const Color(0xFFAEE9C8)
                                      : const Color(0xFFAFC7F5),
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          line.content,
                          maxLines: active ? 4 : 2,
                          overflow: TextOverflow.ellipsis,
                          style: GoogleFonts.notoSansSc(
                            color: const Color(0xC8DCE6FB),
                            fontSize: 11,
                            height: 1.45,
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          if (currentLine != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 2, 10, 10),
              child: Row(
                children: [
                  Icon(_kindIcon(currentLine.kind),
                      size: 14, color: _kindColor(currentLine.kind)),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      '当前片段类型：${_kindLabel(currentLine.kind)}',
                      style: GoogleFonts.notoSansSc(
                        fontSize: 11,
                        color: const Color(0x99D3DBF0),
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildStageArena(
      _ReplayPackage pkg, _ReplayLine? currentLine, int safeCursor) {
    final participants = _participants(pkg);
    final speakerMap = _speakerCounts(pkg.lines);
    final activeSpeaker = currentLine?.speaker ?? '';
    final activeColor = _kindColor(currentLine?.kind ?? 'speech');

    return Container(
      decoration: BoxDecoration(
        color: const Color(0xCC0E172B),
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: const Color(0x3344588A)),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final centerX = constraints.maxWidth / 2;
          final centerY = constraints.maxHeight / 2;
          final radiusX = constraints.maxWidth * 0.36;
          final radiusY = constraints.maxHeight * 0.30;
          final seatList = participants.isNotEmpty
              ? participants
              : (currentLine != null ? [currentLine.speaker] : <String>[]);

          return Stack(
            children: [
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: RadialGradient(
                      center: const Alignment(0, -0.12),
                      radius: 1,
                      colors: [
                        const Color(0x33182342),
                        const Color(0x0010182A),
                      ],
                    ),
                  ),
                ),
              ),
              Positioned(
                left: centerX - constraints.maxWidth * 0.31,
                top: centerY - constraints.maxHeight * 0.21,
                child: Container(
                  width: constraints.maxWidth * 0.62,
                  height: constraints.maxHeight * 0.42,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(400),
                    border: Border.all(color: const Color(0x334D6398)),
                  ),
                ),
              ),
              for (int i = 0; i < seatList.length; i++)
                Builder(builder: (context) {
                  final speaker = seatList[i];
                  final theta =
                      -math.pi / 2 + (2 * math.pi * i / seatList.length);
                  final dx = centerX + radiusX * math.cos(theta) - 38;
                  final dy = centerY + radiusY * math.sin(theta) - 22;
                  final isActive = speaker == activeSpeaker;
                  return Positioned(
                    left: dx,
                    top: dy,
                    child: _buildSeatBadge(
                      speaker: speaker,
                      turns: speakerMap[speaker] ?? 0,
                      active: isActive,
                    ),
                  );
                }),
              Center(
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 240),
                  width: 212,
                  height: 212,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(
                      colors: [
                        Color.alphaBlend(const Color(0xAAFFFFFF), activeColor),
                        const Color(0xAA1A2746),
                        const Color(0x44121928),
                      ],
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: activeColor.withValues(
                            alpha: _playing ? 0.42 : 0.2),
                        blurRadius: _playing ? 38 : 18,
                        spreadRadius: _playing ? 8 : 1,
                      ),
                    ],
                    border: Border.all(color: const Color(0x44FFFFFF)),
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(_kindIcon(currentLine?.kind ?? 'speech'),
                          color: const Color(0xFFF4F7FF), size: 26),
                      const SizedBox(height: 8),
                      Text(
                        activeSpeaker.isEmpty ? '等待开始' : activeSpeaker,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: GoogleFonts.zcoolXiaoWei(
                          fontSize: 27,
                          color: const Color(0xFFF7F9FF),
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        currentLine == null
                            ? '导入复盘包后开始'
                            : '${_kindLabel(currentLine.kind)} · 片段 ${safeCursor + 1}',
                        style: GoogleFonts.notoSansSc(
                          fontSize: 11,
                          color: const Color(0xCCDFE7FF),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _buildSeatBadge({
    required String speaker,
    required int turns,
    required bool active,
  }) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
      decoration: BoxDecoration(
        color: active ? const Color(0xFF2A3B69) : const Color(0xCC111B31),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: active ? const Color(0xFFD7E4FF) : const Color(0x334D6398),
          width: active ? 1.2 : 1,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 22,
            height: 22,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: active ? const Color(0xFFF8C656) : const Color(0xFF243457),
            ),
            child: Text(
              _speakerAbbr(speaker),
              style: GoogleFonts.notoSansSc(
                fontSize: 10,
                fontWeight: FontWeight.w700,
                color:
                    active ? const Color(0xFF1C263F) : const Color(0xFFE7EDFF),
              ),
            ),
          ),
          const SizedBox(width: 7),
          Text(
            speaker,
            style: GoogleFonts.notoSansSc(
              color: const Color(0xFFE9EEFF),
              fontSize: 11,
              fontWeight: active ? FontWeight.w700 : FontWeight.w600,
            ),
          ),
          const SizedBox(width: 7),
          Text(
            '$turns',
            style: GoogleFonts.notoSansSc(
              color: const Color(0x99D3DBF0),
              fontSize: 10,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNowSpeakingCard(_ReplayLine? line, int safeCursor, int total) {
    if (line == null) {
      return Container(
        decoration: BoxDecoration(
          color: const Color(0xCC10192E),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0x3344588A)),
        ),
        alignment: Alignment.center,
        child: Text(
          '尚无可回放片段',
          style: GoogleFonts.notoSansSc(
            fontSize: 14,
            color: const Color(0x99D3DBF0),
          ),
        ),
      );
    }

    final lineColor = _kindColor(line.kind);
    final hasAudio = _hasEmbeddedAudio(line);
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xCC10192E),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0x3344588A)),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_kindIcon(line.kind), color: lineColor, size: 16),
              const SizedBox(width: 6),
              Text(
                '当前发言剧本',
                style: GoogleFonts.notoSansSc(
                  color: const Color(0xFFEAF1FF),
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                ),
              ),
              const Spacer(),
              Text(
                '${safeCursor + 1}/$total',
                style: GoogleFonts.notoSansSc(
                  color: const Color(0x99D3DBF0),
                  fontSize: 11,
                ),
              ),
            ],
          ),
          const SizedBox(height: 9),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _ReplayTag(
                label: line.speaker,
                color: const Color(0xFF6E8CFF),
                compact: true,
              ),
              _ReplayTag(
                label: _kindLabel(line.kind),
                color: lineColor,
                compact: true,
              ),
              _ReplayTag(
                label: hasAudio ? '原声轨' : 'TTS轨',
                color: hasAudio
                    ? const Color(0xFF68D8A6)
                    : const Color(0xFFFF8A65),
                compact: true,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Expanded(
            child: SingleChildScrollView(
              child: Text(
                line.content,
                style: GoogleFonts.notoSansSc(
                  color: const Color(0xFFDCE5FA),
                  fontSize: 14,
                  height: 1.7,
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: 24,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: List.generate(18, (i) {
                final seed = (_cursor * 17 + i * 11) % 13;
                final h = (_playing ? 7 + seed * 1.4 : 5 + seed * 0.7)
                    .clamp(4.0, 24.0)
                    .toDouble();
                return Expanded(
                  child: Align(
                    alignment: Alignment.bottomCenter,
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 240),
                      margin: const EdgeInsets.symmetric(horizontal: 1.3),
                      height: h,
                      decoration: BoxDecoration(
                        color: lineColor.withValues(alpha: 0.72),
                        borderRadius: BorderRadius.circular(10),
                      ),
                    ),
                  ),
                );
              }),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDirectorPanel(_ReplayPackage pkg, int safeCursor) {
    final lines = pkg.lines;
    final total = lines.length;
    final originCount = lines.where((line) => _hasEmbeddedAudio(line)).length;
    final ttsCount = total - originCount;
    final speakerEntries = _speakerCounts(lines).entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final kindEntries = _kindCounts(lines).entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));

    return Container(
      decoration: BoxDecoration(
        color: const Color(0xCC10192E),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0x3344588A)),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.space_dashboard_outlined,
                  size: 16, color: Color(0xFFF8C656)),
              const SizedBox(width: 6),
              Text(
                '导演台',
                style: GoogleFonts.notoSansSc(
                  color: const Color(0xFFEAF1FF),
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _buildStatCard('片段总数', '$total'),
              _buildStatCard('原声轨', '$originCount'),
              _buildStatCard('TTS轨', '$ttsCount'),
              _buildStatCard(
                  '进度',
                  total == 0
                      ? '0%'
                      : '${(((safeCursor + 1) / total) * 100).round()}%'),
            ],
          ),
          const SizedBox(height: 10),
          Expanded(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '发言占比',
                    style: GoogleFonts.notoSansSc(
                      color: const Color(0xFFB9C8EC),
                      fontSize: 11,
                    ),
                  ),
                  const SizedBox(height: 6),
                  for (final entry in speakerEntries.take(6))
                    Builder(builder: (context) {
                      final ratio = total == 0
                          ? 0.0
                          : (entry.value / total).clamp(0.0, 1.0);
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 7),
                        child: Column(
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    entry.key,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: GoogleFonts.notoSansSc(
                                      color: const Color(0xFFDCE5FA),
                                      fontSize: 11,
                                    ),
                                  ),
                                ),
                                Text(
                                  '${entry.value}',
                                  style: GoogleFonts.notoSansSc(
                                    color: const Color(0x99D3DBF0),
                                    fontSize: 10,
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 3),
                            ClipRRect(
                              borderRadius: BorderRadius.circular(999),
                              child: LinearProgressIndicator(
                                value: ratio,
                                minHeight: 4,
                                color: const Color(0xFF6E8CFF),
                                backgroundColor: const Color(0x332A3C66),
                              ),
                            ),
                          ],
                        ),
                      );
                    }),
                  const SizedBox(height: 8),
                  Text(
                    '语义构成',
                    style: GoogleFonts.notoSansSc(
                      color: const Color(0xFFB9C8EC),
                      fontSize: 11,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final entry in kindEntries)
                        _ReplayTag(
                          label: '${_kindLabel(entry.key)} ${entry.value}',
                          color: _kindColor(entry.key),
                          compact: true,
                        ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatCard(String label, String value) {
    return Container(
      width: 140,
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xAA152340),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0x33465C90)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: GoogleFonts.notoSansSc(
              color: const Color(0x99D3DBF0),
              fontSize: 10,
            ),
          ),
          const SizedBox(height: 3),
          Text(
            value,
            style: GoogleFonts.notoSansSc(
              color: const Color(0xFFF3F7FF),
              fontSize: 14,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildControlDock(_ReplayPackage pkg, int total) {
    const minSpeed = 0.8;
    const maxSpeed = 1.3;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
        decoration: BoxDecoration(
          color: const Color(0xCC10192E),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: const Color(0x3344588A)),
        ),
        child: Wrap(
          runSpacing: 10,
          spacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            FilledButton.icon(
              onPressed: _restart,
              style: FilledButton.styleFrom(
                backgroundColor: const Color(0xFF1F2F56),
                foregroundColor: const Color(0xFFEAF1FF),
              ),
              icon: const Icon(Icons.replay),
              label: const Text('回到开场'),
            ),
            FilledButton.icon(
              onPressed: _playing ? _pause : _start,
              style: FilledButton.styleFrom(
                backgroundColor: const Color(0xFF2D4D83),
                foregroundColor: const Color(0xFFF7FAFF),
              ),
              icon: Icon(
                  _playing ? Icons.pause_rounded : Icons.play_arrow_rounded),
              label: Text(_playing ? '暂停现场' : (_cursor == 0 ? '开场复盘' : '继续现场')),
            ),
            Text(
              '语速',
              style: GoogleFonts.notoSansSc(
                color: const Color(0xBBD6DEF2),
                fontSize: 12,
              ),
            ),
            SizedBox(
              width: 260,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  SliderTheme(
                    data: SliderTheme.of(context).copyWith(
                      activeTrackColor: const Color(0xFF7EB6FF),
                      inactiveTrackColor: const Color(0x33465C90),
                      thumbColor: const Color(0xFFF8C656),
                      overlayColor: const Color(0x33F8C656),
                      trackHeight: 3.8,
                    ),
                    child: Slider(
                      value: _playbackSpeed,
                      min: minSpeed,
                      max: maxSpeed,
                      divisions: ((maxSpeed - minSpeed) / 0.05).round(),
                      label: '${_playbackSpeed.toStringAsFixed(2)}x',
                      onChanged: (value) {
                        final rounded = (value * 20).round().toDouble() / 20.0;
                        setState(() {
                          _playbackSpeed =
                              rounded.clamp(minSpeed, maxSpeed).toDouble();
                          _statusLine =
                              '已切换复盘语速 ${_playbackSpeed.toStringAsFixed(2)}x';
                        });
                      },
                    ),
                  ),
                  Row(
                    children: [
                      Text(
                        '0.8x',
                        style: GoogleFonts.notoSansSc(
                          color: const Color(0x99D3DBF0),
                          fontSize: 10,
                        ),
                      ),
                      const Spacer(),
                      Text(
                        _playbackSpeed == 1.0
                            ? '常规语速 1.00x'
                            : '${_playbackSpeed.toStringAsFixed(2)}x',
                        style: GoogleFonts.notoSansSc(
                          color: const Color(0xFFEAF1FF),
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const Spacer(),
                      Text(
                        '1.3x',
                        style: GoogleFonts.notoSansSc(
                          color: const Color(0x99D3DBF0),
                          fontSize: 10,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            Text(
              '已载入 ${pkg.audioByRelPath.length ~/ 2} 段音频 · 共 $total 片段',
              style: GoogleFonts.notoSansSc(
                color: const Color(0x8FD3DBF0),
                fontSize: 11,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ReplayTag extends StatelessWidget {
  final String label;
  final Color color;
  final bool compact;

  const _ReplayTag({
    required this.label,
    required this.color,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 8 : 10,
        vertical: compact ? 4 : 5,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.52)),
      ),
      child: Text(
        label,
        style: GoogleFonts.notoSansSc(
          fontSize: compact ? 10 : 11,
          color: color.withValues(alpha: 0.95),
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
