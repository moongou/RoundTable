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
  });

  @override
  State<GlassControlBar> createState() => _GlassControlBarState();
}

class _GlassControlBarState extends State<GlassControlBar> {
  @override
  Widget build(BuildContext context) {
    if (!widget.isMyTurn && !widget.canInterrupt) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: AppColors.studyWall.withValues(alpha: 0.85),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: AppColors.lampCenter.withValues(alpha: 0.15),
          width: 1,
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.3),
            blurRadius: 12,
            offset: const Offset(0, -4),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: SafeArea(
            child: widget.isPushToTalk && widget.isMyTurn
                ? _buildPttRow()
                : widget.isMyTurn
                    ? _buildTextRow()
                    : _buildInterruptOnly(),
          ),
        ),
      ),
    );
  }

  Widget _buildPttRow() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        PushToTalkButton(
          isEnabled: true,
          onRecordStart: widget.onPttStart,
          onRecordEnd: widget.onPttEnd,
          isRecording: widget.isRecording,
        ),
        const SizedBox(width: 16),
        TextButton.icon(
          onPressed: widget.onSendMessage,
          icon: const Icon(Icons.keyboard, size: 18, color: AppColors.warmWhite),
          label: const Text('文字输入', style: TextStyle(color: AppColors.warmWhite)),
        ),
        if (widget.canInterrupt)
          Padding(
            padding: const EdgeInsets.only(left: 8),
            child: InterruptButton(
              isVisible: true,
              onPressed: widget.onInterrupt,
              hasRaisedHand: widget.hasRaisedHand,
            ),
          ),
      ],
    );
  }

  Widget _buildTextRow() {
    return Row(
      children: [
        Expanded(
          child: TextField(
            controller: widget.inputController,
            style: const TextStyle(color: AppColors.warmWhite),
            decoration: InputDecoration(
              hintText: '说出你的想法...',
              hintStyle: TextStyle(color: AppColors.warmGray),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: BorderSide(color: AppColors.warmGray.withValues(alpha: 0.3)),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
                borderSide: const BorderSide(color: AppColors.amberGold),
              ),
              filled: true,
              fillColor: AppColors.studyWallLight.withValues(alpha: 0.6),
              contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            ),
            onSubmitted: (_) => widget.onSendMessage(),
          ),
        ),
        const SizedBox(width: 8),
        if (widget.isPushToTalk)
          IconButton(
            onPressed: widget.onPttStart,
            icon: const Icon(Icons.mic, color: AppColors.warmWhite),
            style: IconButton.styleFrom(
              backgroundColor: AppColors.amberGold.withValues(alpha: 0.2),
            ),
          ),
        FilledButton(
          onPressed: widget.onSendMessage,
          style: FilledButton.styleFrom(
            backgroundColor: AppColors.amberGold,
            foregroundColor: AppColors.scrollTitle,
            shape: const CircleBorder(),
            padding: const EdgeInsets.all(12),
          ),
          child: const Icon(Icons.send),
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