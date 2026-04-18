import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';

/// 中心区域消息显示
///
/// 在圆桌中央显示最近的消息，营造"围坐讨论"的感觉。
class MessageDisplay extends StatelessWidget {
  final String? currentSpeaker;
  final String? currentMessage;
  final String speakerAvatar;

  const MessageDisplay({
    super.key,
    this.currentSpeaker,
    this.currentMessage,
    this.speakerAvatar = '',
  });

  @override
  Widget build(BuildContext context) {
    if (currentMessage == null || currentSpeaker == null) {
      return const SizedBox.shrink();
    }

    final color = AppColors.getParticipantColor(currentSpeaker!);
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Container(
      constraints: const BoxConstraints(maxWidth: 320),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: isDark
            ? AppColors.darkSurface.withValues(alpha: 0.8)
            : Colors.white.withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: color.withValues(alpha: 0.3),
          width: 1,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.1),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 发言者标识
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (speakerAvatar.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: Text(speakerAvatar, style: const TextStyle(fontSize: 18)),
                ),
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: color,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 6),
              Text(
                currentSpeaker!,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: color,
                      fontWeight: FontWeight.bold,
                    ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // 消息内容
          Text(
            currentMessage!,
            style: Theme.of(context).textTheme.bodyMedium,
            textAlign: TextAlign.center,
            maxLines: 8,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}