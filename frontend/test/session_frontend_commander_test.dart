import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/features/session/session_frontend_commander.dart';

void main() {
  group('SessionFrontendCommander', () {
    test('defer when human input requested during playback', () {
      final commander = SessionFrontendCommander();

      final cmd = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: true,
      );

      expect(cmd, HumanTurnCommand.defer);
      expect(commander.pendingHumanTurn, isTrue);
      expect(commander.pendingHumanSpeaker, '豆苗');
      expect(commander.handApprovedToSpeak, isFalse);
    });

    test('activate immediately when no playback', () {
      final commander = SessionFrontendCommander();

      final cmd = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: false,
      );

      expect(cmd, HumanTurnCommand.activateNow);
      expect(commander.pendingHumanTurn, isFalse);
      expect(commander.handApprovedToSpeak, isTrue);
    });

    test('activation is debounced to avoid duplicate flips', () {
      DateTime now = DateTime(2026, 4, 20, 10, 0, 0, 0);
      final commander = SessionFrontendCommander(
        activationDebounce: const Duration(milliseconds: 500),
        now: () => now,
      );

      commander.markHumanTurnActivated(speaker: '豆苗');
      final immediate = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: false,
      );
      expect(immediate, HumanTurnCommand.defer);
      expect(commander.pendingHumanTurn, isTrue);

      now = now.add(const Duration(milliseconds: 600));
      final later = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: false,
      );
      expect(later, HumanTurnCommand.activateNow);
    });

    test('completion debounce suppresses immediate re-prompt after submit', () {
      DateTime now = DateTime(2026, 4, 20, 10, 0, 0, 0);
      final commander = SessionFrontendCommander(
        completionDebounce: const Duration(milliseconds: 1200),
        now: () => now,
      );

      commander.markHumanTurnCompleted();

      final immediate = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: false,
      );
      expect(immediate, HumanTurnCommand.defer);
      expect(commander.pendingHumanTurn, isTrue);

      now = now.add(const Duration(milliseconds: 1300));
      final later = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: false,
      );
      expect(later, HumanTurnCommand.activateNow);
    });

    test('force-activate pending human turn after guard timeout', () {
      DateTime now = DateTime(2026, 4, 20, 10, 0, 0, 0);
      final commander = SessionFrontendCommander(
        pendingGuard: const Duration(seconds: 3),
        now: () => now,
      );

      commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'moderator_designated_human',
        hasOngoingSpeechPlayback: true,
      );
      expect(
        commander.shouldForceActivatePending(
          hasOngoingSpeechPlayback: false,
        ),
        isFalse,
      );

      now = now.add(const Duration(seconds: 4));
      expect(
        commander.shouldForceActivatePending(
          hasOngoingSpeechPlayback: false,
        ),
        isTrue,
      );
    });

    test('accepts legacy normal human input request reason', () {
      final commander = SessionFrontendCommander();

      final cmd = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'normal',
        hasOngoingSpeechPlayback: false,
      );

      expect(cmd, HumanTurnCommand.activateNow);
      expect(commander.pendingHumanTurn, isFalse);
      expect(commander.handApprovedToSpeak, isTrue);
    });

    test('normalizes request reason before authorization', () {
      final commander = SessionFrontendCommander();

      final cmd = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: '  NORMAL  ',
        hasOngoingSpeechPlayback: false,
      );

      expect(cmd, HumanTurnCommand.activateNow);
      expect(commander.pendingHumanTurn, isFalse);
      expect(commander.handApprovedToSpeak, isTrue);
    });

    test('ignores truly unauthorized human input request reasons', () {
      final commander = SessionFrontendCommander();

      final cmd = commander.onHumanInputRequested(
        speaker: '豆苗',
        requestReason: 'unknown_reason',
        hasOngoingSpeechPlayback: false,
      );

      expect(cmd, HumanTurnCommand.none);
      expect(commander.pendingHumanTurn, isFalse);
      expect(commander.handApprovedToSpeak, isFalse);
    });
  });
}
