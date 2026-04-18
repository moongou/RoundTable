import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';

/// Push-to-Talk 按钮
///
/// 按住开始录音，松开结束录音。
/// 录音中显示脉冲动画和波形指示器。
class PushToTalkButton extends StatefulWidget {
  final bool isEnabled;
  final VoidCallback onRecordStart;
  final VoidCallback onRecordEnd;
  final bool isRecording;

  const PushToTalkButton({
    super.key,
    required this.isEnabled,
    required this.onRecordStart,
    required this.onRecordEnd,
    this.isRecording = false,
  });

  @override
  State<PushToTalkButton> createState() => _PushToTalkButtonState();
}

class _PushToTalkButtonState extends State<PushToTalkButton>
    with TickerProviderStateMixin {
  late AnimationController _pulseController;
  late Animation<double> _pulseAnimation;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );
    _pulseAnimation = Tween<double>(begin: 1.0, end: 1.3).animate(
      CurvedAnimation(parent: _pulseController, curve: Curves.easeInOut),
    );
  }

  @override
  void didUpdateWidget(PushToTalkButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isRecording && !_pulseController.isAnimating) {
      _pulseController.repeat(reverse: true);
    } else if (!widget.isRecording && _pulseController.isAnimating) {
      _pulseController.stop();
      _pulseController.value = 0;
    }
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return GestureDetector(
      onTapDown: widget.isEnabled ? (_) => widget.onRecordStart() : null,
      onTapUp: widget.isEnabled ? (_) => widget.onRecordEnd() : null,
      onTapCancel: widget.isEnabled ? () => widget.onRecordEnd() : null,
      child: AnimatedBuilder(
        animation: _pulseAnimation,
        builder: (context, child) {
          final scale = widget.isRecording ? _pulseAnimation.value : 1.0;
          return Transform.scale(
            scale: scale,
            child: child,
          );
        },
        child: Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: widget.isRecording
                ? Colors.red
                : widget.isEnabled
                    ? colorScheme.primary
                    : colorScheme.surfaceContainerHighest,
            boxShadow: widget.isRecording
                ? [
                    BoxShadow(
                      color: Colors.red.withValues(alpha: 0.4),
                      blurRadius: 16,
                      spreadRadius: 4,
                    ),
                  ]
                : [
                    BoxShadow(
                      color: colorScheme.primary.withValues(alpha: 0.2),
                      blurRadius: 8,
                      offset: const Offset(0, 2),
                    ),
                  ],
          ),
          child: Icon(
            widget.isRecording ? Icons.mic : Icons.mic_none,
            color: widget.isEnabled
                ? (widget.isRecording ? Colors.white : colorScheme.onPrimary)
                : colorScheme.onSurfaceVariant,
            size: 28,
          ),
        ),
      ),
    );
  }
}

/// 打断/举手按钮
class InterruptButton extends StatelessWidget {
  final bool isVisible;
  final VoidCallback onPressed;
  final bool hasRaisedHand;

  const InterruptButton({
    super.key,
    required this.isVisible,
    required this.onPressed,
    this.hasRaisedHand = false,
  });

  @override
  Widget build(BuildContext context) {
    if (!isVisible) return const SizedBox.shrink();

    return AnimatedOpacity(
      opacity: isVisible ? 1.0 : 0.0,
      duration: const Duration(milliseconds: 300),
      child: FloatingActionButton.small(
        onPressed: onPressed,
        backgroundColor: hasRaisedHand
            ? AppColors.accentWarm
            : Theme.of(context).colorScheme.secondaryContainer,
        child: Icon(
          hasRaisedHand ? Icons.back_hand : Icons.pan_tool_outlined,
          size: 20,
          color: hasRaisedHand ? Colors.white : null,
        ),
      ),
    );
  }
}