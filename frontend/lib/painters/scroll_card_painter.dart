import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

class ScrollCardPainter extends CustomPainter {
  final bool isSelected;
  final bool isHovered;

  ScrollCardPainter({
    this.isSelected = false,
    this.isHovered = false,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final rollHeight = 8.0;
    final rollRadius = 4.0;
    final padding = 4.0;

    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.15)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4);
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, size.height - rollHeight - 2, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      shadowPaint,
    );

    final bottomRollPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [AppColors.scrollGold, AppColors.tableWoodDark, AppColors.scrollGold],
      ).createShader(Rect.fromLTWH(0, size.height - rollHeight, size.width, rollHeight));
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, size.height - rollHeight - padding, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      bottomRollPaint,
    );

    final paperPaint = Paint()
      ..color = isHovered || isSelected
          ? AppColors.parchmentDark
          : AppColors.parchment;
    final paperRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
      Radius.circular(2),
    );
    canvas.drawRRect(paperRect, paperPaint);

    final texturePaint = Paint()
      ..color = AppColors.parchmentDark.withValues(alpha: 0.3)
      ..strokeWidth = 0.5;
    for (var y = rollHeight + 20.0; y < size.height - rollHeight - 10; y += 18) {
      canvas.drawLine(
        Offset(padding + 10, y),
        Offset(size.width - padding - 10, y),
        texturePaint,
      );
    }

    if (isSelected) {
      final borderPaint = Paint()
        ..color = AppColors.scrollGold
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
          Radius.circular(2),
        ),
        borderPaint,
      );
    } else if (isHovered) {
      final borderPaint = Paint()
        ..color = AppColors.scrollGold.withValues(alpha: 0.5)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(padding, rollHeight + padding, size.width - padding * 2, size.height - rollHeight * 2 - padding * 2),
          Radius.circular(2),
        ),
        borderPaint,
      );
    }

    final topRollPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [AppColors.scrollGold, AppColors.tableWoodDark, AppColors.scrollGold],
      ).createShader(Rect.fromLTWH(0, 0, size.width, rollHeight));
    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(padding, padding, size.width - padding * 2, rollHeight),
        Radius.circular(rollRadius),
      ),
      topRollPaint,
    );

    if (isSelected) {
      final glowPaint = Paint()
        ..color = AppColors.lampCenter.withValues(alpha: 0.15)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 12);
      canvas.drawRect(
        Rect.fromLTWH(0, 0, size.width, size.height),
        glowPaint,
      );
    }
  }

  @override
  bool shouldRepaint(ScrollCardPainter oldDelegate) {
    return isSelected != oldDelegate.isSelected || isHovered != oldDelegate.isHovered;
  }
}