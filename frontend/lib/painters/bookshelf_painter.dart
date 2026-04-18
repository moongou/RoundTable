import 'dart:math';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

class BookshelfPainter extends CustomPainter {
  final double lightIntensity;

  BookshelfPainter({this.lightIntensity = 0.8});

  @override
  void paint(Canvas canvas, Size size) {
    final wallPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          AppColors.bookshelf,
          AppColors.studyWall,
          AppColors.studyWall.withValues(alpha: 0.9),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), wallPaint);

    final grainPaint = Paint()
      ..color = AppColors.studyWallLight.withValues(alpha: 0.08)
      ..strokeWidth = 1;
    for (var y = 0.0; y < size.height; y += 12 + _pseudoRandom(y) * 8) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grainPaint);
    }

    final shelfPaint = Paint()..color = AppColors.tableWoodDark.withValues(alpha: 0.3);
    final shelfHighlight = Paint()
      ..color = AppColors.lampCenter.withValues(alpha: 0.05 * lightIntensity);

    final shelfYs = [size.height * 0.15, size.height * 0.45, size.height * 0.72];
    for (final shelfY in shelfYs) {
      canvas.drawRect(Rect.fromLTWH(0, shelfY, size.width, 4), shelfPaint);
      canvas.drawRect(Rect.fromLTWH(0, shelfY, size.width, 2), shelfHighlight);
    }

    final bookPaint = Paint()
      ..color = AppColors.studyWallLight.withValues(alpha: 0.06);
    for (final shelfY in shelfYs) {
      var x = 20.0;
      while (x < size.width - 20) {
        final bookWidth = 8 + _pseudoRandom(x + shelfY) * 12;
        canvas.drawRect(
          Rect.fromLTWH(x, shelfY - 40 - _pseudoRandom(x) * 20, bookWidth, 38),
          bookPaint,
        );
        x += bookWidth + 2 + _pseudoRandom(x) * 4;
      }
    }

    final lightPaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.topCenter,
        radius: 0.6,
        colors: [
          AppColors.lampCenter.withValues(alpha: 0.15 * lightIntensity),
          AppColors.lampEdge.withValues(alpha: 0.05 * lightIntensity),
          Colors.transparent,
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), lightPaint);

    final vignettePaint = Paint()
      ..shader = RadialGradient(
        center: Alignment.center,
        radius: 0.7,
        colors: [
          Colors.transparent,
          Colors.transparent,
          AppColors.bookshelf.withValues(alpha: 0.5),
        ],
      ).createShader(Rect.fromLTWH(0, 0, size.width, size.height));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), vignettePaint);
  }

  double _pseudoRandom(double seed) {
    return ((sin(seed * 127.1 + 311.7) * 43758.5453) % 1).abs();
  }

  @override
  bool shouldRepaint(BookshelfPainter oldDelegate) {
    return lightIntensity != oldDelegate.lightIntensity;
  }
}