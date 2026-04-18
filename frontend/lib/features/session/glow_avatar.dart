import 'package:flutter/material.dart';

import '../../theme/app_colors.dart';

/// 发光头像 - 圆桌围坐时使用
///
/// 增强发光效果和灰度切换能力（首页未选中角色显示灰度）。
class GlowAvatar extends StatefulWidget {
  final String name;
  final String avatar;
  final bool isSpeaking;
  final bool isHuman;
  final bool hasRaisedHand;
  final bool isCurrentSpeaker;
  final bool isDimmed;

  const GlowAvatar({
    super.key,
    required this.name,
    required this.avatar,
    this.isSpeaking = false,
    this.isHuman = false,
    this.hasRaisedHand = false,
    this.isCurrentSpeaker = false,
    this.isDimmed = false,
  });

  @override
  State<GlowAvatar> createState() => _GlowAvatarState();
}

class _GlowAvatarState extends State<GlowAvatar>
    with SingleTickerProviderStateMixin {
  late AnimationController _pulseController;

  @override
  void initState() {
    super.initState();
    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
  }

  @override
  void didUpdateWidget(GlowAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isSpeaking && !_pulseController.isAnimating) {
      _pulseController.repeat(reverse: true);
    } else if (!widget.isSpeaking && _pulseController.isAnimating) {
      _pulseController.stop();
      _pulseController.value = 0;
    }
  }

  @override
  void dispose() {
    _pulseController.dispose();
    super.dispose();
  }

  static const _grayscaleFilter = ColorFilter.matrix(<double>[
    0.2126, 0.7152, 0.0722, 0, 0,
    0.2126, 0.7152, 0.0722, 0, 0,
    0.2126, 0.7152, 0.0722, 0, 0,
    0, 0, 0, 1, 0,
  ]);

  @override
  Widget build(BuildContext context) {
    final color = AppColors.getParticipantColor(widget.name);
    const size = 64.0;

    return AnimatedBuilder(
      animation: _pulseController,
      builder: (context, child) {
        final glowScale = widget.isSpeaking ? 1.0 + _pulseController.value * 0.15 : 1.0;
        final glowAlpha = widget.isSpeaking ? 0.4 + _pulseController.value * 0.3 : 0.0;

        final avatarContent = Opacity(
          opacity: widget.isDimmed ? 0.4 : 1.0,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Stack(
                clipBehavior: Clip.none,
                children: [
                  if (widget.isSpeaking)
                    Transform.scale(
                      scale: glowScale,
                      child: Container(
                        width: size,
                        height: size,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          boxShadow: [
                            BoxShadow(
                              color: color.withValues(alpha: glowAlpha),
                              blurRadius: 24,
                              spreadRadius: 4,
                            ),
                          ],
                        ),
                      ),
                    ),
                  Container(
                    width: size,
                    height: size,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: widget.isCurrentSpeaker
                          ? color.withValues(alpha: 0.15)
                          : AppColors.studyWallLight.withValues(alpha: 0.8),
                      border: Border.all(
                        color: widget.isCurrentSpeaker
                            ? color
                            : widget.isSpeaking
                                ? color.withValues(alpha: 0.7)
                                : AppColors.warmGray.withValues(alpha: 0.3),
                        width: widget.isCurrentSpeaker ? 3 : 2,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.3),
                          blurRadius: 8,
                          offset: const Offset(0, 2),
                        ),
                      ],
                    ),
                    child: Center(
                      child: Text(widget.avatar, style: const TextStyle(fontSize: 28)),
                    ),
                  ),
                  if (widget.hasRaisedHand)
                    Positioned(
                      right: -4,
                      top: -4,
                      child: Container(
                        padding: const EdgeInsets.all(3),
                        decoration: const BoxDecoration(
                          color: AppColors.accentWarm,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.back_hand, size: 14, color: Colors.white),
                      ),
                    ),
                  if (widget.isSpeaking)
                    Positioned(
                      right: 2,
                      bottom: 2,
                      child: Container(
                        width: 14,
                        height: 14,
                        decoration: BoxDecoration(
                          color: AppColors.glowGreen,
                          shape: BoxShape.circle,
                          border: Border.all(color: AppColors.warmWhite, width: 2),
                        ),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                widget.name,
                style: TextStyle(
                  color: widget.isCurrentSpeaker ? color : AppColors.warmWhite,
                  fontWeight: widget.isCurrentSpeaker ? FontWeight.bold : FontWeight.normal,
                  fontSize: 12,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        );

        if (widget.isDimmed) {
          return ColorFiltered(
            colorFilter: _grayscaleFilter,
            child: avatarContent,
          );
        }
        return avatarContent;
      },
    );
  }
}