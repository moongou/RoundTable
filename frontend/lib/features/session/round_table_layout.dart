import 'dart:math';

import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';
import 'participant_avatar.dart';

/// 参与者数据
class RoundTableParticipant {
  final String name;
  final String avatar;
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;

  const RoundTableParticipant({
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
  });
}

/// 圆桌布局
///
/// 将参与者以圆形排列在"圆桌"周围，当前发言者高亮。
/// 主持人固定在顶部（12点钟方向）。
class RoundTableLayout extends StatelessWidget {
  final List<RoundTableParticipant> participants;
  final String currentSpeaker;

  const RoundTableLayout({
    super.key,
    required this.participants,
    required this.currentSpeaker,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(
          constraints.maxWidth,
          constraints.maxHeight,
        );

        // 计算圆桌半径和中心
        final radius = min(size.width, size.height) * 0.35;
        final center = Offset(size.width / 2, size.height / 2);

        return Stack(
          children: [
            // 圆桌背景
            _buildTable(context, center, radius),
            // 参与者头像
            ..._buildParticipants(context, center, radius),
          ],
        );
      },
    );
  }

  Widget _buildTable(BuildContext context, Offset center, double radius) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final tableColor = isDark ? AppColors.tableSurfaceDark : AppColors.tableSurface;

    return Positioned(
      left: center.dx - radius,
      top: center.dy - radius,
      child: Container(
        width: radius * 2,
        height: radius * 2,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: tableColor.withValues(alpha: 0.3),
          border: Border.all(
            color: tableColor.withValues(alpha: 0.5),
            width: 2,
          ),
          boxShadow: [
            BoxShadow(
              color: tableColor.withValues(alpha: 0.2),
              blurRadius: 20,
              spreadRadius: 5,
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildParticipants(BuildContext context, Offset center, double radius) {
    final widgets = <Widget>[];
    final total = participants.length;
    if (total == 0) return widgets;

    for (var i = 0; i < total; i++) {
      final participant = participants[i];

      // 主持人放在顶部（-90度 = 12点钟）
      // 其余均匀分布
      final angle = (i / total) * 2 * pi - pi / 2;

      // 头像放在圆桌边缘外侧
      final avatarRadius = radius + 48;
      final x = center.dx + avatarRadius * cos(angle) - 28;
      final y = center.dy + avatarRadius * sin(angle) - 40;

      widgets.add(
        Positioned(
          left: x,
          top: y,
          child: ParticipantAvatar(
            name: participant.name,
            avatar: participant.avatar,
            isSpeaking: participant.isSpeaking,
            isHuman: participant.isHuman,
            hasRaisedHand: participant.hasRaisedHand,
            isCurrentSpeaker: participant.isCurrentSpeaker,
          ),
        ),
      );
    }

    return widgets;
  }
}