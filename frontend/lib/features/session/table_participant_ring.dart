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

    final avatarRadius = tableRadius + 52;

    return LayoutBuilder(
      builder: (context, constraints) {
        final cx = constraints.maxWidth / 2;
        final cy = constraints.maxHeight / 2;
        return Stack(
          clipBehavior: Clip.none,
          children: List.generate(total, (i) {
            final p = participants[i];
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
