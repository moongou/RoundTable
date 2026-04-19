import 'dart:math';

import 'package:flutter/material.dart';

import 'glow_avatar.dart';

class SeatedParticipant {
  final String name;
  final String avatar;
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;
  final bool isDimmed;

  const SeatedParticipant({
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
    this.isDimmed = false,
  });
}

class TableParticipantRing extends StatelessWidget {
  final List<SeatedParticipant> participants;
  final double tableRadius;

  const TableParticipantRing({
    super.key,
    required this.participants,
    required this.tableRadius,
  });

  @override
  Widget build(BuildContext context) {
    final total = participants.length;
    if (total == 0) return const SizedBox.shrink();

    // Reorder: moderator at top (index 0 → angle -π/2), human at bottom (index total÷2)
    final reordered = List<SeatedParticipant>.from(participants);
    if (total >= 2) {
      // Put moderator first
      final modIdx = reordered.indexWhere((p) => p.name == '李老师');
      if (modIdx > 0) {
        final mod = reordered.removeAt(modIdx);
        reordered.insert(0, mod);
      }
      // Put human at total÷2 (bottom)
      final humanIdx = reordered.indexWhere((p) => p.isHuman);
      if (humanIdx >= 0) {
        final human = reordered.removeAt(humanIdx);
        final targetIdx = (reordered.length / 2).round().clamp(1, reordered.length);
        reordered.insert(targetIdx, human);
      }
    }

    final avatarRadius = tableRadius + 52;

    return LayoutBuilder(
      builder: (context, constraints) {
        final cx = constraints.maxWidth / 2;
        final cy = constraints.maxHeight / 2;
        return Stack(
          clipBehavior: Clip.none,
          children: List.generate(total, (i) {
            final p = reordered[i];
            final angle = (i / total) * 2 * pi - pi / 2;

            return Positioned(
              left: cx + avatarRadius * cos(angle) - 32,
              top: cy + avatarRadius * sin(angle) - 38,
              child: GlowAvatar(
                name: p.name,
                avatar: p.avatar,
                isSpeaking: p.isSpeaking,
                isHuman: p.isHuman,
                hasRaisedHand: p.hasRaisedHand,
                isCurrentSpeaker: p.isCurrentSpeaker,
                isDimmed: p.isDimmed,
              ),
            );
          }),
        );
      },
    );
  }
}
