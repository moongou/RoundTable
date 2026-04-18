import 'package:flutter/material.dart';

import '../../models/discussion_models.dart';
import '../../painters/scroll_card_painter.dart';
import '../../theme/app_colors.dart';
import '../../theme/app_theme.dart';

/// 卷轴话题卡片 - 中国风卷轴形式展示话题
class ScrollTopicCard extends StatefulWidget {
  final Topic topic;
  final bool isSelected;
  final VoidCallback onTap;
  final double rotation;

  const ScrollTopicCard({
    super.key,
    required this.topic,
    this.isSelected = false,
    required this.onTap,
    this.rotation = 0,
  });

  @override
  State<ScrollTopicCard> createState() => _ScrollTopicCardState();
}

class _ScrollTopicCardState extends State<ScrollTopicCard> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Transform.rotate(
          angle: widget.rotation * 3.14159 / 180,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 300),
            width: 180,
            height: 200,
            curve: Curves.easeOutCubic,
            child: CustomPaint(
              painter: ScrollCardPainter(
                isSelected: widget.isSelected,
                isHovered: _isHovered,
              ),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppColors.scrollGold.withValues(alpha: 0.3),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        widget.topic.category,
                        style: TextStyle(
                          fontSize: 10,
                          color: AppColors.scrollTitle,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      widget.topic.title,
                      style: AppTheme.calligraphyStyle(
                        fontSize: 18,
                        color: AppColors.scrollTitle,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      widget.topic.description,
                      style: TextStyle(
                        fontSize: 12,
                        color: AppColors.tableWoodDark,
                        height: 1.4,
                      ),
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const Spacer(),
                    if (widget.topic.tags.isNotEmpty)
                      Wrap(
                        spacing: 4,
                        children: widget.topic.tags.take(2).map((tag) => Text(
                          '#$tag',
                          style: TextStyle(fontSize: 10, color: AppColors.warmGray),
                        )).toList(),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}