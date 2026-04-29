String mergeStreamingDraft(String current, String incoming) {
  if (current.isEmpty) return incoming;
  if (incoming.startsWith(current)) return incoming;
  if (current.startsWith(incoming)) return current;
  if (current.endsWith(incoming)) return current;
  return '$current $incoming';
}

String polishTranscript(String raw) {
  var text = raw.trim();
  if (text.isEmpty) return '';

  text = text.replaceAll(RegExp(r'\s+'), ' ');
  text = text.replaceAllMapped(
    RegExp(r'([，。！？；,.!?;])\1+'),
    (match) => match.group(1) ?? '',
  );
  text = text.replaceAllMapped(
    RegExp(r'(嗯|呃|啊|那个|就是)(\s*\1)+'),
    (match) => match.group(1) ?? '',
  );

  final parts = text.split(RegExp(r'[，。！？；,.!?;]+'));
  final dedup = <String>[];
  String prev = '';
  for (final p in parts) {
    final v = p.trim();
    if (v.isEmpty || v == prev) continue;
    dedup.add(v);
    prev = v;
  }

  text = dedup.join('，').trim();
  if (text.isEmpty) return '';
  if (!RegExp(r'[。！？!?]$').hasMatch(text)) {
    text = '$text。';
  }
  return text;
}

String friendlyErrorMessage(String raw) {
  final s = raw.trim();
  if (s.isEmpty || s == '未知错误') {
    return '';
  }
  if (s.contains('Unexpected ASGI message') || s.contains('websocket.send')) {
    return '';
  }
  if (s.contains('WebSocket 事件解析失败')) {
    return '';
  }
  if (s.contains('输入队列不存在') || s.contains('Failed to get user input')) {
    return '用户输入通道暂时不可用，系统已自动跳过本轮并继续讨论。';
  }
  if (s.contains('timeout') || s.contains('超时')) {
    return '等待输入超时，系统已自动进入下一位发言。';
  }
  if (s.contains('讨论出现异常')) {
    return '讨论过程出现短暂异常，系统正在自动恢复。';
  }
  if (s.contains('自动恢复调度') || s.contains('自动跳过并继续')) {
    return '';
  }
  return s;
}

double averageOf(List<double> values) {
  if (values.isEmpty) return 0;
  var total = 0.0;
  for (final v in values) {
    total += v;
  }
  return total / values.length;
}

String formatTtsTraceValue(String? value, {String fallback = '—'}) {
  final normalized = (value ?? '').trim();
  return normalized.isEmpty ? fallback : normalized;
}
