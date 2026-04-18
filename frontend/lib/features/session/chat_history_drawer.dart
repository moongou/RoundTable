import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 聊天记录抽屉 - 暖色书房风格
///
/// 侧边抽屉显示完整的文字聊天记录，保留传统聊天体验。
class ChatHistoryDrawer extends StatelessWidget {
  final List<ChatMessage> messages;
  final String myName;
  final ScrollController? scrollController;

  const ChatHistoryDrawer({
    super.key,
    required this.messages,
    required this.myName,
    this.scrollController,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppColors.studyWall.withValues(alpha: 0.98),
        borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
        border: Border.all(
          color: AppColors.lampCenter.withValues(alpha: 0.1),
        ),
      ),
      child: Column(
        children: [
          // 标题栏
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              color: AppColors.studyWallLight.withValues(alpha: 0.5),
              border: Border(
                bottom: BorderSide(
                  color: AppColors.warmGray.withValues(alpha: 0.2),
                ),
              ),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '讨论记录',
                    style: AppTheme.calligraphyStyleDark(fontSize: 18),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, color: AppColors.warmGray, size: 20),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
          ),
          // 消息列表
          Expanded(
            child: ListView.builder(
              controller: scrollController,
              padding: const EdgeInsets.all(12),
              itemCount: messages.length,
              itemBuilder: (context, index) {
                final msg = messages[index];
                return _HistoryBubble(message: msg, myName: myName);
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoryBubble extends StatelessWidget {
  final ChatMessage message;
  final String myName;

  const _HistoryBubble({required this.message, required this.myName});

  @override
  Widget build(BuildContext context) {
    final isSystem = message.type == 'system';
    final color = AppColors.getParticipantColor(message.source);

    if (isSystem) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Center(
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            decoration: BoxDecoration(
              color: AppColors.studyWallLight.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: AppColors.warmGray.withValues(alpha: 0.2),
              ),
            ),
            child: Text(
              message.content,
              style: TextStyle(color: AppColors.warmGray, fontSize: 12),
              textAlign: TextAlign.center,
            ),
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 发言者色条
          Container(
            width: 3,
            height: 40,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: AppColors.studyWallLight.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: color.withValues(alpha: 0.15),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    message.source,
                    style: TextStyle(
                      color: color,
                      fontWeight: FontWeight.bold,
                      fontSize: 12,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    message.content,
                    style: TextStyle(
                      color: AppColors.warmWhite,
                      fontSize: 13,
                      height: 1.4,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}