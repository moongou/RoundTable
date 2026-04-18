/// 圆桌思辨动画定义
///
/// 使用 flutter_animate 提供统一的动画效果。
/// 包含原有动画和书房沉浸式动画。
library;

import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';

import '../../theme/app_colors.dart';

/// 发言者头像脉冲发光动画
extension SpeakingAnimation on Widget {
  Widget speakPulse({Color? glowColor}) {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shimmer(
            duration: 1500.ms,
            color: (glowColor ?? AppColors.glowAmber).withValues(alpha: 0.3))
        .scale(
          begin: const Offset(1.0, 1.0),
          end: const Offset(1.05, 1.05),
          duration: 1500.ms,
        );
  }
}

/// 消息出现动画
extension MessageAnimation on Widget {
  Widget messageAppear() {
    return animate()
        .fadeIn(duration: 400.ms)
        .slideY(begin: 0.1, end: 0, duration: 400.ms);
  }
}

/// 参与者头像入场动画
extension ParticipantAnimation on Widget {
  Widget participantEntrance({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .scale(
            begin: const Offset(0, 0),
            end: const Offset(1, 1),
            duration: 500.ms,
            curve: Curves.elasticOut)
        .fadeIn(duration: 300.ms);
  }
}

/// 举手/打断抖动动画
extension InterruptAnimation on Widget {
  Widget handRaised() {
    return animate(onPlay: (c) => c.repeat(reverse: true))
        .shake(hz: 2, rotation: 0.05, duration: 500.ms);
  }
}

/// 轮次切换高亮动画
extension TurnChangeAnimation on Widget {
  Widget turnHighlight({Color? color}) {
    return animate()
        .shimmer(
            duration: 300.ms,
            color: (color ?? AppColors.glowBlue).withValues(alpha: 0.3))
        .scale(
          begin: const Offset(0.95, 0.95),
          end: const Offset(1.0, 1.0),
          duration: 300.ms,
        );
  }
}

/// 中心消息区域渐入动画
extension CenterMessageAnimation on Widget {
  Widget centerAppear() {
    return animate().fadeIn(duration: 500.ms, curve: Curves.easeOut).scale(
          begin: const Offset(0.9, 0.9),
          end: const Offset(1.0, 1.0),
          duration: 500.ms,
          curve: Curves.easeOut,
        );
  }
}

// ── 书房沉浸式动画 ──────────────────────────────────────────────────────────

/// 入座时座位的发光渐入动画
extension SeatGlowAnimation on Widget {
  Widget seatGlow({int delayMs = 0, Color? color}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .fadeIn(duration: 600.ms, curve: Curves.easeOut)
        .scale(
          begin: const Offset(0.7, 0.7),
          end: const Offset(1.0, 1.0),
          duration: 600.ms,
          curve: Curves.elasticOut,
        )
        .shimmer(
          duration: 1200.ms,
          color: (color ?? AppColors.amberGold).withValues(alpha: 0.2),
        );
  }
}

/// 灯光暗下动画 - 用于背景变暗聚焦
extension DimToColorAnimation on Widget {
  Widget dimToColor(
      {Color targetColor = AppColors.bookshelf, int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs)).custom(
      duration: 800.ms,
      builder: (context, value, child) {
        return ColorFiltered(
          colorFilter: ColorFilter.mode(
            targetColor.withValues(alpha: 0.3),
            BlendMode.darken,
          ),
          child: child,
        );
      },
    );
  }
}

/// 卷轴展开动画 - 话题卡片展开
extension ScrollUnfoldAnimation on Widget {
  Widget scrollUnfold({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .fadeIn(duration: 500.ms)
        .slideY(
            begin: 0.2, end: 0, duration: 500.ms, curve: Curves.easeOutCubic)
        .scale(
          begin: const Offset(0.8, 0.8),
          end: const Offset(1.0, 1.0),
          duration: 500.ms,
          curve: Curves.easeOutCubic,
        );
  }
}

/// 熄灯效果 - 讨论进入时整体变暗
extension LightsOutAnimation on Widget {
  Widget lightsOut({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs)).custom(
      duration: 1200.ms,
      curve: Curves.easeInOut,
      builder: (context, value, child) {
        return Opacity(
          opacity: 1.0 - value * 0.3,
          child: child,
        );
      },
    );
  }
}

/// 连接警告闪烁动画
extension ConnectionWarningAnimation on Widget {
  Widget connectionWarning({int delayMs = 0}) {
    return animate(delay: Duration(milliseconds: delayMs))
        .fadeIn(duration: 300.ms)
        .then()
        .shimmer(
            duration: 1500.ms,
            color: AppColors.accentWarm.withValues(alpha: 0.3))
        .then()
        .fadeIn(duration: 200.ms);
  }
}
