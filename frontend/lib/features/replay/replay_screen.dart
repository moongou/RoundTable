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
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:dio/dio.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';

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
  _ReplayPackage({
    required this.topicTitle,
    required this.sessionId,
    required this.lines,
    required this.audioByRelPath,
    required this.sessionMeta,
  });
}

class _ReplayScreenState extends State<ReplayScreen> {
  _ReplayPackage? _pkg;
  int _cursor = 0;
  bool _playing = false;
  bool _loading = false;
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
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['zip'],
        withData: true,
      );
      if (result == null || result.files.isEmpty) {
        setState(() => _loading = false);
        return;
      }
      final file = result.files.first;
      final bytes = file.bytes;
      if (bytes == null) {
        throw Exception('无法读取 ZIP 内容');
      }
      final pkg = _parseZip(bytes);
      setState(() {
        _pkg = pkg;
        _cursor = 0;
        _loading = false;
        _statusLine = '已载入 ${pkg.lines.length} 条文稿，含 ${pkg.audioByRelPath.length} 段音频。';
      });
    } catch (e) {
      setState(() {
        _loading = false;
        _error = '加载失败：$e';
      });
    }
  }

  _ReplayPackage _parseZip(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes);
    Map<String, dynamic>? scriptJson;
    final audioByRelPath = <String, Uint8List>{};

    for (final entry in archive) {
      if (!entry.isFile) continue;
      final name = entry.name;
      final content = entry.content as List<int>;
      if (name.startsWith('script/') && name.endsWith('.json')) {
        try {
          scriptJson = json.decode(utf8.decode(content)) as Map<String, dynamic>;
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
      if (content.trim().isEmpty && (recordingRelPath == null || recordingRelPath.isEmpty)) {
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
    );
  }

  // ── 播放控制 ──────────────────────────────────────────────────────────
  Future<void> _start() async {
    if (_pkg == null || _playing) return;
    setState(() => _playing = true);
    while (_playing && !_disposed && _cursor < _pkg!.lines.length) {
      final line = _pkg!.lines[_cursor];
      setState(() => _statusLine = '正在回放第 ${_cursor + 1}/${_pkg!.lines.length} 句…');
      try {
        await _playLine(line);
      } catch (e) {
        // 单句失败不阻塞整体回放
        debugPrint('replay line ${line.index} failed: $e');
      }
      if (!_playing || _disposed) break;
      setState(() => _cursor += 1);
      await Future.delayed(const Duration(milliseconds: 320));
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

  Future<Uint8List?> _synthesizeTts(_ReplayLine line) async {
    try {
      final body = <String, dynamic>{
        'text': line.content,
      };
      if (line.characterId != null && line.characterId!.isNotEmpty) {
        body['character_id'] = line.characterId;
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

  // ── UI ───────────────────────────────────────────────────────────────
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('思辨复盘'),
        actions: [
          if (_pkg != null)
            IconButton(
              icon: const Icon(Icons.folder_open),
              tooltip: '重新选择 ZIP',
              onPressed: _loading ? null : _pickAndLoadZip,
            ),
        ],
      ),
      body: _pkg == null ? _buildPicker() : _buildReplay(),
    );
  }

  Widget _buildPicker() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.replay_circle_filled, size: 72, color: Color(0xFFE6B872)),
            const SizedBox(height: 16),
            const Text(
              '思辨复盘',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            const Text(
              '载入此前从“开发面板 / 历史会议”下载的 ZIP 复盘包，\n按原顺序、原音色完整回放整场讨论。',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.white70, height: 1.5),
            ),
            const SizedBox(height: 4),
            const Text(
              '不会重新生成讨论内容；缺失录音的发言会用 TTS 朗读相同文稿。',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.white38, fontSize: 12),
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              onPressed: _loading ? null : _pickAndLoadZip,
              icon: const Icon(Icons.upload_file),
              label: Text(_loading ? '载入中…' : '选择 ZIP 复盘包'),
            ),
            if (_error != null) ...[
              const SizedBox(height: 16),
              Text(_error!, style: const TextStyle(color: Colors.redAccent)),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildReplay() {
    final pkg = _pkg!;
    final total = pkg.lines.length;
    final progress = total == 0 ? 0.0 : (_cursor / total).clamp(0.0, 1.0);
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          color: const Color(0xFF1A1A22),
          child: Row(
            children: [
              const Icon(Icons.book_outlined, color: Color(0xFFE6B872)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  pkg.topicTitle,
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              Text('${_cursor.clamp(0, total)} / $total',
                  style: const TextStyle(color: Colors.white60)),
            ],
          ),
        ),
        LinearProgressIndicator(value: progress, minHeight: 3),
        if (_statusLine != null)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            alignment: Alignment.centerLeft,
            child: Text(_statusLine!, style: const TextStyle(color: Colors.white54, fontSize: 12)),
          ),
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            itemCount: total,
            itemBuilder: (context, i) => _buildLineTile(pkg.lines[i], i == _cursor),
          ),
        ),
        SafeArea(
          top: false,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: const BoxDecoration(
              color: Color(0xFF15151B),
              border: Border(top: BorderSide(color: Color(0xFF2A2A33))),
            ),
            child: Row(
              children: [
                IconButton(
                  icon: const Icon(Icons.skip_previous),
                  tooltip: '回到开头',
                  onPressed: _restart,
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: _playing ? _pause : _start,
                  icon: Icon(_playing ? Icons.pause : Icons.play_arrow),
                  label: Text(_playing ? '暂停' : (_cursor == 0 ? '开始复盘' : '继续')),
                ),
                const Spacer(),
                Text('${pkg.audioByRelPath.length ~/ 2} 段嵌入音频',
                    style: const TextStyle(color: Colors.white38, fontSize: 12)),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildLineTile(_ReplayLine line, bool active) {
    Color bg = active ? const Color(0xFF22232C) : const Color(0xFF1A1A22);
    Color border = active ? const Color(0xFFE6B872) : const Color(0xFF2A2A33);
    final isNote = line.kind == 'note';
    final hasAudio = line.recordingRelPath != null &&
        (_pkg?.audioByRelPath.containsKey(line.recordingRelPath!) ?? false);
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: bg,
        border: Border.all(color: border),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isNote ? Icons.sticky_note_2_outlined : Icons.record_voice_over,
                size: 16,
                color: isNote ? Colors.white54 : const Color(0xFFE6B872),
              ),
              const SizedBox(width: 6),
              Text(line.speaker,
                  style: TextStyle(
                    fontWeight: FontWeight.w600,
                    color: isNote ? Colors.white60 : Colors.white,
                  )),
              const SizedBox(width: 8),
              if (hasAudio)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2D3F2D),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text('原声',
                      style: TextStyle(color: Color(0xFFA9D4A9), fontSize: 10)),
                )
              else
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2A2A40),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text('TTS',
                      style: TextStyle(color: Color(0xFFA0B0E0), fontSize: 10)),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Text(line.content,
              style: TextStyle(
                color: isNote ? Colors.white60 : Colors.white,
                height: 1.45,
              )),
        ],
      ),
    );
  }
}
