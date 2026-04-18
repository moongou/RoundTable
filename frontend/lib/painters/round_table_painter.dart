import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

class RoundTablePainter extends CustomPainter {
  final double glowIntensity;
  final double tableRadius;

  RoundTablePainter({
    this.glowIntensity = 0.5,
    required this.tableRadius,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = tableRadius;

    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.3)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 20);
    canvas.drawCircle(center.translate(0, 8), radius + 4, shadowPaint);

    final edgePaint = Paint()..color = AppColors.tableWoodEdge;
    canvas.drawCircle(center, radius, edgePaint);

    final tablePaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center,
        radius: 0.5,
        colors: [
          AppColors.tableWood.withValues(alpha: 0.9),
          AppColors.tableWood,
          AppColors.tableWoodDark,
          AppColors.tableWoodEdge,
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius));
    canvas.drawCircle(center, radius - 3, tablePaint);

    final ringPaint = Paint()
      ..color = AppColors.tableWoodDark.withValues(alpha: 0.12)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    for (var r = 30.0; r < radius; r += 20 + _pseudoRandom(r) * 15) {
      canvas.drawCircle(center, r, ringPaint);
    }

    final rayPaint = Paint()
      ..color = AppColors.tableWoodDark.withValues(alpha: 0.06)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.5;
    for (var angle = 0.0; angle < 2 * pi; angle += pi / 12) {
      final inner = Offset(
        center.dx + 20 * cos(angle),
        center.dy + 20 * sin(angle),
      );
      final outer = Offset(
        center.dx + (radius - 10) * cos(angle),
        center.dy + (radius - 10) * sin(angle),
      );
      canvas.drawLine(inner, outer, rayPaint);
    }

    final edgeHighlightPaint = Paint()
      ..shader = SweepGradient(
        startAngle: -pi / 4,
        endAngle: pi * 1.5,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.2),
          Colors.transparent,
          AppColors.lampCenter.withValues(alpha: 0.1),
          Colors.transparent,
          AppColors.lampCenter.withValues(alpha: 0.15),
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius))
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawCircle(center, radius - 2, edgeHighlightPaint);

    final glowPaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center,
        radius: 0.3,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.12 * glowIntensity),
          AppColors.lampEdge.withValues(alpha: 0.04 * glowIntensity),
          Colors.transparent,
        ],
      ).createShader(Rect.fromCircle(center: center, radius: radius * 0.6));
    canvas.drawCircle(center, radius * 0.6, glowPaint);
  }

  double _pseudoRandom(double seed) {
    return ((sin(seed * 127.1 + 311.7) * 43758.5453) % 1).abs();
  }

  @override
  bool shouldRepaint(RoundTablePainter oldDelegate) {
    return glowIntensity != oldDelegate.glowIntensity ||
        tableRadius != oldDelegate.tableRadius;
  }
}