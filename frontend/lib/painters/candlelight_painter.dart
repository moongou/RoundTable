import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

class CandlelightPainter extends CustomPainter {
  final List<CandleParticle> particles;
  final double animationValue;

  CandlelightPainter({
    required this.particles,
    required this.animationValue,
  });

  @override
  void paint(Canvas canvas, Size size) {
    for (final particle in particles) {
      final progress = (animationValue + particle.phaseOffset) % 1.0;
      final x = particle.baseX + sin(progress * 2 * pi * particle.wanderSpeedX) * particle.wanderRadius;
      final y = particle.baseY + sin(progress * 2 * pi * particle.wanderSpeedY) * particle.wanderRadius;

      final brightness = 0.4 + 0.6 * (0.5 + 0.5 * sin(progress * 2 * pi * 3));
      final alpha = particle.maxAlpha * brightness;

      final glowPaint = Paint()
        ..color = AppColors.candleGlow.withValues(alpha: alpha * 0.3)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 8);
      canvas.drawCircle(Offset(x, y), particle.size * 3, glowPaint);

      final corePaint = Paint()
        ..color = AppColors.candleFlame.withValues(alpha: alpha);
      canvas.drawCircle(Offset(x, y), particle.size, corePaint);
    }
  }

  @override
  bool shouldRepaint(CandlelightPainter oldDelegate) {
    return animationValue != oldDelegate.animationValue;
  }
}

class CandleParticle {
  final double baseX;
  final double baseY;
  final double size;
  final double maxAlpha;
  final double phaseOffset;
  final double wanderRadius;
  final double wanderSpeedX;
  final double wanderSpeedY;

  const CandleParticle({
    required this.baseX,
    required this.baseY,
    this.size = 3,
    this.maxAlpha = 0.6,
    this.phaseOffset = 0,
    this.wanderRadius = 15,
    this.wanderSpeedX = 0.5,
    this.wanderSpeedY = 0.7,
  });

  static List<CandleParticle> generate({
    required double areaWidth,
    required double areaHeight,
    int count = 18,
    Random? random,
  }) {
    final rng = random ?? Random(42);
    return List.generate(count, (i) {
      return CandleParticle(
        baseX: rng.nextDouble() * areaWidth,
        baseY: rng.nextDouble() * areaHeight,
        size: 2 + rng.nextDouble() * 3,
        maxAlpha: 0.3 + rng.nextDouble() * 0.4,
        phaseOffset: rng.nextDouble(),
        wanderRadius: 8 + rng.nextDouble() * 20,
        wanderSpeedX: 0.3 + rng.nextDouble() * 0.5,
        wanderSpeedY: 0.4 + rng.nextDouble() * 0.6,
      );
    });
  }
}