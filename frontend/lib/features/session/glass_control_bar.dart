import 'dart:ui';

import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';

/// 半透明毛玻璃控制栏
class GlassControlBar extends StatefulWidget {
  final bool isMyTurn;
  final bool isPushToTalk;
  final bool isRecording;
  final TextEditingController inputController;
  final VoidCallback onSendMessage;
  final VoidCallback onPttStart;
  final VoidCallback onPttEnd;
  final bool canInterrupt;
  final bool hasRaisedHand;
  final VoidCallback onInterrupt;
  final VoidCallback? onSkipTurn;

  const GlassControlBar({
    super.key,
    required this.isMyTurn,
    required this.isPushToTalk,
    required this.isRecording,
    required this.inputController,
    required this.onSendMessage,
    required this.onPttStart,
    required this.onPttEnd,
    required this.canInterrupt,
    required this.hasRaisedHand,
    required this.onInterrupt,
    this.onSkipTurn,
  });

  @override
  State<GlassControlBar> createState() => _GlassControlBarState();
}

class _GlassControlBarState extends State<GlassControlBar> {
  @override
  Widget build(BuildContext context) {
    // 会话页主交互改为左右两侧按钮，底栏仅在我的回合显示提示和跳过。
    if (!widget.isMyTurn) {
      return const SizedBox.shrink();
    }

    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.studyWall.withValues(alpha: 0.92),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: widget.isMyTurn
              ? AppColors.amberGold.withValues(alpha: 0.4)
              : AppColors.lampCenter.withValues(alpha: 0.15),
          width: widget.isMyTurn ? 1.5 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: widget.isMyTurn
                ? AppColors.amberGold.withValues(alpha: 0.12)
                : Colors.black.withValues(alpha: 0.3),
            blurRadius: 16,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: SafeArea(child: _buildMyTurnRow()),
        ),
      ),
    );
  }

  /// 轮到我发言时：简化底栏，仅显示提示和跳过按钮
  Widget _buildMyTurnRow() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(Icons.record_voice_over, color: AppColors.amberGold, size: 14),
        const SizedBox(width: 6),
        Flexible(
          child: Text(
            widget.isRecording ? '正在录音... 双击 Ctrl 或点击结束按钮' : '右侧输入文字 或 点击麦克风说话',
            style: TextStyle(
              color: widget.isRecording
                  ? const Color(0xFF00FFCC)
                  : AppColors.amberGold,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const SizedBox(width: 10),
        // 跳过按钮
        IconButton(
          onPressed: widget.onSkipTurn,
          icon: const Icon(Icons.skip_next, size: 18),
          tooltip: '跳过本轮发言',
          style: IconButton.styleFrom(
            foregroundColor: AppColors.warmGray,
            minimumSize: const Size(36, 36),
          ),
        ),
      ],
    );
  }
}
