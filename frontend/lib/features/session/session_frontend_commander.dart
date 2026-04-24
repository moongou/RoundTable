enum HumanTurnCommand {
  none,
  activateNow,
  defer,
}

/// Minimal frontend commander that only orchestrates:
/// 1) human turn activation
/// 2) auto-skip progression flags
/// 3) interrupt-approved -> human-ready transition
class SessionFrontendCommander {
  SessionFrontendCommander({
    Duration activationDebounce = const Duration(milliseconds: 350),
    Duration completionDebounce = const Duration(milliseconds: 1400),
    Duration pendingGuard = const Duration(seconds: 5),
    DateTime Function()? now,
  })  : _activationDebounce = activationDebounce,
        _completionDebounce = completionDebounce,
        _pendingGuard = pendingGuard,
        _now = now ?? DateTime.now;

  bool _pendingHumanTurn = false;
  String _pendingHumanSpeaker = '';
  bool _handApprovedToSpeak = false;
  DateTime? _pendingSince;
  DateTime? _lastActivatedAt;
  DateTime? _lastCompletedAt;

  final Duration _activationDebounce;
  final Duration _completionDebounce;
  final Duration _pendingGuard;
  final DateTime Function() _now;

  bool get pendingHumanTurn => _pendingHumanTurn;
  String get pendingHumanSpeaker => _pendingHumanSpeaker;
  bool get handApprovedToSpeak => _handApprovedToSpeak;

  HumanTurnCommand onHumanInputRequested({
    required String speaker,
    required bool hasOngoingSpeechPlayback,
  }) {
    if (hasOngoingSpeechPlayback) {
      _pendingHumanTurn = true;
      _pendingHumanSpeaker = speaker;
      _handApprovedToSpeak = false;
      _pendingSince ??= _now();
      return HumanTurnCommand.defer;
    }
    if (!_canActivateNow()) {
      return HumanTurnCommand.none;
    }
    _pendingHumanTurn = false;
    _pendingHumanSpeaker = '';
    _pendingSince = null;
    _handApprovedToSpeak = true;
    return HumanTurnCommand.activateNow;
  }

  HumanTurnCommand onInterruptApprovedForHuman({
    required bool mineApproved,
    required String speaker,
    required bool hasOngoingSpeechPlayback,
  }) {
    if (!mineApproved) {
      return HumanTurnCommand.none;
    }

    if (hasOngoingSpeechPlayback) {
      _pendingHumanTurn = true;
      _pendingHumanSpeaker = speaker;
      _handApprovedToSpeak = false;
      _pendingSince ??= _now();
      return HumanTurnCommand.defer;
    }

    if (!_canActivateNow()) {
      return HumanTurnCommand.none;
    }

    _pendingHumanTurn = false;
    _pendingHumanSpeaker = '';
    _pendingSince = null;
    _handApprovedToSpeak = true;
    return HumanTurnCommand.activateNow;
  }

  void markHumanTurnActivated({required String speaker}) {
    _pendingHumanTurn = false;
    _pendingHumanSpeaker = '';
    _pendingSince = null;
    _handApprovedToSpeak = true;
    _lastActivatedAt = _now();
  }

  void markHumanTurnCompleted() {
    _pendingHumanTurn = false;
    _pendingHumanSpeaker = '';
    _pendingSince = null;
    _handApprovedToSpeak = false;
    _lastCompletedAt = _now();
  }

  bool shouldForceActivatePending({
    required bool hasOngoingSpeechPlayback,
  }) {
    if (!_pendingHumanTurn || hasOngoingSpeechPlayback) {
      return false;
    }
    final since = _pendingSince;
    if (since == null) {
      return false;
    }
    if (_now().difference(since) < _pendingGuard) {
      return false;
    }
    if (!_canActivateNow()) {
      return false;
    }
    return true;
  }

  bool onAutoSkipShouldPumpTts({
    required bool hasQueuedTts,
    required bool isTtsPlaying,
  }) {
    markHumanTurnCompleted();
    return hasQueuedTts && !isTtsPlaying;
  }

  bool _canActivateNow() {
    final last = _lastActivatedAt;
    if (last != null && _now().difference(last) < _activationDebounce) {
      return false;
    }
    final completed = _lastCompletedAt;
    if (completed != null &&
        _now().difference(completed) < _completionDebounce) {
      return false;
    }
    return true;
  }
}
