import 'package:flutter/material.dart';

/// 底部字幕条 - 悬浮在屏幕下方，半透明，不遮挡角色头像
class SpeakingBubble extends StatelessWidget {
  final String speaker;
  final String content;
  final Color speakerColor;
  final bool isVisible;

  const SpeakingBubble({
    super.key,
    required this.speaker,
    required this.content,
    required this.speakerColor,
    this.isVisible = true,
  });

  @override
  Widget build(BuildContext context) {
    return AnimatedOpacity(
      opacity: isVisible ? 1.0 : 0.0,
      duration: const Duration(milliseconds: 400),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Text(
            speaker,
            style: TextStyle(
              color: speakerColor,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.4,
              shadows: [
                Shadow(
                  color: Colors.black.withValues(alpha: 0.85),
                  blurRadius: 6,
                  offset: const Offset(0, 1),
                ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          Text(
            content,
            style: TextStyle(
              color: Colors.white,
              fontSize: 18,
              fontWeight: FontWeight.w600,
              height: 1.35,
              shadows: [
                Shadow(
                  color: Colors.black.withValues(alpha: 0.95),
                  blurRadius: 10,
                  offset: const Offset(0, 1),
                ),
                Shadow(
                  color: speakerColor.withValues(alpha: 0.35),
                  blurRadius: 16,
                  offset: const Offset(0, 0),
                ),
              ],
            ),
            textAlign: TextAlign.center,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
