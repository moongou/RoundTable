import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 圆桌参与者头像
///
/// 显示角色头像（emoji）、名字、发言状态指示。
class ParticipantAvatar extends StatelessWidget {
  final String name;
  final String avatar; // emoji
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;

  const ParticipantAvatar({
    super.key,
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
  });

  @override
  Widget build(BuildContext context) {
    final color = AppColors.getParticipantColor(name);
    final colorScheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // 头像圆圈
        Stack(
          clipBehavior: Clip.none,
          children: [
            // 发光外圈
            if (isSpeaking)
              Container(
                width: 56,
                height: 56,
                decoration: AppTheme.glowBoxDecoration(color, blurRadius: 16),
              ),

            // 头像
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isSpeaking
                    ? color.withValues(alpha: 0.2)
                    : isDark
                        ? AppColors.darkSurface
                        : colorScheme.surfaceContainerHighest,
                border: Border.all(
                  color: isCurrentSpeaker
                      ? color
                      : isSpeaking
                          ? color.withValues(alpha: 0.7)
                          : (isDark ? Colors.white.withValues(alpha: 0.1) : colorScheme.outlineVariant),
                  width: isCurrentSpeaker ? 3 : 2,
                ),
              ),
              child: Center(
                child: Text(avatar, style: const TextStyle(fontSize: 24)),
              ),
            ),

            // 举手标识
            if (hasRaisedHand)
              Positioned(
                right: -4,
                top: -4,
                child: Container(
                  padding: const EdgeInsets.all(2),
                  decoration: const BoxDecoration(
                    color: AppColors.accentWarm,
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.back_hand, size: 14, color: Colors.white),
                ),
              ),

            // 发言中指示器（脉冲点）
            if (isSpeaking)
              Positioned(
                right: 2,
                bottom: 2,
                child: Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    color: Colors.green,
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.white, width: 2),
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 4),
        // 名字
        Text(
          name,
          style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: isCurrentSpeaker ? color : null,
                fontWeight: isCurrentSpeaker ? FontWeight.bold : FontWeight.normal,
              ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ],
    );
  }
}