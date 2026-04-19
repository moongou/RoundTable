import 'dart:ui';

import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';
import 'push_to_talk_button.dart';

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
    if (!widget.isMyTurn && !widget.canInterrupt)
      return const SizedBox.shrink();

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
          child: SafeArea(
            child: widget.isMyTurn ? _buildMyTurnRow() : _buildInterruptOnly(),
          ),
        ),
      ),
    );
  }

  /// 轮到我发言时：显示提示 + PTT 按钮 + 文字输入 + 跳过
  Widget _buildMyTurnRow() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // 提示文字
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.record_voice_over,
                  color: AppColors.amberGold, size: 14),
              const SizedBox(width: 6),
              Text(
                widget.isRecording ? '正在录音... 松开空格键结束' : '轮到你了！按住空格键发言，或直接输入文字',
                style: TextStyle(
                  color: widget.isRecording
                      ? const Color(0xFF00FFCC)
                      : AppColors.amberGold,
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
        ),
        // 操作行：PTT + 文字框 + 发送 + 跳过
        Row(
          children: [
            // PTT 按钮
            if (widget.isPushToTalk)
              GestureDetector(
                onTapDown: (_) => widget.onPttStart(),
                onTapUp: (_) => widget.onPttEnd(),
                onTapCancel: widget.onPttEnd,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 200),
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: widget.isRecording
                        ? const Color(0xFF00FFCC).withValues(alpha: 0.2)
                        : AppColors.amberGold.withValues(alpha: 0.15),
                    border: Border.all(
                      color: widget.isRecording
                          ? const Color(0xFF00FFCC)
                          : AppColors.amberGold,
                      width: 2,
                    ),
                  ),
                  child: Icon(
                    widget.isRecording ? Icons.mic : Icons.mic_none,
                    color: widget.isRecording
                        ? const Color(0xFF00FFCC)
                        : AppColors.amberGold,
                    size: 22,
                  ),
                ),
              ),
            if (widget.isPushToTalk) const SizedBox(width: 10),
            // 文字输入框
            Expanded(
              child: TextField(
                controller: widget.inputController,
                style:
                    const TextStyle(color: AppColors.warmWhite, fontSize: 14),
                decoration: InputDecoration(
                  hintText: '或直接输入文字...',
                  hintStyle:
                      const TextStyle(color: AppColors.warmGray, fontSize: 13),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide(
                        color: AppColors.warmGray.withValues(alpha: 0.3)),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide(
                        color: AppColors.warmGray.withValues(alpha: 0.3)),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: const BorderSide(
                        color: AppColors.amberGold, width: 1.5),
                  ),
                  filled: true,
                  fillColor: AppColors.studyWallLight.withValues(alpha: 0.5),
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  isDense: true,
                ),
                onSubmitted: (_) => widget.onSendMessage(),
              ),
            ),
            const SizedBox(width: 6),
            // 发送按钮
            FilledButton(
              onPressed: widget.onSendMessage,
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.amberGold,
                foregroundColor: AppColors.scrollTitle,
                shape: const CircleBorder(),
                padding: const EdgeInsets.all(10),
                minimumSize: const Size(40, 40),
              ),
              child: const Icon(Icons.send, size: 18),
            ),
            const SizedBox(width: 4),
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
        ),
      ],
    );
  }

  Widget _buildInterruptOnly() {
    return Center(
      child: InterruptButton(
        isVisible: widget.canInterrupt,
        onPressed: widget.onInterrupt,
        hasRaisedHand: widget.hasRaisedHand,
      ),
    );
  }
}
